import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import report_hard as rh
import tasks_frontier as tfr


def _fake_run(dir, label, model, task_rows, score=70.0):
    agg = {"n_tasks": len(task_rows), "final_score": score, "correctness_pct": 75.0,
           "max_iter": 1, "hallucinations": 0, "tool_accuracy_pct": 90.0,
           "efficiency_pct": 80.0}
    fam = {}
    json.dump({"model": model, "label": label, "aggregate": agg, "by_family": fam,
               "tasks": task_rows}, open(os.path.join(dir, f"{label}.json"), "w"))
    with open(os.path.join(dir, f"{label}.jsonl"), "w") as f:
        for r in task_rows:
            f.write(json.dumps({**r, "answer": f"answer-{label}-{r['id']}",
                                "transcript": []}) + "\n")


def _rows(ok_ids):
    tasks = rh.build_hard_atlassian_tasks()
    return [{"id": t["id"], "category": t["category"], "correct": t["id"] in ok_ids,
             "status": "ok", "iterations": 3, "negative": t["negative"]}
            for t in tasks]


def _frontier_rows(ok_ids):
    tasks = tfr.build_frontier_tasks()
    return [{"id": t["id"], "category": t["category"], "correct": t["id"] in ok_ids,
             "status": "ok", "iterations": 3, "negative": t["negative"]}
            for t in tasks]


def test_collect_groups_reps_and_averages(tmp_path):
    all_ids = {t["id"] for t in rh.build_hard_atlassian_tasks()}
    for rep, ok, score in (("r1", all_ids, 72.5), ("r2", all_ids - {"hard_ch_sla"}, 68.0)):
        _fake_run(str(tmp_path), f"m1-{rep}", "model-one", _rows(ok), score=score)
    data = rh.collect(str(tmp_path))
    assert len(data["models"]) == 1
    m = data["models"][0]
    assert m["base"] == "m1" and m["reps"] == ["m1-r1", "m1-r2"]
    assert m["scores"] == [72.5, 68.0]  # per-rep final_score, ascending label order
    assert m["per_task"]["hard_ch_sla"]["passes"] == 1
    assert m["per_task"]["hard_ch_sla"]["reps"] == 2
    assert any("answer-m1-r2" in a["answer"] for a in m["per_task"]["hard_ch_sla"]["answers"])


def test_collect_ignores_archive_subdirs(tmp_path):
    os.makedirs(tmp_path / "v1_old")
    _fake_run(str(tmp_path / "v1_old"), "old-r1", "old", _rows(set()))
    _fake_run(str(tmp_path), "m1-r1", "model-one", _rows(set()))
    data = rh.collect(str(tmp_path))
    assert [m["base"] for m in data["models"]] == ["m1"]


def test_render_full_and_artifact_forms(tmp_path):
    all_ids = {t["id"] for t in rh.build_hard_atlassian_tasks()}
    _fake_run(str(tmp_path), "m1-r1", "model-one", _rows(all_ids))
    data = rh.collect(str(tmp_path))
    full = rh.render(data)
    frag = rh.render(data, artifact=True)
    assert full.lstrip().lower().startswith("<!doctype html>")
    assert frag.lstrip().startswith("<title>")
    for bad in ("<html", "<head", "<body"):
        assert bad not in frag.lower()
    assert "hard_ch_postmortem" in full
    assert "ACCEPTANCE" in full
    assert "Postmortem OPS-777" in full          # acceptance text made it in
    assert "http://" not in full and "https://" not in full   # CSP: no external refs
    assert full.count("model-one") >= 1


def test_render_escapes_answers(tmp_path):
    rows = _rows(set())
    _fake_run(str(tmp_path), "m1-r1", "model-one", rows)
    # poison one answer with markup
    lines = open(tmp_path / "m1-r1.jsonl").read().splitlines()
    r0 = json.loads(lines[0]); r0["answer"] = "<script>alert(1)</script>"
    lines[0] = json.dumps(r0)
    open(tmp_path / "m1-r1.jsonl", "w").write("\n".join(lines))
    out = rh.render(rh.collect(str(tmp_path)))
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out


