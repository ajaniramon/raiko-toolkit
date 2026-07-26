"""SKILLS tier: ~40 self-contained tasks that measure whether a local model:

  - activate    (12): calls the `skill` tool with the RIGHT skill when a task
                       clearly matches one skill's description.
  - discriminate (8): picks the right one of two confusable skills
                       (log-triage vs incident-report, release-notes vs commit-message).
  - negative    (12): does NOT call `skill` for tasks that match no skill at all
                       (plain arithmetic, geography, translation, ...).
  - follow       (8): when the right skill is obvious (even named in the prompt),
                       checks COMPLIANCE with its loaded instructions: the
                       `[SKILL:<name>]` marker on the first line, plus one
                       correct datum pulled from the inline data.

Every prompt is fully self-contained (data inline: mini-CSV, log lines, a small
diff, a table schema, a short transcript) -- no filesystem tools are needed to
answer any of these tasks.

RECORDER is the shared, module-level list the `skill` tool dispatch (see
make_skill_dispatch) appends to every time the model calls `skill`. The runner
(run_skills.py) clears it before every task and reads it right after run_task
returns, so graders can close over it to check WHICH skill(s) were requested
and in what order, without needing harness.py's grader_ctx machinery (that path
is for tasks that need `check(answer, ctx)`; ours are all `check(answer)`).

Each task also carries two extra fields used only by selftest_skills.py (NOT by
harness.py, which ignores unknown task keys): `oracle_calls`/`oracle_answer`
(what a perfect model would produce) and `bad_calls`/`bad_answer` (one concrete
way a model can get this task wrong). This lets selftest_skills.py validate every
grader deterministically, with no model in the loop.
"""

import re

from tasks import has_number, contains, contains_any  # noqa: F401 (re-exported for callers)

# Populated by the `skill` tool dispatch at runtime (see make_skill_dispatch).
# The runner clears this before each task with RECORDER.clear().
RECORDER = []

SKILL_NAMES = [
    "csv-analysis", "log-triage", "incident-report", "release-notes",
    "commit-message", "regex-builder", "sql-helper", "meeting-minutes",
]


def make_skill_dispatch(skills):
    """Build the {"skill": handler} dispatch table for run_task(dispatch=...).

    tools.call_tool() invokes `table[name](**args)` with the JSON-decoded
    arguments as kwargs, so the handler must accept `name` as a keyword arg
    (matching the `skill` tool schema's single "name" parameter)."""
    from engine.skills import load_skill

    def handler(name):
        RECORDER.append(str(name))
        return load_skill(skills, str(name))

    return {"skill": handler}


# --------------------------------------------------------------------------- #
# Grader building blocks
# --------------------------------------------------------------------------- #

def _marker_ok(answer: str, expected: str) -> bool:
    """True if the final answer STARTS with `[SKILL:<expected>]`, tolerant of
    leading whitespace (per the SKILL.md compliance instruction)."""
    return (answer or "").lstrip().startswith(f"[SKILL:{expected}]")


def _activated_correctly(expected: str) -> bool:
    """True if the FIRST skill the model requested (if any) was the right one."""
    return bool(RECORDER) and RECORDER[0] == expected


def _skill_check(expected: str):
    """Grader for activate/discriminate: right skill requested first, AND the
    final answer's first line is the matching marker."""
    def check(answer):
        return _activated_correctly(expected) and _marker_ok(answer, expected)
    return check


def _follow_check(expected: str, data_ok):
    """Grader for follow: marker compliance AND a correct datum in the answer."""
    def check(answer):
        return _marker_ok(answer, expected) and bool(data_ok(answer))
    return check


def _negative_check(data_ok):
    """Grader for negative: no skill call at all AND the plain answer is correct."""
    def check(answer):
        return len(RECORDER) == 0 and bool(data_ok(answer))
    return check


def _in_order(text: str, *terms: str) -> bool:
    """True if every term appears in `text` (word-bounded, case-insensitive) and
    they appear in the given order. Used for the alphabetical/numeric sort tasks."""
    low = (text or "").lower()
    positions = []
    for t in terms:
        m = re.search(r"\b" + re.escape(t.lower()) + r"\b", low)
        if not m:
            return False
        positions.append(m.start())
    return all(positions[i] < positions[i + 1] for i in range(len(positions) - 1))


