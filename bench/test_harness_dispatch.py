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


def _mk(**kw):
    d = dict(correct=True, tool_ok=True, used_tool=True, negative=False, hallucinated=False,
             malformed_json=0, status="ok", efficiency=1.0, category="c", difficulty=None,
             iterations=2, latency_s=0.1, completion_tokens=0)
    d.update(kw); return d


def test_aggregate_weights_70_15_15_and_maxiter_penalty():
    import harness
    # perfecto (correct+tool+eff=1) -> 100
    assert harness.aggregate([_mk(), _mk()])["final_score"] == 100.0
    # pesos 70/15/15: fallar solo tool quita 15 -> 85
    assert harness.aggregate([_mk(tool_ok=False)])["final_score"] == 85.0
    # max_iter penaliza explícitamente (además de bajar correct/eff)
    a = harness.aggregate([_mk(), _mk(), _mk(status="max_iter", correct=False, tool_ok=False, efficiency=0.2)])
    assert a["max_iter"] == 1
    assert a["final_score"] < 100.0
    assert "avg_iterations" in a
