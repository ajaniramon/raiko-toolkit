"""Engine turn-loop smoke tests (no network): streaming, tool dispatch with diff,
permission hook decisions, allowlist persistence, interrupt, and the per-session
working directory (relative paths, workspace, concurrent sessions)."""

import json
import os
import threading
from pathlib import Path

from conftest import fake_client, text_delta, tool_delta, usage_chunk

import tools
from engine import protocol as p
from engine import store
from engine.session import Session
from context import ContextTracker
from tools import TOOLS


def make_session(cfg, script, hook=None, cwd=None, max_iterations=None):
    events = []
    s = Session(cfg, emit=events.append, ask_permission=hook, persist=False, cwd=cwd,
                max_iterations=max_iterations)
    s.provider, s.model, s.is_local = "openai", "gpt-test", False
    s.tracker = ContextTracker("gpt-test")
    s.client = fake_client(script)
    return s, events


def test_turn_with_tool_diff_and_text(cfg):
    ws = cfg["permissions"]["workspace"]
    target = os.path.join(ws, "hello.txt")
    script = [
        [tool_delta(0, "c1", "write_file",
                    json.dumps({"path": target, "content": "hola\n"})),
         usage_chunk(100, 20)],
        [text_delta("hecho."), usage_chunk(140, 4)],
    ]
    s, events = make_session(cfg, script)
    assert s.run_turn("crea hello.txt") == "completed"
    kinds = [type(e).__name__ for e in events]
    assert kinds == ["TurnStarted", "CostUpdate", "SegmentEnd", "ToolCallStarted",
                     "ToolCallResult", "TextDelta", "CostUpdate", "SegmentEnd", "TurnDone"]
    tr = next(e for e in events if isinstance(e, p.ToolCallResult))
    assert tr.ok and tr.path == target and "hola" in tr.diff
    assert open(target).read() == "hola\n"
    assert events[-1].output_tokens == 24
    assert s.messages[-1]["content"] == "hecho."


def test_dangerous_exec_denied_by_hook(cfg):
    asked = []
    danger = json.dumps({"code": "import shutil; shutil.rmtree('C:/x')"})
    script = [
        [tool_delta(0, "c1", "run_python", danger), usage_chunk(10, 5)],
        [text_delta("no."), usage_chunk(12, 2)],
    ]
    s, events = make_session(cfg, script, hook=lambda r: (asked.append(r), "deny")[1])
    s.run_turn("borra todo")
    tr = next(e for e in events if isinstance(e, p.ToolCallResult))
    assert tr.result.startswith("DENIED by user") and not tr.ok
    assert asked[0].tool == "run_python" and asked[0].scope == "danger"


def test_allow_always_persists_allowlist(cfg):
    outside = os.path.join(os.path.dirname(cfg["permissions"]["workspace"]), "fuera.txt")
    script = [
        [tool_delta(0, "c1", "write_file",
                    json.dumps({"path": outside, "content": "x\n"})),
         usage_chunk(10, 5)],
        [text_delta("ok"), usage_chunk(12, 2)],
    ]
    s, events = make_session(cfg, script, hook=lambda r: "allow_always")
    s.run_turn("escribe fuera")
    assert open(outside).read() == "x\n"
    assert "write_file" in cfg["permissions"]["allow"]


def test_no_hook_denies(cfg):
    danger = json.dumps({"code": "import shutil; shutil.rmtree('C:/x')"})
    script = [
        [tool_delta(0, "c1", "run_python", danger), usage_chunk(10, 5)],
        [text_delta("ok"), usage_chunk(12, 2)],
    ]
    s, events = make_session(cfg, script, hook=None)
    s.run_turn("peligro")
    tr = next(e for e in events if isinstance(e, p.ToolCallResult))
    assert tr.result.startswith("DENIED")


def test_blocked_tools_error_even_when_benign(cfg):
    script = [
        [tool_delta(0, "c1", "run_python", json.dumps({"code": "print(1)"})),
         usage_chunk(10, 5)],
        [text_delta("ok"), usage_chunk(12, 2)],
    ]
    s, events = make_session(cfg, script, hook=lambda r: "allow_once")
    s.blocked_tools.add("run_python")
    s.run_turn("ejecuta")
    tr = next(e for e in events if isinstance(e, p.ToolCallResult))
    assert not tr.ok and "disabled on this interface" in tr.result


def test_always_ask_overrides_allowlist(cfg):
    cfg["permissions"]["allow"] = ["run_python"]
    asked = []
    script = [
        [tool_delta(0, "c1", "run_python", json.dumps({"code": "print(1)"})),
         usage_chunk(10, 5)],
        [text_delta("ok"), usage_chunk(12, 2)],
    ]
    s, events = make_session(cfg, script,
                             hook=lambda r: (asked.append(r), "allow_once")[1])
    s.always_ask_tools.add("run_python")
    s.run_turn("ejecuta")
    assert asked, "always_ask tool did not consult the hook despite the allowlist"
    tr = next(e for e in events if isinstance(e, p.ToolCallResult))
    assert tr.ok


