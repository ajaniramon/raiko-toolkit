"""Runner for the CIRCUIT tier with the 3 winners.

Spins up a dev Vault, stores the Mac credentials as a secret, and for each
model runs the circuit: the agent reads the secret from Vault and copies a file
to the Mac over SSH/SFTP. Each task is verified by reading the file back on the
Mac. Resumable (incremental JSONL).
"""

import argparse
import json
import os
import sys

from openai import OpenAI
from rich.console import Console
from rich.table import Table

import fixtures
import models as registry
import serve
import vaultsvc
from harness import run_task, aggregate   # also puts the repo root on sys.path
from tools import TOOLS
import tools as _tools
from tasks_circuit import build_circuit_tasks, PAYLOAD_TOKEN


# The Mac SFTP tool is NOT part of the shipped toolset (tools.py): in production the
# agent operates the Mac through the MCP server, whose file/shell tools already run
# ON the Mac. This benchmark tier still tests an SSH copy, so we register the tool
# locally (test-only) and feed it the gitignored Mac credentials.
def copy_file_to_mac(local_path, remote_path, host, username, password, port=22):
    """Copy a local file to a remote host over SFTP/SSH. Test-only helper."""
    import paramiko
    from pathlib import Path
    if not Path(local_path).is_file():
        return f"ERROR: local file not found: {local_path}"
    transport = None
    try:
        transport = paramiko.Transport((host, int(port)))
        transport.banner_timeout = 20
        transport.connect(username=username, password=password)
        sftp = paramiko.SFTPClient.from_transport(transport)
        sftp.put(local_path, remote_path)
        sftp.close()
        return f"OK: copied {local_path} -> {host}:{remote_path}"
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"
    finally:
        if transport is not None:
            transport.close()


_COPY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "copy_file_to_mac",
        "description": "Copy a local file to a remote machine (the Mac) over SFTP/SSH using "
                       "username+password authentication. Returns OK on success.",
        "parameters": {
            "type": "object",
            "properties": {
                "local_path": {"type": "string", "description": "Path of the local file to copy."},
                "remote_path": {"type": "string", "description": "Destination path on the remote machine."},
                "host": {"type": "string", "description": "Remote host/IP."},
                "username": {"type": "string"},
                "password": {"type": "string"},
                "port": {"type": "integer", "default": 22},
            },
            "required": ["local_path", "remote_path", "host", "username", "password"],
        },
    },
}
# register the test-only tool so harness.call_tool can dispatch it for this tier
_tools.DISPATCH["copy_file_to_mac"] = copy_file_to_mac

console = Console()
HERE = os.path.dirname(os.path.abspath(__file__))
CIRC_DIR = os.path.join(HERE, "results", "circuit")
CIRC_LOGS = os.path.join(CIRC_DIR, "logs")
SCRATCH = (r"C:\Users\RAMN~1\AppData\Local\Temp\claude"
           r"\C--Users-Ram-n-Desktop-agent\9f784f53-c05e-4e87-a7ba-e6b921ad38b0\scratchpad")

TARGET_RUNS = [("qwen35-9b", "nothink"), ("gemma4-12b", "nothink"), ("qwythos", "think")]

# Mac data (NOT versioned): mac-credentials.txt at the repo root, with
# line 1 = "user:pass" and line 2 (optional) = host. The environment
# (MAC_USER/MAC_PASS/MAC_HOST) takes priority. Harmless defaults for the repo.
def _mac_creds():
    user = os.environ.get("MAC_USER", "")
    pw = os.environ.get("MAC_PASS", "")
    host = os.environ.get("MAC_HOST", "")
    cred_file = os.path.join(os.path.dirname(HERE), "mac-credentials.txt")
    try:
        with open(cred_file, encoding="utf-8") as fh:
            lines = [ln.strip() for ln in fh if ln.strip()]
        if lines and ":" in lines[0]:
            fu, fp = lines[0].split(":", 1)
            user, pw = user or fu, pw or fp
        if len(lines) > 1:
            host = host or lines[1]
    except Exception:
        pass
    return user, pw, (host or "localhost")

