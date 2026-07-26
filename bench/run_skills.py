"""Runner for the SKILLS tier: does a local model ACTIVATE the `skill` tool when
a task matches a skill's description, discriminate between confusable skills,
stay quiet when nothing matches, and FOLLOW the loaded instructions?

Same resumable-JSONL pattern as run_hard.py (flush+fsync, resume by id, one
model/mode server at a time). All 40 tasks are self-contained (no filesystem
needed), so unlike run_hard.py there is no per-task sandbox: everything runs
from one fixed scratch dir where the 8 synthetic skills are materialized once.
"""

import argparse
import json
import os
import sys
import tempfile

from openai import OpenAI
from rich.console import Console
from rich.table import Table

# allow importing tools.py / engine.* from the repo root (same trick as run_hard.py)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import TOOLS as ALL_TOOLS, DISPATCH as ALL_DISPATCH  # noqa: E402
from engine.skills import skill_tool_schema, skills_index  # noqa: E402
from engine.config import load_config, resolve_key  # noqa: E402

import fixtures_skills
import models as registry
import serve
import tasks_skills
from harness import run_task, aggregate, SYSTEM_PROMPT as BASE_SYSTEM_PROMPT
from tasks_skills import RECORDER, build_tasks_skills, make_skill_dispatch

console = Console()
HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS_DIR = os.path.join(HERE, "results", "skills")
SKILLS_LOGS = os.path.join(SKILLS_DIR, "logs")
# Fixed scratch dir (portable temp dir; created on demand). No per-task sandbox
# is needed since every task is self-contained -- this only hosts the
# materialized SKILL.md files.
SCRATCH = os.path.join(tempfile.gettempdir(), "raiko-bench-skills")
os.makedirs(SCRATCH, exist_ok=True)

DEFAULT_RUNS = ("ornith-9b-q4:think,ornith-9b-q4:nothink,"
                "ornith-9b-q5:think,ornith-9b-q5:nothink,qwen35-9b:nothink")

# Cloud reference runs: alias -> (provider section in tui_config.json, model id).
# These skip llama-server entirely; base_url/api_key come from the provider's
# config (or its conventional env var). Mode still selects enable_thinking,
# which cloud endpoints are free to ignore.
REMOTE_MODELS = {
    "deepseek-v4-flash": ("nano", "deepseek/deepseek-v4-flash"),
}

# A subset of REAL tools.py tools, so calling `skill` is never the model's only
# option -- it has to actually decide, not just call the one tool it's given.
DISTRACTOR_NAMES = ["read_file", "list_dir", "grep", "run_python"]
DISTRACTOR_TOOLS = [t for t in ALL_TOOLS if t["function"]["name"] in DISTRACTOR_NAMES]

# built once per process: the fixtures/tools don't depend on which model runs
SKILLS = fixtures_skills.build_skills(SCRATCH)
SKILL_DISPATCH = make_skill_dispatch(SKILLS)
TOOLS = [skill_tool_schema()] + DISTRACTOR_TOOLS
DISPATCH = dict(SKILL_DISPATCH)
DISPATCH.update({n: ALL_DISPATCH[n] for n in DISTRACTOR_NAMES})

SYSTEM_PROMPT = BASE_SYSTEM_PROMPT + "\n\n" + skills_index(SKILLS)

MAX_ITERS = 4  # override with --max-iters


def _partial(label):
    return os.path.join(SKILLS_LOGS, f"{label}.jsonl")


def load_partial(label):
    done = {}
    if os.path.exists(_partial(label)):
        for line in open(_partial(label), encoding="utf-8"):
            line = line.strip()
            if line:
                try:
                    r = json.loads(line); done[r["id"]] = r
                except Exception:
                    pass
    return done


def is_complete(label, total):
    p = os.path.join(SKILLS_DIR, f"{label}.json")
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))["aggregate"].get("n_tasks") == total
        except Exception:
            return False
    return False


