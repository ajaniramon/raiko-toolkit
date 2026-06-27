"""Regenera results/report.md a partir de los JSON ya guardados en disco.

Útil para ensamblar el informe final aunque el run completo se cortara a medias:
lee todos los results/<run>.json válidos (n_tasks == TARGET) y reconstruye el
leaderboard + tablas. Ignora restos de smokes (n_tasks pequeño).
"""

import glob
import json
import os

from run_bench import write_report, print_leaderboard, RESULTS_DIR

TARGET_TASKS = 104
KNOWN_ALIASES = ("qwythos", "hauhau", "qwen35-9b", "gemma4-12b")


def main():
    runs = {}
    for path in glob.glob(os.path.join(RESULTS_DIR, "*.json")):
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        label = data.get("run") or os.path.basename(path)[:-5]
        agg = data.get("aggregate") or {}
        if agg.get("n_tasks") != TARGET_TASKS:
            continue  # descarta smokes / runs viejos
        if not any(label.startswith(a) for a in KNOWN_ALIASES):
            continue
        runs[label] = {"label": label, "agg": agg}

    all_runs = list(runs.values())
    if not all_runs:
        print("No hay runs válidos de", TARGET_TASKS, "tareas todavía.")
        return
    write_report(all_runs, None, TARGET_TASKS)
    print_leaderboard(all_runs)
    print(f"\nInforme regenerado desde {len(all_runs)} runs: {os.path.join(RESULTS_DIR, 'report.md')}")


if __name__ == "__main__":
    main()