_MAC_USER, _MAC_PASS, _MAC_HOST = _mac_creds()
MAC = {"host": _MAC_HOST, "port": "22", "username": _MAC_USER, "password": _MAC_PASS}
SECRET = dict(MAC, credentials=f"{_MAC_USER}:{_MAC_PASS}")

FULL_TOOLS = TOOLS + [_COPY_SCHEMA]

CIRCUIT_SYSTEM = (
    "You are a tool-using agent with access to filesystem tools, a Vault secret tool "
    "(vault_get_secret) and a remote file-copy tool (copy_file_to_mac). To accomplish a "
    "task, call the appropriate tools: e.g. first read the required secret from Vault, then "
    "use those credentials to copy a file. Always call tools to perform actions; never guess "
    "or fabricate. When finished, briefly state what you did."
)


def _partial(label):
    return os.path.join(CIRC_LOGS, f"{label}.jsonl")


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
    p = os.path.join(CIRC_DIR, f"{label}.json")
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))["aggregate"].get("n_tasks") == total
        except Exception:
            return False
    return False


def save_run(label, results, agg):
    os.makedirs(CIRC_LOGS, exist_ok=True)
    json.dump({"run": label, "aggregate": agg,
               "tasks": [{k: v for k, v in r.items() if k != "transcript"} for r in results]},
              open(os.path.join(CIRC_DIR, f"{label}.json"), "w", encoding="utf-8"),
              indent=2, ensure_ascii=False)
    with open(os.path.join(CIRC_LOGS, f"{label}.log"), "w", encoding="utf-8") as f:
        for r in results:
            f.write(f"\n{'='*80}\n[{r['id']}]\nPROMPT: {r['prompt']}\n")
            for turn in r["transcript"]:
                if "error" in turn:
                    f.write(f"  ERROR: {turn['error']}\n"); continue
                if turn.get("reasoning"):
                    f.write(f"  [THINK] {turn['reasoning']}\n")
                if turn.get("content"):
                    f.write(f"  [SAY]   {turn['content']}\n")
                for c in turn.get("tool_calls", []):
                    # hide the password in the log
                    args = c["arguments"].replace(MAC["password"], "****")
                    f.write(f"  [TOOL]  {c['name']}({args})\n  [RESULT]{c['result'][:300]}\n")
            f.write(f"FINAL: {r['answer']}\nCORRECT={r['correct']} TOOL_OK={r['tool_ok']} SCORE={r['score']}\n")


def run_suite(client, alias, label, tasks, root, enable_thinking):
    os.makedirs(CIRC_LOGS, exist_ok=True)
    done = load_partial(label)
    results = []
    f = open(_partial(label), "a", encoding="utf-8")
    try:
        for i, task in enumerate(tasks, 1):
            if task["id"] in done:
                results.append(done[task["id"]])
                console.print(f"  [{label}] {i}/{len(tasks)} {task['id']:<16} [dim]cached[/]")
                continue
            r = run_task(client, alias, task, root, enable_thinking,
                         tools=FULL_TOOLS, grader_root=True, system_prompt=CIRCUIT_SYSTEM)
            results.append(r)
            f.write(json.dumps(r, ensure_ascii=False) + "\n"); f.flush(); os.fsync(f.fileno())
            mark = "[green]OK [/]" if r["correct"] else "[red]FAIL[/]"
            tool = "" if r["tool_ok"] else "[yellow](tool?)[/]"
            console.print(f"  [{label}] {i}/{len(tasks)} {task['id']:<16} {mark} {tool} "
                          f"score={r['score']:.2f} iters={r['iterations']} {r['latency_s']}s [dim]{r['status']}[/]")
    finally:
        f.close()
    return results, aggregate(results)


