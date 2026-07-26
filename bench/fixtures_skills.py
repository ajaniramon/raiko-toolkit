"""Fixtures for the SKILLS benchmark tier: 8 synthetic Agent Skills, materialized
on disk as SKILL.md files and exposed as engine.skills.Skill objects.

Each skill's body starts with a verifiable compliance instruction (the model must
put a `[SKILL:<name>]` marker as the first line of its final answer), so grading
does not need to guess whether the loaded instructions were actually followed.

Two deliberately confusable pairs are included, to test discrimination between
similar-sounding skills:
  - log-triage vs incident-report (both are about "something went wrong")
  - release-notes vs commit-message (both are about "summarize a code change")
"""

import os
import shutil
import sys
from pathlib import Path

import yaml

# allow importing engine.* from the repo root (same trick as run_hard.py)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.skills import Skill  # noqa: E402


def _steps(*lines: str) -> str:
    return "\n".join(f"{i}. {line}" for i, line in enumerate(lines, 1))


def _body(name: str, *steps: str) -> str:
    header = (
        f"MANDATORY: the FIRST line of your final answer must be exactly "
        f"`[SKILL:{name}]` (e.g. `[SKILL:csv-analysis]`). Then follow the steps below."
    )
    return header + "\n\n" + _steps(*steps)


SKILLS_SRC = {
    "csv-analysis": {
        "name": "csv-analysis",
        "description": (
            "Analyze CSV data: column stats, means, counts, filtering. Use for "
            "questions about CSV-formatted data."
        ),
        "body": _body(
            "csv-analysis",
            "Identify the column(s) relevant to the question.",
            "Parse the CSV data carefully, row by row.",
            "Compute the requested statistic exactly (sum/mean/count/filter).",
            "State the result as a precise number that matches the data.",
            "Keep the summary short: the answer, then a one-line note of method.",
        ),
    },
    "log-triage": {
        "name": "log-triage",
        "description": (
            "Triage application logs: find errors, count severities, identify the "
            "failing component."
        ),
        "body": _body(
            "log-triage",
            "Scan the log lines for severity markers (ERROR, WARN, FATAL).",
            "Count how many lines are at each severity.",
            "Identify which component/service is named in the ERROR lines.",
            "State the failing component clearly.",
            "Note whether the issue looks isolated or recurring.",
        ),
    },
    "incident-report": {
        "name": "incident-report",
        "description": (
            "Write a structured incident report (impact, timeline, root cause, "
            "actions) from an outage description."
        ),
        "body": _body(
            "incident-report",
            "Extract the impact (what broke, who was affected).",
            "Reconstruct a timeline of events in chronological order.",
            "State the root cause in one sentence.",
            "List the remediation/follow-up actions.",
            "Format as sections: Impact / Timeline / Root Cause / Actions.",
        ),
    },
    "release-notes": {
        "name": "release-notes",
        "description": (
            "Turn a changelog or list of merged changes into user-facing release notes."
        ),
        "body": _body(
            "release-notes",
            "Group changes into Features / Fixes / Improvements.",
            "Rewrite each entry in plain, user-facing language (no PR numbers or jargon).",
            "Order items by importance within each group.",
            "Keep each bullet to one line.",
            "Add a short one-line summary at the top.",
        ),
    },
    "commit-message": {
        "name": "commit-message",
        "description": (
            "Write a conventional commit message (type(scope): subject) from a code diff."
        ),
        "body": _body(
            "commit-message",
            "Determine the change type (feat, fix, refactor, docs, test, chore).",
            "Identify the scope (module/file affected).",
            "Write a concise imperative subject line under ~72 chars.",
            "Format exactly as type(scope): subject.",
            "Add a short body line only if the diff needs more context.",
        ),
    },
    "regex-builder": {
        "name": "regex-builder",
        "description": (
            "Craft and explain a regular expression matching a described text pattern."
        ),
        "body": _body(
            "regex-builder",
            "Restate the pattern requirements precisely.",
            "Build the regex incrementally, using anchors/quantifiers as needed.",
            "Verify the regex mentally against the given example(s).",
            "Provide the final regex.",
            "Briefly explain each part of the pattern.",
        ),
    },
    "sql-helper": {
        "name": "sql-helper",
        "description": (
            "Write an SQL query from a natural-language request and a table schema."
        ),
        "body": _body(
            "sql-helper",
            "Map the requested fields/filters to the given schema's columns.",
            "Choose the right clauses (SELECT/WHERE/GROUP BY/ORDER BY/JOIN).",
            "Write a single valid SQL statement.",
            "Double check column and table names match the schema exactly.",
            "Return the SQL query as the final answer.",
        ),
    },
    "meeting-minutes": {
        "name": "meeting-minutes",
        "description": (
            "Turn a raw meeting transcript into concise minutes with decisions and "
            "action items."
        ),
        "body": _body(
            "meeting-minutes",
            "Identify decisions made during the meeting.",
            "Identify action items and their owners (if stated).",
            "Summarize discussion points briefly.",
            "Format as sections: Decisions / Action Items / Summary.",
            "Keep it concise -- no verbatim transcript quotes.",
        ),
    },
}


def build_skills(base_dir: str) -> list:
    """Materialize every skill in SKILLS_SRC under <base_dir>/skillsfix/<name>/SKILL.md
    (wiping that dir first) and return the corresponding list of Skill objects,
    built directly -- NOT via discover_skills, which would also pick up any real
    skills installed on this machine (repo/HOME) and contaminate the bench."""
    skills_root = Path(base_dir) / "skillsfix"
    if skills_root.exists():
        shutil.rmtree(skills_root)
    skills_root.mkdir(parents=True, exist_ok=True)

    skills = []
    for name, spec in SKILLS_SRC.items():
        skill_dir = skills_root / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_md = skill_dir / "SKILL.md"

        frontmatter = yaml.safe_dump(
            {"name": spec["name"], "description": spec["description"]},
            default_flow_style=False, allow_unicode=True, sort_keys=False,
        )
        content = f"---\n{frontmatter}---\n\n{spec['body']}\n"
        skill_md.write_text(content, encoding="utf-8")

        skills.append(Skill(
            name=spec["name"],
            description=spec["description"],
            path=str(skill_md.resolve()),
            base_dir=str(skill_dir.resolve()),
            source="extra",
        ))
    return skills
