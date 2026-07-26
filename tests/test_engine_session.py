"""Engine turn-loop smoke tests (no network): streaming, tool dispatch with diff,
permission hook decisions, allowlist persistence, interrupt."""

import json
import os
from pathlib import Path

from conftest import fake_client, text_delta, tool_delta, usage_chunk

from engine import protocol as p
from engine.session import Session
from context import ContextTracker
from tools import TOOLS


def make_session(cfg, script, hook=None):
    events = []
    s = Session(cfg, emit=events.append, ask_permission=hook, persist=False)
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
