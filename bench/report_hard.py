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
    # Hoisted above the loop: needed both to size-check run files below and,
    # further down, to build per-task/family stats.
    tasks = build_hard_atlassian_tasks()
    n_tasks = len(tasks)

    runs = {}
    for path in sorted(glob.glob(os.path.join(results_dir, "*.json"))):
        # Foreign/malformed files (not our run format) are silently skipped;
        # everything from parsing to key access for one file lives in this
        # try block so any structural surprise just drops that file, instead
        # of killing report generation for every other run.
        try:
            d = json.load(open(path, encoding="utf-8"))
            if not isinstance(d, dict) or "aggregate" not in d or "tasks" not in d or "label" not in d:
                continue
            # Touch the aggregate fields the rest of collect() depends on so an
            # incomplete-but-otherwise-valid run file is skipped here, not with
            # a raw KeyError deep in the per-model aggregation below.
            agg = d["aggregate"]
            agg["final_score"], agg["correctness_pct"], agg["max_iter"], agg["hallucinations"]
            # Stale runs from a different task suite (e.g. after a task-count
            # change) must not silently merge into the current suite's rep
            # groups and skew the means — drop them with a visible warning.
            if len(d["tasks"]) != n_tasks:
                print(f"skipping {d['label']}: {len(d['tasks'])} tasks != current suite {n_tasks}",
                      file=sys.stderr)
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
        except Exception:
            continue

    groups = {}
    for label, d in runs.items():
        m = _REP_RE.match(label)
        base = m.group("base") if m else label
        groups.setdefault(base, []).append(d)
    for g in groups.values():
        g.sort(key=lambda d: d["label"])

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
            # Per-rep final scores, ascending label order (same order as "reps").
            "scores": [a["final_score"] for a in aggs],
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


# ---------------------------------------------------------------------------
# HTML renderer — dark, futuristic, self-contained (no external assets/fonts).
# ---------------------------------------------------------------------------

_FAM_LABEL = {"hard_multihop": "Multi-hop (F1)", "hard_conflict": "Conflicts (F3)",
              "hard_constraint": "Constraints (F4)", "hard_false_premise": "False premise (F5)",
              "hard_false_premise_chain": "Chained false premise (F5c)"}


def _esc(s):
    # quote=True: text also lands inside title="..."/aria-label="..." attributes,
    # so quotes must be escaped too (plan-owner decision over the brief's quote=False).
    return _html.escape(s or "", quote=True)


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


def _fmt_ok(x):
    """Render a mean-ok count: whole number when it lands on one, else 1 decimal."""
    return str(int(x)) if float(x).is_integer() else f"{x:.1f}"


def _bar_html(m):
    """Leaderboard bar: gradient fill (mean), whisker span (min-max), per-rep dots."""
    lo = max(0.0, min(100.0, m["min_score"]))
    hi = max(0.0, min(100.0, m["max_score"]))
    mean = max(0.0, min(100.0, m["mean_score"]))
    reps = m.get("reps", [])
    dots = "".join(
        f'<span class="bar-dot" style="left:{max(0.0, min(100.0, s)):.1f}%" '
        f'title="{_esc(reps[i]) if i < len(reps) else f"rep {i + 1}"}: {s:.1f}"></span>'
        for i, s in enumerate(m.get("scores", [])))
    return (f'<div class="bar-wrap" role="img" '
            f'aria-label="mean score {mean:.1f} of 100, range {lo:.1f} to {hi:.1f}">'
            f'<span class="bar-whisker" style="left:{lo:.1f}%;width:{max(hi - lo, 0):.1f}%"></span>'
            f'<span class="bar-fill" style="width:{mean:.1f}%"></span>{dots}</div>')


