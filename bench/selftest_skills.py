"""Selftest for the SKILLS tier -- validates fixtures and all 40 graders
WITHOUT a model in the loop (no llama-server, no HTTP requests).

For every task it simulates two runs by directly manipulating tasks_skills.
RECORDER (exactly what the `skill` tool dispatch would have appended) and
feeding a synthetic final answer to the task's check(answer):

  - "perfect model": RECORDER = task['oracle_calls'], answer = task['oracle_answer']
    -> check(...) MUST be True.
  - "clumsy model":  RECORDER = task['bad_calls'],    answer = task['bad_answer']
    -> check(...) MUST be False.

Each task's bad_* fields exercise one concrete failure mode (wrong skill
requested, a false-positive `skill` call on a negative task, or a missing
`[SKILL:...]` marker on an otherwise-correct follow-up answer).

Exit code is 0 iff every task passes BOTH the oracle-positive and the
oracle-negative check, and every fixture loads correctly.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root: tools/engine

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)  # bench/: tasks, fixtures_skills, tasks_skills

import tempfile  # noqa: E402

import fixtures_skills  # noqa: E402
import tasks_skills  # noqa: E402
from engine.skills import load_skill  # noqa: E402

SCRATCH = os.path.join(tempfile.gettempdir(), "raiko-bench-skills-selftest")


def check_fixtures() -> list:
    """Returns a list of failure messages (empty if everything is fine)."""
    fails = []
    skills = fixtures_skills.build_skills(SCRATCH)

    expected_names = set(fixtures_skills.SKILLS_SRC.keys())
    got_names = {s.name for s in skills}
    if len(skills) != 8:
        fails.append(f"expected 8 skills, got {len(skills)}")
    if got_names != expected_names:
        fails.append(f"skill name mismatch: expected {expected_names}, got {got_names}")

    for name in expected_names:
        text = load_skill(skills, name)
        marker = f"[SKILL:{name}]"
        if marker not in text:
            fails.append(f"skill '{name}': load_skill() body missing literal marker '{marker}'")
        if "MANDATORY" not in text:
            fails.append(f"skill '{name}': load_skill() body missing the MANDATORY compliance line")

    unknown = load_skill(skills, "does-not-exist")
    if "ERROR" not in unknown:
        fails.append("load_skill() with an unknown name did not return an ERROR")

    return fails, skills


def check_tasks(skills) -> tuple:
    """Returns (failures, counts_by_category)."""
    fails = []
    tasks = tasks_skills.build_tasks_skills()
    counts = {}
    seen_ids = set()

    for task in tasks:
        counts[task["category"]] = counts.get(task["category"], 0) + 1
        if task["id"] in seen_ids:
            fails.append(f"duplicate task id: {task['id']}")
        seen_ids.add(task["id"])

        check = task["check"]

        # -- oracle positive: a perfect model must pass --
        tasks_skills.RECORDER.clear()
        tasks_skills.RECORDER.extend(task["oracle_calls"])
        try:
            ok = bool(check(task["oracle_answer"]))
        except Exception as e:
            fails.append(f"[{task['id']}] oracle check raised {type(e).__name__}: {e}")
            ok = False
        if not ok:
            fails.append(f"[{task['id']}] oracle-positive FAILED "
                         f"(calls={task['oracle_calls']!r} answer={task['oracle_answer']!r})")

        # -- clumsy model: a wrong-skill / no-marker / false-positive attempt must fail --
        tasks_skills.RECORDER.clear()
        tasks_skills.RECORDER.extend(task["bad_calls"])
        try:
            bad_ok = bool(check(task["bad_answer"]))
        except Exception as e:
            fails.append(f"[{task['id']}] bad-case check raised {type(e).__name__}: {e}")
            bad_ok = True  # treat as failure of the invariant (should have been False)
        if bad_ok:
            fails.append(f"[{task['id']}] oracle-negative FAILED -- clumsy answer scored True "
                         f"(calls={task['bad_calls']!r} answer={task['bad_answer']!r})")

        tasks_skills.RECORDER.clear()

    return fails, counts


def main():
    print("== SKILLS selftest (no model, no server) ==\n")

    fixture_fails, skills = check_fixtures()
    print(f"Fixtures: {len(fixture_fails)} problem(s)")
    for m in fixture_fails:
        print(f"  FAIL: {m}")
    if not fixture_fails:
        print(f"  OK: 8 skills materialized under {os.path.join(SCRATCH, 'skillsfix')}")

    task_fails, counts = check_tasks(skills)

    print("\nTask counts by category:")
    for cat in ("activate", "discriminate", "negative", "follow"):
        print(f"  {cat:<14} {counts.get(cat, 0)}")
    print(f"  {'TOTAL':<14} {sum(counts.values())}")

    print(f"\nGrader checks: {len(task_fails)} problem(s)")
    for m in task_fails:
        print(f"  FAIL: {m}")

    all_fails = fixture_fails + task_fails
    print()
    if all_fails:
        print(f"SELFTEST FAILED: {len(all_fails)} problem(s) total.")
        sys.exit(1)
    else:
        print("SELFTEST OK: all fixtures valid, all 40 graders pass their oracle "
             "positive AND negative case.")
        sys.exit(0)


if __name__ == "__main__":
    main()
