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


def test_seed_hardening():
    """Round-2 discovery-hardening seed additions (C1): a second non-Running
    pod in prod for a DIFFERENT service (service-scoped counting now
    matters), a memory-adjacent PR/commit decoy for a different service, and
    a decoy open incident ticket — none of which may confirm or shift the
    checkout-api incident's facts."""
    from mock_k8s import MockK8s
    from mock_git import MockGit
    from mock_sql import MockSQL

    k = MockK8s()
    prod_pods = [p for p in k._pods if p["namespace"] == "prod"]
    non_running = [p for p in prod_pods if p["status"] != "Running"]
    assert len(non_running) == 2, "prod must have exactly 2 non-Running pods overall"
    checkout_non_running = [p for p in non_running if p["name"].startswith("checkout-api")]
    assert len(checkout_non_running) == 1, "checkout-api must still have exactly 1 non-Running pod"

    s = MockSQL()
    latest = s.query(
        "SELECT commit_hash FROM deploys WHERE service='checkout-api' "
        "ORDER BY deployed_at DESC LIMIT 1")
    assert "c9f2e41" in latest, "checkout-api's latest deploy must still be c9f2e41"

    g = MockGit()
    assert "#49" in g.pr_list()
    v49 = g.pr_view(49)
    assert "checkout" not in v49.lower()

    issues, pages, vault = ff.build_frontier_atlassian()
    ops799 = next(i for i in issues if i["key"] == "OPS-799")
    assert "crashloop" not in ops799["description"].lower()
    assert "crashloop" not in ops799["summary"].lower()