def _render_header(data):
    M, k = data["models"], data["kpis"]
    sub = f'{data["n_tasks"]} tasks &middot; {len(M)} models &middot; {data["n_reps_max"]} reps'
    chip = lambda n, label: (f'<div class="chip"><span class="chip-n">{n}</span>'
                              f'<span class="chip-l">{label}</span></div>')
    chips = "".join([
        chip(k["solved_by_all"], "solved by all"),
        chip(k["failed_by_all"], "failed by all"),
        chip(k["contested"], "contested"),
        chip(k["neg_tasks"], "negative-premise"),
    ])
    scoring = ("70% correct &middot; 15% tool &middot; 15% efficiency vs per-task budget "
               "&middot; hard cap 12 iters")
    # NOTE: intentionally a <div>, not <header> — the artifact-fragment test
    # asserts the literal substring "<head" is absent, which "<header" contains.
    return (f'<div class="hdr"><h1>raiko HARD tier &mdash; full results</h1>'
            f'<p class="sub">{sub}</p>'
            f'<div class="chips">{chips}</div>'
            f'<p class="scoring">{scoring}</p></div>')


def _render_verdict(M):
    if not M:
        return '<section class="panel verdict"><h2 class="section-label">Verdict</h2><p>No data.</p></section>'
    items = []
    for i, m in enumerate(M, 1):
        items.append(
            f'<li><span class="rk">{i}.</span> <span class="nm">{_esc(m["model"])}</span> '
            f'<span class="sc">{m["mean_score"]:.1f}</span> '
            f'<span class="rng">({m["min_score"]:.1f}&ndash;{m["max_score"]:.1f})</span></li>')
    lead = (f'<p class="verdict-line">Top model: <strong>{_esc(M[0]["model"])}</strong> '
            f'at {M[0]["mean_score"]:.1f} mean score.</p>')
    tie = ""
    if len(M) >= 2:
        top, second = M[0], M[1]
        spread = (top["max_score"] - top["min_score"]) + (second["max_score"] - second["min_score"])
        if top["mean_score"] - second["mean_score"] <= spread:
            tie = (f'<p class="tie">Statistical tie: {_esc(top["model"])} and {_esc(second["model"])} '
                   f'overlap within their rep spread (gap {top["mean_score"] - second["mean_score"]:.1f} '
                   f'&le; combined spread {spread:.1f}).</p>')
    return (f'<section class="panel verdict"><h2 class="section-label">Verdict</h2>'
            f'{lead}<ol class="ranks">{"".join(items)}</ol>{tie}</section>')


def _render_board(M):
    rows = []
    for i, m in enumerate(M, 1):
        reps_lbl = ", ".join(_esc(r) for r in m["reps"])
        rows.append(
            '<tr>'
            f'<td class="num">{i}</td>'
            f'<td><div class="mname">{_esc(m["model"])}</div><div class="mreps">{reps_lbl}</div></td>'
            f'<td>{_bar_html(m)}</td>'
            f'<td class="num">{m["mean_correct"]:.1f}% ({m["min_correct"]:.1f}&ndash;{m["max_correct"]:.1f})</td>'
            f'<td class="num">{m["max_iter"]:.1f}</td>'
            f'<td class="num">{m["hallucinations"]:.1f}</td>'
            '</tr>')
    return (f'<section class="panel board"><h2 class="section-label">Leaderboard</h2>'
            f'<div class="scroll"><table class="tbl"><thead><tr>'
            f'<th>#</th><th>Model</th><th>Score</th><th>Correct (min&ndash;max)</th>'
            f'<th>Max iter</th><th>Halluc.</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div></section>')


def _render_heatmap(M):
    fams = list(_FAM_LABEL.items())
    head_cells = "".join(f'<th>{_esc(lbl)}</th>' for _, lbl in fams)
    rows = []
    for m in M:
        cells = []
        for fkey, flabel in fams:
            fam = m["families"].get(fkey)
            if not fam or not fam["total"]:
                cells.append('<td class="heat empty" aria-label="no data">&mdash;</td>')
                continue
            frac = fam["ok"] / fam["total"]
            bg, ink = _heat(frac)
            txt = f'{_fmt_ok(fam["ok"])}/{fam["total"]}'
            title = f'{_esc(m["model"])} &mdash; {_esc(flabel)}: {txt} ({frac * 100:.0f}%)'
            cells.append(
                f'<td class="heat" style="background:{bg};color:{ink}" '
                f'title="{title}" aria-label="{title}">{txt}</td>')
        rows.append(f'<tr><td class="rowlbl">{_esc(m["model"])}</td>{"".join(cells)}</tr>')
    return (f'<section class="panel heatmap"><h2 class="section-label">Family heatmap</h2>'
            f'<div class="scroll"><table class="tbl heat-tbl"><thead><tr>'
            f'<th>Model</th>{head_cells}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div></section>')


