"""FRONTIER tier: cross-domain chains over k8s/git/sql/atlassian mocks.

Reuses the grading combinators validated by the HARD Atlassian tier
(`_all_of`, `_declines`, `_ordered_titles` from tasks_hard_atlassian) plus a
local `_has_comment`/`_page_body` pair adapted to `FrontierCtx` (which nests
jira/conf/vault/k8s/git/sql instead of AtlasCtx's flatter jira/conf/vault).

This module currently defines X1 (cross-domain chains) and X2 (root-cause
identification); X3 (false premise) and X4 (live-vs-doc conflict) are
appended by a later change via their own `_extend_*` functions, mirroring
the one-family-per-function structure of tasks_hard_atlassian.py.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tasks import has_number
from tasks_advanced import rf
from tasks_hard_atlassian import _all_of, _declines, _ordered_titles


def _page_body(ctx, title):
    return (ctx.conf.page_by_title(title) or {}).get("body", "")


def _has_comment(ctx, key, text):
    i = ctx.jira.issue(key)
    return bool(i) and any(text.lower() in x.lower() for x in i["comments"])


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


# ------------------------------- tasks --------------------------------------
def build_frontier_tasks():
    tasks = []

    def add(id, category, prompt, expect, check, negative=False):
        tasks.append({"id": id, "category": category, "difficulty": "frontier",
                      "prompt": prompt, "expect_tools": expect, "check": check,
                      "negative": negative, "setup": "frontier"})

    _extend_chains(add)      # X1
    _extend_rootcause(add)   # X2

    # Iteration budget per family: chains and root-cause investigations get
    # the full 8-hop allowance (they legitimately need k8s -> git/sql ->
    # atlassian round trips); false-premise/conflict families (added later)
    # get tighter budgets since they should conclude faster.
    _BUDGET = {"frontier_chain": 8, "frontier_rootcause": 8,
               "frontier_false_premise": 3, "frontier_conflict": 5}
    for t in tasks:
        t["iter_budget"] = _BUDGET.get(t["category"], 8)
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
        "Read OPS-812, check the actual state of the pod it mentions, and if "
        "the live state differs from the ticket, find the latest checkout-api "
        "deploy in the `deploys` table and comment its commit_hash on OPS-812.",
        ["jira_get", "k8s_get_pod", "sql_query", "jira_comment"],
        lambda a, c: _has_comment(c, "OPS-812", "c9f2e41"))

    add("x1_pod_pr_comment", C,
        "Find why the crashing checkout-api pod was killed (its last exit "
        "reason), locate the merged PR that changed that setting, and comment "
        "'culprit: PR #<n>' on OPS-812.",
        ["k8s_get_pod", "gh_pr_list", "gh_pr_view", "jira_comment"],
        lambda a, c: _has_comment(c, "OPS-812", "PR #47"))

    add("x1_logs_file", C,
        "Get the previous-container logs of the crashing checkout-api pod and "
        "write the root-cause line to a file 'cause.txt'.",
        ["k8s_logs", "write_file"],
        lambda a, c: (lambda t: "out of memory" in t or "oom" in t)(
            (rf(c.root, "cause.txt") or "").lower()))

    add("x1_deploy_author_assign", C,
        "Find who authored the deploy that broke checkout-api (deploys table) "
        "and assign OPS-812 to that person.",
        ["sql_query", "jira_assign"],
        lambda a, c: c.jira.issue("OPS-812")["assignee"] == "dan@raiko.dev")

    add("x1_sql_orders_comment", C,
        "Count orders with status 'failed' created after the OPS-812 incident "
        "started (see the incidents table) and comment 'failed orders: <n>' "
        "on OPS-812.",
        ["sql_query", "jira_comment"],
        lambda a, c: _has_comment(c, "OPS-812", "37"))

    add("x1_runbook_escalate", C,
        "Open the 'Checkout Service Runbook', find the incident escalation "
        "contact, and comment that person's email (<firstname>@raiko.dev) on "
        "OPS-812.",
        ["confluence_get", "jira_comment"],
        lambda a, c: _has_comment(c, "OPS-812", "erin@raiko.dev"))

    add("x1_vault_service_pods", C,
        "Read the Vault secret at 'secret/data/checkout' (it has a "
        "'service'), list that service's pods in prod, and write the number "
        "of non-Running pods to 'podcount.txt'.",
        ["vault_get_secret", "k8s_list_pods", "write_file"],
        lambda a, c: bool(re.fullmatch(r"\D*1\D*", rf(c.root, "podcount.txt") or "")))

    add("x1_pr_diff_page", C,
        "Open the diff of the PR that changed checkout-api's memory limits "
        "and create a Confluence page 'Postmortem checkout-api' in ENG naming "
        "the changed file and the new limit.",
        ["gh_pr_view", "git_diff", "confluence_create"],
        _all_of(lambda a, c: c.conf.page_by_title("Postmortem checkout-api") is not None,
                lambda a, c: "128" in _page_body(c, "Postmortem checkout-api"),
                lambda a, c: "k8s.yaml" in _page_body(c, "Postmortem checkout-api")))

    add("x1_staging_compare", C,
        "Compare the checkout-api memory limit between prod and staging pods "
        "and write both values to 'limits.txt'.",
        ["k8s_get_pod", "k8s_list_pods", "write_file"],
        _all_of(lambda a, c: "128" in (rf(c.root, "limits.txt") or ""),
                lambda a, c: "512" in (rf(c.root, "limits.txt") or "")))

    add("x1_revision_comment", C,
        "Get the rollout status of the checkout-api deployment in prod and "
        "comment 'revision: <n>' on OPS-812.",
        ["k8s_rollout_status", "jira_comment"],
        lambda a, c: _has_comment(c, "OPS-812", "revision: 12"))

    add("x1_incident_sev_page", C,
        "Look up the severity of the incident linked to OPS-812 in the "
        "`incidents` table and create a page 'Incident Severity OPS-812' in "
        "ENG whose body contains it.",
        ["sql_query", "confluence_create"],
        _all_of(lambda a, c: c.conf.page_by_title("Incident Severity OPS-812") is not None,
                lambda a, c: "sev1" in _page_body(c, "Incident Severity OPS-812")))

    add("x1_commit_msg_file", C,
        "Take the commit_hash of the latest checkout-api deploy from SQL, "
        "look it up in git, and write the commit message to 'commitmsg.txt'.",
        ["sql_query", "git_show", "write_file"],
        lambda a, c: "reduce memory limits" in (rf(c.root, "commitmsg.txt") or ""))


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
        lambda a, c: (_has_comment(c, "OPS-812", "memory") or _has_comment(c, "OPS-812", "OOM"))
        and not _has_comment(c, "OPS-812", "timeout"))

    add("x2_which_pr_broke", C,
        "Which merged PR is the actual root cause of OPS-812? Answer with the "
        "PR number and one sentence of evidence.",
        ["git_log", "gh_pr_list", "gh_pr_view"],
        lambda a, c: "#47" in a and "#45" not in a)

    add("x2_bad_version", C,
        "Which checkout-api version introduced the crashloop? Answer the "
        "version string.",
        ["k8s_get_pod", "sql_query"],
        lambda a, c: "2.4.1" in a and "2.3.9" not in a)

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
        lambda a, c: has_number(a, 37) and "sev1" in a)

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
