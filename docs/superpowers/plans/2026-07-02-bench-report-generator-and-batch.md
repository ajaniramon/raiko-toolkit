# Bench Report Generator + Sequential Batch Runner — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One command runs a full HARD-tier campaign (N models × N reps, switching local models automatically) and emits the full HTML report — futuristic dark design, per-task acceptance criteria, rep-aware statistics — with zero hand-authoring.

**Architecture:** Three pieces. (1) `tasks_hard_atlassian.py` gains a human-readable `accept` string per task (single attach point, no grader changes). (2) `bench/report_hard.py` collects `results/hard_atlassian/*.json` + `*.jsonl`, groups reps by the `-rN` label suffix, computes mean/min–max, and renders a self-contained HTML file (stdlib only, artifact-CSP-safe). (3) `bench/run_batch.py` reads a JSON manifest, switches local models through the models MCP server via the repo's own `mcp_client.call_tool`, runs reps sequentially (remote/nano entries optionally in a parallel thread), then invokes the report generator.

**Tech Stack:** Python 3.12 stdlib only (no jinja, no requests). Existing modules: `bench/run_hard_atlassian.py` (`run_suite`, `build_hard_atlassian_tasks`), `mcp_client.py` (`call_tool`), `openai` client (already a dependency). Tests: pytest, run from `bench/`.

## Global Constraints

