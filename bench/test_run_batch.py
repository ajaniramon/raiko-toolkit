import os, sys, json
import pytest
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


def test_frontier_run_model_is_importable_and_injectable(tmp_path, monkeypatch):
    import run_frontier as rf
    # Same fake client as the HARD-tier check above: no tool calls -> fast completion.
    class _Msg: content = "nope"; tool_calls = []; reasoning_content = None
    class _Choice: message = _Msg()
    class _Resp: choices = [_Choice()]; usage = None
    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kw): return _Resp()
    monkeypatch.setattr(rf, "FRONTIER_DIR", str(tmp_path))
    agg = rf.run_model(_Client(), "fake-model", "smoke", think=False, limit=2)
    assert agg["n_tasks"] == 2
    assert os.path.exists(tmp_path / "smoke.json")


def test_batch_switches_and_runs_reps(monkeypatch, tmp_path):
    import run_batch as rb
    calls = {"switch": [], "runs": []}
    monkeypatch.setattr(rb, "_mcp_call", lambda url, name, args: (
        calls["switch"].append(args["alias"]),
        json.dumps({"ok": True, "loaded_model": args["alias"]}))[-1])
    monkeypatch.setattr(rb, "_run_one",
                        lambda client, model, label: calls["runs"].append((model, label)))
    manifest = {"mcp_url": "http://x/mcp", "local_url": "http://x/v1", "reps": 2,
                "runs": [{"provider": "local", "model": "qwythos", "label": "qt"},
                         {"provider": "local", "model": "gemma4-12b", "label": "gm"}]}
    p = tmp_path / "b.json"; p.write_text(json.dumps(manifest))
    rb.main(["--manifest", str(p), "--no-report"])
    assert calls["switch"] == ["qwythos", "gemma4-12b"]       # one switch per model
    assert calls["runs"] == [("qwythos", "qt-r1"), ("qwythos", "qt-r2"),
                             ("gemma4-12b", "gm-r1"), ("gemma4-12b", "gm-r2")]


def test_batch_remote_runs_do_not_switch(monkeypatch, tmp_path):
    import run_batch as rb
    switched, runs = [], []
    monkeypatch.setattr(rb, "_mcp_call", lambda *a, **k: switched.append(a))
    monkeypatch.setattr(rb, "_run_one", lambda client, model, label: runs.append(label))
    manifest = {"mcp_url": "http://x/mcp", "local_url": "http://x/v1", "reps": 1,
                "runs": [{"provider": "nano", "model": "deepseek/x", "label": "ds",
                          "url": "https://nano.example/v1"}]}
    p = tmp_path / "b.json"; p.write_text(json.dumps(manifest))
    rb.main(["--manifest", str(p), "--no-report", "--no-remote-parallel"])
    assert switched == [] and runs == ["ds-r1"]


def test_batch_frontier_tier_routes_to_frontier_seam(monkeypatch, tmp_path):
    import run_batch as rb
    calls = {"switch": [], "runs": []}
    monkeypatch.setattr(rb, "_mcp_call", lambda url, name, args: (
        calls["switch"].append(args["alias"]),
        json.dumps({"ok": True, "loaded_model": args["alias"]}))[-1])
    monkeypatch.setattr(rb, "_run_one_frontier",
                        lambda client, model, label: calls["runs"].append((model, label)))
    # A frontier-tier manifest must never touch the HARD seam.
    monkeypatch.setattr(rb, "_run_one", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("HARD seam used for a frontier-tier manifest")))
    manifest = {"mcp_url": "http://x/mcp", "local_url": "http://x/v1", "reps": 2, "tier": "frontier",
                "runs": [{"provider": "local", "model": "qwythos", "label": "qt"}]}
    p = tmp_path / "b.json"; p.write_text(json.dumps(manifest))
    rb.main(["--manifest", str(p), "--no-report"])
    assert calls["switch"] == ["qwythos"]
    assert calls["runs"] == [("qwythos", "qt-r1"), ("qwythos", "qt-r2")]


def test_batch_default_tier_is_hard(monkeypatch, tmp_path):
    import run_batch as rb
    calls = []
    monkeypatch.setattr(rb, "_mcp_call", lambda url, name, args: json.dumps({"ok": True}))
    monkeypatch.setattr(rb, "_run_one", lambda client, model, label: calls.append(label))
    monkeypatch.setattr(rb, "_run_one_frontier", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("frontier seam used for a manifest with no explicit tier")))
    manifest = {"mcp_url": "http://x/mcp", "local_url": "http://x/v1", "reps": 1,
                "runs": [{"provider": "local", "model": "qwythos", "label": "qt"}]}
    p = tmp_path / "b.json"; p.write_text(json.dumps(manifest))
    rb.main(["--manifest", str(p), "--no-report"])
    assert calls == ["qt-r1"]


def test_batch_frontier_tier_report_call_uses_frontier_dir_and_module(monkeypatch, tmp_path):
    import run_batch as rb
    import run_frontier
    report_calls = []
    monkeypatch.setattr(rb.report_hard, "main", lambda *a, **k: report_calls.append((a, k)))
    monkeypatch.setattr(rb, "_mcp_call", lambda url, name, args: json.dumps({"ok": True}))
    monkeypatch.setattr(rb, "_run_one_frontier", lambda client, model, label: None)
    manifest = {"mcp_url": "http://x/mcp", "local_url": "http://x/v1", "reps": 1, "tier": "frontier",
                "runs": [{"provider": "local", "model": "qwythos", "label": "qt"}]}
    p = tmp_path / "b.json"; p.write_text(json.dumps(manifest))
    rb.main(["--manifest", str(p)])
    assert report_calls == [((["--dir", run_frontier.FRONTIER_DIR, "--tasks-module", "tasks_frontier"],), {})]


def test_batch_remote_failure_aborts_and_skips_report(monkeypatch, tmp_path):
    import run_batch as rb
    report_calls = []
    monkeypatch.setattr(rb.report_hard, "main", lambda *a, **k: report_calls.append((a, k)))
    monkeypatch.setattr(rb, "_mcp_call", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("no local runs, _mcp_call should not be called")))

    def _boom(client, model, label):
        raise RuntimeError("remote task exploded")
    monkeypatch.setattr(rb, "_run_one", _boom)
    manifest = {"mcp_url": "http://x/mcp", "local_url": "http://x/v1", "reps": 1,
                "runs": [{"provider": "nano", "model": "deepseek/x", "label": "ds",
                          "url": "https://nano.example/v1"}]}
    p = tmp_path / "b.json"; p.write_text(json.dumps(manifest))
    # no --no-remote-parallel -> exercises the daemon-thread path; no --no-report ->
    # the report seam must NOT be invoked because the remote campaign failed
    with pytest.raises(SystemExit):
        rb.main(["--manifest", str(p)])
    assert report_calls == []
