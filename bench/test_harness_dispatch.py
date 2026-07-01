# bench/test_harness_dispatch.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import call_tool

def test_call_tool_uses_injected_dispatch():
    calls = {}
    def fake(**kw):
        calls.update(kw)
        return "FAKE-OK"
    out = call_tool("jira_search", '{"query": "outage"}', dispatch={"jira_search": fake})
    assert out == "FAKE-OK"
    assert calls == {"query": "outage"}

def test_call_tool_unknown_in_injected_dispatch():
    out = call_tool("nope", "{}", dispatch={"jira_search": lambda **k: "x"})
    assert out.startswith("ERROR: unknown tool")

def test_run_task_passes_grader_ctx(monkeypatch):
    import harness
    # cliente falso: primera respuesta sin tool_calls, contenido = "done"
    class _Msg:
        content = "done"; tool_calls = []; reasoning_content = None
    class _Choice:
        message = _Msg()
    class _Resp:
        choices = [_Choice()]; usage = None
    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kw): return _Resp()
    seen = {}
    task = {"id": "t", "category": "c", "prompt": "p", "expect_tools": [],
            "check": lambda a, ctx: seen.setdefault("ctx", ctx) or (a == "done")}
    r = harness.run_task(_Client(), "m", task, root=".", enable_thinking=False,
                         tools=[], grader_ctx={"marker": 1})
    assert r["correct"] is True
    assert seen["ctx"] == {"marker": 1}