def test_collect_skips_file_without_label(tmp_path):
    all_ids = {t["id"] for t in rh.build_hard_atlassian_tasks()}
    _fake_run(str(tmp_path), "m1-r1", "model-one", _rows(all_ids))
    # Foreign-looking file: has "aggregate"/"tasks" (passes the first guard)
    # but no "label" — must not raise a KeyError on runs[d["label"]].
    agg = {"n_tasks": 1, "final_score": 50.0, "correctness_pct": 50.0,
           "max_iter": 1, "hallucinations": 0}
    json.dump({"aggregate": agg, "tasks": []}, open(tmp_path / "no-label.json", "w"))
    data = rh.collect(str(tmp_path))
    assert [m["base"] for m in data["models"]] == ["m1"]


def test_collect_excludes_mismatched_task_count_from_group(tmp_path, capsys):
    all_ids = {t["id"] for t in rh.build_hard_atlassian_tasks()}
    _fake_run(str(tmp_path), "m1-r1", "model-one", _rows(all_ids), score=72.5)
    # A stale rep for the same base label, but from a run of a different
    # (larger) task suite — must be dropped instead of corrupting the group.
    extra_rows = _rows(all_ids) + [
        {"id": f"fake_extra_{i}", "category": "hard_multihop", "correct": True,
         "status": "ok", "iterations": 1, "negative": False}
        for i in range(3)
    ]
    _fake_run(str(tmp_path), "m1-r2", "model-one", extra_rows, score=99.0)
    data = rh.collect(str(tmp_path))
    assert len(data["models"]) == 1
    m = data["models"][0]
    assert m["reps"] == ["m1-r1"]
    assert m["mean_score"] == 72.5
    assert "skipping m1-r2" in capsys.readouterr().err


def test_cli_writes_file(tmp_path):
    all_ids = {t["id"] for t in rh.build_hard_atlassian_tasks()}
    _fake_run(str(tmp_path), "m1-r1", "model-one", _rows(all_ids))
    out = tmp_path / "rep.html"
    rh.main(["--dir", str(tmp_path), "--out", str(out)])
    assert out.exists() and "raiko HARD tier" in out.read_text()


# --- tier-aware: --tasks-module / collect(tasks=...) with the FRONTIER suite ---

def test_resolve_task_builder_frontier():
    builder, tier_label = rh.resolve_task_builder("tasks_frontier")
    assert tier_label == "FRONTIER"
    assert len(builder()) == 40


def test_resolve_task_builder_default_hard():
    builder, tier_label = rh.resolve_task_builder("tasks_hard_atlassian")
    assert tier_label == "HARD"
    assert builder is rh.build_hard_atlassian_tasks


def test_collect_groups_frontier_tasks(tmp_path):
    all_ids = {t["id"] for t in tfr.build_frontier_tasks()}
    assert len(all_ids) == 40
    ok = all_ids - {"x1_logs_file"}
    _fake_run(str(tmp_path), "m1-r1", "model-one", _frontier_rows(ok), score=64.0)
    data = rh.collect(str(tmp_path), tfr.build_frontier_tasks())
    assert data["n_tasks"] == 40
    assert len(data["models"]) == 1
    m = data["models"][0]
    assert m["base"] == "m1" and m["mean_score"] == 64.0
    assert m["per_task"]["x1_logs_file"]["passes"] == 0
    assert m["per_task"]["x1_ticket_pod_deploy"]["passes"] == 1


def test_cli_writes_frontier_report_with_tasks_module(tmp_path):
    all_ids = {t["id"] for t in tfr.build_frontier_tasks()}
    _fake_run(str(tmp_path), "m1-r1", "model-one", _frontier_rows(all_ids))
    out = tmp_path / "rep.html"
    rh.main(["--dir", str(tmp_path), "--out", str(out), "--tasks-module", "tasks_frontier"])
    text = out.read_text()
    assert "raiko FRONTIER tier" in text
    assert "x1_ticket_pod_deploy" in text
    assert "Chains (X1)" in text


# ---- paired comparison + exact McNemar -------------------------------------
# "51/52 vs 52/52" is not a result: at n=52 you need >=6 discordant tasks in
# one direction before the difference clears p<0.05. These pin the arithmetic
# so a near-tie is reported as inconclusive instead of as a win.

def test_mcnemar_exact_thresholds():
    assert rh._mcnemar_exact(0, 0) == 1.0
    assert rh._mcnemar_exact(1, 0) == 1.0          # the "51 vs 52" case
    assert round(rh._mcnemar_exact(5, 0), 4) == 0.0625   # still not significant
    assert round(rh._mcnemar_exact(6, 0), 4) == 0.0312   # first significant count
    assert rh._mcnemar_exact(3, 1) > 0.05
    assert rh._mcnemar_exact(0, 6) == rh._mcnemar_exact(6, 0)   # symmetric