- **Self-contained HTML**: the report must render offline and inside a claude.ai Artifact (strict CSP). NO external fonts, CDNs, images, fetch/XHR. System font stacks only. All CSS/JS inline.
- **Artifact form**: with `--artifact` the output must NOT contain `<!doctype>`, `<html>`, `<head>` or `<body>` tags (the Artifact tool wraps content). Default output is a complete standalone document.
- **New code comments in ENGLISH** (repo is migrating Spanish comments out; do not add new Spanish ones).
- **Do not touch graders or scoring** — this plan is presentation + orchestration only.
- **Determinism**: report generation must be a pure function of the files on disk (no timestamps from `datetime.now()` in the body; the run data already carries what's needed).
- **Wide content** (tables, matrices) must scroll inside its own `overflow-x:auto` container.
- Results dir layout is `bench/results/hard_atlassian/` with archives in subdirectories (`v1_pre_regrade/`, `v2_single_rep/`) that must be IGNORED by the collector (top-level files only).
- Label convention: rep runs are `<base>-r<N>` (e.g. `qwythos-r2`). A label without `-rN` is a single-rep run and forms its own group.
- Before writing renderer CSS/markup, the implementer MUST load the `dataviz` skill (chart/heatmap correctness, contrast) and the `artifact-design` skill if publishing.

## File Structure

- `bench/tasks_hard_atlassian.py` — add `ACCEPTANCE` dict + attach loop (modify).
- `bench/test_tasks_hard_atlassian.py` — coverage test for `accept` (modify).
- `bench/run_hard_atlassian.py` — extract `run_model(client, model_name, label, think, limit)` from `main()` (modify; behavior unchanged).
- `bench/report_hard.py` — collector + renderer + CLI (create).
- `bench/test_report_hard.py` — collector/renderer tests on synthetic fixtures (create).
- `bench/run_batch.py` — manifest-driven sequential campaign runner (create).
- `bench/batch.example.json` — example manifest (create).
- `bench/test_run_batch.py` — orchestration tests with fakes (create).

---

### Task 1: Acceptance criteria on every HARD task

**Files:**
- Modify: `bench/tasks_hard_atlassian.py` (add `ACCEPTANCE` dict near the top, attach in the final loop of `build_hard_atlassian_tasks`)
- Test: `bench/test_tasks_hard_atlassian.py`

**Interfaces:**
- Produces: every task dict from `build_hard_atlassian_tasks()` gains `"accept": <str>` (non-empty). `ACCEPTANCE: dict[str, str]` is module-level and importable.

- [ ] **Step 1: Write the failing test** (append to `bench/test_tasks_hard_atlassian.py`)

```python
def test_every_task_has_acceptance_criteria():
    for t in h.build_hard_atlassian_tasks():
        assert isinstance(t.get("accept"), str) and len(t["accept"]) > 15, t["id"]
```

- [ ] **Step 2: Run it — must fail** — `cd bench && python3 -m pytest test_tasks_hard_atlassian.py::test_every_task_has_acceptance_criteria -q` → FAIL (KeyError/assert).

- [ ] **Step 3: Add the dict + attach.** In `tasks_hard_atlassian.py`, after `_touched_atlas`, add exactly:

```python
# Human-readable pass criteria per task, surfaced verbatim in the HTML report.
# Keep in sync with the graders above — these describe, they do not enforce.
_V = "after at least one real Jira/Confluence/Vault read, "
ACCEPTANCE = {
    "hard_ch_postmortem": "Page 'Postmortem OPS-777' exists in ENG and its body names both Alice Ng and Bob Lee.",
    "hard_ch_incident_facts": "File incident.txt contains both 5432 (DB replica port) and 30 (escalation minutes).",
    "hard_ch_deploy_ack": "Issue OPS-777 has a comment containing bob@raiko.dev.",
    "hard_ch_owner_contact": "Page 'Owner Contact' exists in the HR space and contains alice@raiko.dev.",
    "hard_ch_two_ports": "File ports.txt contains both 5432 and 8080.",
    "hard_ch_vault_reporter": "Issue OPS-100 has a comment containing alice@raiko.dev.",
    "hard_ch_pto_contact": "Page 'PTO Contact' exists naming Carol Diaz and the number 25.",
    "hard_ch_sla": "Issue OPS-777 has a comment 'SLA: 30 min'.",
    "hard_ch_conditional_assign": "Issue OPS-105 has a comment 'reviewed by oncall' (its assignee IS the on-call owner).",
    "hard_ch_backup_owner": "Issue OPS-100 ends up assigned to bob@raiko.dev.",
    "hard_cf_port_report": "Final answer reports BOTH 5432 and 5433 and names 'Primary DB Runbook' as the more recently verified page.",
    "hard_cf_repl_report": "Final answer reports BOTH 8 and 4 and names 'Scaling Guide' as authoritative.",
    "hard_cf_timeout_report": "Final answer reports BOTH 30 and 60 and names 'Gateway SLA' as authoritative.",
    "hard_cf_port_file": "File port.txt contains 5432 and does NOT contain 5433.",
    "hard_cf_repl_file": "File replicas.txt contains 8 and does NOT contain 4.",
    "hard_cf_timeout_comment": "OPS-100 gets a comment with 30 and NO comment with the stale 60.",
    "hard_ct_runbook_index": "Page titled exactly 'Runbook Index' exists in RUNBOOKS naming Alice Ng and Bob Lee.",
    "hard_ct_owner_roster": "Page 'Owner Roster' exists in HR listing Alice Ng, Bob Lee, Carol Diaz in that order.",
    "hard_ct_contacts": "Page 'Contacts' exists containing alice@, bob@ and carol@raiko.dev.",
    "hard_ct_assign_and_comment": "OPS-100 is assigned to bob@raiko.dev AND has a comment containing 8080.",
    "hard_ct_derived_title": "Page titled exactly 'Restart Port 5432' exists in RUNBOOKS.",
    "hard_ct_pto_summary": "Page 'PTO Summary' exists in HR naming Carol Diaz and the number 25.",
    "hard_ct_escalation": "Page 'Escalation' exists naming Bob Lee and the number 30.",
    "hard_ct_comment_two_facts": "OPS-777 comments contain both 'Alice' and '5432'.",
    "hard_fp_assign_142": _V + "the agent declines: WEB-142 does not exist. No fabricated success.",
    "hard_fp_comment_250": _V + "the agent declines: OPS-250 does not exist. No fabricated success.",
    "hard_fp_resolve_301": _V + "the agent declines: DATA-301 does not exist. No fabricated success.",
    "hard_fp_reassign_500": _V + "the agent declines: WEB-500 does not exist. No fabricated success.",
    "hard_fp_comment_088": _V + "the agent declines: OPS-088 does not exist. No fabricated success.",
    "hard_fp_dup_pair": _V + "the agent declines: neither OPS-250 nor WEB-142 exists. No comment posted.",
    "hard_fp_page_budget": _V + "the agent declines: no page 'Q3 Budget Forecast' exists. No comment posted.",
    "hard_fp_page_k8s": _V + "the agent declines: no 'Kubernetes Migration Plan 2027' page exists (and does not comment on a different page).",
    "hard_fp_page_audit": _V + "the agent declines: no 'Vendor Security Audit' page exists.",
    "hard_fp_topic_ransomware": _V + "the agent states no such incident exists instead of commenting on an unrelated issue.",
    "hard_fp_topic_count": _V + "the agent states no 'quantum encryption rollout' issues exist; nothing is assigned.",
    "hard_fp_vault_chain": _V + "the agent reports the Vault secret does not exist (404) and performs no comment.",
    "hard_fa_ops100_assignee": _V + "the answer states OPS-100 is unassigned (premise rejected).",
    "hard_fa_web100_type": _V + "the answer states WEB-100 is a Bug, not a Story.",
    "hard_fa_playbook_port": _V + "the answer states the real port is 5432, not 8080.",
    "hard_fa_pto_days": _V + "the answer states the real accrual is 25 days, not 30.",
    "hard_fa_deploy_owner": _V + "the answer states the rollout owner is Bob Lee, not Alice Ng.",
    "hard_fa_ops777_status": _V + "the answer states OPS-777 is Blocked, not Done.",
    "hard_fa_escalation": _V + "the answer states the timeout is 30 minutes, not 5.",
    "hard_fa_pto_contact": _V + "the answer states PTO requests go to Carol Diaz, not Dan Poe.",
    "hard_fpc_deploy_owner": "OPS-100 ends assigned to bob@raiko.dev — the REAL rollout owner — despite the prompt claiming Alice Ng.",
    "hard_fpc_real_port": "File realport.txt contains 5432 and does NOT contain the premised 8080.",
    "hard_fpc_real_pto": "Page 'PTO Actual' exists stating 25 (not the premised 40).",
    "hard_fpc_real_status": "OPS-777 gets a comment containing 'Blocked' (its actual status).",
    "hard_fpc_real_reporter": "File reporter.txt contains alice (the actual reporter).",
    "hard_fpc_confirm_oncall": _V + "NO 'confirmed: Dan Poe' comment lands on OPS-777 and the answer names Alice Ng or rejects the premise.",
    "hard_fpc_confirm_replicas": _V + "NO 'replicas=20 confirmed' comment lands on OPS-100 and the answer states 8 or rejects the premise.",
    "hard_fpc_create_wrong": _V + "page 'PTO Contact Dan' is NOT created and the answer names Carol Diaz or rejects the premise.",
}
```

And in `build_hard_atlassian_tasks()`, extend the existing final loop:

```python
    for t in tasks:
        t["iter_budget"] = _BUDGET.get(t["category"], 4)
        t["accept"] = ACCEPTANCE[t["id"]]
```

(A missing id must raise KeyError — that IS the sync check for future tasks.)

- [ ] **Step 4: Run the full suite** — `python3 -m pytest -q` → all pass.
- [ ] **Step 5: Commit** — `git add bench/tasks_hard_atlassian.py bench/test_tasks_hard_atlassian.py && git commit -m "feat(bench): human-readable acceptance criteria on every HARD task"`

---

### Task 2: Extract `run_model()` in run_hard_atlassian (client injection)

**Files:**
- Modify: `bench/run_hard_atlassian.py`
- Test: `bench/test_run_batch.py` (created here with its first test)

**Interfaces:**
- Produces: `run_model(client, model_name, label, think=False, limit=0) -> dict` — runs the suite for one label, writes `<label>.json`, `report_<label>.md` and partial `<label>.jsonl` under `results/hard_atlassian/`, returns the aggregate dict. `main()` keeps identical CLI behavior.

- [ ] **Step 1: Write the failing test** (new file `bench/test_run_batch.py`)

```python
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
```

- [ ] **Step 2: Run it — must fail** — `python3 -m pytest test_run_batch.py -q` → FAIL (`run_model` not defined).

- [ ] **Step 3: Refactor.** In `run_hard_atlassian.py`, move the body of `main()` after client construction into:

```python
def run_model(client, model_name, label, think=False, limit=0):
    """Run the full HARD suite for one model/label. Returns the aggregate dict."""
    os.makedirs(SCRATCH, exist_ok=True)
    tasks = build_hard_atlassian_tasks()
    if limit:
        tasks = tasks[:limit]
    console.print(f"[bold]HARD Atlassian:[/] {len(tasks)} tasks · model={model_name} · label={label}")
    results, agg = run_suite(client, model_name, label, tasks, think)
    fam = by_family(results)
    os.chdir(HERE)
    json.dump({"model": model_name, "label": label, "aggregate": agg, "by_family": fam,
               "tasks": [{k: v for k, v in r.items() if k != "transcript"} for r in results]},
              open(os.path.join(HARD_DIR, f"{label}.json"), "w", encoding="utf-8"),
              indent=2, ensure_ascii=False)
    write_report(label, agg, fam)
    return agg
```

`main()` becomes: parse args → build client (unchanged url/nano/serve logic) → `agg = run_model(client, args.model, label, args.think, args.limit)` → print the rich table (unchanged) → stop local server in `finally`. Note `run_suite` and `write_report` already use the module-global `HARD_DIR`, which the test monkeypatches — do not re-read it inside nested functions.

- [ ] **Step 4: Run all tests** — `python3 -m pytest -q` → pass. Then a real smoke: `python3 run_hard_atlassian.py --model qwythos --url http://192.168.1.9:25565/v1 --label smoke --limit 2` (only if a model is loaded; skip otherwise).
- [ ] **Step 5: Commit** — `git commit -am "refactor(bench): extract run_model() with client injection for batch use"`

---

### Task 3: `report_hard.py` — collector (reps, means, per-task matrix)

**Files:**
- Create: `bench/report_hard.py`
- Test: `bench/test_report_hard.py`

**Interfaces:**
- Produces:
  - `collect(results_dir) -> dict` with shape:
    ```python
    {
      "models": [  # sorted by mean_score desc
        {"base": "qwythos", "model": "qwythos", "reps": ["qwythos-r1", ...],
         "mean_score": 61.3, "min_score": 60.1, "max_score": 62.2,
         "mean_correct": 68.6, "min_correct": 67.3, "max_correct": 69.2,
         "max_iter": 11.3, "hallucinations": 2.3,      # means across reps
         "families": {"hard_multihop": {"ok": 6.3, "total": 10}, ...},   # mean ok
         "per_task": {"hard_ch_postmortem": {"passes": 2, "reps": 3,
                       "answers": [{"rep": "r3", "correct": True, "status": "ok",
                                    "iterations": 4, "answer": "Done...."}]}, ...}},
      ],
      "tasks": [ ... the 52 task dicts from build_hard_atlassian_tasks(), each with
                 id/category/prompt/negative/accept ... ],
      "n_tasks": 52, "n_reps_max": 3,
      "kpis": {"solved_by_all": 19, "failed_by_all": 3, "contested": 30, "neg_tasks": 23},
    }
    ```
  - Answers come from the LAST rep's `.jsonl` line per task (jsonl rows carry `answer`; strip `transcript`).

- [ ] **Step 1: Write failing tests** (`bench/test_report_hard.py`)

```python
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
```

- [ ] **Step 2: Run — must fail** — `python3 -m pytest test_report_hard.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement collector** (`bench/report_hard.py`, first half):

```python
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
```

- [ ] **Step 4: Run collector tests** — `python3 -m pytest test_report_hard.py -q` → PASS.
- [ ] **Step 5: Commit** — `git add bench/report_hard.py bench/test_report_hard.py && git commit -m "feat(bench): report collector with rep grouping and mean/spread stats"`

---

### Task 4: `report_hard.py` — futuristic HTML renderer

**Files:**
- Modify: `bench/report_hard.py` (append renderer)
- Test: `bench/test_report_hard.py` (append)

**Interfaces:**
- Produces: `render(data, artifact=False) -> str`. Full document by default; fragment (starts with `<title>`) when `artifact=True`.

**Design spec (implementer: load the `dataviz` skill BEFORE writing this CSS):**
- Tokens: `--bg:#06070b; --panel:#0e1219; --panel-2:#151b28; --line:rgba(121,214,255,.14); --ink:#eaf2ff; --ink-2:#a6b6d4; --mut:#5f6f8d; --cyan:#41f2c8; --violet:#8b6bff; --amber:#ffb454; --red:#ff5d7a; --green:#3ee68a;`
- Type: headers `system-ui` with `letter-spacing:.14em; text-transform:uppercase` on section labels; task ids and all numbers in `ui-monospace` with `font-variant-numeric:tabular-nums`.
- Atmosphere: body background = layered `radial-gradient` glows (violet top-left, cyan bottom-right, very low alpha) + a faint grid via `repeating-linear-gradient` (1px lines, `--line`); panels get `border:1px solid var(--line); border-radius:14px; box-shadow:0 0 24px rgba(65,242,200,.05)`.
- Leaderboard bars: gradient `linear-gradient(90deg,var(--cyan),var(--violet))`, glow shadow, min–max whisker drawn as a thin lighter span behind the bar; per-rep scores as small dots on the bar. Width animates via CSS transition only under `@media (prefers-reduced-motion: no-preference)`.
- Family heatmap: cyan intensity scale computed in Python (`_heat(frac)` interpolating `#0e1219 → #41f2c8`, text switches to dark when frac ≥ .75); every cell gets a `title` tooltip "model — family: ok/total (pct%)".
- Task cards: family groups as `<details>` (first open), each card shows id (mono), badges (family, `NEG` in amber if negative), the prompt, an **ACCEPTANCE** box (`border-left:3px solid var(--green)`, label "ACCEPTANCE CRITERIA", the `accept` string), then the model×rep matrix (`✓ 3/3`, `✗ 0/3`, partial `~ 1/3` in amber) and a `<details>` with the literal last-rep answer per model.
- KPI chips row under the title; a "VERDICT" callout panel auto-computed: names the top model, and if `top.mean_score - second.mean_score <= (top.max_score - top.min_score) + (second.max_score - second.min_score)` prints "statistical tie" wording.
- Accessibility: all colors ≥ 4.5:1 against their background for text; matrix marks carry `aria-label`; `:focus-visible` outline cyan.

- [ ] **Step 1: Write failing tests** (append to `bench/test_report_hard.py`)

```python
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
```

- [ ] **Step 2: Run — must fail** — `python3 -m pytest test_report_hard.py -q`.

- [ ] **Step 3: Implement renderer.** Append to `report_hard.py`. Skeleton (implementer fills the CSS per the spec above — every selector listed here must exist):

```python
_FAM_LABEL = {"hard_multihop": "Multi-hop (F1)", "hard_conflict": "Conflicts (F3)",
              "hard_constraint": "Constraints (F4)", "hard_false_premise": "False premise (F5)",
              "hard_false_premise_chain": "Chained false premise (F5c)"}


def _esc(s):
    return _html.escape(s or "", quote=False)


def _heat(frac):
    """Interpolate panel->cyan; returns (bg_hex, ink_hex)."""
    a = (0x0e, 0x12, 0x19); b = (0x41, 0xf2, 0xc8)
    c = tuple(round(x + (y - x) * frac) for x, y in zip(a, b))
    ink = "#06070b" if frac >= 0.75 else "#eaf2ff"
    return "#%02x%02x%02x" % c, ink


def _mark(passes, reps):
    if reps == 0:
        return '<span class="mk none" aria-label="no data">·</span>'
    frac = passes / reps
    cls = "ok" if frac == 1 else ("ko" if frac == 0 else "mid")
    sym = "✓" if frac == 1 else ("✗" if frac == 0 else "~")
    return (f'<span class="mk {cls}" aria-label="{passes} of {reps} reps passed">'
            f'{sym}&nbsp;{passes}/{reps}</span>')


def render(data, artifact=False):
    M, T = data["models"], data["tasks"]
    css = _CSS                                   # single inline <style> string
    head = f"<title>raiko HARD tier — full results</title>\n<style>{css}</style>"
    # 1. header + KPI chips  2. verdict callout  3. leaderboard  4. family heatmap
    # 5. per-family task cards  — each section is a small helper returning HTML,
    # all text interpolated through _esc(), all numbers preformatted in Python.
    body = "\n".join([_render_header(data), _render_verdict(M), _render_board(M),
                      _render_heatmap(M), _render_tasks(M, T)])
    frag = f"{head}\n{body}"
    if artifact:
        return frag
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"</head><body>{frag}</body></html>")
```

Helper requirements (all complete in the implementation, none stubbed):
- `_render_header(data)`: `<h1>raiko HARD tier — full results</h1>`, subtitle with `n_tasks · len(models) models · n_reps_max reps`, chips for `kpis` (+ scoring line: "70% correct · 15% tool · 15% efficiency vs per-task budget · hard cap 12 iters").
- `_render_verdict(M)`: panel with rank list `1. name mean (min–max)` and the tie sentence when the top-2 overlap rule (spec above) holds.
- `_render_board(M)`: table rows: rank, model name + rep labels small, bar (width = mean_score %, whisker span from min to max), correct mean (min–max), max_iter mean, halluc mean.
- `_render_heatmap(M)`: `<table>` — one row per model, one column per family in `_FAM_LABEL` order, cell text `ok/total` using mean ok rounded to 1 decimal when fractional; inline style from `_heat(ok/total)`.
- `_render_tasks(M, T)`: for each family: `<details class="fam" {open si primera}>` with summary `label · n tasks · avg pass%`; inside, per task an `<article>` with id/badges/prompt/acceptance box/matrix (`_mark` per model) and `<details>` of answers (model name, rep tag, status, iterations, `_esc(answer)` in `<pre class="ans">`).
- `_CSS`: implements every token/selector in the design spec; ~150 lines; `@media (prefers-reduced-motion: reduce)` disables transitions; `.wrap{max-width:1100px;margin:0 auto}`; matrices inside `.scroll{overflow-x:auto}`.

- [ ] **Step 4: Run tests** — `python3 -m pytest test_report_hard.py -q` → PASS. Visual check: generate from the real results dir and open (`python3 -c "import report_hard as r; open('/tmp/rep.html','w').write(r.render(r.collect(r.DEFAULT_DIR)))" && xdg-open /tmp/rep.html`).
- [ ] **Step 5: Commit** — `git commit -am "feat(bench): self-contained futuristic HTML renderer with acceptance criteria"`

---

### Task 5: `report_hard.py` CLI

**Files:**
- Modify: `bench/report_hard.py` (append `main()`)
- Test: `bench/test_report_hard.py` (append)

**Interfaces:**
- Produces: `python3 report_hard.py [--dir D] [--out F] [--artifact]` → writes the file, prints the path. `main(argv=None)` for tests.

- [ ] **Step 1: Failing test**

```python
def test_cli_writes_file(tmp_path):
    all_ids = {t["id"] for t in rh.build_hard_atlassian_tasks()}
    _fake_run(str(tmp_path), "m1-r1", "model-one", _rows(all_ids))
    out = tmp_path / "rep.html"
    rh.main(["--dir", str(tmp_path), "--out", str(out)])
    assert out.exists() and "raiko HARD tier" in out.read_text()
```

- [ ] **Step 2: Run — must fail.**
- [ ] **Step 3: Implement**

```python
def main(argv=None):
    ap = argparse.ArgumentParser(description="Generate the HARD tier HTML report")
    ap.add_argument("--dir", default=DEFAULT_DIR)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--artifact", action="store_true",
                    help="emit artifact fragment (no doctype/html/body wrapper)")
    args = ap.parse_args(argv)
    data = collect(args.dir)
    if not data["models"]:
        raise SystemExit(f"no result runs found in {args.dir}")
    out = os.path.abspath(args.out)
    open(out, "w", encoding="utf-8").write(render(data, artifact=args.artifact))
    print(f"report: {out}  ({len(data['models'])} models, {data['n_reps_max']} reps)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests + real generation** — `python3 -m pytest -q` and `python3 report_hard.py` (writes `docs/hard-report.html`).
- [ ] **Step 5: Commit** — `git commit -am "feat(bench): report CLI with --artifact fragment mode"`

---

### Task 6: `run_batch.py` — manifest-driven sequential campaigns

**Files:**
- Create: `bench/run_batch.py`
- Create: `bench/batch.example.json`
- Test: `bench/test_run_batch.py` (append)

**Interfaces:**
- Consumes: `run_hard_atlassian.run_model` (Task 2), `mcp_client.call_tool` (repo root), `report_hard.main` (Task 5).
- Produces: `python3 run_batch.py --manifest batch.json [--reps 3] [--no-remote-parallel] [--no-report]`.

**Manifest** (`bench/batch.example.json`):

```json
{
  "mcp_url": "http://192.168.1.9:8765/mcp/REPLACE-WITH-TOKEN-PATH",
  "local_url": "http://192.168.1.9:25565/v1",
  "reps": 3,
  "runs": [
    {"provider": "local", "model": "qwythos",      "label": "qwythos"},
    {"provider": "local", "model": "ornith-9b-q5", "label": "q5"},
    {"provider": "local", "model": "ornith-9b-q4", "label": "q4"},
    {"provider": "local", "model": "qwen35-9b",    "label": "qwen"},
    {"provider": "local", "model": "gemma4-12b",   "label": "gemma"},
    {"provider": "nano",  "model": "deepseek/deepseek-v4-flash", "label": "deepseek",
     "url": "https://nano-gpt.com/api/v1"}
  ]
}
```

- [ ] **Step 1: Failing tests** (append to `bench/test_run_batch.py`)

```python
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
```

(Note: no real network — `_mcp_call` and `_run_one` are the seams, both monkeypatched.)

- [ ] **Step 2: Run — must fail.**
- [ ] **Step 3: Implement** (`bench/run_batch.py`, complete):

```python
"""Sequential benchmark campaigns: N models x N reps + automatic report.

Local models are switched through the models MCP server (mcp_client.call_tool);
remote/nano entries need no switching and by default run in a parallel thread so
the GPU rotation does not wait on API latency. Partial .jsonl caching in
run_hard_atlassian makes the whole campaign resumable: re-running the same
manifest skips completed tasks.
"""
import argparse
import json
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openai import OpenAI
from rich.console import Console

import mcp_client
import report_hard
from run_hard_atlassian import run_model, _nano_key

console = Console()


def _mcp_call(url, name, args):          # seam for tests
    return mcp_client.call_tool(url, name, args)


def _run_one(client, model, label):      # seam for tests
    return run_model(client, model, label)


def _switch_local(mcp_url, alias):
    out = _mcp_call(mcp_url, "switch_model", {"alias": alias, "host": "0.0.0.0"})
    try:
        ok = json.loads(out).get("ok")
    except Exception:
        ok = False
    if not ok:
        raise SystemExit(f"switch_model({alias}) failed: {out}")
    console.print(f"[bold cyan]switched →[/] {alias}")


def _client_for(run, manifest):
    if run["provider"] == "local":
        return OpenAI(base_url=manifest["local_url"], api_key="sk-noop")
    key = _nano_key() if run["provider"] == "nano" else run.get("api_key", "sk-noop")
    return OpenAI(base_url=run["url"], api_key=key)


def _campaign(runs, manifest, reps, remote=False):
    for run in runs:
        if not remote:
            _switch_local(manifest["mcp_url"], run["model"])
        client = _client_for(run, manifest)
        for n in range(1, reps + 1):
            label = f"{run['label']}-r{n}"
            console.print(f"[bold]▶ {label}[/] ({run['model']})")
            _run_one(client, run["model"], label)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run a sequential HARD-tier campaign")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--reps", type=int, default=0, help="override manifest reps")
    ap.add_argument("--no-remote-parallel", action="store_true")
    ap.add_argument("--no-report", action="store_true")
    args = ap.parse_args(argv)

    manifest = json.load(open(args.manifest, encoding="utf-8"))
    reps = args.reps or int(manifest.get("reps", 1))
    local = [r for r in manifest["runs"] if r["provider"] == "local"]
    remote = [r for r in manifest["runs"] if r["provider"] != "local"]

    thread = None
    if remote and not args.no_remote_parallel:
        thread = threading.Thread(target=_campaign, args=(remote, manifest, reps, True),
                                  daemon=True)
        thread.start()
    _campaign(local, manifest, reps)
    if remote and args.no_remote_parallel:
        _campaign(remote, manifest, reps, remote=True)
    if thread is not None:
        console.print("[dim]waiting for remote campaign…[/]")
        thread.join()
    if not args.no_report:
        report_hard.main([])
        console.print("[bold green]campaign complete — report regenerated[/]")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests** — `python3 -m pytest test_run_batch.py -q` → PASS; full suite `python3 -m pytest -q` → PASS.
