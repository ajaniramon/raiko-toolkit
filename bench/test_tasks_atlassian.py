import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tasks_atlassian as ta

def test_build_returns_enough_tasks():
    tasks = ta.build_atlassian_tasks()
    assert 150 <= len(tasks) <= 175

def test_all_tasks_have_required_fields():
    for t in ta.build_atlassian_tasks():
        for f in ("id", "category", "difficulty", "prompt", "expect_tools", "check", "setup"):
            assert f in t, f"{t.get('id')} missing {f}"
        assert t["setup"] == "atlassian"
        assert t["difficulty"] in ("easy", "medium", "hard")

def test_ids_unique():
    ids = [t["id"] for t in ta.build_atlassian_tasks()]
    assert len(ids) == len(set(ids))

def test_deterministic():
    a = [t["id"] for t in ta.build_atlassian_tasks()]
    b = [t["id"] for t in ta.build_atlassian_tasks()]
    assert a == b

def test_vault_gated_count():
    tasks = ta.build_atlassian_tasks()
    assert 8 <= sum(1 for t in tasks if t["category"] == "vault_gated") <= 12

def test_category_coverage_matches_design():
    from collections import Counter
    tasks = ta.build_atlassian_tasks()
    c = Counter(t["category"] for t in tasks)
    assert c["chain"] >= 30, c
    assert c["conf_read"] >= 12, c
    assert c["conf_search"] >= 12, c
    assert c["conf_write"] >= 2, c
    assert 150 <= len(tasks) <= 175, len(tasks)