def _avg_pass_pct(fam_tasks, M):
    total, n = 0.0, 0
    for t in fam_tasks:
        for m in M:
            pt = m["per_task"].get(t["id"])
            if pt and pt["reps"]:
                total += pt["passes"] / pt["reps"]
                n += 1
    return (total / n * 100) if n else 0.0


def _render_task_card(t, M):
    badges = [f'<span class="badge badge-fam">{_esc(_FAM_LABEL.get(t["category"], t["category"]))}</span>']
    if t.get("negative"):
        badges.append('<span class="badge badge-neg">NEG</span>')
    matrix_rows = []
    for m in M:
        pt = m["per_task"].get(t["id"], {"passes": 0, "reps": 0})
        matrix_rows.append(
            f'<tr><td>{_esc(m["model"])}</td><td>{_mark(pt["passes"], pt["reps"])}</td></tr>')
    answer_blocks = []
    for m in M:
        pt = m["per_task"].get(t["id"])
        if not pt or not pt["answers"]:
            continue
        last = pt["answers"][-1]  # brief: display ONLY the last rep's answer per model
        answer_blocks.append(
            '<div class="answer">'
            f'<div class="answer-meta"><span class="am-model">{_esc(m["model"])}</span>'
            f'<span class="am-rep">{_esc(last["rep"])}</span>'
            f'<span class="am-status">{_esc(last["status"])}</span>'
            f'<span class="am-iter">{last["iterations"]} iter</span></div>'
            f'<pre class="ans">{_esc(last["answer"])}</pre></div>')
    return (
        '<article class="task">'
        f'<div class="task-head"><span class="tid">{_esc(t["id"])}</span>{"".join(badges)}</div>'
        f'<p class="prompt">{_esc(t["prompt"])}</p>'
        '<div class="accept"><span class="accept-label">ACCEPTANCE CRITERIA</span>'
        f'<p>{_esc(t["accept"])}</p></div>'
        f'<div class="scroll"><table class="tbl matrix"><tbody>{"".join(matrix_rows)}</tbody></table></div>'
        f'<details class="answers"><summary>Answers (last rep per model)</summary>'
        f'{"".join(answer_blocks)}</details>'
        '</article>')


def _render_tasks(M, T):
    by_fam = {}
    for t in T:
        by_fam.setdefault(t["category"], []).append(t)
    sections, first = [], True
    for fkey, flabel in _FAM_LABEL.items():
        fam_tasks = by_fam.get(fkey)
        if not fam_tasks:
            continue
        avg_pct = _avg_pass_pct(fam_tasks, M)
        open_attr = " open" if first else ""
        first = False
        cards = "".join(_render_task_card(t, M) for t in fam_tasks)
        sections.append(
            f'<details class="fam"{open_attr}><summary>{_esc(flabel)} '
            f'&middot; {len(fam_tasks)} tasks &middot; {avg_pct:.0f}% avg pass</summary>'
            f'<div class="fam-body">{cards}</div></details>')
    return f'<section class="tasks"><h2 class="section-label">Tasks</h2>{"".join(sections)}</section>'