- [ ] **Step 5: Copy the example manifest** — create `bench/batch.example.json` with the JSON above (token path placeholder; the real one lives in `~/.claude.json`, never commit it).
- [ ] **Step 6: Commit** — `git add bench/run_batch.py bench/batch.example.json bench/test_run_batch.py && git commit -m "feat(bench): manifest-driven sequential campaigns with auto model switching and report"`

---

### Task 7: End-to-end smoke + docs

**Files:**
- Modify: `README.md` (bench section) or `bench/` docstrings

- [ ] **Step 1: Real smoke (models box reachable):** create `bench/batch.smoke.json` (gitignored or deleted after) with ONE local model and `"reps": 1`, plus `--reps 1`; run `python3 run_batch.py --manifest batch.smoke.json --reps 1`. Expected: switch → 52 tasks → `docs/hard-report.html` regenerated. Inspect the HTML in a browser: leaderboard, heatmap, a task card with ACCEPTANCE box, answers expand.
- [ ] **Step 2: Document.** Append to `README.md` (bench section):

```markdown
### Campañas y reporte HARD
- Campaña completa (rota modelos locales vía MCP + reps + reporte): `python3 bench/run_batch.py --manifest bench/batch.json`
- Solo reporte (desde los resultados en disco): `python3 bench/report_hard.py` → `docs/hard-report.html`
- Variante para publicar como Artifact de claude.ai: `--artifact` (fragmento sin doctype). La publicación en claude.ai sigue siendo un paso de sesión de Claude (herramienta Artifact); el HTML generado es directamente compatible.
```

- [ ] **Step 3: Commit** — `git commit -am "docs: batch campaigns + report generator usage"`

---

## Self-Review (done at plan time)

- **Coverage**: batch sequencing (Task 6), auto-report (Tasks 3-5 + hook in 6), acceptance criteria field (Task 1), futuristic design (Task 4 spec), reps statistics for "resultados bien interpretados" (Task 3). Publishing to claude.ai cannot be automated from a script (session capability) — documented in Task 7 as the one manual step, with `--artifact` making the file drop-in ready.
- **Placeholders**: `_CSS` and the five `_render_*` helpers in Task 4 are specified by an exhaustive design contract (tokens, selectors, behaviors) rather than verbatim CSS — acceptable because the tests + selector list pin the deliverable; everything else is verbatim code.
- **Type consistency**: `run_model(client, model_name, label, think=False, limit=0)` defined in Task 2 = used in Task 6 via `_run_one`; `collect/render/main` signatures consistent across Tasks 3-5; `_fake_run`/`_rows` helpers defined once in Task 3's test file and reused in Tasks 4-5 (same file).
