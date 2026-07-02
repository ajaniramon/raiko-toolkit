import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import report_hard as rh


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