_CSS = """
:root{
  --bg:#06070b; --panel:#0e1219; --panel-2:#151b28; --line:rgba(121,214,255,.14);
  --ink:#eaf2ff; --ink-2:#a6b6d4; --mut:#5f6f8d;
  --cyan:#41f2c8; --violet:#8b6bff; --amber:#ffb454; --red:#ff5d7a; --green:#3ee68a;
  /* --violet is the accent for gradients/borders; --violet-ink is a lightened
     step of the same hue reserved for small text, so badge labels clear 4.5:1
     against panel-2 (plain --violet only reaches ~4.6:1, too tight a margin). */
  --violet-ink:#a68cff;
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{
  background-color:var(--bg);
  background-image:
    radial-gradient(900px 600px at -10% -10%, rgba(139,107,255,.10), transparent 60%),
    radial-gradient(900px 600px at 110% 110%, rgba(65,242,200,.08), transparent 60%),
    repeating-linear-gradient(0deg, var(--line) 0 1px, transparent 1px 48px),
    repeating-linear-gradient(90deg, var(--line) 0 1px, transparent 1px 48px);
  color:var(--ink);
  font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  line-height:1.5;
  min-height:100vh;
  overflow-x:hidden;               /* the page body never scrolls sideways */
}
.wrap{max-width:1100px;margin:0 auto;padding:2.5rem 1.25rem 5rem}
.mono,.tid,.num,.chip-n,.sc,.rng,.rk,.mk,td.heat,.mreps,.am-rep,.am-iter,pre.ans{
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  font-variant-numeric:tabular-nums;
}
h1{font-size:1.9rem;margin:0 0 .35rem;letter-spacing:.02em}
.section-label{
  font-family:system-ui,sans-serif;text-transform:uppercase;letter-spacing:.14em;
  font-size:.8rem;color:var(--ink-2);margin:0 0 1rem;font-weight:600;
}
.sub{color:var(--ink-2);margin:.15rem 0 1.1rem}
.scoring{color:var(--ink-2);font-size:.85rem;margin:.6rem 0 0}
.scroll{overflow-x:auto}

.panel{
  background:var(--panel);
  border:1px solid var(--line);
  border-radius:14px;
  box-shadow:0 0 24px rgba(65,242,200,.05);
  padding:1.25rem 1.4rem;
  margin:1.5rem 0;
}

/* KPI chips */
.chips{display:flex;flex-wrap:wrap;gap:.6rem}
.chip{
  display:flex;flex-direction:column;align-items:flex-start;gap:.1rem;
  background:var(--panel-2);border:1px solid var(--line);border-radius:999px;
  padding:.5rem 1rem;
}
.chip-n{font-size:1.15rem;font-weight:700;color:var(--cyan)}
.chip-l{font-size:.68rem;text-transform:uppercase;letter-spacing:.08em;color:var(--ink-2)}

/* Verdict */
.verdict-line{margin:.2rem 0 .8rem}
.ranks{margin:0;padding-left:0;list-style:none}
.ranks li{margin:.25rem 0}
.rk{color:var(--ink-2)}
.nm{font-weight:600}
.sc{color:var(--cyan)}
.rng{color:var(--ink-2)}
.tie{color:var(--amber);font-style:italic;margin:.8rem 0 0;padding-top:.6rem;border-top:1px solid var(--line)}

/* Tables */
.tbl{width:100%;border-collapse:collapse;min-width:560px}
.tbl th,.tbl td{padding:.55rem .7rem;text-align:left;border-bottom:1px solid var(--line)}
.tbl thead th{
  text-transform:uppercase;letter-spacing:.08em;font-size:.72rem;color:var(--ink-2);
  font-weight:600;border-bottom:1px solid var(--line);
}
.tbl tbody tr:hover{background:var(--panel-2)}
.tbl td.num{text-align:right}
.mname{font-weight:600}
.mreps{color:var(--ink-2);font-size:.75rem}

/* Leaderboard bar */
.bar-wrap{position:relative;width:180px;max-width:100%;height:14px}
.bar-whisker{
  position:absolute;top:50%;height:2px;background:var(--ink-2);opacity:.45;
  transform:translateY(-50%);border-radius:2px;
}
.bar-fill{
  position:absolute;left:0;top:3px;height:8px;border-radius:5px;
  background:linear-gradient(90deg,var(--cyan),var(--violet));
  box-shadow:0 0 10px rgba(65,242,200,.45);
}
.bar-dot{
  position:absolute;top:50%;width:6px;height:6px;border-radius:50%;
  background:var(--ink);box-shadow:0 0 0 2px var(--panel);
  transform:translate(-50%,-50%);
}
@media (prefers-reduced-motion:no-preference){
  .bar-fill{transition:width .6s ease}
}

/* Heatmap */
.heat-tbl td.heat{text-align:center;font-weight:600;min-width:64px}
.heat-tbl td.heat.empty{color:var(--ink-2);text-align:center}
.heat-tbl td.rowlbl{font-weight:600;white-space:nowrap}

/* Family + task cards */
details.fam{
  background:var(--panel);border:1px solid var(--line);border-radius:14px;
  margin:1rem 0;box-shadow:0 0 24px rgba(65,242,200,.05);
}
details.fam>summary{
  cursor:pointer;padding:1rem 1.4rem;list-style:none;
  text-transform:uppercase;letter-spacing:.1em;font-size:.85rem;
  color:var(--ink-2);font-weight:600;
}
details.fam>summary::-webkit-details-marker{display:none}
details.fam>summary::before{content:"\\25B8\\0020";color:var(--cyan)}
details.fam[open]>summary::before{content:"\\25BE\\0020"}
.fam-body{padding:0 1.4rem 1.4rem}

.task{
  background:var(--panel-2);border:1px solid var(--line);border-radius:12px;
  padding:1rem 1.2rem;margin:1rem 0;
}
.task-head{display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;margin-bottom:.5rem}
.tid{color:var(--ink);font-weight:600}
.badge{
  display:inline-block;padding:.1rem .55rem;border-radius:999px;
  font-size:.68rem;text-transform:uppercase;letter-spacing:.06em;font-weight:600;
}
.badge-fam{background:rgba(139,107,255,.16);color:var(--violet-ink);border:1px solid rgba(139,107,255,.4)}
.badge-neg{background:rgba(255,180,84,.18);color:var(--amber);border:1px solid rgba(255,180,84,.45)}
.prompt{color:var(--ink);margin:.4rem 0 .8rem}

.accept{
  border-left:3px solid var(--green);background:var(--panel);
  border-radius:0 8px 8px 0;padding:.6rem .9rem;margin:.6rem 0 .9rem;
}
.accept-label{
  display:block;text-transform:uppercase;letter-spacing:.1em;font-size:.68rem;
  font-weight:700;color:var(--green);margin-bottom:.25rem;
}
.accept p{margin:0;color:var(--ink)}

.matrix{min-width:280px}
.matrix td{border-bottom:1px solid var(--line);padding:.35rem .6rem}
.mk{
  display:inline-flex;align-items:center;gap:.25rem;padding:.1rem .45rem;
  border-radius:6px;font-weight:600;
}
.mk.ok{color:var(--green);background:rgba(62,230,138,.12)}
.mk.ko{color:var(--red);background:rgba(255,93,122,.12)}
.mk.mid{color:var(--amber);background:rgba(255,180,84,.12)}
.mk.none{color:var(--ink-2)}

details.answers{margin-top:.8rem}
details.answers>summary{cursor:pointer;color:var(--ink-2);font-size:.85rem}
.answer{margin-top:.6rem;padding-top:.6rem;border-top:1px solid var(--line)}
.answer-meta{display:flex;gap:.7rem;flex-wrap:wrap;color:var(--ink-2);font-size:.78rem;margin-bottom:.3rem}
.am-model{color:var(--ink);font-weight:600}
pre.ans{
  white-space:pre-wrap;word-break:break-word;background:var(--bg);
  border:1px solid var(--line);border-radius:8px;padding:.6rem .8rem;
  max-height:280px;overflow-y:auto;color:var(--ink-2);font-size:.82rem;margin:0;
}

a{color:var(--cyan)}
:focus-visible{outline:2px solid var(--cyan);outline-offset:2px}

@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{transition:none!important;animation:none!important}
}
"""


def render(data, artifact=False):
    M, T = data["models"], data["tasks"]
    css = _CSS                                   # single inline <style> string
    head = f"<title>raiko HARD tier — full results</title>\n<style>{css}</style>"
    # 1. header + KPI chips  2. verdict callout  3. leaderboard  4. family heatmap
    # 5. per-family task cards  — each section is a small helper returning HTML,
    # all text interpolated through _esc(), all numbers preformatted in Python.
    body_inner = "\n".join([_render_header(data), _render_verdict(M), _render_board(M),
                            _render_heatmap(M), _render_tasks(M, T)])
    body = f'<div class="wrap">{body_inner}</div>'
    frag = f"{head}\n{body}"
    if artifact:
        return frag
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"</head><body>{frag}</body></html>")


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
