"""Benchmark orchestrator.

For each model in the registry: starts llama-server (--jinja), and runs the full
suite in thinking ON and OFF mode; kills the server; moves on to the next. At the
end it writes a comparative leaderboard and logs EVERYTHING (reasoning + completions +
tools) in bench/results/logs/.

Usage:
  python run_bench.py                         # all models, both modes
  python run_bench.py --models qwythos        # only one
  python run_bench.py --modes think           # only one mode
  python run_bench.py --limit 5               # quick smoke (5 tasks)
  python run_bench.py --no-serve --alias X    # use an already-running server
"""

import argparse
import json
import os
import sys
import tempfile
import time
from datetime import datetime

from openai import OpenAI
from rich.console import Console
from rich.table import Table

import fixtures
import models as registry
import serve
from harness import run_task, aggregate
from tasks import build_tasks

console = Console()
HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(HERE, "results")
LOGS_DIR = os.path.join(RESULTS_DIR, "logs")
# Per-run sandbox workspace (portable temp dir; created on demand).
SCRATCH = os.path.join(tempfile.gettempdir(), "raiko-bench")
os.makedirs(SCRATCH, exist_ok=True)

MODE_FLAGS = {"think": True, "nothink": False}


def _partial_path(label):
    return os.path.join(LOGS_DIR, f"{label}.jsonl")


def load_partial(label):
    """Loads already-completed results from the incremental JSONL (id -> result)."""
    done = {}
    path = _partial_path(label)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    done[r["id"]] = r
                except Exception:
                    continue
    return done


def is_complete(label, total):
    """True if this run's summary json already has the `total` tasks."""
    summ = os.path.join(RESULTS_DIR, f"{label}.json")
    if os.path.exists(summ):
        try:
            return json.load(open(summ, encoding="utf-8"))["aggregate"].get("n_tasks") == total
        except Exception:
            return False
    return False


def run_suite(client, model_name, run_label, tasks, root, enable_thinking):
    """Runs the tasks of a run with RESUME: skips the already-done ones (incremental
    JSONL) and saves each result instantly to survive interruptions/sleeps."""
    os.makedirs(LOGS_DIR, exist_ok=True)
    done = load_partial(run_label)
    results = []
    f = open(_partial_path(run_label), "a", encoding="utf-8")
    try:
        for i, task in enumerate(tasks, 1):
            if task["id"] in done:
                results.append(done[task["id"]])
                console.print(f"  [{run_label}] {i:>2}/{len(tasks)} {task['id']:<18} [dim]cached[/]")
                continue
            r = run_task(client, model_name, task, root, enable_thinking)
            results.append(r)
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
            mark = "[green]OK [/]" if r["correct"] else "[red]FAIL[/]"
            tool = "" if r["tool_ok"] else "[yellow](tool?)[/]"
            console.print(f"  [{run_label}] {i:>2}/{len(tasks)} {task['id']:<18} "
                          f"{mark} {tool} score={r['score']:.2f} "
                          f"iters={r['iterations']} {r['latency_s']}s "
                          f"[dim]{r['status']}[/]")
    finally:
        f.close()
    agg = aggregate(results)
    return results, agg


