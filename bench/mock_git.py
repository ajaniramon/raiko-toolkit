"""In-process mock of a git repo + GitHub PRs for the Frontier tier.

Mirrors the style of mock_atlassian.py / mock_k8s.py: seeded, deterministic,
in-process state; every method returns a plain tool-result STRING; unknown
inputs return an "ERROR: ..." string, never raise.

Planted incident (culprit / decoy pair, see mock_k8s.py for the runtime
symptom): commit `c9f2e41` / PR #47 "reduce memory limits to cut infra
costs" (dan@raiko.dev, 2026-06-28) drops the checkout-api memory limit in
`deploy/k8s.yaml` from 512Mi to 128Mi — this is the actual root cause of the
OOMKilled crash loop. PR #45 "fix checkout timeout" (alice@raiko.dev,
2026-06-25) is a plausible-looking decoy merged just three days earlier: it
only touches an HTTP client timeout and never mentions memory at all, so an
agent that jumps on the nearest merged PR instead of tracing evidence will
misattribute the incident to it. `b4e9c77` is an older filler commit (a
2.3.9 release bump) that predates both and is cross-referenced by
mock_sql.py's `deploys` table, so `diff(b4e9c77, c9f2e41)` must span the
whole window. The remaining filler commits (docs/tests/lint/deps) carry
neither "memory" nor "timeout" so they cannot be mistaken for evidence.

Round-2 hardening: `f7d3b19` / PR #49 "tune JVM heap flags for search-api"
(erin@raiko.dev, 2026-06-27) is a second, memory-ADJACENT decoy for a
DIFFERENT service — it never touches checkout-api or its k8s memory limit,
so it never satisfies any checkout-api grader; x2_which_pr_broke tolerates
it being named only to be rejected, same as PR #45.
"""
import copy

