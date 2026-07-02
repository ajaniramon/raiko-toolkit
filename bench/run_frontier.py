"""Runner for the FRONTIER tier — cross-domain chains over k8s/git/sql/Atlassian.

40 tasks spanning 4 families (X1 chains, X2 root-cause, X3 false premise, X4
live-vs-doc conflict) over deterministic in-process mocks: Kubernetes, Git/GitHub,
SQL (real SQLite) plus the existing Atlassian mocks (Jira/Confluence/Vault),
extended with cross-referencing seed data (see fixtures_frontier.py). Mirrors
run_hard_atlassian.py's structure exactly, with its own system prompt and
results dir.

Remote-first (models usually live on another box, served by the MCP `models` server or a
cloud provider); falls back to a local llama-server via `serve` when no --url is given.

Usage:
  python run_frontier.py --model ornith-9b-q4 --url http://192.168.1.9:25565/v1
  python run_frontier.py --model deepseek/deepseek-v4-flash --url https://nano-gpt.com/api/v1 --nano
  python run_frontier.py --model qwen35-9b            # local serve fallback
"""
import argparse
import json
import os
import tempfile

from openai import OpenAI
from rich.console import Console
from rich.table import Table

import fixtures_frontier as ff
from harness import run_task, aggregate
from tools import TOOLS, DISPATCH
from mock_atlassian import MockJira, MockConfluence, MockVault, build_atlas_impls
from fixtures_atlassian import USERS
from mock_k8s import MockK8s
from mock_git import MockGit
from mock_sql import MockSQL
from tasks_frontier import build_frontier_tasks

console = Console()
HERE = os.path.dirname(os.path.abspath(__file__))
FRONTIER_DIR = os.path.join(HERE, "results", "frontier")
SCRATCH = os.path.join(tempfile.gettempdir(), "raiko-bench")

# Own prompt (like ATLAS_SYSTEM in run_hard_atlassian): mentions all six tool
# domains this tier exposes and states the live-vs-doc trust order up front,
# since the X4 family exists specifically to test that ordering.
FRONTIER_SYSTEM = (
    "You are a tool-using agent with access to Jira, Confluence, Vault, Kubernetes, "
    "Git/GitHub and SQL tools, plus basic file tools for writing output files. Always "
    "verify claims against the live systems before acting or answering; documents can "
    "be stale — the live system is authoritative. If something does not exist or a "
    "stated fact is wrong, say so plainly instead of inventing or confirming it. When "
    "done, reply with a short, direct final message stating the outcome."
)

# Same hard cap as the HARD tier (see run_hard_atlassian.py for the rationale):
# separate from the per-family iter_budget (3-8) that drives the efficiency score.
HARD_MAX_ITERS = 12


def _nano_key():
    for p in (os.path.expanduser("~/.raiko/tui_config.json"),
              os.path.join(HERE, "..", "tui_config.json")):
        try:
            cfg = json.load(open(p, encoding="utf-8"))
            nano = (cfg.get("providers", {}) or {}).get("nano") or cfg.get("nano") or {}
            if nano.get("api_key"):
                return nano["api_key"]
        except Exception:
            pass
    return os.environ.get("NANO_GPT_API_KEY", "")


def _partial(label):
    return os.path.join(FRONTIER_DIR, f"{label}.jsonl")


def load_partial(label):
    done = {}
    if os.path.exists(_partial(label)):
        for line in open(_partial(label), encoding="utf-8"):
            line = line.strip()
            if line:
                try:
                    r = json.loads(line)
                    done[r["id"]] = r
                except Exception:
                    pass
    return done


def run_suite(client, model_name, label, tasks, enable_thinking):
    os.makedirs(FRONTIER_DIR, exist_ok=True)
    done = load_partial(label)
    results = []
    f = open(_partial(label), "a", encoding="utf-8")
    try:
        for i, task in enumerate(tasks, 1):
            if task["id"] in done:
                results.append(done[task["id"]])
                console.print(f"  [{label}] {i:>2}/{len(tasks)} {task['id']:<26} [dim]cached[/]")
                continue
            os.chdir(SCRATCH)
            root = os.path.join(SCRATCH, "frontiertmp", f"{label}_{i}")
            os.makedirs(root, exist_ok=True)
            os.chdir(root)
            issues, pages, vault_seed = ff.build_frontier_atlassian()
            jira = MockJira(issues, USERS)
            conf = MockConfluence(pages, USERS)
            vault = MockVault(vault_seed)
            k8s = MockK8s()
            git = MockGit()
            sql = MockSQL()
            disp = dict(DISPATCH)
            disp.update(build_atlas_impls(jira, conf, vault))
            disp.update(ff.build_frontier_impls(k8s, git, sql))
            tools = TOOLS + ff.FRONTIER_TOOLS
            ctx = ff.FrontierCtx(jira, conf, vault, k8s, git, sql, root)
            r = run_task(client, model_name, task, root, enable_thinking,
                         tools=tools, dispatch=disp, grader_ctx=ctx,
                         system_prompt=FRONTIER_SYSTEM, max_iterations=HARD_MAX_ITERS)
            results.append(r)
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            f.flush(); os.fsync(f.fileno())
            mark = "[green]OK [/]" if r["correct"] else "[red]FAIL[/]"
            console.print(f"  [{label}] {i:>2}/{len(tasks)} {task['id']:<26} {mark} "
                          f"iters={r['iterations']} {r['latency_s']}s [dim]{r['status']}[/]")
    finally:
        f.close()
    return results, aggregate(results)


