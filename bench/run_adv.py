"""Runner for the ADVANCED tier against the 3 winners.

For each run (alias, mode): loads the model and runs the advanced suite with the
FULL SET of tools (includes write_file, edit_file, run_python, run_powershell).
Each task runs on a freshly-rebuilt sandbox (writes mutate the tree). Resumable
(incremental JSONL with fsync) and with anti-destructive guards in the execution
tools.

Usage:
  python run_adv.py                 # the 3 winners
  python run_adv.py --runs qwythos:think
"""

import argparse
import json
import os
import shutil
import sys
import tempfile

from openai import OpenAI
from rich.console import Console
from rich.table import Table

import fixtures
import models as registry
import serve
from harness import run_task, aggregate
from tools import TOOLS
from tasks_advanced import build_tasks_adv

console = Console()
HERE = os.path.dirname(os.path.abspath(__file__))
ADV_DIR = os.path.join(HERE, "results", "adv")
ADV_LOGS = os.path.join(ADV_DIR, "logs")
# Per-run sandbox workspace (portable temp dir; created on demand).
SCRATCH = os.path.join(tempfile.gettempdir(), "raiko-bench")
os.makedirs(SCRATCH, exist_ok=True)

# the 3 winners of the base tier (run = model + its winning mode).
# Order: fast ones first (nothink), qwythos-think (slow) at the end.
TARGET_RUNS = [("qwen35-9b", "nothink"), ("gemma4-12b", "nothink"), ("qwythos", "think")]

FULL_TOOLS = TOOLS  # all, including the write/execute ones


def _partial(label):
    return os.path.join(ADV_LOGS, f"{label}.jsonl")


def load_partial(label):
    done = {}
    if os.path.exists(_partial(label)):
        for line in open(_partial(label), encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                done[r["id"]] = r
            except Exception:
                pass
    return done


def is_complete(label, total):
    p = os.path.join(ADV_DIR, f"{label}.json")
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))["aggregate"].get("n_tasks") == total
        except Exception:
            return False
    return False


def save_run(label, results, agg):
    os.makedirs(ADV_LOGS, exist_ok=True)
    summary = {"run": label, "aggregate": agg,
               "tasks": [{k: v for k, v in r.items() if k != "transcript"} for r in results]}
    json.dump(summary, open(os.path.join(ADV_DIR, f"{label}.json"), "w", encoding="utf-8"),
              indent=2, ensure_ascii=False)
    with open(os.path.join(ADV_LOGS, f"{label}.log"), "w", encoding="utf-8") as f:
        for r in results:
            f.write(f"\n{'='*80}\n[{r['id']}] ({r['category']})\nPROMPT: {r['prompt']}\n")
            for turn in r["transcript"]:
                if "error" in turn:
                    f.write(f"  ERROR: {turn['error']}\n"); continue
                if turn.get("reasoning"):
                    f.write(f"  [THINK] {turn['reasoning']}\n")
                if turn.get("content"):
                    f.write(f"  [SAY]   {turn['content']}\n")
                for c in turn.get("tool_calls", []):
                    f.write(f"  [TOOL]  {c['name']}({c['arguments']})\n  [RESULT]{c['result'][:400]}\n")
            f.write(f"FINAL: {r['answer']}\nCORRECT={r['correct']} TOOL_OK={r['tool_ok']} SCORE={r['score']}\n")


def run_suite(client, alias, label, tasks, enable_thinking):
    os.makedirs(ADV_LOGS, exist_ok=True)
    done = load_partial(label)
    results = []
    f = open(_partial(label), "a", encoding="utf-8")
    try:
        for i, task in enumerate(tasks, 1):
            if task["id"] in done:
                results.append(done[task["id"]])
                console.print(f"  [{label}] {i:>2}/{len(tasks)} {task['id']:<16} [dim]cached[/]")
                continue
            # FRESH sandbox per task in a UNIQUE dir (avoids the WinError 32 from
            # deleting a dir that a just-finished subprocess still holds on
            # Windows). chdir to a stable dir before building.
            os.chdir(SCRATCH)
            base = os.path.join(SCRATCH, "advtmp", f"{label}_{i}")
            root = fixtures.build_sandbox(base)["root"]
            os.chdir(root)
            r = run_task(client, alias, task, root, enable_thinking,
                         tools=FULL_TOOLS, grader_root=True)
            results.append(r)
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            f.flush(); os.fsync(f.fileno())
            mark = "[green]OK [/]" if r["correct"] else "[red]FAIL[/]"
            tool = "" if r["tool_ok"] else "[yellow](tool?)[/]"
            console.print(f"  [{label}] {i:>2}/{len(tasks)} {task['id']:<16} {mark} {tool} "
                          f"score={r['score']:.2f} iters={r['iterations']} {r['latency_s']}s [dim]{r['status']}[/]")
    finally:
        f.close()
    return results, aggregate(results)


