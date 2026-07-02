import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_run_model_is_importable_and_injectable(tmp_path, monkeypatch):
    import run_hard_atlassian as rha
    # fake client: always answers "nope" with no tool calls -> every task completes fast
    class _Msg: content = "nope"; tool_calls = []; reasoning_content = None
    class _Choice: message = _Msg()
    class _Resp: choices = [_Choice()]; usage = None
    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kw): return _Resp()
    monkeypatch.setattr(rha, "HARD_DIR", str(tmp_path))
    agg = rha.run_model(_Client(), "fake-model", "smoke", think=False, limit=2)
    assert agg["n_tasks"] == 2
    assert os.path.exists(tmp_path / "smoke.json")