def build_tasks_skills() -> list:
    tasks = []

    def add(id, category, prompt, expect, check, negative=False, expected_skill=None,
             oracle_calls=None, oracle_answer="", bad_calls=None, bad_answer=""):
        tasks.append({
            "id": id, "category": category, "prompt": prompt,
            "expect_tools": expect, "check": check, "negative": negative,
            # extra metadata, consumed by run_skills.py (skills-specific report
            # metrics) and by selftest_skills.py (oracle validation); harness.py
            # ignores unknown task keys.
            "expected_skill": expected_skill,
            "oracle_calls": oracle_calls or [], "oracle_answer": oracle_answer,
            "bad_calls": bad_calls if bad_calls is not None else (oracle_calls or []),
            "bad_answer": bad_answer,
        })

    # ------------------------------------------------------------------ #
    # activate / discriminate helper: same grader shape, only the prompt
    # and the (expected, distractor) skill pair differ.
    # ------------------------------------------------------------------ #
    def add_pick(id, category, prompt, expected_skill, distractor_skill):
        oracle_answer = f"[SKILL:{expected_skill}]\nDone, following the steps above."
        bad_answer = f"[SKILL:{distractor_skill}]\nDone, following the steps above."
        add(id, category, prompt, ["skill"], _skill_check(expected_skill),
            expected_skill=expected_skill,
            oracle_calls=[expected_skill], oracle_answer=oracle_answer,
            bad_calls=[distractor_skill], bad_answer=bad_answer)

    # ================================================================== #
    # ACTIVATE (12) -- at least one task per skill, all self-contained.
    # ================================================================== #
    add_pick("act_csv_mean", "activate",
        "Here is a small CSV:\n\nname,score\nAda,88\nBob,72\nCleo,95\nDan,61\n\n"
        "What is the mean of the 'score' column?",
        "csv-analysis", "sql-helper")

    add_pick("act_csv_filter", "activate",
        "product,qty,price\nwidget,3,10\ngadget,1,25\ngizmo,5,4\n\n"
        "Which product has the highest qty*price total, and what is that total?",
        "csv-analysis", "sql-helper")

    add_pick("act_log_component", "activate",
        "Log lines:\n"
        "10:00:01 INFO api: request received\n"
        "10:00:02 ERROR payments: timeout connecting to db\n"
        "10:00:03 ERROR payments: retry failed\n"
        "10:00:04 WARN api: slow response\n\n"
        "Which component is failing here?",
        "log-triage", "incident-report")

    add_pick("act_log_counts", "activate",
        "Log excerpt:\n"
        "11:00:00 INFO worker: job started\n"
        "11:00:01 ERROR worker: out of memory\n"
        "11:00:02 ERROR worker: out of memory\n"
        "11:00:03 ERROR worker: out of memory\n"
        "11:00:04 INFO worker: job finished\n\n"
        "How many ERROR-severity lines are there, and which component do they come from?",
        "log-triage", "incident-report")

    add_pick("act_incident_1", "activate",
        "Last night the checkout service went down for 12 minutes starting at 02:03 "
        "due to a bad database migration; the on-call engineer rolled it back at "
        "02:15. About 1,200 orders failed during that window. Please write this up "
        "as a formal incident report.",
        "incident-report", "log-triage")

    add_pick("act_incident_2", "activate",
        "Here's what happened during today's outage: the notification service "
        "stopped sending emails at 08:20 because an expired SMTP credential was "
        "rejected; the team rotated the credential at 08:50 and service resumed "
        "at 08:52. Write the incident report.",
        "incident-report", "log-triage")

    add_pick("act_release_1", "activate",
        "Recent merged changes:\n"
        "- Fixed crash on startup when the config file is missing\n"
        "- Added CSV export to the reports page\n"
        "- Reduced page load time by 25%\n\n"
        "Turn this into user-facing release notes.",
        "release-notes", "commit-message")

    add_pick("act_release_2", "activate",
        "Changelog:\n"
        "- fix: duplicate entries in the activity feed\n"
        "- feat: keyboard shortcuts for search\n"
        "- chore: bumped internal logging library\n\n"
        "Write the public release notes for this update.",
        "release-notes", "commit-message")

    add_pick("act_commit", "activate",
        "Diff:\n"
        "--- a/src/payments.py\n+++ b/src/payments.py\n@@\n"
        "-def charge(amount):\n-    return gateway.send(amount)\n"
        "+def charge(amount):\n+    if amount <= 0:\n"
        "+        raise ValueError('amount must be positive')\n"
        "+    return gateway.send(amount)\n\n"
        "Write a commit message for this diff.",
        "commit-message", "release-notes")

    add_pick("act_regex", "activate",
        "I need a regular expression that matches a US ZIP code: either 5 digits, "
        "or 5 digits followed by a dash and 4 more digits (e.g. 12345 or 12345-6789).",
        "regex-builder", "sql-helper")

    add_pick("act_sql", "activate",
        "Table schema: orders(id, customer_id, amount, status, created_at). Write "
        "a query that returns the total amount of orders with status 'paid', "
        "grouped by customer_id.",
        "sql-helper", "csv-analysis")

    add_pick("act_meeting", "activate",
        "Transcript:\n"
        "Alice: We agreed to push the release to next Tuesday.\n"
        "Bob: I'll update the deployment doc by Friday.\n"
        "Carol: Let's also add a rollback plan before we ship.\n\n"
        "Summarize this into meeting minutes.",
        "meeting-minutes", "incident-report")

    # ================================================================== #
    # DISCRIMINATE (8) -- 4 per confusable pair.
    # ================================================================== #
    add_pick("disc_lt_component", "discriminate",
        "Here are some log lines:\n"
        "10:15:01 INFO api: request ok\n"
        "10:15:02 ERROR billing: charge failed - card declined\n"
        "10:15:03 ERROR billing: retry failed\n"
        "10:15:04 INFO api: request ok\n\n"
        "Which component is failing, and how many ERROR lines are there?",
        "log-triage", "incident-report")

    add_pick("disc_lt_counts", "discriminate",
        "Log excerpt:\n"
        "09:00:00 WARN cache: high memory usage\n"
        "09:00:01 ERROR auth: token validation failed\n"
        "09:00:02 ERROR auth: token validation failed\n"
        "09:00:03 WARN cache: high memory usage\n\n"
        "Count how many lines are WARN vs ERROR, and name the component behind "
        "the errors.",
        "log-triage", "incident-report")

    add_pick("disc_ir_narrative_1", "discriminate",
        "Here is what happened during yesterday's outage: at 03:14 the payments "
        "API started returning 503s. Engineers rolled back the 2.3.0 deploy at "
        "03:40 and errors stopped by 03:45. About 900 customers were affected. "
        "Write the formal incident report.",
        "incident-report", "log-triage")

    add_pick("disc_ir_narrative_2", "discriminate",
        "Summary of last night's incident: the search service became unresponsive "
        "at 22:10 after a config push enabled a broken cache setting; on-call "
        "reverted the config at 22:35, restoring service by 22:40. Please produce "
        "a structured incident write-up for the postmortem doc.",
        "incident-report", "log-triage")

    add_pick("disc_rn_changelog_1", "discriminate",
        "Here are this sprint's merged changes:\n"
        "- Fixed a bug where exports timed out on large files (PR #482)\n"
        "- Added support for dark mode in settings (PR #490)\n"
        "- Improved startup time by 30% (PR #501)\n\n"
        "Turn this into release notes for our end users.",
        "release-notes", "commit-message")

    add_pick("disc_rn_changelog_2", "discriminate",
        "Changelog for v3.2:\n"
        "- fix: null pointer when profile picture missing\n"
        "- feat: CSV export for invoices\n"
        "- perf: reduced memory usage in the sync job\n\n"
        "Write user-facing release notes from this.",
        "release-notes", "commit-message")

    add_pick("disc_cm_diff_1", "discriminate",
        "Here is a diff:\n"
        "--- a/src/auth.py\n+++ b/src/auth.py\n@@\n"
        "-def login(user, pwd):\n-    return check(user, pwd)\n"
        "+def login(user, pwd):\n+    if not pwd:\n"
        "+        raise ValueError('password required')\n"
        "+    return check(user, pwd)\n\n"
        "Write a commit message for this change.",
        "commit-message", "release-notes")

    add_pick("disc_cm_diff_2", "discriminate",
        "Diff:\n"
        "--- a/src/utils.py\n+++ b/src/utils.py\n@@\n"
        "-def slugify(s):\n-    return s.lower()\n"
        "+def slugify(s):\n+    return s.strip().lower().replace(' ', '-')\n\n"
        "Give me a commit message for this diff.",
        "commit-message", "release-notes")

    # ================================================================== #
    # NEGATIVE (12) -- no skill matches; deterministic, model-answerable
    # without any tool. expect_tools=[] because the ideal response calls NO
    # tool at all: with zero tool calls, harness.py's `tool_ok = any(n in
    # expect_tools for n in tool_calls_made)` is False no matter what
    # expect_tools contains (any() over an empty tool_calls_made is always
    # False). This is a real, unavoidable blind spot of the generic 0.15
    # tool_ok term for this category -- NOT something we can fix without
    # touching harness.py. We deliberately do not "game" expect_tools to
    # compensate; instead run_skills.py's false_positive_rate metric (built
    # straight from `skills_called`) is the metric that actually measures
    # false activation here, and report_skills.md documents the tool_ok
    # ceiling explicitly so it isn't misread as a correctness failure.
    # ------------------------------------------------------------------ #
    def add_neg(id, prompt, data_ok, oracle_answer):
        add(id, "negative", prompt, [], _negative_check(data_ok), negative=True,
            expected_skill=None,
            oracle_calls=[], oracle_answer=oracle_answer,
            bad_calls=["csv-analysis"], bad_answer=oracle_answer)

    add_neg("neg_math_add", "What is 47 + 58?",
            lambda a: has_number(a, 105), "105")
    add_neg("neg_math_mul", "What is 12 * 8?",
            lambda a: has_number(a, 96), "96")
    add_neg("neg_math_div", "What is 144 divided by 12?",
            lambda a: has_number(a, 12), "12")
    add_neg("neg_capital_fr", "What is the capital of France?",
            lambda a: contains(a, "paris"), "Paris")
    add_neg("neg_capital_jp", "What is the capital of Japan?",
            lambda a: contains(a, "tokyo"), "Tokyo")
    add_neg("neg_translate_es", "Translate the phrase 'good morning' into Spanish.",
            lambda a: contains_any(a, "buenos días", "buenos dias"), "Buenos días")
    add_neg("neg_translate_fr", "Translate the word 'thank you' into French.",
            lambda a: contains(a, "merci"), "Merci")
    add_neg("neg_sort_words", "Sort these words alphabetically: banana, apple, cherry",
            lambda a: _in_order(a, "apple", "banana", "cherry"), "apple, banana, cherry")
    add_neg("neg_sort_numbers", "Sort these numbers in ascending order: 9, 3, 7, 1",
            lambda a: _in_order(a, "1", "3", "7", "9"), "1, 3, 7, 9")
    add_neg("neg_concept_loop",
            "Explain in one sentence what a for loop is used for in programming.",
            lambda a: contains_any(a, "iterat", "repeat"),
            "A for loop repeats a block of code a set number of times or over each "
            "item in a collection.")
    add_neg("neg_concept_recursion",
            "Explain in one sentence what recursion means in programming.",
            lambda a: contains_any(a, "itself", "recursive"),
            "Recursion is when a function calls itself to solve smaller instances "
            "of the same problem.")
    add_neg("neg_vowel_count", "How many vowels are in the word 'orange'?",
            lambda a: has_number(a, 3), "3")

    # ================================================================== #
    # FOLLOW (8) -- the skill is obvious/named; what matters is COMPLIANCE
    # (marker on the first line) plus one verifiable datum.
    # ------------------------------------------------------------------ #
    def add_follow(id, prompt, expected_skill, data_ok, good_data, bad_data):
        oracle_answer = f"[SKILL:{expected_skill}]\n{good_data}"
        bad_answer = f"Sure -- {bad_data}"  # correct datum, but no marker
        add(id, "follow", prompt, ["skill"], _follow_check(expected_skill, data_ok),
            expected_skill=expected_skill,
            oracle_calls=[expected_skill], oracle_answer=oracle_answer,
            bad_calls=[expected_skill], bad_answer=bad_answer)

    add_follow("follow_csv_mean",
        "Use the csv-analysis skill to compute the mean of the 'score' column in "
        "this CSV:\n\nname,score\nAda,88\nBob,72\nCleo,95\nDan,61\n",
        "csv-analysis", lambda a: has_number(a, 79),
        "The mean of the score column is 79.", "the mean of the score column is 79.")

    add_follow("follow_log_errors",
        "Use the log-triage skill on these log lines and tell me exactly how many "
        "ERROR lines there are:\n"
        "10:00:01 INFO app: started\n"
        "10:00:02 ERROR db: connection refused\n"
        "10:00:03 ERROR db: connection refused\n"
        "10:00:04 WARN app: high latency\n"
        "10:00:05 ERROR api: 500 response\n",
        "log-triage", lambda a: has_number(a, 3),
        "There are 3 ERROR lines.", "there are 3 ERROR lines.")

    add_follow("follow_incident_cause",
        "Use the incident-report skill to summarize this outage, and tell me the "
        "root cause: at 14:02 the primary database failed over to its replica, "
        "causing a 6-minute checkout outage. The failover was delayed because an "
        "expired TLS certificate on the primary blocked the automatic script "
        "until an engineer restarted it manually.",
        "incident-report", lambda a: contains_any(a, "certificate", "tls"),
        "Root cause: an expired TLS certificate blocked the automatic failover script.",
        "the root cause was an expired TLS certificate blocking the failover script.")

    add_follow("follow_release_count",
        "Use the release-notes skill to turn this changelog into release notes, "
        "and state how many changes are listed in total:\n"
        "- Fixed crash on startup\n- Added dark mode\n- Improved export speed\n",
        "release-notes", lambda a: has_number(a, 3),
        "3 changes in this release: ...", "there are 3 changes listed in total.")

    add_follow("follow_commit_scope",
        "Use the commit-message skill to write a commit message for this diff, "
        "which only touches src/auth.py -- make sure your message's scope "
        "reflects that file:\n"
        "--- a/src/auth.py\n+++ b/src/auth.py\n@@\n"
        "-def login(user, pwd):\n-    return check(user, pwd)\n"
        "+def login(user, pwd):\n+    if not pwd:\n"
        "+        raise ValueError('password required')\n"
        "+    return check(user, pwd)\n",
        "commit-message", lambda a: contains(a, "auth"),
        "fix(auth): require password before checking credentials",
        "fix(auth): require password before checking credentials")

    add_follow("follow_regex_zip",
        "Use the regex-builder skill to craft a regex that matches a 5-digit ZIP "
        "code (exactly 5 digits, nothing more).",
        "regex-builder", lambda a: contains_any(a, r"\d{5}", r"[0-9]{5}"),
        r"The regex is ^\d{5}$.", r"the regex is ^\d{5}$.")

    add_follow("follow_sql_engineering",
        "Use the sql-helper skill. Table schema: employees(id, name, department, "
        "salary). Write a query that selects all employees in the 'engineering' "
        "department.",
        "sql-helper",
        lambda a: contains(a, "select") and contains(a, "engineering"),
        "SELECT * FROM employees WHERE department = 'engineering';",
        "select * from employees where department = 'engineering';")

    add_follow("follow_meeting_decision",
        "Use the meeting-minutes skill on this transcript and tell me what day "
        "the team decided to launch:\n"
        "Alice: Let's lock the launch for this Friday.\n"
        "Bob: I'll prep the deck by Thursday.\n"
        "Carol: Sounds good, Friday it is.\n",
        "meeting-minutes", lambda a: contains(a, "friday"),
        "Decision: launch on Friday.", "the decision was to launch on friday.")

    return tasks