def test_interrupt_mid_stream(cfg):
    s, events = make_session(cfg, [[]])
    class InterruptingChunks:
        def __iter__(self):
            yield text_delta("empiezo...")
            s.interrupt()
            yield text_delta("no deberia verse")
        def close(self):
            pass
    s.client = fake_client([InterruptingChunks()])
    assert s.run_turn("largo") == "interrupted"
    texts = "".join(e.text for e in events if isinstance(e, p.TextDelta))
    assert texts == "empiezo..."


def test_rewind_and_clear(cfg):
    script = [[text_delta("respuesta"), usage_chunk(10, 3)]]
    s, events = make_session(cfg, script)
    s.run_turn("pregunta")
    assert s.rewind_last_user() == "pregunta"
    assert all(m.get("role") != "user" for m in s.messages)
    s.session_cost = 1.23
    s.clear()
    assert len(s.messages) == 1 and s.messages[0]["role"] == "system"
    assert s.session_cost == 0.0 and s.session_id is None


# ---------- Agent Skills integration ----------

def _write_skill(root, name, description="a test skill for the test suite", body="Do the thing.\n"):
    """Create <root>/<name>/SKILL.md with minimal valid frontmatter."""
    d = os.path.join(root, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(f"---\nname: {name}\ndescription: {description}\n---\n{body}")
    return d


def _isolate_skill_roots(monkeypatch, tmp_path):
    """Point every root discover_skills() scans (RAIKO_HOME/skills, ~/.agents/skills,
    ~/.claude/skills) at an empty tmp dir, so tests are unaffected by whatever real
    skills happen to exist on the machine running the suite."""
    monkeypatch.setenv("RAIKO_HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")


def test_skills_index_appears_in_system_prompt(cfg, tmp_path, monkeypatch):
    _isolate_skill_roots(monkeypatch, tmp_path)
    _write_skill(os.path.join(str(tmp_path), "skills"), "git-release")
    s = Session(cfg, emit=lambda e: None, persist=False)
    prompt = s.messages[0]["content"]
    assert "<available_skills>" in prompt
    assert "git-release" in prompt


def test_execute_tool_skill_loads_body(cfg, tmp_path, monkeypatch):
    _isolate_skill_roots(monkeypatch, tmp_path)
    _write_skill(os.path.join(str(tmp_path), "skills"), "git-release", body="Step 1: tag the release.\n")
    s = Session(cfg, emit=lambda e: None, persist=False)
    result = s.execute_tool("skill", json.dumps({"name": "git-release"}))
    assert "Step 1: tag the release." in result
    assert "git-release" in result


def test_execute_tool_skill_unknown_name(cfg, tmp_path, monkeypatch):
    _isolate_skill_roots(monkeypatch, tmp_path)
    _write_skill(os.path.join(str(tmp_path), "skills"), "git-release")
    s = Session(cfg, emit=lambda e: None, persist=False)
    result = s.execute_tool("skill", json.dumps({"name": "does-not-exist"}))
    assert result.startswith("ERROR: unknown skill")


def test_no_skills_means_no_skill_tool_or_prompt_mention(cfg, tmp_path, monkeypatch):
    _isolate_skill_roots(monkeypatch, tmp_path)   # no skills/ subfolder created
    s = Session(cfg, emit=lambda e: None, persist=False)
    assert "skill" not in s.messages[0]["content"]
    assert s._skill_tools == []
    tool_names = [t["function"]["name"] for t in (TOOLS + s.mcp_tools + s._skill_tools)]
    assert "skill" not in tool_names


def test_apply_persona_keeps_skills_block(cfg, tmp_path, monkeypatch):
    _isolate_skill_roots(monkeypatch, tmp_path)
    _write_skill(os.path.join(str(tmp_path), "skills"), "git-release")
    s = Session(cfg, emit=lambda e: None, persist=False)
    s.apply_persona("A custom persona for this test.")
    prompt = s.messages[0]["content"]
    assert "A custom persona for this test." in prompt
    assert "<available_skills>" in prompt
    assert "git-release" in prompt


def test_clear_keeps_skills_block(cfg, tmp_path, monkeypatch):
    _isolate_skill_roots(monkeypatch, tmp_path)
    _write_skill(os.path.join(str(tmp_path), "skills"), "git-release")
    script = [[text_delta("respuesta"), usage_chunk(10, 3)]]
    s, events = make_session(cfg, script)
    s.run_turn("hola")
    s.clear()
    prompt = s.messages[0]["content"]
    assert "<available_skills>" in prompt
    assert "git-release" in prompt


# ---------------------------------------------------------------------------
# Per-session working directory
# ---------------------------------------------------------------------------

def _project(cfg, tmp_path, name):
    """A folder to root a session in, with the config workspace left unset so
    the session's own cwd is the write boundary."""
    cfg["permissions"]["workspace"] = ""
    d = tmp_path / name
    d.mkdir()
    return d


def test_relative_tool_paths_resolve_against_the_session_cwd(cfg, tmp_path):
    """A write to "hola.txt" lands in the session's folder, not the process's."""
    project = _project(cfg, tmp_path, "project")
    script = [
        [tool_delta(0, "c1", "write_file",
                    json.dumps({"path": "hola.txt", "content": "hola\n"})),
         usage_chunk(100, 20)],
        [text_delta("hecho."), usage_chunk(140, 4)],
    ]
    s, events = make_session(cfg, script, cwd=str(project))
    assert s.run_turn("crea hola.txt") == "completed"
    tr = next(e for e in events if isinstance(e, p.ToolCallResult))
    assert tr.ok, tr.result
    assert (project / "hola.txt").read_text(encoding="utf-8") == "hola\n"
    assert not (Path.cwd() / "hola.txt").exists()


def test_workspace_defaults_to_the_session_cwd(cfg, tmp_path):
    project = _project(cfg, tmp_path, "project")
    s, _ = make_session(cfg, [[]], cwd=str(project))
    assert s.workspace() == str(project)
    assert s._in_workspace("nested/file.txt")
    assert not s._in_workspace(str(tmp_path / "elsewhere" / "file.txt"))


def test_explicit_workspace_still_wins_over_the_cwd(cfg, tmp_path):
    """permissions.workspace is a user-level confinement: starting the agent in
    another folder must not widen it."""
    project = tmp_path / "project"
    project.mkdir()
    s, _ = make_session(cfg, [[]], cwd=str(project))
    assert s.workspace() == cfg["permissions"]["workspace"]
    assert not s._in_workspace("hola.txt")


def test_session_saves_its_cwd_and_resume_restores_cwd_and_workspace(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(store, "SESSIONS_DIR", str(tmp_path / "sessions"))
    project = _project(cfg, tmp_path, "project")
    script = [[text_delta("hecho."), usage_chunk(10, 3)]]
    s, _ = make_session(cfg, script, cwd=str(project))
    s.persist = True
    s.run_turn("hola")

    saved = store.load_session(s.session_id)
    assert saved["cwd"] == str(project)
    assert store.last_session(project)["id"] == s.session_id

    other, _ = make_session(cfg, [[]], cwd=str(tmp_path))
    other.resume(saved)
    assert other.cwd == str(project)
    assert other.workspace() == str(project)      # workspace follows the cwd back
    assert other.messages == saved["messages"]


def test_resume_keeps_an_explicitly_chosen_cwd(cfg, tmp_path):
    project, elsewhere = _project(cfg, tmp_path, "project"), _project(cfg, tmp_path, "elsewhere")
    saved = {"id": "s1", "cwd": str(project), "messages": [{"role": "user", "content": "hi"}]}
    s, _ = make_session(cfg, [[]], cwd=str(elsewhere))
    s.resume(saved, keep_cwd=True)
    assert s.cwd == str(elsewhere)


def test_resume_of_a_session_whose_folder_is_gone_keeps_the_current_cwd(cfg, tmp_path):
    project = _project(cfg, tmp_path, "project")
    saved = {"id": "s1", "cwd": str(tmp_path / "deleted"),
             "messages": [{"role": "user", "content": "hi"}]}
    s, _ = make_session(cfg, [[]], cwd=str(project))
    s.resume(saved)
    assert s.cwd == str(project)


def test_pre_cwd_sessions_resume_without_a_cwd_key(cfg, tmp_path):
    """Sessions saved before this feature have no cwd: resuming one must not
    crash and must leave the session where it already is."""
    project = _project(cfg, tmp_path, "project")
    legacy = {"id": "old", "provider": "openai", "model": "gpt-test",
              "messages": [{"role": "system", "content": "s"},
                           {"role": "user", "content": "hola"}]}
    s, _ = make_session(cfg, [[]], cwd=str(project))
    s.resume(legacy)
    assert s.cwd == str(project)
    assert s.messages == legacy["messages"]


def test_concurrent_sessions_do_not_share_a_working_directory(cfg, tmp_path, monkeypatch):
    """The regression that rules out a process-wide chdir: two sessions, two
    folders, both inside a tool call at the same time. Each write must land in
    its own session's folder."""
    alpha = _project(cfg, tmp_path, "alpha")
    beta = _project(cfg, tmp_path, "beta")
    barrier = threading.Barrier(2, timeout=10)
    real_write = tools.DISPATCH["write_file"]

    def write_file(path, content):
        barrier.wait()          # hold both sessions inside the tool simultaneously
        return real_write(path, content)

    monkeypatch.setitem(tools.DISPATCH, "write_file", write_file)

    def turn(cwd, text):
        script = [
            [tool_delta(0, "c1", "write_file",
                        json.dumps({"path": "out.txt", "content": text})),
             usage_chunk(10, 5)],
            [text_delta("ok"), usage_chunk(12, 2)],
        ]
        s, _ = make_session(cfg, script, cwd=str(cwd))
        s.run_turn("escribe out.txt")

    threads = [threading.Thread(target=turn, args=(alpha, "soy alpha")),
               threading.Thread(target=turn, args=(beta, "soy beta"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads)

    assert (alpha / "out.txt").read_text(encoding="utf-8") == "soy alpha"
    assert (beta / "out.txt").read_text(encoding="utf-8") == "soy beta"


def _echo_round(call_id):
    """One tool-call round the engine will always execute (exec is allowed via
    skip_permissions in these tests), so the loop keeps iterating."""
    return [tool_delta(0, call_id, "run_python", json.dumps({"code": "print(1)"})),
            usage_chunk(10, 5)]


def test_session_cap_overrides_the_config_cap(cfg):
    cfg["max_iterations"] = 8
    script = [_echo_round(f"c{i}") for i in range(4)]
    s, events = make_session(cfg, script, max_iterations=2)
    s.skip_permissions = True
    assert s.iteration_cap() == 2
    assert s.run_turn("sigue") == "max_iterations"
    # exactly 2 rounds ran, so the 3rd scripted response was never requested
    assert len([e for e in events if isinstance(e, p.ToolCallResult)]) == 2
    # the cap is announced before TurnDone, naming itself
    assert isinstance(events[-2], p.Notice) and events[-2].kind == "warning"
    assert "2 tool-call rounds" in events[-2].text


def test_config_cap_applies_when_the_session_has_none(cfg):
    cfg["max_iterations"] = 1
    s, events = make_session(cfg, [_echo_round("c1")])
    s.skip_permissions = True
    assert s.iteration_cap() == 1
    assert s.run_turn("sigue") == "max_iterations"


def test_junk_caps_fall_back_to_the_config_instead_of_raising(cfg):
    cfg["max_iterations"] = 8
    for junk in (0, -5, "many", True, None):
        s, _ = make_session(cfg, [[]], max_iterations=junk)
        assert s.iteration_cap() == 8, junk


def test_set_max_iterations_changes_the_cap_between_turns(cfg):
    s, _ = make_session(cfg, [[]], max_iterations=3)
    assert s.set_max_iterations(40) == 40 and s.iteration_cap() == 40
    assert s.set_max_iterations(None) == cfg["max_iterations"]   # back to the config


def test_session_saves_its_cap_and_resume_restores_it(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(store, "SESSIONS_DIR", str(tmp_path / "sessions"))
    script = [[text_delta("hecho."), usage_chunk(10, 3)]]
    s, _ = make_session(cfg, script, max_iterations=42)
    s.persist = True
    s.run_turn("hola")

    saved = store.load_session(s.session_id)
    assert saved["max_iterations"] == 42

    other, _ = make_session(cfg, [[]])
    other.resume(saved)
    assert other.iteration_cap() == 42

    chosen, _ = make_session(cfg, [[]], max_iterations=7)
    chosen.resume(saved)          # a cap picked for THIS session wins over the saved one
    assert chosen.iteration_cap() == 7


def test_sessions_with_different_caps_never_touch_the_shared_config(cfg):
    """`raiko web` hands the same cfg dict to every session: a per-session cap
    must live on the session, not in that dict."""
    alpha, _ = make_session(cfg, [[]], max_iterations=10)
    beta, _ = make_session(cfg, [[]], max_iterations=200)
    assert (alpha.iteration_cap(), beta.iteration_cap()) == (10, 200)
    assert cfg["max_iterations"] == 8


def test_exec_tools_run_in_the_session_cwd(cfg, tmp_path):
    project = _project(cfg, tmp_path, "project")
    script = [
        [tool_delta(0, "c1", "run_python",
                    json.dumps({"code": "import os; print(os.getcwd())"})),
         usage_chunk(10, 5)],
        [text_delta("ok"), usage_chunk(12, 2)],
    ]
    s, events = make_session(cfg, script, cwd=str(project))
    s.skip_permissions = True
    s.run_turn("dime el cwd")
    tr = next(e for e in events if isinstance(e, p.ToolCallResult))
    assert os.path.realpath(tr.result.strip()) == os.path.realpath(str(project))
