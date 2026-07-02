import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tasks_frontier as tf
import fixtures_frontier as ff
from mock_atlassian import MockJira, MockConfluence, MockVault
from mock_k8s import MockK8s
from mock_git import MockGit
from mock_sql import MockSQL


def _ctx(root):
    issues, pages, vault = ff.build_frontier_atlassian()
    from fixtures_atlassian import USERS
    return ff.FrontierCtx(
        MockJira(issues, USERS), MockConfluence(pages, USERS), MockVault(vault),
        MockK8s(), MockGit(), MockSQL(), root,
    )


def test_build_and_fields():
    ts = tf.build_frontier_tasks()
    assert len(ts) == 20
    for t in ts:
        for f in ("id", "category", "difficulty", "prompt", "expect_tools", "check", "negative", "setup"):
            assert f in t, f"{t.get('id')} missing {f}"
        assert t["difficulty"] == "frontier"
        assert t["setup"] == "frontier"


def test_x1_x2_present():
    c = Counter(t["category"] for t in tf.build_frontier_tasks())
    assert c["frontier_chain"] == 12
    assert c["frontier_rootcause"] == 8


def test_ids_unique():
    ids = [t["id"] for t in tf.build_frontier_tasks()]
    assert len(ids) == len(set(ids))


def test_all_fail_on_empty(tmp_path):
    c = _ctx(str(tmp_path))
    for t in tf.build_frontier_tasks():
        assert t["check"]("", c) is False, f"{t['id']} passes with empty answer and fresh state"


def _perform_all_correct(ctx, tmp_path):
    """Drive the mock state (and sandbox) to the correct end-state for every
    X1/X2 task that grades on side effects rather than the answer text."""
    # X1 — cross-domain chains
    ctx.jira.comment("OPS-812", "c9f2e41")
    ctx.jira.comment("OPS-812", "culprit: PR #47")
    (tmp_path / "cause.txt").write_text("fatal: out of memory - heap limit 128Mi exceeded")
    ctx.jira.assign("OPS-812", "dan@raiko.dev")
    ctx.jira.comment("OPS-812", "failed orders: 37")
    ctx.jira.comment("OPS-812", "escalation contact: erin@raiko.dev")
    (tmp_path / "podcount.txt").write_text("1")
    ctx.conf.create("ENG", "Postmortem checkout-api", "Changed deploy/k8s.yaml memory limit to 128Mi.")
    (tmp_path / "limits.txt").write_text("prod=128Mi staging=512Mi")
    ctx.jira.comment("OPS-812", "revision: 12")
    ctx.conf.create("ENG", "Incident Severity OPS-812", "Severity: sev1")
    (tmp_path / "commitmsg.txt").write_text("PR #47: reduce memory limits to cut infra costs")
    # X2 — root cause (state-based subset)
    ctx.jira.comment("OPS-812", "root cause: OOM (out of memory) after PR #47")
    ctx.conf.create("ENG", "Fix Plan", "Restore the memory limit to 512Mi.")
    (tmp_path / "timeline.txt").write_text(
        "deploy at 2026-06-28T14:12, incident started at 2026-06-28T14:30")


_GOOD_ANSWER = (
    "Root cause: PR #47 reduced checkout-api's memory limit, triggering OOM crashes. "
    "The crashlooping version is 2.4.1. Restore the memory limit to 512 (currently 128). "
    "37 orders failed since the sev1 incident began. "
    "The pod actually failing is checkout-api-7d9f8b6c4-x2x9k; "
    "payments-api-66c8d-p4q1r is not the cause."
)


def test_all_winnable(tmp_path):
    c = _ctx(str(tmp_path))
    _perform_all_correct(c, tmp_path)
    for t in tf.build_frontier_tasks():
        assert t["check"](_GOOD_ANSWER, c) is True, f"{t['id']} is NOT winnable"


def test_negates_near():
    assert tf._negates_near("payments-api is not the cause.", "payments") is True
    assert tf._negates_near("payments-api is the actual cause.", "payments") is False
    assert tf._negates_near("no mention of that word here.", "payments") is False


def test_used_helper():
    class _C:
        tool_calls = ["k8s_get_pod", "jira_comment"]
    assert tf._used(_C(), "k8s_get_pod") is True
    assert tf._used(_C(), "sql_query", "jira_comment") is True
    assert tf._used(_C(), "vault_get_secret") is False

    class _Empty:
        pass
    assert tf._used(_Empty(), "k8s_get_pod") is False