def write_report(all_runs, n_tasks):
    rows = sorted(all_runs, key=lambda x: x["agg"].get("final_score", 0), reverse=True)
    lines = ["# ADVANCED benchmark (write / edit / python / powershell)", "",
             f"- **{n_tasks} tasks** per run · sandbox rebuilt per task · graders over the filesystem.",
             "- New tools: `write_file`, `edit_file`, `run_python`, `run_powershell` (with guards).",
             "- Only the 3 winners of the base tier.", "",
             "## Leaderboard", "",
             "| # | Run | Score | Correct% | Tool% | BadJSON | Errs | Lat(s) | OutTok |",
             "|---|---|---|---|---|---|---|---|---|"]
    for i, run in enumerate(rows, 1):
        a = run["agg"]
        lines.append(f"| {i} | **{run['label']}** | **{a['final_score']}** | {a['correctness_pct']} | "
                     f"{a['tool_accuracy_pct']} | {a['malformed_json']} | {a['errors_timeouts']} | "
                     f"{a['avg_latency_s']} | {a['total_completion_tokens']} |")
    cats = sorted({c for run in rows for c in run["agg"]["by_category"]})
    lines += ["", "## Accuracy by category (%)", "",
              "| Category | " + " | ".join(r["label"] for r in rows) + " |",
              "|---|" + "---|" * len(rows)]
    for c in cats:
        lines.append(f"| {c} | " + " | ".join(str(r["agg"]["by_category"].get(c, "—")) for r in rows) + " |")
    lines += ["", f"Transcripts in `results/adv/logs/`.", ""]
    os.makedirs(ADV_DIR, exist_ok=True)
    open(os.path.join(ADV_DIR, "report_adv.md"), "w", encoding="utf-8").write("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", help="alias:mode,alias:mode (default: the 3 winners)")
    ap.add_argument("--limit", type=int, default=0, help="use only the first N tasks (smoke)")
    args = ap.parse_args()

    os.makedirs(ADV_LOGS, exist_ok=True)
    target = TARGET_RUNS
    if args.runs:
        target = [(x.split(":")[0], x.split(":")[1]) for x in args.runs.split(",")]

    # clean temporary sandboxes from previous runs (safe cwd first)
    os.chdir(HERE)
    shutil.rmtree(os.path.join(SCRATCH, "advtmp"), ignore_errors=True)

    tasks = build_tasks_adv()
    if args.limit:
        tasks = tasks[:args.limit]
    total = len(tasks)
    console.print(f"[bold]Advanced tier:[/] {total} tasks · runs: {target}")
    all_runs = []

    for alias, mode in target:
        label = f"{alias}-{mode}"
        if is_complete(label, total):
            console.print(f"[dim]{label}: already complete, skipping[/]")
            agg = json.load(open(os.path.join(ADV_DIR, f"{label}.json"), encoding="utf-8"))["aggregate"]
            all_runs.append({"label": label, "agg": agg})
            continue
        model = next((m for m in registry.MODELS if m["alias"] == alias), None)
        if model is None:
            console.print(f"[red]unknown alias: {alias}[/]"); continue
        console.rule(f"[bold magenta]{label}[/]")
        proc = None
        try:
            proc = serve.start_server(model, log=console.print)
            client = OpenAI(base_url=serve.base_url(), api_key="sk-noop")
            results, agg = run_suite(client, alias, label, tasks, mode == "think")
            save_run(label, results, agg)
            all_runs.append({"label": label, "agg": agg})
        except Exception as e:
            console.print(f"[red]failure in {label}: {e}[/]")
        finally:
            serve.stop_server(proc, log=console.print)

    if all_runs:
        write_report(all_runs, total)
        t = Table(title="Advanced Leaderboard")
        for col in ["Run", "Score", "Correct%", "Tool%"]:
            t.add_column(col)
        for run in sorted(all_runs, key=lambda x: x["agg"].get("final_score", 0), reverse=True):
            a = run["agg"]
            t.add_row(run["label"], str(a["final_score"]), str(a["correctness_pct"]), str(a["tool_accuracy_pct"]))
        console.print(t)
    console.print(f"[green]Done.[/] Report: {os.path.join(ADV_DIR, 'report_adv.md')}")


if __name__ == "__main__":
    main()