DEFAULT_SEED = {
    "commits": [
        {
            "hash": "c9f2e41",
            "date": "2026-06-28",
            "author": "dan@raiko.dev",
            "message": "PR #47: reduce memory limits to cut infra costs",
            "files": ["deploy/k8s.yaml"],
            "diff": (
                "--- a/deploy/k8s.yaml\n"
                "+++ b/deploy/k8s.yaml\n"
                "@@ -12,7 +12,7 @@\n"
                "     resources:\n"
                "       limits:\n"
                "-        memory: 512Mi\n"
                "+        memory: 128Mi\n"
            ),
        },
        {
            # Memory-adjacent decoy (round-2 hardening): a JVM heap tuning PR
            # for a DIFFERENT service (search-api, not checkout-api) merged
            # the day before the incident. It never touches checkout-api's
            # k8s memory limit, so an agent that pattern-matches on "memory"
            # instead of tracing the actual checkout-api commit/PR (#47 /
            # c9f2e41) will misattribute the incident to it.
            "hash": "f7d3b19",
            "date": "2026-06-27",
            "author": "erin@raiko.dev",
            "message": "PR #49: tune JVM heap flags for search-api",
            "files": ["config/jvm.conf"],
            "diff": (
                "--- a/config/jvm.conf\n"
                "+++ b/config/jvm.conf\n"
                "@@\n"
                "-Xmx512m -Xms256m\n"
                "+Xmx768m -Xms384m\n"
            ),
        },
        {
            "hash": "f3a1c88",
            "date": "2026-06-27",
            "author": "bob@raiko.dev",
            "message": "test: add integration test for cart serialization",
            "files": ["tests/test_cart_serialization.py"],
            "diff": (
                "--- a/tests/test_cart_serialization.py\n"
                "+++ b/tests/test_cart_serialization.py\n"
                "@@\n"
                "+def test_cart_serialization_roundtrip():\n"
                "+    assert True\n"
            ),
        },
        {
            "hash": "e5d2a91",
            "date": "2026-06-26",
            "author": "carol@raiko.dev",
            "message": "chore: async checkout experiment cleanup",
            "files": ["services/checkout/async_checkout.py"],
            "diff": (
                "--- a/services/checkout/async_checkout.py\n"
                "+++ b/services/checkout/async_checkout.py\n"
                "@@\n"
                "-# TODO: revisit async flow\n"
                "+# removed: experiment abandoned after perf review\n"
            ),
        },
        {
            "hash": "a1b7d03",
            "date": "2026-06-25",
            "author": "alice@raiko.dev",
            "message": "PR #45: fix checkout timeout - bump client timeout to 30s",
            "files": ["services/checkout/client.py"],
            "diff": (
                "--- a/services/checkout/client.py\n"
                "+++ b/services/checkout/client.py\n"
                "@@\n"
                "-TIMEOUT_SECONDS = 15\n"
                "+TIMEOUT_SECONDS = 30\n"
            ),
        },
        {
            "hash": "d8c3b72",
            "date": "2026-06-23",
            "author": "carol@raiko.dev",
            "message": "docs: update README for checkout-api",
            "files": ["README.md"],
            "diff": (
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@\n"
                "-Onboarding instructions coming soon.\n"
                "+See docs/ for onboarding instructions.\n"
            ),
        },
        {
            "hash": "c2a9f14",
            "date": "2026-06-21",
            "author": "bob@raiko.dev",
            "message": "chore: bump dependency versions",
            "files": ["requirements.txt"],
            "diff": (
                "--- a/requirements.txt\n"
                "+++ b/requirements.txt\n"
                "@@\n"
                "-requests==2.28.0\n"
                "+requests==2.31.0\n"
            ),
        },
        {
            "hash": "b4e9c77",
            "date": "2026-06-20",
            "author": "alice@raiko.dev",
            "message": "release checkout-api 2.3.9",
            "files": ["VERSION"],
            "diff": (
                "--- a/VERSION\n"
                "+++ b/VERSION\n"
                "@@\n"
                "-2.3.8\n"
                "+2.3.9\n"
            ),
        },
        {
            "hash": "9f1e6a0",
            "date": "2026-06-18",
            "author": "dan@raiko.dev",
            "message": "test: add unit tests for cart client",
            "files": ["tests/test_cart_client.py"],
            "diff": (
                "--- a/tests/test_cart_client.py\n"
                "+++ b/tests/test_cart_client.py\n"
                "@@\n"
                "+def test_cart_client_basic():\n"
                "+    assert True\n"
            ),
        },
    ],
    "prs": [
        {
            "number": 47,
            "title": "reduce memory limits to cut infra costs",
            "author": "dan@raiko.dev",
            "state": "merged",
            "merged_at": "2026-06-28",
            "commit": "c9f2e41",
            "files": ["deploy/k8s.yaml"],
            "body": "Reduces the memory request/limit for checkout-api from 512Mi to 128Mi to cut infra costs.",
        },
        {
            "number": 46,
            "title": "experiment: async checkout",
            "author": "bob@raiko.dev",
            "state": "closed",
            "merged_at": None,
            "commit": "e5d2a91",
            "files": ["services/checkout/async_checkout.py"],
            "body": "Prototype for an async checkout flow; abandoned after a perf review.",
        },
        {
            "number": 45,
            "title": "fix checkout timeout - bump client timeout to 30s",
            "author": "alice@raiko.dev",
            "state": "merged",
            "merged_at": "2026-06-25",
            "commit": "a1b7d03",
            "files": ["services/checkout/client.py"],
            "body": (
                "Increases the HTTP client timeout for checkout requests from "
                "15s to 30s to reduce false-positive failures under load."
            ),
        },
        {
            "number": 44,
            "title": "update README",
            "author": "carol@raiko.dev",
            "state": "merged",
            "merged_at": "2026-06-23",
            "commit": "d8c3b72",
            "files": ["README.md"],
            "body": "Refreshes onboarding instructions in the README.",
        },
        {
            "number": 49,
            "title": "tune JVM heap flags for search-api",
            "author": "erin@raiko.dev",
            "state": "merged",
            "merged_at": "2026-06-27",
            "commit": "f7d3b19",
            "files": ["config/jvm.conf"],
            "body": "Increases the JVM heap size for search-api to reduce GC pause "
                    "frequency under load.",
        },
        {
            "number": 48,
            "title": "add retry logic to cart client",
            "author": "bob@raiko.dev",
            "state": "open",
            "merged_at": None,
            "commit": None,
            "files": ["services/checkout/cart_client.py"],
            "body": "Adds retry with backoff for transient cart-service failures.",
        },
    ],
}


