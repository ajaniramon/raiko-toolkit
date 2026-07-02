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