def aggregate_skills(results: list) -> dict:
    """Skills-tier-specific metrics on top of harness.aggregate()'s generic ones.

    activation_rate:    of activate+discriminate tasks, % where the FIRST skill
                         requested was the right one.
    wrong_skill_rate:    of activate+discriminate tasks, % where SOME skill was
                         requested but the first one was wrong.
    false_positive_rate: of negative tasks, % where the model called `skill` at all
                         (regardless of which skill or whether the plain answer
                         was correct).
    marker_compliance:   of activate+discriminate+follow tasks, % whose final
                         answer starts with the correct `[SKILL:<name>]` marker.
    """
    def pct(numer, denom):
        return round(100 * numer / denom, 1) if denom else None

    def activated_correctly(r):
        called = r.get("skills_called") or []
        return bool(called) and called[0] == r.get("expected_skill")

    def marker_ok(r):
        exp = r.get("expected_skill")
        if not exp:
            return False
        return (r.get("answer") or "").lstrip().startswith(f"[SKILL:{exp}]")

    positives = [r for r in results if r["category"] in ("activate", "discriminate")]
    negatives = [r for r in results if r["category"] == "negative"]
    follow = [r for r in results if r["category"] == "follow"]
    marker_pool = positives + follow

    by_cat = {}
    for cat in sorted({r["category"] for r in results}):
        items = [r for r in results if r["category"] == cat]
        by_cat[cat] = {
            "n": len(items),
            "correct_pct": pct(sum(r["correct"] for r in items), len(items)),
            "avg_score": round(sum(r["score"] for r in items) / len(items), 3) if items else None,
        }

    return {
        "activation_rate": pct(sum(1 for r in positives if activated_correctly(r)), len(positives)),
        "wrong_skill_rate": pct(sum(1 for r in positives
                                     if (r.get("skills_called") or []) and not activated_correctly(r)),
                                len(positives)),
        "false_positive_rate": pct(sum(1 for r in negatives if r.get("skills_called")), len(negatives)),
        "marker_compliance": pct(sum(1 for r in marker_pool if marker_ok(r)), len(marker_pool)),
        "by_category_detail": by_cat,
    }


def save_run(label, results, agg, skills_agg):
    os.makedirs(SKILLS_LOGS, exist_ok=True)
    full_agg = dict(agg)
    full_agg["skills"] = skills_agg
    json.dump({"run": label, "aggregate": full_agg,
               "tasks": [{k: v for k, v in r.items() if k != "transcript"} for r in results]},
              open(os.path.join(SKILLS_DIR, f"{label}.json"), "w", encoding="utf-8"),
              indent=2, ensure_ascii=False)
    with open(os.path.join(SKILLS_LOGS, f"{label}.log"), "w", encoding="utf-8") as f:
        for r in results:
            f.write(f"\n{'='*80}\n[{r['id']}] ({r['category']}) expected={r.get('expected_skill')}\n"
                    f"PROMPT: {r['prompt']}\n")
            for turn in r["transcript"]:
                if "error" in turn:
                    f.write(f"  ERROR: {turn['error']}\n"); continue
                if turn.get("reasoning"):
                    f.write(f"  [THINK] {turn['reasoning'][:600]}\n")
                if turn.get("content"):
                    f.write(f"  [SAY]   {turn['content']}\n")
                for c in turn.get("tool_calls", []):
                    f.write(f"  [TOOL]  {c['name']}({c['arguments'][:200]})\n  [RESULT]{c['result'][:300]}\n")
            f.write(f"SKILLS_CALLED: {r.get('skills_called')}\n"
                    f"FINAL: {r['answer']}\nCORRECT={r['correct']} TOOL_OK={r['tool_ok']} SCORE={r['score']}\n")