def test_compare_pair_counts_discordant_tasks():
    a = {"base": "A", "per_task": {"t1": {"passes": 1, "reps": 1},
                                   "t2": {"passes": 1, "reps": 1},
                                   "t3": {"passes": 0, "reps": 1},
                                   "t4": {"passes": 0, "reps": 1}}}
    b = {"base": "B", "per_task": {"t1": {"passes": 1, "reps": 1},
                                   "t2": {"passes": 0, "reps": 1},
                                   "t3": {"passes": 1, "reps": 1},
                                   "t4": {"passes": 0, "reps": 1}}}
    r = rh.compare_pair(a, b, ["t1", "t2", "t3", "t4"])
    assert r["both"] == 1 and r["neither"] == 1
    assert r["a_only"] == ["t2"] and r["b_only"] == ["t3"]
    assert r["n_compared"] == 4
    assert r["p"] == 1.0                     # 1 vs 1 discordant -> nothing
    assert r["significant"] is False


def test_compare_pair_majority_vote_across_reps():
    """At temperature 0 reps barely differ, so a task counts as passed when a
    strict majority of its reps passed; 1-of-3 is a fail, 2-of-3 a pass."""
    a = {"base": "A", "per_task": {"t1": {"passes": 2, "reps": 3},
                                   "t2": {"passes": 1, "reps": 3}}}
    b = {"base": "B", "per_task": {"t1": {"passes": 1, "reps": 3},
                                   "t2": {"passes": 3, "reps": 3}}}
    r = rh.compare_pair(a, b, ["t1", "t2"])
    assert r["a_only"] == ["t1"] and r["b_only"] == ["t2"]


def test_compare_pair_skips_tasks_missing_for_either_model():
    a = {"base": "A", "per_task": {"t1": {"passes": 1, "reps": 1},
                                   "t2": {"passes": 1, "reps": 0}}}
    b = {"base": "B", "per_task": {"t1": {"passes": 0, "reps": 1},
                                   "t2": {"passes": 0, "reps": 1}}}
    r = rh.compare_pair(a, b, ["t1", "t2"])
    assert r["n_compared"] == 1               # t2 had no reps for A
    assert r["a_only"] == ["t1"]


def test_compare_pair_reports_significance_when_earned():
    ids = [f"t{i}" for i in range(10)]
    # A wins 6 tasks, B wins none -> p = 0.031
    a = {"base": "A", "per_task": {i: {"passes": 1, "reps": 1} for i in ids}}
    b = {"base": "B", "per_task": {i: {"passes": 0 if n < 6 else 1, "reps": 1}
                                   for n, i in enumerate(ids)}}
    r = rh.compare_pair(a, b, ids)
    assert len(r["a_only"]) == 6 and r["b_only"] == []
    assert r["significant"] is True and r["p"] < 0.05


def test_pairwise_covers_every_pair_and_sorts_by_p():
    models = [
        {"base": "A", "per_task": {"t1": {"passes": 1, "reps": 1}}},
        {"base": "B", "per_task": {"t1": {"passes": 1, "reps": 1}}},
        {"base": "C", "per_task": {"t1": {"passes": 0, "reps": 1}}},
    ]
    rows = rh.pairwise(models, ["t1"])
    assert len(rows) == 3                     # 3 models -> 3 unordered pairs
    assert all(r["p"] <= rows[i + 1]["p"] for i, r in enumerate(rows[:-1]))


def test_report_html_includes_pairwise_section(tmp_path):
    all_ids = {t["id"] for t in rh.build_hard_atlassian_tasks()}
    _fake_run(str(tmp_path), "m1-r1", "model-one", _rows(all_ids), score=90.0)
    _fake_run(str(tmp_path), "m2-r1", "model-two",
              _rows(all_ids - {"hard_ch_sla"}), score=80.0)
    data = rh.collect(str(tmp_path))
    html = rh.render(data)
    assert "Head-to-head" in html
    assert "McNemar" in html
    # A single discordant task must be reported as inconclusive, not as a win.
    assert "inconclusive" in html.lower()
