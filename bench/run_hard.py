"""Runner for the HARDCORE tier (real incident) against the 3 winners.

FRESH sandbox per task (unique dir) because several tasks edit/fix code.
Full tool set, strict graders (some run the code). Resumable.
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

import fixtures_hard
import models as registry
import serve
from harness import run_task, aggregate
from tools import TOOLS
from tasks_hard import build_tasks_hard

console = Console()
HERE = os.path.dirname(os.path.abspath(__file__))
HARD_DIR = os.path.join(HERE, "results", "hard")
HARD_LOGS = os.path.join(HARD_DIR, "logs")
# Per-run sandbox workspace (portable temp dir; created on demand).
SCRATCH = os.path.join(tempfile.gettempdir(), "raiko-bench")
os.makedirs(SCRATCH, exist_ok=True)

TARGET_RUNS = [("qwen35-9b", "nothink"), ("gemma4-12b", "nothink"), ("qwythos", "think")]
FULL_TOOLS = TOOLS

HARD_SYSTEM = (
    "You are a senior software engineer and sysadmin debugging a production service. "
    "Use the available tools (read/grep/find/edit_file/run_python/run_powershell) to "
    "investigate and act. Be rigorous: parse real tool output carefully, correlate across "
    "files, and NEVER fabricate values — if you state a number or a name it must come from "
    "the tools. When you fix code, verify it by running it. End with a short, direct answer "
    "containing exactly the values requested."
)

# v2: additionally tells it to STOP and answer as soon as it has the value (anti-loop)
HARD_SYSTEM_V2 = HARD_SYSTEM + (
    " CRITICAL: The moment you have gathered the specific value(s) the task asks for, STOP "
    "calling tools and immediately write your final answer in plain text. Do NOT keep "
    "searching or second-guessing once you have the answer. If a task asks for several files "
    "to be changed, change all of them and then confirm. Beware of decoy/similar values — "
    "pick the one the task actually asks for and commit to it."
)

# set in main() according to the flags
SYS_PROMPT = HARD_SYSTEM
MAX_ITERS = None


def _partial(label):
    return os.path.join(HARD_LOGS, f"{label}.jsonl")


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
    p = os.path.join(HARD_DIR, f"{label}.json")
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))["aggregate"].get("n_tasks") == total
        except Exception:
            return False
    return False


def save_run(label, results, agg):
    os.makedirs(HARD_LOGS, exist_ok=True)
    json.dump({"run": label, "aggregate": agg,
               "tasks": [{k: v for k, v in r.items() if k != "transcript"} for r in results]},
              open(os.path.join(HARD_DIR, f"{label}.json"), "w", encoding="utf-8"),
              indent=2, ensure_ascii=False)
    with open(os.path.join(HARD_LOGS, f"{label}.log"), "w", encoding="utf-8") as f:
        for r in results:
            f.write(f"\n{'='*80}\n[{r['id']}] ({r['category']})\nPROMPT: {r['prompt']}\n")
            for turn in r["transcript"]:
                if "error" in turn:
                    f.write(f"  ERROR: {turn['error']}\n"); continue
                if turn.get("reasoning"):
                    f.write(f"  [THINK] {turn['reasoning'][:600]}\n")
                if turn.get("content"):
                    f.write(f"  [SAY]   {turn['content']}\n")
                for c in turn.get("tool_calls", []):
                    f.write(f"  [TOOL]  {c['name']}({c['arguments'][:200]})\n  [RESULT]{c['result'][:300]}\n")
            f.write(f"FINAL: {r['answer']}\nCORRECT={r['correct']} TOOL_OK={r['tool_ok']} SCORE={r['score']}\n")


def run_suite(client, alias, label, tasks, enable_thinking):
    os.makedirs(HARD_LOGS, exist_ok=True)
    done = load_partial(label)
    results = []
    f = open(_partial(label), "a", encoding="utf-8")
    try:
        for i, task in enumerate(tasks, 1):
            if task["id"] in done:
                results.append(done[task["id"]])
                console.print(f"  [{label}] {i:>2}/{len(tasks)} {task['id']:<18} [dim]cached[/]")
                continue
            os.chdir(SCRATCH)
            base = os.path.join(SCRATCH, "hardtmp", f"{label}_{i}")
            root = fixtures_hard.build_sandbox(base)["root"]
            os.chdir(root)
            r = run_task(client, alias, task, root, enable_thinking,
                         tools=FULL_TOOLS, grader_root=True,
                         system_prompt=SYS_PROMPT, max_iterations=MAX_ITERS)
            results.append(r)
            f.write(json.dumps(r, ensure_ascii=False) + "\n"); f.flush(); os.fsync(f.fileno())
            mark = "[green]OK [/]" if r["correct"] else "[red]FAIL[/]"
            tool = "" if r["tool_ok"] else "[yellow](tool?)[/]"
            console.print(f"  [{label}] {i:>2}/{len(tasks)} {task['id']:<18} {mark} {tool} "
                          f"score={r['score']:.2f} iters={r['iterations']} {r['latency_s']}s [dim]{r['status']}[/]")
    finally:
        f.close()
    return results, aggregate(results)


def write_report(all_runs, n_tasks):
    rows = sorted(all_runs, key=lambda x: x["agg"].get("final_score", 0), reverse=True)
    lines = ["# HARDCORE benchmark (real dev/sysadmin incident)", "",
             f"- **{n_tasks} tasks** multi-step · fresh sandbox per task · strict graders.",
             "- Includes parsing tracebacks, correlating configs, finding a secret, FIXING",
             "  a bug (its self-test is run), multi-file bumps and access-log analysis.",
             "- Only the 3 winners.", "",
             "## Leaderboard", "",
             "| # | Run | Score | Correct% | Tool% | Eff% | MaxIter | BadJSON | Errs | Lat(s) | OutTok |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, run in enumerate(rows, 1):
        a = run["agg"]
        lines.append(f"| {i} | **{run['label']}** | **{a['final_score']}** | {a['correctness_pct']} | "
                     f"{a['tool_accuracy_pct']} | {a['efficiency_pct']} | {a['max_iter']} | "
                     f"{a['malformed_json']} | {a['errors_timeouts']} | "
                     f"{a['avg_latency_s']} | {a['total_completion_tokens']} |")
    cats = sorted({c for run in rows for c in run["agg"]["by_category"]})
    lines += ["", "## Accuracy by category (%)", "",
              "| Category | " + " | ".join(r["label"] for r in rows) + " |",
              "|---|" + "---|" * len(rows)]
    for c in cats:
        lines.append(f"| {c} | " + " | ".join(str(r["agg"]["by_category"].get(c, "—")) for r in rows) + " |")
    lines += ["", "Transcripts in `results/hard/logs/`.", ""]
    os.makedirs(HARD_DIR, exist_ok=True)
    open(os.path.join(HARD_DIR, "report_hard.md"), "w", encoding="utf-8").write("\n".join(lines))


def main():
    global HARD_DIR, HARD_LOGS, SYS_PROMPT, MAX_ITERS
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", help="alias:modo,...")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tag", default="", help="suffix for the results dir (e.g. v2)")
    ap.add_argument("--max-iters", type=int, default=0, help="override of MAX_ITERATIONS")
    ap.add_argument("--strong", action="store_true", help="use the v2 system prompt (anti-loop)")
    args = ap.parse_args()

    if args.tag:
        HARD_DIR = os.path.join(HERE, "results", f"hard_{args.tag}")
        HARD_LOGS = os.path.join(HARD_DIR, "logs")
    if args.max_iters:
        MAX_ITERS = args.max_iters
    if args.strong:
        SYS_PROMPT = HARD_SYSTEM_V2
    console.print(f"[dim]config: tag='{args.tag or 'base'}' max_iters={MAX_ITERS or 6} "
                  f"prompt={'v2-strong' if args.strong else 'base'}[/]")

    os.makedirs(HARD_LOGS, exist_ok=True)
    os.chdir(HERE)
    shutil.rmtree(os.path.join(SCRATCH, "hardtmp"), ignore_errors=True)

    truth = fixtures_hard.build_sandbox(os.path.join(SCRATCH, "hardtmp", "ref"))["truth"]
    tasks = build_tasks_hard(truth)
    if args.limit:
        tasks = tasks[:args.limit]
    total = len(tasks)
    target = TARGET_RUNS
    if args.runs:
        target = [(x.split(":")[0], x.split(":")[1]) for x in args.runs.split(",")]
    console.print(f"[bold]HARDCORE:[/] {total} tasks · runs: {target}")

    all_runs = []
    for alias, mode in target:
        label = f"{alias}-{mode}"
        if not args.limit and is_complete(label, total):
            console.print(f"[dim]{label}: already complete, skipping[/]")
            agg = json.load(open(os.path.join(HARD_DIR, f"{label}.json"), encoding="utf-8"))["aggregate"]
            all_runs.append({"label": label, "agg": agg}); continue
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
            os.chdir(HERE)

    if all_runs:
        write_report(all_runs, total)
        t = Table(title="HARDCORE Leaderboard")
        for col in ["Run", "Score", "Correct%", "Tool%"]:
            t.add_column(col)
        for run in sorted(all_runs, key=lambda x: x["agg"].get("final_score", 0), reverse=True):
            a = run["agg"]
            t.add_row(run["label"], str(a["final_score"]), str(a["correctness_pct"]), str(a["tool_accuracy_pct"]))
        console.print(t)
    console.print(f"[green]Done.[/] Report: {os.path.join(HARD_DIR, 'report_hard.md')}")


if __name__ == "__main__":
    main()
