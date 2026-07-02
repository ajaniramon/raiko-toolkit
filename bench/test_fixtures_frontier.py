import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fixtures_frontier as ff
import fixtures_atlassian as fx


def test_atlassian_extension_adds_crossref_entities():
    issues, pages, vault = ff.build_frontier_atlassian()
    assert any(i["key"] == "OPS-812" for i in issues)
    ops812 = next(i for i in issues if i["key"] == "OPS-812")
    assert "5 restarts" in ops812["description"] and "2.3.9" in ops812["description"]
    titles = {p["title"] for p in pages}
    assert {"Checkout Service Runbook", "Deploy Log 2026-06", "Checkout Scaling Notes"} <= titles
    assert vault["secret/data/checkout"]["service"] == "checkout-api"
    assert "secret/data/checkout-db" not in vault


def test_extension_does_not_mutate_base_seeds():
    base_issues = len(fx.build_jira_seed())
    ff.build_frontier_atlassian()
    assert len(fx.build_jira_seed()) == base_issues


def test_tool_wiring_complete():
    from mock_k8s import MockK8s
    from mock_git import MockGit
    from mock_sql import MockSQL
    impls = ff.build_frontier_impls(MockK8s(), MockGit(), MockSQL())
    names = {t["function"]["name"] for t in ff.FRONTIER_TOOLS}
    assert set(impls) == names
    assert "2.4.1" in impls["sql_query"](query="SELECT version FROM deploys WHERE id=31")
    assert "CrashLoopBackOff" in impls["k8s_list_pods"](namespace="prod")


def test_anti_poison_lint():
    """No seed body anywhere may support an X3 false premise."""
    from mock_k8s import MockK8s
    from mock_git import MockGit
    issues, pages, vault = ff.build_frontier_atlassian()
    corpus = " ".join(i["summary"] + " " + i["description"] for i in issues)
    corpus += " ".join(p["body"] for p in pages)
    corpus += MockGit().log(50) + MockGit().pr_list()
    corpus += MockK8s().list_pods("prod") + MockK8s().list_pods("staging") + MockK8s().events("prod")
    for needle in ff.X3_POISON:
        assert needle not in corpus, f"seed supports false premise: {needle}"