def write_report(all_runs, n_tasks):
    rows = sorted(all_runs, key=lambda x: x["agg"].get("final_score", 0), reverse=True)
    lines = ["# CIRCUIT benchmark (Vault -> SSH/SCP to the Mac)", "",
             f"- **{n_tasks} tasks** per run. The agent reads the secret from Vault and copies the",
             "  file to the Mac; it's verified by reading the file back over SSH.",
             "- New tools: `vault_get_secret`, `copy_file_to_mac`. KV v2 secret at `secret/data/mac`.",
             "- Only the 3 winners.", "",
             "## Leaderboard", "",
             "| # | Run | Score | Correct% | Tool% | Errs | Lat(s) |",
             "|---|---|---|---|---|---|---|"]
    for i, run in enumerate(rows, 1):
        a = run["agg"]
        lines.append(f"| {i} | **{run['label']}** | **{a['final_score']}** | {a['correctness_pct']} | "
                     f"{a['tool_accuracy_pct']} | {a['errors_timeouts']} | {a['avg_latency_s']} |")
    lines += ["", "Transcripts (password redacted) in `results/circuit/logs/`.", ""]
    os.makedirs(CIRC_DIR, exist_ok=True)
    open(os.path.join(CIRC_DIR, "report_circuit.md"), "w", encoding="utf-8").write("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", help="alias:mode,... (default: 3 winners)")
    args = ap.parse_args()
    os.makedirs(CIRC_LOGS, exist_ok=True)
    target = TARGET_RUNS
    if args.runs:
        target = [(x.split(":")[0], x.split(":")[1]) for x in args.runs.split(",")]

    vault = vaultsvc.start_vault(log=console.print)
    all_runs = []
    try:
        vaultsvc.seed_secret("secret/data/mac", SECRET, log=console.print)
        os.environ["VAULT_ADDR"] = vaultsvc.ADDR
        os.environ["VAULT_TOKEN"] = vaultsvc.TOKEN

        # number of tasks (same for all)
        total = len(build_circuit_tasks(MAC, "x"))
        console.print(f"[bold]Circuit:[/] {total} tasks · runs: {target}")

        for alias, mode in target:
            label = f"{alias}-{mode}"
            if is_complete(label, total):
                console.print(f"[dim]{label}: already complete, skipping[/]")
                agg = json.load(open(os.path.join(CIRC_DIR, f"{label}.json"), encoding="utf-8"))["aggregate"]
                all_runs.append({"label": label, "agg": agg}); continue
            model = next((m for m in registry.MODELS if m["alias"] == alias), None)
            if model is None:
                console.print(f"[red]unknown alias: {alias}[/]"); continue
            console.rule(f"[bold magenta]{label}[/]")
            proc = None
            try:
                proc = serve.start_server(model, log=console.print)
                client = OpenAI(base_url=serve.base_url(), api_key="sk-noop")
                os.chdir(SCRATCH)
                root = fixtures.build_sandbox(SCRATCH)["root"]
                os.chdir(root)
                open(os.path.join(root, "payload.txt"), "w", encoding="utf-8").write(PAYLOAD_TOKEN)
                tasks = build_circuit_tasks(MAC, alias)
                results, agg = run_suite(client, alias, label, tasks, root, mode == "think")
                save_run(label, results, agg)
                all_runs.append({"label": label, "agg": agg})
            except Exception as e:
                console.print(f"[red]failure in {label}: {e}[/]")
            finally:
                serve.stop_server(proc, log=console.print)
                os.chdir(HERE)
    finally:
        vaultsvc.stop_vault(vault, log=console.print)

    if all_runs:
        write_report(all_runs, len(build_circuit_tasks(MAC, "x")))
        t = Table(title="Circuit Leaderboard")
        for col in ["Run", "Score", "Correct%", "Tool%"]:
            t.add_column(col)
        for run in sorted(all_runs, key=lambda x: x["agg"].get("final_score", 0), reverse=True):
            a = run["agg"]
            t.add_row(run["label"], str(a["final_score"]), str(a["correctness_pct"]), str(a["tool_accuracy_pct"]))
        console.print(t)
    console.print(f"[green]Done.[/] Report: {os.path.join(CIRC_DIR, 'report_circuit.md')}")


if __name__ == "__main__":
    main()
