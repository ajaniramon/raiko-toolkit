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


class _AttrCtx:
    pass


def _tool_client(n_tool_turns):
    """Cliente falso: n turnos con tool_calls y luego (si el loop sigue vivo) texto."""
    class _TC:
        id = "tc1"
        class function:
            name = "confluence_get"; arguments = "{}"
    calls = {"n": 0}
    def create(**kw):
        class _Msg:
            reasoning_content = None
        m = _Msg()
        if calls["n"] < n_tool_turns:
            m.content = ""; m.tool_calls = [_TC()]
        else:
            m.content = "final answer"; m.tool_calls = []
        calls["n"] += 1
        class _Choice: message = m
        class _Resp: choices = [_Choice()]; usage = None
        return _Resp()
    class _Client:
        class chat:
            class completions:
                pass
    _Client.chat.completions.create = staticmethod(create)
    return _Client()


def test_max_iter_with_side_effect_is_not_correct():
    """El bug del reporte v1: side-effect aterrizado + run colgado en max_iter puntuaba
    1.00 con '(no answer)'. Sin respuesta final NO hay task correcta, y eff = 0."""
    import harness
    task = {"id": "t", "category": "c", "prompt": "p", "expect_tools": ["confluence_get"],
            "check": lambda a, ctx: True, "iter_budget": 6}   # el grader de estado diría OK
    r = harness.run_task(_tool_client(99), "m", task, root=".", enable_thinking=False,
                         tools=[], dispatch={"confluence_get": lambda **k: "page"},
                         grader_ctx=_AttrCtx())
    assert r["status"] == "max_iter"
    assert r["correct"] is False
    assert r["efficiency"] == 0.0


def test_grader_ctx_gets_tool_calls():
    import harness
    ctx = _AttrCtx()
    task = {"id": "t", "category": "c", "prompt": "p", "expect_tools": ["confluence_get"],
            "check": lambda a, c: "confluence_get" in c.tool_calls}
    r = harness.run_task(_tool_client(2), "m", task, root=".", enable_thinking=False,
                         tools=[], dispatch={"confluence_get": lambda **k: "page"},
                         grader_ctx=ctx)
    assert r["correct"] is True
    assert ctx.tool_calls == ["confluence_get", "confluence_get"]


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
    # max_iter cuenta vía per-task (correct=False, eff=0) — sin multa extra en aggregate
    a = harness.aggregate([_mk(), _mk(), _mk(status="max_iter", correct=False, tool_ok=False, efficiency=0.0)])
    assert a["max_iter"] == 1
    assert a["final_score"] == round(100 * (0.70 * 2/3 + 0.15 * 2/3 + 0.15 * 2/3), 1)
    assert "avg_iterations" in a


def test_dsml_fallback_parses_and_executes():
    """Tool calls emitidos como texto DSML (glitch de DeepSeek vía provider): el harness
    los parsea, los ejecuta y el run continúa en vez de morir con markup como respuesta."""
    import harness
    sample = ('Let me check.\n<｜DSML｜tool_calls>\n'
              '<｜DSML｜invoke name="jira_get">\n'
              '<｜DSML｜parameter name="key" string="true">OPS-777</｜DSML｜parameter>\n'
              '</｜DSML｜invoke>\n</｜DSML｜tool_calls>')
    calls = harness._parse_dsml_calls(sample)
    assert calls == [("jira_get", {"key": "OPS-777"})]

    class _TC:  # primer turno: DSML en texto; segundo: respuesta final
        pass
    turns = {"n": 0}
    def create(**kw):
        class _Msg:
            reasoning_content = None; tool_calls = []
        m = _Msg()
        m.content = sample if turns["n"] == 0 else "final answer: Blocked"
        turns["n"] += 1
        class _Choice: message = m
        class _Resp: choices = [_Choice()]; usage = None
        return _Resp()
    class _Client:
        class chat:
            class completions:
                pass
    _Client.chat.completions.create = staticmethod(create)

    task = {"id": "t", "category": "c", "prompt": "p", "expect_tools": ["jira_get"],
            "check": lambda a, c: "Blocked" in a}
    r = harness.run_task(_Client(), "m", task, root=".", enable_thinking=False,
                         tools=[], dispatch={"jira_get": lambda **k: "status: Blocked"},
                         grader_ctx=_AttrCtx())
    assert r["correct"] is True
    assert r["dsml_recovered"] == 1
    assert r["tools_called"] == ["jira_get"]
