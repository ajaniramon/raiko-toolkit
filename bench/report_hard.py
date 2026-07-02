"""HARD-tier HTML report generator.

Reads results/hard_atlassian/*.json (+ .jsonl for literal answers), groups rep runs
by the `-rN` label suffix, computes mean and min-max spread across reps, and renders
a single self-contained HTML file (no external assets — artifact-CSP safe).
"""
import argparse
import glob
import html as _html
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tasks_hard_atlassian import build_hard_atlassian_tasks  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DIR = os.path.join(HERE, "results", "hard_atlassian")
DEFAULT_OUT = os.path.join(HERE, "..", "docs", "hard-report.html")
_REP_RE = re.compile(r"^(?P<base>.+)-r(?P<n>\d+)$")


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def collect(results_dir):
    runs = {}
    for path in sorted(glob.glob(os.path.join(results_dir, "*.json"))):
        try:
            d = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(d, dict) or "aggregate" not in d or "tasks" not in d:
            continue
        answers = {}
        jsonl = path[:-5] + ".jsonl"
        if os.path.exists(jsonl):
            for line in open(jsonl, encoding="utf-8"):
                try:
                    r = json.loads(line)
                    answers[r["id"]] = r.get("answer") or ""
                except Exception:
                    pass
        d["_answers"] = answers
        runs[d["label"]] = d

    groups = {}
    for label, d in runs.items():
        m = _REP_RE.match(label)
        base = m.group("base") if m else label
        groups.setdefault(base, []).append(d)
    for g in groups.values():
        g.sort(key=lambda d: d["label"])

    tasks = build_hard_atlassian_tasks()
    models = []
    for base, g in groups.items():
        aggs = [d["aggregate"] for d in g]
        per_task = {}
        for t in tasks:
            entries = []
            for d in g:
                row = next((r for r in d["tasks"] if r["id"] == t["id"]), None)
                if row is None:
                    continue
                rep = (_REP_RE.match(d["label"]) or [None]).group("n") if _REP_RE.match(d["label"]) else "1"
                entries.append({"rep": f"r{rep}", "correct": bool(row["correct"]),
                                "status": row.get("status", ""),
                                "iterations": row.get("iterations", 0),
                                "answer": d["_answers"].get(t["id"], "")})
            per_task[t["id"]] = {"passes": sum(e["correct"] for e in entries),
                                 "reps": len(entries), "answers": entries}
        fams = {}
        for t in tasks:
            f = fams.setdefault(t["category"], {"ok": 0.0, "total": 0})
            f["total"] += 1
            pt = per_task[t["id"]]
            f["ok"] += (pt["passes"] / pt["reps"]) if pt["reps"] else 0.0
        models.append({
            "base": base, "model": g[0].get("model", base),
            "reps": [d["label"] for d in g],
            "mean_score": round(_mean([a["final_score"] for a in aggs]), 1),
            "min_score": min(a["final_score"] for a in aggs),
            "max_score": max(a["final_score"] for a in aggs),
            "mean_correct": round(_mean([a["correctness_pct"] for a in aggs]), 1),
            "min_correct": min(a["correctness_pct"] for a in aggs),
            "max_correct": max(a["correctness_pct"] for a in aggs),
            "max_iter": round(_mean([a["max_iter"] for a in aggs]), 1),
            "hallucinations": round(_mean([a["hallucinations"] for a in aggs]), 1),
            "families": fams, "per_task": per_task,
        })
    models.sort(key=lambda m: -m["mean_score"])

    solved = failed = 0
    for t in tasks:
        fracs = [m["per_task"][t["id"]] for m in models]
        if all(p["reps"] and p["passes"] == p["reps"] for p in fracs):
            solved += 1
        elif all(p["passes"] == 0 for p in fracs):
            failed += 1
    return {"models": models, "tasks": tasks, "n_tasks": len(tasks),
            "n_reps_max": max((len(m["reps"]) for m in models), default=0),
            "kpis": {"solved_by_all": solved, "failed_by_all": failed,
                     "contested": len(tasks) - solved - failed,
                     "neg_tasks": sum(1 for t in tasks if t["negative"])}}
