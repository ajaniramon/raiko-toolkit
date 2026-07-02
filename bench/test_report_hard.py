import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import report_hard as rh


def _fake_run(dir, label, model, task_rows):
    agg = {"n_tasks": len(task_rows), "final_score": 70.0, "correctness_pct": 75.0,
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


def test_collect_groups_reps_and_averages(tmp_path):
    all_ids = {t["id"] for t in rh.build_hard_atlassian_tasks()}
    for rep, ok in (("r1", all_ids), ("r2", all_ids - {"hard_ch_sla"})):
        _fake_run(str(tmp_path), f"m1-{rep}", "model-one", _rows(ok))
    data = rh.collect(str(tmp_path))
    assert len(data["models"]) == 1
    m = data["models"][0]
    assert m["base"] == "m1" and m["reps"] == ["m1-r1", "m1-r2"]
    assert m["per_task"]["hard_ch_sla"]["passes"] == 1
    assert m["per_task"]["hard_ch_sla"]["reps"] == 2
    assert any("answer-m1-r2" in a["answer"] for a in m["per_task"]["hard_ch_sla"]["answers"])


def test_collect_ignores_archive_subdirs(tmp_path):
    os.makedirs(tmp_path / "v1_old")
    _fake_run(str(tmp_path / "v1_old"), "old-r1", "old", _rows(set()))
    _fake_run(str(tmp_path), "m1-r1", "model-one", _rows(set()))
    data = rh.collect(str(tmp_path))
    assert [m["base"] for m in data["models"]] == ["m1"]
