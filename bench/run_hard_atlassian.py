"""Runner for the HARD Atlassian tier — the frontier discriminator.

The floor tiers saturate (a competent 9B scores ~97%); this battery is curated to
SEPARATE strong models: multi-hop planning, disambiguation, conflict detection,
constraint-satisfying writes, and anti-sycophancy (false-premise) tasks. It runs on
the same dependency-free in-process mocks (rebuilt fresh per task).

Remote-first (models usually live on another box, served by the MCP `models` server or a
cloud provider); falls back to a local llama-server via `serve` when no --url is given.

Usage:
  python run_hard_atlassian.py --model ornith-9b-q4 --url http://192.168.1.9:25565/v1
  python run_hard_atlassian.py --model deepseek/deepseek-v4-flash --url https://nano-gpt.com/api/v1 --nano
  python run_hard_atlassian.py --model qwen35-9b            # local serve fallback
"""
import argparse
import json
import os
import tempfile

from openai import OpenAI
from rich.console import Console
from rich.table import Table

import fixtures_atlassian as fx
from harness import run_task, aggregate
from tools import TOOLS, DISPATCH
from mock_atlassian import MockJira, MockConfluence, MockVault, AtlasCtx, build_atlas_impls
from tasks_hard_atlassian import build_hard_atlassian_tasks

console = Console()
HERE = os.path.dirname(os.path.abspath(__file__))
HARD_DIR = os.path.join(HERE, "results", "hard_atlassian")
SCRATCH = os.path.join(tempfile.gettempdir(), "raiko-bench")


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
    return os.path.join(HARD_DIR, f"{label}.jsonl")


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
    os.makedirs(HARD_DIR, exist_ok=True)
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
            root = os.path.join(SCRATCH, "hardtmp", f"{label}_{i}")
            os.makedirs(root, exist_ok=True)
            os.chdir(root)
            jira = MockJira(fx.build_jira_seed(), fx.USERS)
            conf = MockConfluence(fx.build_confluence_seed(), fx.USERS)
            vault = MockVault(fx.build_vault_seed())
            disp = dict(DISPATCH)
            disp.update(build_atlas_impls(jira, conf, vault))
            ctx = AtlasCtx(jira, conf, vault, root)
            r = run_task(client, model_name, task, root, enable_thinking,
                         tools=TOOLS, dispatch=disp, grader_ctx=ctx)
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
    os.makedirs(HARD_DIR, exist_ok=True)
    lines = ["# HARD Atlassian tier — frontier discriminator", "",
             f"- **{agg['n_tasks']} tasks** · in-process mocks · all-or-nothing graders · "
             f"scoring 70/15/15 with a max-iter (flailing) penalty.", "",
             "## Result", "",
             "| Run | Score | Correct% | Tool% | Eff% | MaxIter | Halluc |",
             "|---|---|---|---|---|---|---|",
             f"| **{label}** | **{agg['final_score']}** | {agg['correctness_pct']} | "
             f"{agg['tool_accuracy_pct']} | {agg['efficiency_pct']} | {agg['max_iter']} | "
             f"{agg['hallucinations']} |", "",
             "## By family (correct/total)", ""]
    for c, v in fam.items():
        lines.append(f"- `{c}` — {v}")
    lines += ["", f"Transcripts in `results/hard_atlassian/`.", ""]
    open(os.path.join(HARD_DIR, f"report_{label}.md"), "w", encoding="utf-8").write("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="model name/alias sent to the API")
    ap.add_argument("--url", default="", help="OpenAI-compatible base_url; omit for local serve")
    ap.add_argument("--nano", action="store_true", help="read the nano-gpt API key from tui_config")
    ap.add_argument("--label", default="", help="label for the result files (default: sanitized model)")
    ap.add_argument("--think", action="store_true", help="enable the model's thinking template")
    ap.add_argument("--limit", type=int, default=0, help="only the first N tasks (smoke)")
    args = ap.parse_args()

    os.makedirs(SCRATCH, exist_ok=True)
    label = args.label or args.model.replace("/", "_")
    tasks = build_hard_atlassian_tasks()
    if args.limit:
        tasks = tasks[:args.limit]
    console.print(f"[bold]HARD Atlassian:[/] {len(tasks)} tasks · model={args.model}")

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

        results, agg = run_suite(client, args.model, label, tasks, args.think)
        fam = by_family(results)
        os.chdir(HERE)
        json.dump({"model": args.model, "label": label, "aggregate": agg, "by_family": fam,
                   "tasks": [{k: v for k, v in r.items() if k != "transcript"} for r in results]},
                  open(os.path.join(HARD_DIR, f"{label}.json"), "w", encoding="utf-8"),
                  indent=2, ensure_ascii=False)
        write_report(label, agg, fam)

        t = Table(title=f"HARD Atlassian — {label}")
        for col in ["Score", "Correct%", "Tool%", "Eff%", "MaxIter", "Halluc"]:
            t.add_column(col)
        t.add_row(str(agg["final_score"]), str(agg["correctness_pct"]), str(agg["tool_accuracy_pct"]),
                  str(agg["efficiency_pct"]), str(agg["max_iter"]), str(agg["hallucinations"]))
        console.print(t)
        console.print("[bold]by family:[/] " + "  ".join(f"{c}={v}" for c, v in fam.items()))
    finally:
        if proc is not None:
            import serve
            serve.stop_server(proc, log=console.print)


if __name__ == "__main__":
    main()
