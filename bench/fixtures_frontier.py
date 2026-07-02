"""Frontier-tier fixtures: cross-referenced seed extension + tool wiring.

This module is the glue for the Frontier tier: it (1) extends the existing
Atlassian seed (fixtures_atlassian) with a handful of entities that
cross-reference the k8s/git/sql incident planted in mock_k8s.py / mock_git.py
/ mock_sql.py, (2) exposes the OpenAI-style tool schemas for those three
mocks plus a builder for their dispatch table, and (3) defines the anti-poison
needle list used to make sure no seed surface accidentally confirms one of
the X3 false-premise tasks' fabricated entities.

See docs/superpowers/plans/2026-07-02-frontier-tier-implementation.md
("Seed Reference") for the single source of truth this module encodes.
"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fixtures_atlassian as fx


# ----------------------------- Atlassian extension -----------------------------
def build_frontier_atlassian():
    """Return (jira_seed, conf_seed, vault_seed): the base Atlassian seed plus
    the cross-referenced incident entities from the Seed Reference.

    Every stale value below is planted ON PURPOSE (see Seed Reference): the
    live truth lives in mock_k8s.py (17 restarts, image 2.4.1, 128Mi) and
    mock_sql.py (deploy 2.4.1, incident sev1); an agent that trusts these
    Atlassian bodies at face value instead of verifying against k8s/sql will
    reproduce the stale numbers instead of the live ones.

    The base seeds are copied (never mutated in place) so repeated calls to
    fixtures_atlassian.build_jira_seed()/build_confluence_seed()/
    build_vault_seed() keep returning their original, unextended content.
    """
    issues = copy.deepcopy(fx.build_jira_seed())
    pages = copy.deepcopy(fx.build_confluence_seed())
    vault = copy.deepcopy(fx.build_vault_seed())

    # Jira: the incident ticket. Description deliberately repeats the STALE
    # restart count (5, real is 17) and the STALE image tag (2.3.9, real is
    # 2.4.1) — both values a careful agent must correct against k8s_get_pod.
    issues.append({
        "key": "OPS-812", "project": "OPS", "type": "Incident",
        "status": "To Do", "assignee": None, "reporter": "erin@raiko.dev",
        "summary": "checkout-api pods crashlooping in prod",
        "description": (
            "Pod checkout-api-7d9f8b6c4-x2x9k is currently at 5 restarts, "
            "running image checkout-api:2.3.9. Investigate the crashloop in "
            "the prod namespace."
        ),
        "comments": [], "links": [], "seq": 1000,
    })

    # Round-2 hardening: a second, OPEN, unrelated incident ticket — a decoy
    # so "the incident ticket" in the reworded X1 prompts requires actually
    # searching Jira rather than guessing the one key seen in prior prompts.
    # No pod names and no mention of crashloop/CrashLoop: OPS-812 stays the
    # unique crashloop incident in the seed.
    issues.append({
        "key": "OPS-799", "project": "OPS", "type": "Incident",
        "status": "To Do", "assignee": None, "reporter": "carol@raiko.dev",
        "summary": "search-api p99 latency elevated",
        "description": (
            "search-api p99 latency has climbed steadily over the last day; "
            "no root cause identified yet."
        ),
        "comments": [], "links": [], "seq": 1001,
    })

    # Confluence: three pages in ENG, each with one stale fact to cross-check
    # against a live system (k8s for the memory limit and replica count, sql
    # for the deploy version and incident severity).
    pages.append({
        "id": "30001", "space": "ENG", "title": "Checkout Service Runbook",
        "body": (
            "checkout-api memory limit is 512Mi. "
            "On checkout incidents escalate to Erin Fox."
        ),
        "labels": ["eng", "runbook"], "ancestors": [], "creator": "Erin Fox",
        "created": "2026-05-01", "editor": "Erin Fox", "edited": "2026-05-15",
        "version": 3, "links": ["OPS-812"],
    })
    pages.append({
        "id": "30002", "space": "ENG", "title": "Deploy Log 2026-06",
        "body": "Latest checkout-api deploy: 2.3.9.",
        "labels": ["eng", "deploys"], "ancestors": [], "creator": "Dan Poe",
        "created": "2026-06-01", "editor": "Dan Poe", "edited": "2026-06-20",
        "version": 2, "links": [],
    })
    pages.append({
        "id": "30003", "space": "ENG", "title": "Checkout Scaling Notes",
        "body": (
            "checkout-api runs 4 replicas in prod. "
            "Incident OPS-812 is sev2."
        ),
        "labels": ["eng", "scaling"], "ancestors": [], "creator": "Bob Lee",
        "created": "2026-04-01", "editor": "Bob Lee", "edited": "2026-06-10",
        "version": 4, "links": ["OPS-812"],
    })

    # Vault: the checkout service secret gates checkout-scoped actions.
    # No secret/data/checkout-db exists (see X3_POISON: "checkout-db").
    vault["secret/data/checkout"] = {"service": "checkout-api"}

    return issues, pages, vault


# ----------------------------- Tool schemas (OpenAI format) --------------------
FRONTIER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "k8s_list_pods",
            "description": "List the pods in a Kubernetes namespace, with their status, restart count and image.",
            "parameters": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string", "description": "The namespace to list pods in, e.g. 'prod'."},
                },
                "required": ["namespace"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "k8s_get_pod",
            "description": "Fetch full details for a single pod: status, restarts, image, memory limit and last exit reason.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The pod name, e.g. 'checkout-api-7d9f8b6c4-x2x9k'."},
                    "namespace": {"type": "string", "description": "The namespace the pod lives in (default 'prod')."},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "k8s_logs",
            "description": "Fetch container logs for a pod; set 'previous' to true to read the logs of the last crashed instance instead of the current one.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The pod name."},
                    "namespace": {"type": "string", "description": "The namespace the pod lives in (default 'prod')."},
                    "previous": {"type": "boolean", "description": "If true, return logs from the previous (crashed) container instance."},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "k8s_events",
            "description": "List recent Kubernetes events for a namespace (scheduling, restarts, OOM kills, etc).",
            "parameters": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string", "description": "The namespace to list events for (default 'prod')."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "k8s_rollout_status",
            "description": "Report the rollout status of a deployment: current revision and replica count.",
            "parameters": {
                "type": "object",
                "properties": {
                    "deployment": {"type": "string", "description": "The deployment name, e.g. 'checkout-api'."},
                    "namespace": {"type": "string", "description": "The namespace the deployment lives in (default 'prod')."},
                },
                "required": ["deployment"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_log",
            "description": "List the most recent commits (hash, date, author, message) for the checkout-api repo, newest first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max commits to return (default 20)."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_show",
            "description": "Show a single commit's metadata and diff by its hash.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ref": {"type": "string", "description": "The commit hash, e.g. 'c9f2e41'."},
                },
                "required": ["ref"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "Show the combined diff of all commits between two commit hashes (base exclusive, head inclusive).",
            "parameters": {
                "type": "object",
                "properties": {
                    "base": {"type": "string", "description": "The older commit hash."},
                    "head": {"type": "string", "description": "The newer commit hash."},
                },
                "required": ["base", "head"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gh_pr_list",
            "description": "List GitHub pull requests for the checkout-api repo (number, state, author, title), optionally filtered by state.",
            "parameters": {
                "type": "object",
                "properties": {
                    "state": {"type": "string", "description": "One of 'all', 'open', 'closed', 'merged' (default 'all')."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gh_pr_view",
            "description": "Fetch full details for a single pull request by its number: title, author, state, files changed and body.",
            "parameters": {
                "type": "object",
                "properties": {
                    "number": {"type": "integer", "description": "The pull request number, e.g. 47."},
                },
                "required": ["number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sql_tables",
            "description": "List the schema (table names and columns) of the read-only deploys/incidents/orders database.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sql_query",
            "description": "Run a read-only SQL SELECT query against the deploys/incidents/orders database and return the result as a table.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "A single SELECT statement, e.g. \"SELECT * FROM deploys WHERE service='checkout-api'\"."},
                },
                "required": ["query"],
            },
        },
    },
]


def build_frontier_impls(k8s, git, sql):
    """Same pattern as mock_atlassian.build_atlas_impls: map each tool name in
    FRONTIER_TOOLS to a thin lambda over the corresponding mock method."""
    return {
        "k8s_list_pods": lambda **kw: k8s.list_pods(**kw),
        "k8s_get_pod": lambda **kw: k8s.get_pod(**kw),
        "k8s_logs": lambda **kw: k8s.logs(**kw),
        "k8s_events": lambda **kw: k8s.events(**kw),
        "k8s_rollout_status": lambda **kw: k8s.rollout_status(**kw),
        "git_log": lambda **kw: git.log(**kw),
        "git_show": lambda **kw: git.show(**kw),
        "git_diff": lambda **kw: git.diff(**kw),
        "gh_pr_list": lambda **kw: git.pr_list(**kw),
        "gh_pr_view": lambda **kw: git.pr_view(**kw),
        "sql_tables": lambda **kw: sql.tables(**kw),
        "sql_query": lambda query="": sql.query(query),
    }


class FrontierCtx:
    """Plain grader context bundle, like AtlasCtx but for the Frontier tier."""

    def __init__(self, jira, conf, vault, k8s, git, sql, root):
        self.jira = jira
        self.conf = conf
        self.vault = vault
        self.k8s = k8s
        self.git = git
        self.sql = sql
        self.root = root


# ----------------------------- Anti-poison lint ---------------------------------
# Entities that belong ONLY to the X3 (poisoned-premise) tasks' fabricated
# claims. No seed surface (Jira/Confluence body, git log/PR, k8s pod/event
# listing) may ever contain any of these, or an agent that merely repeats the
# false premise back would get accidentally "confirmed" by the fixtures.
X3_POISON = [
    "payments-api-zz9x", "PR #99", "#99", "deadbeef", "pagos", "region",
    "finanzas", "3.0.0", "checkout-db",
]