def run_suite(client, alias, label, tasks, enable_thinking):
    os.makedirs(SKILLS_LOGS, exist_ok=True)
    done = load_partial(label)
    results = []
    f = open(_partial(label), "a", encoding="utf-8")
    try:
        for i, task in enumerate(tasks, 1):
            if task["id"] in done:
                results.append(done[task["id"]])
                console.print(f"  [{label}] {i:>2}/{len(tasks)} {task['id']:<24} [dim]cached[/]")
                continue
            RECORDER.clear()
            os.chdir(SCRATCH)
            r = run_task(client, alias, task, SCRATCH, enable_thinking,
                         tools=TOOLS, system_prompt=SYSTEM_PROMPT,
                         max_iterations=MAX_ITERS, dispatch=DISPATCH)
            r["skills_called"] = list(RECORDER)
            r["expected_skill"] = task.get("expected_skill")
            results.append(r)
            f.write(json.dumps(r, ensure_ascii=False) + "\n"); f.flush(); os.fsync(f.fileno())
            mark = "[green]OK [/]" if r["correct"] else "[red]FAIL[/]"
            tool = "" if r["tool_ok"] else "[yellow](tool?)[/]"
            console.print(f"  [{label}] {i:>2}/{len(tasks)} {task['id']:<24} {mark} {tool} "
                          f"score={r['score']:.2f} skills={r['skills_called']} "
                          f"iters={r['iterations']} {r['latency_s']}s [dim]{r['status']}[/]")
    finally:
        f.close()
    return results, aggregate(results), aggregate_skills(results)


