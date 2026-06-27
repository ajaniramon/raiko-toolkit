"""Runner del tier CIRCUITO con los 3 ganadores.

Monta un Vault dev, mete las credenciales del Mac como secreto, y para cada
modelo corre el circuito: el agente lee el secreto de Vault y copia un fichero
al Mac por SSH/SFTP. Cada tarea se verifica leyendo el fichero de vuelta en el
Mac. Resumible (JSONL incremental).
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
from harness import run_task, aggregate
from tools import TOOLS
from tasks_circuit import build_circuit_tasks, PAYLOAD_TOKEN

console = Console()
HERE = os.path.dirname(os.path.abspath(__file__))
CIRC_DIR = os.path.join(HERE, "results", "circuit")
CIRC_LOGS = os.path.join(CIRC_DIR, "logs")
SCRATCH = (r"C:\Users\RAMN~1\AppData\Local\Temp\claude"
           r"\C--Users-Ram-n-Desktop-agent\9f784f53-c05e-4e87-a7ba-e6b921ad38b0\scratchpad")

TARGET_RUNS = [("qwen35-9b", "nothink"), ("gemma4-12b", "nothink"), ("qwythos", "think")]

# credenciales del Mac: se leen de mac-credentials.txt (NO versionado) o del entorno.
def _mac_creds():
    cred_file = os.path.join(os.path.dirname(HERE), "mac-credentials.txt")
    try:
        with open(cred_file, encoding="utf-8") as fh:
            user, pw = fh.read().strip().split(":", 1)
            return user, pw
    except Exception:
        return os.environ.get("MAC_USER", ""), os.environ.get("MAC_PASS", "")

_MAC_USER, _MAC_PASS = _mac_creds()
MAC = {"host": os.environ.get("MAC_HOST", "100.102.204.32"), "port": "22",
       "username": _MAC_USER, "password": _MAC_PASS}
SECRET = dict(MAC, credentials=f"{_MAC_USER}:{_MAC_PASS}")

FULL_TOOLS = TOOLS

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
                    # ocultar el password en el log
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
    lines = ["# Benchmark CIRCUITO (Vault -> SSH/SCP al Mac)", "",
             f"- **{n_tasks} tareas** por run. El agente lee el secreto de Vault y copia el",
             "  fichero al Mac; se verifica leyendo el fichero de vuelta por SSH.",
             "- Tools nuevas: `vault_get_secret`, `copy_file_to_mac`. Secreto KV v2 en `secret/data/mac`.",
             "- Solo los 3 ganadores.", "",
             "## Leaderboard", "",
             "| # | Run | Score | Correct% | Tool% | Errs | Lat(s) |",
             "|---|---|---|---|---|---|---|"]
    for i, run in enumerate(rows, 1):
        a = run["agg"]
        lines.append(f"| {i} | **{run['label']}** | **{a['final_score']}** | {a['correctness_pct']} | "
                     f"{a['tool_accuracy_pct']} | {a['errors_timeouts']} | {a['avg_latency_s']} |")
    lines += ["", "Transcripts (password censurado) en `results/circuit/logs/`.", ""]
    os.makedirs(CIRC_DIR, exist_ok=True)
    open(os.path.join(CIRC_DIR, "report_circuit.md"), "w", encoding="utf-8").write("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", help="alias:modo,... (default: 3 ganadores)")
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

        # nº de tareas (mismo para todos)
        total = len(build_circuit_tasks(MAC, "x"))
        console.print(f"[bold]Circuito:[/] {total} tareas · runs: {target}")

        for alias, mode in target:
            label = f"{alias}-{mode}"
            if is_complete(label, total):
                console.print(f"[dim]{label}: ya completo, salto[/]")
                agg = json.load(open(os.path.join(CIRC_DIR, f"{label}.json"), encoding="utf-8"))["aggregate"]
                all_runs.append({"label": label, "agg": agg}); continue
            model = next((m for m in registry.MODELS if m["alias"] == alias), None)
            if model is None:
                console.print(f"[red]alias desconocido: {alias}[/]"); continue
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
                console.print(f"[red]fallo en {label}: {e}[/]")
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
    console.print(f"[green]Listo.[/] Informe: {os.path.join(CIRC_DIR, 'report_circuit.md')}")


if __name__ == "__main__":
    main()