def save_run(run_label, results, agg):
    """Saves summary json + full transcript (jsonl) + readable log."""
    os.makedirs(LOGS_DIR, exist_ok=True)
    # summary without transcript
    summary = {"run": run_label, "aggregate": agg,
               "tasks": [{k: v for k, v in r.items() if k != "transcript"} for r in results]}
    with open(os.path.join(RESULTS_DIR, f"{run_label}.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    # full transcript (everything the LLM said)
    with open(os.path.join(LOGS_DIR, f"{run_label}.jsonl"), "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # readable log
    with open(os.path.join(LOGS_DIR, f"{run_label}.log"), "w", encoding="utf-8") as f:
        for r in results:
            f.write(f"\n{'='*80}\n[{r['id']}] ({r['category']}) {'NEGATIVE' if r['negative'] else ''}\n")
            f.write(f"PROMPT: {r['prompt']}\n")
            for turn in r["transcript"]:
                if "error" in turn:
                    f.write(f"  -- turn {turn['turn']} ERROR: {turn['error']}\n")
                    continue
                f.write(f"  -- turn {turn['turn']} --\n")
                if turn.get("reasoning"):
                    f.write(f"  [THINK] {turn['reasoning']}\n")
                if turn.get("content"):
                    f.write(f"  [SAY]   {turn['content']}\n")
                for call in turn.get("tool_calls", []):
                    f.write(f"  [TOOL]  {call['name']}({call['arguments']})"
                            f"{' !BADJSON' if call['malformed_json'] else ''}\n")
                    f.write(f"  [RESULT]{call['result'][:500]}\n")
            f.write(f"FINAL ANSWER: {r['answer']}\n")
            f.write(f"CORRECT={r['correct']} TOOL_OK={r['tool_ok']} "
                    f"SCORE={r['score']} STATUS={r['status']}\n")


def write_report(all_runs, truth, n_tasks):
    """Writes results/report.md with leaderboard + categories."""
    rows = sorted(all_runs, key=lambda x: x["agg"].get("final_score", 0), reverse=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [f"# Local tool-calling benchmark — {ts}", ""]
    lines.append(f"- **{n_tasks} tasks** per run (read-only tools, deterministic decoding "
                 f"`temperature=0, seed=42`).")
    lines.append("- Each model is evaluated in **thinking ON** (`think`) and **OFF** (`nothink`).")
    lines.append("- Final score = 70% correctness + 20% tool-selection + 10% efficiency − penalties.")
    lines.append("")

    lines.append("## 🏆 Leaderboard")
    lines.append("")
    hdr = ("| # | Run | Score | Correct% | Tool% | Effic% | MaxIter | Neg ok | Halluc | NoTool | "
           "BadJSON | Errs | Lat(s) | OutTok |")
    sep = "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"
    lines += [hdr, sep]
    for i, run in enumerate(rows, 1):
        a = run["agg"]
        if not a:
            lines.append(f"| {i} | {run['label']} | — (failed to load) ||||||||||||")
            continue
        lines.append(
            f"| {i} | **{run['label']}** | **{a['final_score']}** | {a['correctness_pct']} | "
            f"{a['tool_accuracy_pct']} | {a['efficiency_pct']} | {a['max_iter']} | {a['negatives_ok']} | "
            f"{a['hallucinations']} | {a['no_tool_calls']} | {a['malformed_json']} | "
            f"{a['errors_timeouts']} | {a['avg_latency_s']} | {a['total_completion_tokens']} |")
    lines.append("")

    # categories
    cats = sorted({c for run in rows if run["agg"] for c in run["agg"]["by_category"]})
    if cats:
        lines.append("## Accuracy by category (%)")
        lines.append("")
        lines.append("| Category | " + " | ".join(run["label"] for run in rows if run["agg"]) + " |")
        lines.append("|---|" + "---|" * len([r for r in rows if r["agg"]]))
        for c in cats:
            cells = [str(run["agg"]["by_category"].get(c, "—")) for run in rows if run["agg"]]
            lines.append(f"| {c} | " + " | ".join(cells) + " |")
        lines.append("")

    # verdict
    if rows and rows[0]["agg"]:
        win = rows[0]
        lines.append("## Verdict")
        lines.append("")
        lines.append(f"**Winner: `{win['label']}`** with a score of **{win['agg']['final_score']}/100** "
                     f"({win['agg']['correctness_pct']}% of tasks correct, "
                     f"{win['agg']['tool_accuracy_pct']}% tool-selection accuracy).")
        lines.append("")
        lines.append("Full transcripts (reasoning + answers + tools) in "
                     "`results/logs/<run>.jsonl` and `.log`.")
    lines.append("")

    with open(os.path.join(RESULTS_DIR, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def print_leaderboard(all_runs):
    rows = sorted(all_runs, key=lambda x: x["agg"].get("final_score", 0), reverse=True)
    table = Table(title="Leaderboard", show_lines=False)
    for col in ["#", "Run", "Score", "Correct%", "Tool%", "Neg", "Halluc", "Lat(s)"]:
        table.add_column(col)
    for i, run in enumerate(rows, 1):
        a = run["agg"]
        if not a:
            table.add_row(str(i), run["label"], "— failed to load", "", "", "", "", "")
            continue
        table.add_row(str(i), run["label"], str(a["final_score"]),
                      str(a["correctness_pct"]), str(a["tool_accuracy_pct"]),
                      a["negatives_ok"], str(a["hallucinations"]), str(a["avg_latency_s"]))
    console.print(table)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", help="comma-separated aliases (default: all)")
    ap.add_argument("--modes", default="think,nothink", help="think,nothink")
    ap.add_argument("--limit", type=int, default=0, help="use only the first N tasks")
    ap.add_argument("--no-serve", action="store_true", help="use an already-running server")
    ap.add_argument("--alias", default="local", help="model alias if --no-serve")
    args = ap.parse_args()

    os.makedirs(RESULTS_DIR, exist_ok=True)
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]

    # sandbox + ground truth (once); the cwd becomes the sandbox
    info = fixtures.build_sandbox(SCRATCH)
    root = info["root"]
    os.chdir(root)
    tasks = build_tasks(info["truth"])
    if args.limit:
        tasks = tasks[:args.limit]
    console.print(f"[bold]Sandbox:[/] {root}  ·  {len(tasks)} tasks  ·  modes: {modes}")

    all_runs = []

    if args.no_serve:
        client = OpenAI(base_url=serve.base_url(), api_key="sk-noop")
        for mode in modes:
            label = f"{args.alias}-{mode}"
            console.rule(f"[bold cyan]{label}[/]")
            results, agg = run_suite(client, args.alias, label, tasks, root, MODE_FLAGS[mode])
            save_run(label, results, agg)
            all_runs.append({"label": label, "agg": agg})
    else:
        chosen = registry.select(args.models.split(",") if args.models else None)
        total = len(tasks)
        for model in chosen:
            labels = [f"{model['alias']}-{m}" for m in modes]
            # if all modes for this model are already complete, don't even load the model
            if not args.limit and all(is_complete(l, total) for l in labels):
                console.print(f"[dim]{model['alias']}: already complete, skipping (not loading the model)[/]")
                for l in labels:
                    agg = json.load(open(os.path.join(RESULTS_DIR, f"{l}.json"), encoding="utf-8"))["aggregate"]
                    all_runs.append({"label": l, "agg": agg})
                continue
            console.rule(f"[bold magenta]Loading model: {model['alias']}[/]")
            proc = None
            try:
                proc = serve.start_server(model, log=console.print)
            except Exception as e:
                console.print(f"[red]Could not load {model['alias']}: {e}[/]")
                for mode in modes:
                    all_runs.append({"label": f"{model['alias']}-{mode}", "agg": {}})
                continue
            try:
                client = OpenAI(base_url=serve.base_url(), api_key="sk-noop")
                for mode in modes:
                    label = f"{model['alias']}-{mode}"
                    console.rule(f"[bold cyan]{label}[/]")
                    results, agg = run_suite(client, model["alias"], label, tasks, root, MODE_FLAGS[mode])
                    save_run(label, results, agg)
                    all_runs.append({"label": label, "agg": agg})
            finally:
                serve.stop_server(proc, log=console.print)

    write_report(all_runs, info["truth"], len(tasks))
    print_leaderboard(all_runs)
    console.print(f"\n[bold green]Done.[/] Report: {os.path.join(RESULTS_DIR, 'report.md')}")
    console.print(f"Full logs: {LOGS_DIR}")


if __name__ == "__main__":
    main()