def write_report(all_runs, n_tasks):
    rows = sorted(all_runs, key=lambda x: x["agg"].get("final_score", 0), reverse=True)
    lines = ["# SKILLS benchmark (skill-tool activation, discrimination, restraint, compliance)", "",
             f"- **{n_tasks} tasks**: 12 activate, 8 discriminate, 12 negative, 8 follow.",
             "- All self-contained (inline CSV/log/diff/schema/transcript data) -- no filesystem needed.",
             "- Distractor tools (read_file, list_dir, grep, run_python) are also offered, so calling "
             "`skill` is never the only option.",
             "", "**Note on `negative` tasks and `tool_ok`**: for this category the ideal response calls "
             "NO tool at all. harness.py's generic `tool_ok = any(n in expect_tools for n in "
             "tool_calls_made)` is False whenever `tool_calls_made` is empty, regardless of "
             "`expect_tools` -- so a perfectly-behaved negative-task response structurally scores "
             "`tool_ok=False` (max per-task score 0.85, not 1.0). This is a known ceiling of the "
             "generic scoring formula for this category, not a real failure -- `false_positive_rate` "
             "below (built directly from which skills were actually called) is the metric that "
             "measures false activation correctly.", "",
             "## Leaderboard", "",
             "| # | Run | Score | Correct% | Tool% | Eff% | Activation% | WrongSkill% | FalsePos% | MarkerOK% |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for i, run in enumerate(rows, 1):
        a, s = run["agg"], run["skills"]
        lines.append(f"| {i} | **{run['label']}** | **{a['final_score']}** | {a['correctness_pct']} | "
                     f"{a['tool_accuracy_pct']} | {a['efficiency_pct']} | {s['activation_rate']} | "
                     f"{s['wrong_skill_rate']} | {s['false_positive_rate']} | {s['marker_compliance']} |")

    cats = sorted({c for run in rows for c in run["skills"]["by_category_detail"]})
    lines += ["", "## Correctness by category (%)", "",
              "| Category | " + " | ".join(r["label"] for r in rows) + " |",
              "|---|" + "---|" * len(rows)]
    for c in cats:
        lines.append(f"| {c} | " + " | ".join(
            str(r["skills"]["by_category_detail"].get(c, {}).get("correct_pct", "—")) for r in rows
        ) + " |")

    lines += ["", "Transcripts (including `skills_called` per task) in `results/skills/logs/`.", ""]
    os.makedirs(SKILLS_DIR, exist_ok=True)
    open(os.path.join(SKILLS_DIR, "report_skills.md"), "w", encoding="utf-8").write("\n".join(lines))


def main():
    global SKILLS_DIR, SKILLS_LOGS, MAX_ITERS
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default=DEFAULT_RUNS, help="alias:mode,... (mode: think|nothink)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tag", default="", help="suffix for the results dir (e.g. v2)")
    ap.add_argument("--max-iters", type=int, default=0, help="override of MAX_ITERS (default 4)")
    args = ap.parse_args()

    if args.tag:
        SKILLS_DIR = os.path.join(HERE, "results", f"skills_{args.tag}")
        SKILLS_LOGS = os.path.join(SKILLS_DIR, "logs")
    if args.max_iters:
        MAX_ITERS = args.max_iters
    console.print(f"[dim]config: tag='{args.tag or 'base'}' max_iters={MAX_ITERS}[/]")

    os.makedirs(SKILLS_LOGS, exist_ok=True)
    os.chdir(HERE)

    tasks = build_tasks_skills()
    if args.limit:
        tasks = tasks[:args.limit]
    total = len(tasks)
    target = [(x.split(":")[0], x.split(":")[1]) for x in args.runs.split(",")]
    console.print(f"[bold]SKILLS:[/] {total} tasks · runs: {target}")

    all_runs = []
    for alias, mode in target:
        label = f"{alias}-{mode}"
        if not args.limit and is_complete(label, total):
            console.print(f"[dim]{label}: already complete, skipping[/]")
            agg = json.load(open(os.path.join(SKILLS_DIR, f"{label}.json"), encoding="utf-8"))["aggregate"]
            all_runs.append({"label": label, "agg": agg, "skills": agg.get("skills", {})})
            continue
        if alias in REMOTE_MODELS:
            provider, model_id = REMOTE_MODELS[alias]
            pcfg = load_config().get(provider) or {}
            key = resolve_key(provider, pcfg)
            if not (pcfg.get("base_url") and key):
                console.print(f"[red]{label}: provider '{provider}' not configured[/]"); continue
            console.rule(f"[bold magenta]{label}[/] [dim](remote: {provider})[/]")
            try:
                client = OpenAI(base_url=pcfg["base_url"], api_key=key)
                results, agg, skills_agg = run_suite(client, model_id, label, tasks, mode == "think")
                save_run(label, results, agg, skills_agg)
                all_runs.append({"label": label, "agg": agg, "skills": skills_agg})
            except Exception as e:
                console.print(f"[red]failure in {label}: {e}[/]")
            finally:
                os.chdir(HERE)
            continue
        model = next((m for m in registry.MODELS if m["alias"] == alias), None)
        if model is None:
            console.print(f"[red]unknown alias: {alias}[/]"); continue
        console.rule(f"[bold magenta]{label}[/]")
        proc = None
        try:
            proc = serve.start_server(model, log=console.print)
            client = OpenAI(base_url=serve.base_url(), api_key="sk-noop")
            results, agg, skills_agg = run_suite(client, alias, label, tasks, mode == "think")
            save_run(label, results, agg, skills_agg)
            all_runs.append({"label": label, "agg": agg, "skills": skills_agg})
        except Exception as e:
            console.print(f"[red]failure in {label}: {e}[/]")
        finally:
            serve.stop_server(proc, log=console.print)
            os.chdir(HERE)

    if all_runs:
        write_report(all_runs, total)
        t = Table(title="SKILLS Leaderboard")
        for col in ["Run", "Score", "Correct%", "Activation%", "FalsePos%"]:
            t.add_column(col)
        for run in sorted(all_runs, key=lambda x: x["agg"].get("final_score", 0), reverse=True):
            a, s = run["agg"], run["skills"]
            t.add_row(run["label"], str(a["final_score"]), str(a["correctness_pct"]),
                      str(s["activation_rate"]), str(s["false_positive_rate"]))
        console.print(t)
    console.print(f"[green]Done.[/] Report: {os.path.join(SKILLS_DIR, 'report_skills.md')}")


if __name__ == "__main__":
    main()
