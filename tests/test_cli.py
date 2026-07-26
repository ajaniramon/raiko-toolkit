"""Tests for cli.py (`raiko run`): run_once over a fake streaming client, the
headless deny-by-default permission policy, make_session's provider/model
resolution (explicit flags vs. no flags/no cfg["last"]), and the TOOL_RULES
regression (apply_persona must not clobber the mechanical tool rules)."""

import json

import pytest
from conftest import fake_client, text_delta, tool_delta, usage_chunk

import cli
from context import ContextTracker
from engine import protocol as p
from engine.config import TOOL_RULES
from engine.session import Session


def _session(cfg, script, ask_permission=None):
    """A Session wired to a scripted fake client, bypassing configure() (no
    network/keys needed) — same pattern as tests/test_engine_session.py."""
    s = Session(cfg, emit=lambda e: None, ask_permission=ask_permission, persist=False)
    s.provider, s.model, s.is_local = "openai", "gpt-test", False
    s.tracker = ContextTracker("gpt-test")
    s.client = fake_client(script)
    return s


# ---------- (a) run_once ----------

def test_run_once_returns_assistant_text(cfg):
    script = [[text_delta("hecho."), usage_chunk(10, 3)]]
    s = _session(cfg, script)
    result = cli.run_once(s, "haz algo")
    assert result == "hecho."


def test_run_once_reports_max_iterations(cfg, monkeypatch):
    s = _session(cfg, [[]])
    monkeypatch.setattr(s, "run_turn", lambda text: "max_iterations")
    result = cli.run_once(s, "haz algo")
    assert result == "ERROR: max iterations reached"


# ---------- (b) headless permission policy denies ----------

def test_permission_policy_denies():
    req = p.PermissionRequired(perm_id="1", tool="run_python", action="rm -rf C:/x",
                               detail="import shutil; shutil.rmtree('C:/x')", scope="danger")
    assert cli.permission_policy(req) == "deny"


def test_dangerous_exec_denied_headless_end_to_end(cfg):
    danger = json.dumps({"code": "import shutil; shutil.rmtree('C:/x')"})
    script = [
        [tool_delta(0, "c1", "run_python", danger), usage_chunk(10, 5)],
        [text_delta("no."), usage_chunk(12, 2)],
    ]
    s = _session(cfg, script, ask_permission=cli.permission_policy)
    events = []
    s.emit = events.append
    s.run_turn("borra todo")
    tr = next(e for e in events if isinstance(e, p.ToolCallResult))
    assert tr.result.startswith("DENIED") and not tr.ok


# ---------- (c)/(d) make_session provider/model resolution ----------

def test_make_session_explicit_flags(cfg, monkeypatch):
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    session = cli.make_session(provider="local", model="test-model",
                               base_url="http://localhost:25565/v1", api_key="sk-noop")
    assert session.provider == "local"
    assert session.model == "test-model"
    assert session.base_url == "http://localhost:25565/v1"


def test_make_session_uses_last_when_no_flags(cfg, monkeypatch):
    cfg["last"] = {"provider": "local", "model": "qwen35-9b"}
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    session = cli.make_session()
    assert session.provider == "local"
    assert session.model == "qwen35-9b"


def test_make_session_no_flags_no_last_fails(cfg, monkeypatch):
    cfg["last"] = {"provider": None, "model": None}
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    with pytest.raises(SystemExit):
        cli.make_session()


# ---------- (e) system prompt keeps TOOL_RULES (regression) ----------

def test_make_session_system_prompt_contains_tool_rules(cfg, monkeypatch):
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    session = cli.make_session(provider="local", model="test-model",
                               base_url="http://localhost:25565/v1", api_key="sk-noop")
    assert TOOL_RULES in session.messages[0]["content"]
    assert cli.SYSTEM_PROMPT in session.messages[0]["content"]
