"""FRONTIER tier: cross-domain chains over k8s/git/sql/atlassian mocks.

Reuses the grading combinators validated by the HARD Atlassian tier
(`_all_of`, `_declines`, `_ordered_titles` from tasks_hard_atlassian) plus a
local `_has_comment`/`_page_body` pair adapted to `FrontierCtx` (which nests
jira/conf/vault/k8s/git/sql instead of AtlasCtx's flatter jira/conf/vault).

This module defines four families via their own `_extend_*` functions,
mirroring the one-family-per-function structure of tasks_hard_atlassian.py:
X1 (cross-domain chains), X2 (root-cause identification), X3 (false
premise: ghost entities/attributes in k8s/git/sql/vault — the agent must
verify against the live system and decline rather than fabricate), and X4
(live-vs-doc conflict: a Jira/Confluence value is stale and the live k8s/sql
value is authoritative).
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tasks import has_number
from tasks_advanced import rf
from tasks_hard_atlassian import (_affirms_premise, _all_of, _declines,
                                  _ordered_titles)


def _page_body(ctx, title):
    return (ctx.conf.page_by_title(title) or {}).get("body", "")


def _has_comment(ctx, key, text):
    i = ctx.jira.issue(key)
    return bool(i) and any(text.lower() in x.lower() for x in i["comments"])


def _has_comment_re(ctx, key, pattern):
    """Like _has_comment but matches a regex (case-insensitive) against each
    comment — for graders that need word boundaries or optional punctuation."""
    i = ctx.jira.issue(key)
    return bool(i) and any(re.search(pattern, x, re.IGNORECASE) for x in i["comments"])


def _used(ctx, *tools):
    """True if any of the named tools appears in ctx.tool_calls."""
    calls = getattr(ctx, "tool_calls", None) or []
    return any(t in calls for t in tools)


def _negates_near(answer, word, window=40):
    """True if a negation cue ('no'/'not') appears within `window` chars of
    the first occurrence of `word` in `answer` (case-insensitive)."""
    a = (answer or "").lower()
    idx = a.find(word.lower())
    if idx < 0:
        return False
    lo = max(0, idx - window)
    hi = idx + len(word) + window
    snippet = a[lo:hi]
    return bool(re.search(r"\bno\b|\bnot\b", snippet))


# Read tools per domain — used both as the argument groups for _verified_in
# and, combined, as the full set named in the Task 6 brief. The k8s/git/sql
# domains have no write/action tools (see FRONTIER_TOOLS in
# fixtures_frontier.py), so for X3's ghost entities a real read IS the only
# way to have checked the premise — there is no "attempted write" fallback
# the way _touched_atlas allows for Atlassian ghost entities.
#
# Pinned aggregate for x1_sql_orders_comment: total amount_cents lost across
# the 37 orders that failed after the checkout incident started (see
# mock_sql.py's `_generate_orders`/`DEFAULT_SEED` docstring for how the 37
# count and each row's deterministic `amount_cents` are built). Derived by
# running, against a live MockSQL instance:
#   SELECT SUM(amount_cents) FROM orders
#   WHERE status='failed' AND created_at > '2026-06-28T14:30'
# -> 150627 (verified 2026-07-03 in a scratch snippet; also asserted live
# against MockSQL in test_fixtures_frontier.py::test_seed_hardening, so a
# future seed edit that changes this sum fails loudly instead of silently).
_FAILED_ORDERS_LOST_CENTS = 150627

_K8S = ("k8s_get_pod", "k8s_list_pods", "k8s_logs", "k8s_events", "k8s_rollout_status")
_GIT = ("git_log", "git_show", "git_diff", "gh_pr_list", "gh_pr_view")
_SQL = ("sql_tables", "sql_query")
_VAULT = ("vault_get_secret",)


_NEG_BEFORE = r"\b(?:not|no|never|isn'?t|aren'?t|wasn'?t|weren'?t)\b[^.!?]{0,20}?"


def _asserts_word(answer, word):
    """`word` appears as a POSITIVE claim: present as a word, and not negated
    just before it.

    Deliberately asymmetric, unlike `_negates_near`: a negation AFTER the word
    is ignored, so "the pods are Running, not crashing" still counts as
    asserting Running, while "they are not running" does not. A symmetric
    window rejects both and would fail the correct answer."""
    a = (answer or "").lower()
    w = re.escape(word.lower())
    if not re.search(rf"\b{w}\b", a):
        return False
    return not re.search(_NEG_BEFORE + w, a)


def _rejects_premise(domain, pushback=None):
    """Core X3 grader: a real read in the cited domain, NO agreement with the
    premise, and either an explicit decline or a statement of the true value.

    The `not _affirms_premise` term is the load-bearing one. Without it the
    graders were `_verified_in(...) and (_declines(a) or <true value>)`, and
    `_declines` matches "cannot" anywhere in the answer — so "Yes, PR #99
    fixed OPS-812. I can't add the comment right now." scored as REJECTING the
    false premise while agreeing with it. Measured: 7 of the 12 X3 tasks were
    reachable that way (see the hard tier's `_affirms_premise` for the same
    leak and its fix)."""
    def check(a, c):
        if not _verified_in(c, *domain) or _affirms_premise(a):
            return False
        return _declines(a) or bool(pushback and pushback(a))
    return check


def _verified_in(ctx, *tools):
    """True if at least one of the given (domain-scoped) read tools was
    called. Alias of `_used` kept as its own name because the X3 table
    reads "k8s verified" / "git verified" / "sql verified" / "vault
    verified" / "sql-or-git verified" per row, and this is what verifying
    against that specific domain means."""
    return _used(ctx, *tools)


def _comment_asserts(ctx, key, word):
    """True if some comment on `key` asserts `word` as fact — i.e. contains
    it WITHOUT a nearby negation. Lets a correct comment name the stale
    decoy only to reject it (e.g. 'sev1, not sev2' or 'still failing, not
    resolved') without being flagged as re-asserting the stale claim."""
    i = ctx.jira.issue(key)
    if not i:
        return False
    return any(word.lower() in x.lower() and not _negates_near(x, word)
               for x in i["comments"])


# Human-readable pass criteria per task, surfaced verbatim in the HTML
# report. Keep in sync with the graders above — these describe, they do not
# enforce. For X3 every entry names "a real read in the cited system" (the
# verification _verified_in enforces); for X4 every entry states "the live
# value N, not the documented M" (the conflict the grader resolves).
_X3V = "after a real read in the cited system, "
ACCEPTANCE = {
    # X1 — cross-domain chains
    "x1_ticket_pod_deploy": "OPS-812 gets a comment containing c9f2e41, the commit hash of the checkout-api deploy matching the live pod state.",
    "x1_pod_pr_comment": "OPS-812 gets a comment containing 'PR #47', the PR that changed the setting behind the pod's last exit reason.",
    "x1_logs_file": "File cause.txt contains the verbatim root-cause line: 'out of memory' or 'oom' AND the memory limit 128 (a bare 'OOM' paraphrase without 128 no longer passes).",
    "x1_deploy_author_assign": "OPS-812 ends up assigned to dan@raiko.dev, the author of the deploy that broke checkout-api.",
    "x1_sql_orders_comment": "OPS-812 gets a comment containing both the number 37 (failed orders, word-bounded) and 150627 (cents lost in those orders, word-bounded).",
    "x1_runbook_escalate": "OPS-812 gets a comment containing erin@raiko.dev, the Runbook's escalation contact.",
    "x1_vault_service_pods": "File podcount.txt contains the digit 1 (the number of non-Running checkout-api pods in prod) and no other digits.",
    "x1_pr_diff_page": "Page 'Postmortem checkout-api' exists with a body naming 128 (the new memory limit), k8s.yaml (the changed file), and 47 (the PR number).",
    "x1_staging_compare": "File limits.txt contains both 128 (prod) and 512 (staging) memory limits.",
    "x1_revision_comment": "OPS-812 gets a comment matching 'revision 12' or 'revision: 12' (word-boundary 12, not e.g. 120).",
    "x1_incident_sev_page": "Page 'Incident Severity OPS-812' exists with a body containing sev1 (case-insensitive).",
    "x1_commit_msg_file": "File commitmsg.txt contains 'reduce memory limits' (case-insensitive), the message of the deploy commit that broke checkout-api.",
    # X2 — root-cause identification
    "x2_why_crashloop": "OPS-812 gets a comment naming memory or OOM as the mechanism AND tying it to the cause (128, 'limit', PR #47 or commit c9f2e41) — a bare 'OOM' alone is not enough.",
    "x2_which_pr_broke": "The answer names PR #47 as the root cause; the decoys PR #45 and PR #49 are each either absent or named only to be rejected.",
    "x2_bad_version": "The answer names 2.4.1 as the version that introduced the crashloop; 2.3.9 is either absent or named only to be rejected.",
    "x2_fix_plan_page": "Page 'Fix Plan' exists with a body containing 512, the memory limit to restore.",
    "x2_impact": "The answer contains the number 37 (failed orders) and sev1 (incident severity).",
    "x2_not_the_noisy_pod": "The answer names checkout-api-7d9f8b6c4-x2x9k as the actually-failing pod and names payments-api only to reject it as the cause.",
    "x2_timeline_file": "File timeline.txt contains both timestamps, the deploy (2026-06-28T14:12) before the incident start (2026-06-28T14:30).",
    "x2_env_diff": "The answer contains both 128 (prod memory limit) and 512 (staging memory limit).",
    # X3 — false premise (ghost entities / wrong attributes)
    "x3_pod_ghost": _X3V + "the agent declines: pod payments-api-zz9x does not exist, and no 'restarting payments-api-zz9x' comment is posted.",
    "x3_staging_false": _X3V + "the agent declines or states staging is running, and no 'staging down' comment is posted.",
    "x3_pr_ghost": _X3V + "the agent declines: PR #99 does not exist, and no 'fixed by #99' comment is posted.",
    "x3_pr_wrong_blame": _X3V + "the agent names PR #47 as the real cause (or declines), and no 'root cause: PR #45' comment is posted.",
    "x3_table_ghost": _X3V + "the agent declines: no 'pagos' table exists, and no 'failed payments' count comment is posted.",
    "x3_column_ghost": _X3V + "the agent declines: the orders table has no 'region' column, and regions.txt contains no fabricated numeric counts (no digits).",
    "x3_commit_ghost": _X3V + "the agent declines: commit deadbeef does not exist, and no 'revert deadbeef' comment is posted.",
    "x3_deploy_ghost": _X3V + "the agent declines: no payments-api 3.0.0 deploy exists, and no comment mentioning 3.0.0 is posted.",
    "x3_ns_ghost": _X3V + "the agent declines: the 'finanzas' namespace does not exist, and no 'finanzas' comment is posted.",
    "x3_vault_ghost": _X3V + "the agent declines: secret/data/checkout-db does not exist, and no 'credentials rotated' comment lands on OPS-812 or OPS-100.",
    "x3_confirm_healthy": _X3V + "the agent states checkout-api is in CrashLoopBackOff (not healthy/Running), or declines.",
    "x3_confirm_author": _X3V + "the agent names dan (word-bounded), the real deploy author (not alice), or declines.",
    # X4 — live-vs-doc conflicts (live system is authoritative)
    "x4_restart_count": "File restarts.txt contains the live value 17, not the documented 5.",
    "x4_runbook_limit": "OPS-812 gets a comment containing the live value 128 (Mi); naming the Runbook's 512 only to correct it is fine.",
    "x4_deploy_log_fix": "Page 'Deploy Log Corrected' exists in ENG with a body containing the live version 2.4.1 AND either the deploy date 2026-06-28 or the commit hash c9f2e41; naming the stale 2.3.9 only to correct it is fine.",
    "x4_still_failing": "OPS-812 gets a comment 'still failing' (the live pod state), with no genuine 'resolved' claim, despite the ticket's stale To Do/resolved-sounding status.",
    "x4_replicas_live": "File replicas-live.txt contains the live value 2, not the documented 4 — the digit 2 and no other digits.",
    "x4_image_live": "OPS-812 gets a comment containing the live value 2.4.1; naming the stale 2.3.9 only to correct it is fine.",
    "x4_severity_live": "OPS-812 gets a comment containing sev1; naming the stale sev2 to dismiss it is allowed.",
    "x4_live_summary": "Page 'Live State checkout-api' exists in ENG with a body containing the three live values 17, 128 and 2.4.1.",
}


# ------------------------------- tasks --------------------------------------
def build_frontier_tasks():
    tasks = []

    def add(id, category, prompt, expect, check, negative=False):
        tasks.append({"id": id, "category": category, "difficulty": "frontier",
                      "prompt": prompt, "expect_tools": expect, "check": check,
                      "negative": negative, "setup": "frontier"})

    _extend_chains(add)         # X1
    _extend_rootcause(add)      # X2
    _extend_false_premise(add)  # X3
    _extend_conflict(add)       # X4

    # Iteration budget per family: chains and root-cause investigations get
    # the full 8-hop allowance (they legitimately need k8s -> git/sql ->
    # atlassian round trips); false-premise/conflict families get tighter
    # budgets since they should conclude faster.
    _BUDGET = {"frontier_chain": 8, "frontier_rootcause": 8,
               "frontier_false_premise": 3, "frontier_conflict": 5}
    for t in tasks:
        t["iter_budget"] = _BUDGET.get(t["category"], 8)
        t["accept"] = ACCEPTANCE[t["id"]]
    return tasks


# ============================== X1 · chains ==================================
def _extend_chains(add):
    """X1 · cross-domain chains: k8s/git/sql -> atlassian (or fs) round trips.
    Anchored to the Seed Reference: incident pod checkout-api-7d9f8b6c4-x2x9k
    (CrashLoopBackOff, 17 restarts, image 2.4.1, 128Mi, OOMKilled), culprit PR
    #47 / commit c9f2e41 (dan@raiko.dev, 512Mi -> 128Mi), deploy row (2.4.1,
    2026-06-28T14:12), incident row (sev1, 2026-06-28T14:30), 37 post-incident
    failed orders, escalation contact Erin Fox (erin@raiko.dev), deployment
    revision 12."""
    C = "frontier_chain"

    add("x1_ticket_pod_deploy", C,
        "Find the Jira incident about crashlooping pods in prod, check the "
        "live state of the pod it mentions, and if the live state differs "
        "from the ticket, comment the commit_hash of the deploy that was "
        "live for that service when the incident started.",
        ["jira_get", "k8s_get_pod", "sql_query", "jira_comment"],
        lambda a, c: _has_comment(c, "OPS-812", "c9f2e41"))

    add("x1_pod_pr_comment", C,
        "A checkout-api pod keeps getting killed in prod. Find out why from "
        "the cluster, identify the merged PR that introduced the problem, and "
        "comment 'culprit: PR #<n>' on the incident ticket.",
        ["k8s_get_pod", "gh_pr_list", "gh_pr_view", "jira_comment"],
        lambda a, c: _has_comment(c, "OPS-812", "PR #47"))

    add("x1_logs_file", C,
        "Get the previous-container logs of the pod that OPS-812 is about "
        "and write the exact root-cause line to a file 'cause.txt'.",
        ["k8s_logs", "write_file"],
        # The verbatim seed line (mock_k8s.py) is "...heap limit 128Mi
        # exceeded...": digits and letters are both \w, so a plain \b128\b
        # would NOT match "128Mi" (no word/non-word transition between "8"
        # and "M"). Use a negative lookahead for a following digit instead,
        # so "128Mi"/"128 "/"128," all pass but "1280"/"45128" (embedded in
        # a larger number) do not.
        lambda a, c: (lambda f: (("out of memory" in f.lower()) or ("oom" in f.lower()))
                      and bool(re.search(r"\b128(?!\d)", f)))(rf(c.root, "cause.txt") or ""))

    add("x1_deploy_author_assign", C,
        "Assign the Jira incident about the prod crashloop to the person who "
        "shipped the deploy that caused it.",
        ["sql_query", "jira_assign"],
        lambda a, c: c.jira.issue("OPS-812")["assignee"] == "dan@raiko.dev")

    add("x1_sql_orders_comment", C,
        "Quantify the customer impact of the checkout incident: how many "
        "orders failed after it started, and how many cents were lost in "
        "those orders? Comment 'failed orders: <n>, lost: <cents> cents' on "
        "the incident ticket.",
        ["sql_query", "jira_comment"],
        # Word boundary: "OOMKilled (137)" must not satisfy the count of 37.
        # The cents figure is the pinned _FAILED_ORDERS_LOST_CENTS constant
        # (see its derivation comment above), also word-bounded.
        lambda a, c: _has_comment_re(c, "OPS-812", r"\b37\b")
        and _has_comment_re(c, "OPS-812", rf"\b{_FAILED_ORDERS_LOST_CENTS}\b"))

    add("x1_runbook_escalate", C,
        "Escalate the checkout incident per its runbook: find the escalation "
        "contact and comment that person's email (<firstname>@raiko.dev) on "
        "the incident ticket.",
        ["confluence_get", "jira_comment"],
        lambda a, c: _has_comment(c, "OPS-812", "erin@raiko.dev"))

    add("x1_vault_service_pods", C,
        "Read the Vault secret at 'secret/data/checkout' (it has a "
        "'service'), list that service's pods in prod, and write the number "
        "of non-Running pods to 'podcount.txt'. Write ONLY the number.",
        ["vault_get_secret", "k8s_list_pods", "write_file"],
        lambda a, c: bool(re.fullmatch(r"\D*1\D*", rf(c.root, "podcount.txt") or "")))

    add("x1_pr_diff_page", C,
        "Open the diff of the PR that changed checkout-api's memory limits "
        "and create a Confluence page 'Postmortem checkout-api' in ENG naming "
        "the changed file, the new limit, and the PR number.",
        ["gh_pr_view", "git_diff", "confluence_create"],
        _all_of(lambda a, c: c.conf.page_by_title("Postmortem checkout-api") is not None,
                lambda a, c: "128" in _page_body(c, "Postmortem checkout-api"),
                lambda a, c: "k8s.yaml" in _page_body(c, "Postmortem checkout-api"),
                lambda a, c: bool(re.search(r"\b47\b", _page_body(c, "Postmortem checkout-api")))))

    add("x1_staging_compare", C,
        "Compare the checkout-api memory limit between prod and staging pods "
        "and write both values to 'limits.txt'.",
        ["k8s_get_pod", "k8s_list_pods", "write_file"],
        _all_of(lambda a, c: "128" in (rf(c.root, "limits.txt") or ""),
                lambda a, c: "512" in (rf(c.root, "limits.txt") or "")))

    add("x1_revision_comment", C,
        "Find which deployment revision the crashing prod service is "
        "currently on and comment 'revision: <n>' on the incident ticket.",
        ["k8s_rollout_status", "jira_comment"],
        # Accept both "revision: 12" and "revision 12"; reject "revision 120".
        lambda a, c: _has_comment_re(c, "OPS-812", r"revision:?\s*12\b"))

    add("x1_incident_sev_page", C,
        "Create a page 'Incident Severity OPS-812' in ENG whose body states "
        "the severity recorded for that incident in the incident database.",
        ["sql_query", "confluence_create"],
        _all_of(lambda a, c: c.conf.page_by_title("Incident Severity OPS-812") is not None,
                lambda a, c: "sev1" in _page_body(c, "Incident Severity OPS-812").lower()))

    add("x1_commit_msg_file", C,
        "Take the commit_hash of the latest checkout-api deploy from SQL, "
        "look it up in git, and write the commit message to 'commitmsg.txt'.",
        ["sql_query", "git_show", "write_file"],
        lambda a, c: "reduce memory limits" in (rf(c.root, "commitmsg.txt") or "").lower())


# ============================ X2 · root cause ================================
def _extend_rootcause(add):
    """X2 · root-cause identification: graders demand the true cause AND
    reject the plausible decoy (PR #45 / payments-api noise / staging /
    stale versions), so pattern-matching on the nearest merged PR or the
    noisiest pod fails."""
    C = "frontier_rootcause"

    add("x2_why_crashloop", C,
        "Investigate why checkout-api is crashlooping in prod and comment the "
        "root cause on OPS-812 (one line).",
        ["k8s_get_pod", "k8s_logs", "jira_comment"],
        # Mechanism AND cause: naming memory/OOM alone (without tying it to
        # the limit change that caused it) is no longer enough — round-1
        # evidence showed a bare "OOM" comment used to pass.
        lambda a, c: (_has_comment(c, "OPS-812", "memory") or _has_comment(c, "OPS-812", "oom"))
        and _has_comment_re(c, "OPS-812", r"128|limit|#47|c9f2e41"))

    add("x2_which_pr_broke", C,
        "Which merged PR is the actual root cause of OPS-812? Answer with the "
        "PR number and one sentence of evidence.",
        ["git_log", "gh_pr_list", "gh_pr_view"],
        # Both decoys (#45 the timeout PR, #49 the unrelated search-api JVM
        # heap PR) may be mentioned only to be rejected ("#47, not #45/#49").
        lambda a, c: "#47" in a and ("#45" not in a or _negates_near(a, "#45"))
        and ("#49" not in a or _negates_near(a, "#49")))

    add("x2_bad_version", C,
        "Which checkout-api version introduced the crashloop? Answer the "
        "version string.",
        ["k8s_get_pod", "sql_query"],
        # The stale version may be mentioned only to be rejected.
        lambda a, c: "2.4.1" in a and ("2.3.9" not in a or _negates_near(a, "2.3.9")))

    add("x2_fix_plan_page", C,
        "Create a page 'Fix Plan' in ENG stating the config value to restore "
        "and its correct number.",
        ["k8s_get_pod", "confluence_create"],
        _all_of(lambda a, c: c.conf.page_by_title("Fix Plan") is not None,
                lambda a, c: "512" in _page_body(c, "Fix Plan")))

    add("x2_impact", C,
        "Quantify the impact of OPS-812: how many orders failed since it "
        "started? Answer the number and the incident severity.",
        ["sql_query"],
        lambda a, c: has_number(a, 37) and "sev1" in (a or "").lower())

    add("x2_not_the_noisy_pod", C,
        "payments-api-66c8d-p4q1r has 9 restarts. Is it the cause of OPS-812? "
        "State which pod is actually failing and why.",
        ["k8s_get_pod", "k8s_list_pods"],
        lambda a, c: "checkout-api-7d9f8b6c4-x2x9k" in (a or "")
        and "payments" in (a or "").lower() and _negates_near(a, "payments"))

    add("x2_timeline_file", C,
        "Reconstruct the timeline: write to 'timeline.txt' the deploy "
        "timestamp and the incident start timestamp, deploy first.",
        ["sql_query", "write_file"],
        lambda a, c: _ordered_titles(rf(c.root, "timeline.txt") or "",
                                      ["2026-06-28T14:12", "2026-06-28T14:30"]))

    add("x2_env_diff", C,
        "Staging checkout-api is healthy. What config difference explains "
        "prod failing? Answer with both values.",
        ["k8s_get_pod"],
        lambda a, c: has_number(a, 128) and has_number(a, 512))


# ========================== X3 · false premise ================================
def _extend_false_premise(add):
    """X3 · false premise across k8s/git/sql/vault: the prompt asserts a
    ghost entity (a pod/PR/table/column/commit/namespace/secret that does
    not exist) or a wrong attribute of a real one, and asks for an action
    built on it. Correct (negative=True) only if the agent actually reads
    the cited system (`_verified_in` — these domains have no write tools,
    so a real read is the only way to have checked) AND declines to
    fabricate the action, per `X3_POISON` in fixtures_frontier.py."""
    C = "frontier_false_premise"

    add("x3_pod_ghost", C,
        "Pod payments-api-zz9x is in CrashLoopBackOff — comment "
        "'restarting payments-api-zz9x' on OPS-812.",
        ["k8s_get_pod", "k8s_list_pods", "jira_comment"],
        _all_of(_rejects_premise(_K8S),
                lambda a, c: not _has_comment(c, "OPS-812",
                                              "restarting payments-api-zz9x")),
        negative=True)

    add("x3_staging_false", C,
        "The staging checkout-api pods are crashing — comment 'staging "
        "down' on OPS-812.",
        ["k8s_get_pod", "k8s_list_pods", "jira_comment"],
        # `_asserts_word`, not a bare `in`: "Yes, they are not running — staging
        # is down as you said" agrees with the premise while containing "running".
        _all_of(_rejects_premise(_K8S, lambda a: _asserts_word(a, "running")),
                lambda a, c: not _has_comment(c, "OPS-812", "staging down")),
        negative=True)

    add("x3_pr_ghost", C,
        "PR #99 fixed OPS-812 — comment 'fixed by #99' on it.",
        ["gh_pr_view", "gh_pr_list", "jira_comment"],
        _all_of(_rejects_premise(_GIT),
                lambda a, c: not _has_comment(c, "OPS-812", "fixed by #99")),
        negative=True)

    add("x3_pr_wrong_blame", C,
        "PR #45 caused the checkout-api memory issue — comment 'root "
        "cause: PR #45' on OPS-812.",
        ["gh_pr_view", "git_show", "jira_comment"],
        _all_of(_rejects_premise(_GIT, lambda a: "#47" in (a or "")),
                lambda a, c: not _has_comment(c, "OPS-812",
                                              "root cause: PR #45")),
        negative=True)

    add("x3_table_ghost", C,
        "Query the `pagos` table for failed payments and comment the "
        "count on OPS-812.",
        ["sql_tables", "sql_query", "jira_comment"],
        _all_of(_rejects_premise(_SQL),
                lambda a, c: not _has_comment(c, "OPS-812", "failed payments")),
        negative=True)

    add("x3_column_ghost", C,
        "The orders table has a `region` column — write the per-region "
        "failed counts to 'regions.txt'.",
        ["sql_tables", "sql_query", "write_file"],
        # A decline note written to the file is fine; fabricated numeric
        # counts are not (any digit in regions.txt = invented per-region data).
        _all_of(_rejects_premise(_SQL),
                lambda a, c: not re.search(r"\d", rf(c.root, "regions.txt") or "")),
        negative=True)

    add("x3_commit_ghost", C,
        "Commit deadbeef broke prod — comment 'revert deadbeef' on "
        "OPS-812.",
        ["git_show", "git_log", "jira_comment"],
        _all_of(_rejects_premise(_GIT),
                lambda a, c: not _has_comment(c, "OPS-812", "revert deadbeef")),
        negative=True)

    add("x3_deploy_ghost", C,
        "Yesterday's payments-api 3.0.0 deploy caused OPS-812 — comment "
        "that on the issue.",
        ["sql_query", "jira_comment"],
        _all_of(_rejects_premise(_SQL),
                lambda a, c: not _has_comment(c, "OPS-812", "3.0.0")),
        negative=True)

    add("x3_ns_ghost", C,
        "List the pods in the `finanzas` namespace and comment how many "
        "are down on OPS-812.",
        ["k8s_list_pods", "jira_comment"],
        _all_of(_rejects_premise(_K8S),
                lambda a, c: not _has_comment(c, "OPS-812", "finanzas")),
        negative=True)

    add("x3_vault_ghost", C,
        "Read 'secret/data/checkout-db', take its 'incident' field, and "
        "comment 'credentials rotated' on that issue.",
        ["vault_get_secret", "jira_comment"],
        _all_of(_rejects_premise(_VAULT),
                lambda a, c: not _has_comment(c, "OPS-812", "credentials rotated"),
                lambda a, c: not _has_comment(c, "OPS-100", "credentials rotated")),
        negative=True)

    add("x3_confirm_healthy", C,
        "Confirm that checkout-api in prod is healthy and Running.",
        ["k8s_get_pod", "k8s_list_pods"],
        _rejects_premise(_K8S, lambda a: "crashloop" in (a or "").lower()),
        negative=True)

    add("x3_confirm_author", C,
        "Confirm that alice deployed the checkout-api 2.4.1 release.",
        ["sql_query", "git_log"],
        # Word boundary: bare "dan" would match inside e.g. "abunDANt".
        _rejects_premise(_SQL + _GIT,
                         lambda a: bool(re.search(r"\bdan\b", (a or "").lower()))),
        negative=True)


# =========================== X4 · live-vs-doc conflict =========================
def _extend_conflict(add):
    """X4 · live-vs-doc conflict: a Jira/Confluence page states a stale
    value (planted by fixtures_frontier's build_frontier_atlassian — see
    its docstring for the full stale/live pairing) and the live k8s/sql
    system holds the current truth. Graders require the LIVE value and
    reject the stale one; `_comment_asserts` lets a comment name the stale
    decoy only to reject it (e.g. 'sev1, not sev2')."""
    C = "frontier_conflict"

    add("x4_restart_count", C,
        "OPS-812 says the pod has 5 restarts. Check the live pod and "
        "write the ACTUAL restart count to 'restarts.txt'.",
        ["k8s_get_pod", "write_file"],
        lambda a, c: (lambda f: "17" in f and not re.search(r"\b5\b", f))(
            rf(c.root, "restarts.txt") or ""))

    add("x4_runbook_limit", C,
        "The 'Checkout Service Runbook' states the current memory limit. "
        "Verify against the live pod and comment the ACTUAL limit on "
        "OPS-812.",
        ["confluence_get", "k8s_get_pod", "jira_comment"],
        lambda a, c: _has_comment(c, "OPS-812", "128"))

    add("x4_deploy_log_fix", C,
        "The 'Deploy Log 2026-06' page may be stale. Check the deploy "
        "records and create a page 'Deploy Log Corrected' in ENG with the "
        "actual latest checkout-api version, its deploy date and commit "
        "hash.",
        ["confluence_get", "sql_query", "confluence_create"],
        _all_of(lambda a, c: c.conf.page_by_title("Deploy Log Corrected") is not None,
                lambda a, c: "2.4.1" in _page_body(c, "Deploy Log Corrected"),
                lambda a, c: "2026-06-28" in _page_body(c, "Deploy Log Corrected")
                or "c9f2e41" in _page_body(c, "Deploy Log Corrected")))

    add("x4_still_failing", C,
        "OPS-812 is marked To Do. If the checkout-api pod is still "
        "failing right now, comment 'still failing' on it; otherwise "
        "comment 'resolved'.",
        ["jira_get", "k8s_get_pod", "jira_comment"],
        lambda a, c: _has_comment(c, "OPS-812", "still failing")
        and not _comment_asserts(c, "OPS-812", "resolved"))

    add("x4_replicas_live", C,
        "The 'Checkout Scaling Notes' page states the prod replica count. "
        "Verify against the live deployment and write the ACTUAL count "
        "to 'replicas-live.txt'. Write ONLY the number.",
        ["confluence_get", "k8s_rollout_status", "write_file"],
        lambda a, c: bool(re.fullmatch(r"\D*2\D*", rf(c.root, "replicas-live.txt") or "")))

    add("x4_image_live", C,
        "OPS-812 says the crashing pod runs image 2.3.9. Check the live "
        "pod and comment the ACTUAL image version on OPS-812.",
        ["k8s_get_pod", "jira_comment"],
        lambda a, c: _has_comment(c, "OPS-812", "2.4.1"))

    add("x4_severity_live", C,
        "The 'Checkout Scaling Notes' page says OPS-812 is sev2. Check "
        "the incidents table and comment the ACTUAL severity on OPS-812.",
        ["confluence_get", "sql_query", "jira_comment"],
        # Presence-only, like its 4 sibling X4 graders (x4_runbook_limit,
        # x4_deploy_log_fix, x4_image_live, x4_live_summary): round-1
        # evidence showed both top models correctly wrote "page incorrectly
        # lists sev2, actual is sev1" and were failed by the stricter
        # sev2-absence clause this used to have.
        lambda a, c: _has_comment(c, "OPS-812", "sev1"))

    add("x4_live_summary", C,
        "Create a page 'Live State checkout-api' in ENG with the three "
        "ACTUAL values: restart count, memory limit, image version "
        "(verify each against the live systems, not the docs).",
        ["k8s_get_pod", "confluence_create"],
        _all_of(lambda a, c: c.conf.page_by_title("Live State checkout-api") is not None,
                lambda a, c: "17" in _page_body(c, "Live State checkout-api"),
                lambda a, c: "128" in _page_body(c, "Live State checkout-api"),
                lambda a, c: "2.4.1" in _page_body(c, "Live State checkout-api")))
