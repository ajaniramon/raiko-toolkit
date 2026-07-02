import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mock_git import MockGit


def test_log_and_show():
    g = MockGit()
    assert "c9f2e41" in g.log() and "reduce memory limits" in g.log()
    s = g.show("c9f2e41")
    assert "512Mi" in s and "128Mi" in s and "deploy/k8s.yaml" in s
    assert g.show("deadbeef").startswith("ERROR")


def test_pr_list_and_view():
    g = MockGit()
    lst = g.pr_list()
    assert "#47" in lst and "#45" in lst and "#48" in lst
    v47 = g.pr_view(47)
    assert "deploy/k8s.yaml" in v47 and "merged" in v47.lower()
    assert "dan@raiko.dev" in v47
    v45 = g.pr_view(45)
    assert "timeout" in v45 and "memory" not in v45.lower()
    assert g.pr_view(99).startswith("ERROR")


def test_diff_between_refs():
    g = MockGit()
    d = g.diff("b4e9c77", "c9f2e41")
    assert "128Mi" in d
    assert g.diff("x", "y").startswith("ERROR")


def test_no_poisoned_content():
    g = MockGit()
    blob = g.log(50) + g.pr_list()
    assert "deadbeef" not in blob and "#99" not in blob