def by_family(results):
    fam = {}
    for r in results:
        d = fam.setdefault(r["category"], [0, 0])
        d[1] += 1
        d[0] += 1 if r["correct"] else 0
    return {c: f"{ok}/{tot}" for c, (ok, tot) in sorted(fam.items())}


def write_report(label, agg, fam):
    os.makedirs(FRONTIER_DIR, exist_ok=True)
    lines = ["# FRONTIER tier — cross-domain chains", "",
             f"- **{agg['n_tasks']} tasks** · in-process mocks (k8s/git/sql/Atlassian) · "
             f"all-or-nothing graders · scoring 70/15/15 · hard cap {HARD_MAX_ITERS} iters "
             "(a run that dies at the cap scores 0 on correct+eff; no extra penalty).", "",
             "## Result", "",
             "| Run | Score | Correct% | Tool% | Eff% | MaxIter | Halluc |",
             "|---|---|---|---|---|---|---|",
             f"| **{label}** | **{agg['final_score']}** | {agg['correctness_pct']} | "
             f"{agg['tool_accuracy_pct']} | {agg['efficiency_pct']} | {agg['max_iter']} | "
             f"{agg['hallucinations']} |", "",
             "## By family (correct/total)", ""]
    for c, v in fam.items():
        lines.append(f"- `{c}` — {v}")
    lines += ["", f"Transcripts in `results/frontier/`.", ""]
    open(os.path.join(FRONTIER_DIR, f"report_{label}.md"), "w", encoding="utf-8").write("\n".join(lines))


def run_model(client, model_name, label, think=False, limit=0):
    """Run the full FRONTIER suite for one model/label. Returns the aggregate dict."""
    os.makedirs(SCRATCH, exist_ok=True)
    tasks = build_frontier_tasks()
    if limit:
        tasks = tasks[:limit]
    console.print(f"[bold]FRONTIER:[/] {len(tasks)} tasks · model={model_name} · label={label}")
    results, agg = run_suite(client, model_name, label, tasks, think)
    fam = by_family(results)
    os.chdir(HERE)
    json.dump({"model": model_name, "label": label, "aggregate": agg, "by_family": fam,
               "tasks": [{k: v for k, v in r.items() if k != "transcript"} for r in results]},
              open(os.path.join(FRONTIER_DIR, f"{label}.json"), "w", encoding="utf-8"),
              indent=2, ensure_ascii=False)
    write_report(label, agg, fam)
    console.print("[bold]by family:[/] " + "  ".join(f"{c}={v}" for c, v in fam.items()))
    return agg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="model name/alias sent to the API")
    ap.add_argument("--url", default="", help="OpenAI-compatible base_url; omit for local serve")
    ap.add_argument("--nano", action="store_true", help="read the nano-gpt API key from tui_config")
    ap.add_argument("--label", default="", help="label for the result files (default: sanitized model)")
    ap.add_argument("--think", action="store_true", help="enable the model's thinking template")
    ap.add_argument("--limit", type=int, default=0, help="only the first N tasks (smoke)")
    args = ap.parse_args()

    label = args.label or args.model.replace("/", "_")

    proc = None
    try:
        if args.url:
            key = _nano_key() if args.nano else "sk-noop"
            client = OpenAI(base_url=args.url, api_key=key)
        else:
            import serve
            import models as registry
            model = next((m for m in registry.MODELS if m["alias"] == args.model), None)
            if model is None:
                raise SystemExit(f"unknown local alias: {args.model}")
            proc = serve.start_server(model, log=console.print)
            client = OpenAI(base_url=serve.base_url(), api_key="sk-noop")

        agg = run_model(client, args.model, label, args.think, args.limit)

        t = Table(title=f"FRONTIER — {label}")
        for col in ["Score", "Correct%", "Tool%", "Eff%", "MaxIter", "Halluc"]:
            t.add_column(col)
        t.add_row(str(agg["final_score"]), str(agg["correctness_pct"]), str(agg["tool_accuracy_pct"]),
                  str(agg["efficiency_pct"]), str(agg["max_iter"]), str(agg["hallucinations"]))
        console.print(t)
    finally:
        if proc is not None:
            import serve
            serve.stop_server(proc, log=console.print)


if __name__ == "__main__":
    main()
