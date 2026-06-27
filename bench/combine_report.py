"""Regenerates results/report.md from the JSON files already saved on disk.

Useful for assembling the final report even if the full run was cut off midway:
reads all valid results/<run>.json (n_tasks == TARGET) and rebuilds the
leaderboard + tables. Ignores leftover smokes (small n_tasks).
"""

import glob
import json
import os

from run_bench import write_report, print_leaderboard, RESULTS_DIR

TARGET_TASKS = 104
KNOWN_ALIASES = ("qwythos", "hauhau", "qwen35-9b", "gemma4-12b", "gemma4-v2", "ornith-9b")


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
            continue  # discards smokes / old runs
        if not any(label.startswith(a) for a in KNOWN_ALIASES):
            continue
        runs[label] = {"label": label, "agg": agg}

    all_runs = list(runs.values())
    if not all_runs:
        print("No valid runs of", TARGET_TASKS, "tasks yet.")
        return
    write_report(all_runs, None, TARGET_TASKS)
    print_leaderboard(all_runs)
    print(f"\nReport regenerated from {len(all_runs)} runs: {os.path.join(RESULTS_DIR, 'report.md')}")


if __name__ == "__main__":
    main()