class MockGit:
    """Deterministic, in-process stand-in for `git`/`gh` CLI tools.

    Rebuilt fresh per task; `seed` defaults to DEFAULT_SEED (fixtures_frontier
    will later pass an explicit, possibly extended, seed).
    """

    def __init__(self, seed=None):
        seed = seed or DEFAULT_SEED
        # Commits are stored newest-first, matching `git log` order.
        self._commits = [dict(c) for c in seed["commits"]]
        self._prs = [dict(p) for p in seed["prs"]]

    def _find_commit(self, ref):
        ref = (ref or "").strip()
        for c in self._commits:
            if c["hash"] == ref:
                return c
        return None

    def _find_pr(self, number):
        try:
            number = int(number)
        except (TypeError, ValueError):
            return None
        for p in self._prs:
            if p["number"] == number:
                return p
        return None

    def log(self, limit=20):
        try:
            n = max(1, int(limit or 20))
        except (TypeError, ValueError):
            n = 20
        lines = [
            f"{c['hash']} {c['date']} {c['author']}  {c['message']}"
            for c in self._commits[:n]
        ]
        return "\n".join(lines)

    def show(self, ref):
        c = self._find_commit(ref)
        if not c:
            return f"ERROR: not found: commit {ref}"
        return (
            f"commit {c['hash']}\n"
            f"Author: {c['author']}\n"
            f"Date:   {c['date']}\n"
            "\n"
            f"    {c['message']}\n"
            "\n"
            f"{c['diff']}"
        )

    def diff(self, base, head):
        b = self._find_commit(base)
        h = self._find_commit(head)
        if not b or not h:
            return f"ERROR: not found: diff {base}..{head}"
        idx_b = self._commits.index(b)
        idx_h = self._commits.index(h)
        lo, hi = min(idx_b, idx_h), max(idx_b, idx_h)
        # Commits strictly newer than the older ref, up to and including the newer ref.
        between = self._commits[lo:hi]
        if not between:
            return f"No changes between {base} and {head}."
        chunks = [c["diff"] for c in between if c.get("diff")]
        return f"diff {base}..{head}\n" + "\n".join(chunks)

    def pr_list(self, state="all"):
        prs = list(self._prs)
        if state and state != "all":
            prs = [p for p in prs if p["state"] == state]
        if not prs:
            return f"No PRs with state '{state}'."
        lines = ["#     STATE               AUTHOR              TITLE"]
        for p in sorted(prs, key=lambda p: -p["number"]):
            state_disp = "closed (unmerged)" if p["state"] == "closed" else p["state"]
            lines.append(
                f"#{p['number']:<5}{state_disp:<20}{p['author']:<20}{p['title']}"
            )
        return "\n".join(lines)

    def pr_view(self, number):
        p = self._find_pr(number)
        if not p:
            return f"ERROR: not found: PR #{number}"
        state_line = f"State: {p['state']}"
        if p["state"] == "merged":
            state_line += f" (merged {p['merged_at']})"
        elif p["state"] == "closed":
            state_line += " (unmerged)"
        lines = [
            f"PR #{p['number']}: {p['title']}",
            f"Author: {p['author']}",
            state_line,
            f"Files changed: {', '.join(p['files'])}",
            "",
            p["body"],
        ]
        if p.get("commit"):
            lines += ["", f"Commit: {p['commit']}"]
        return "\n".join(lines)
