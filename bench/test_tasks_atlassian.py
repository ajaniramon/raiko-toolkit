import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tasks_atlassian as ta

def test_build_returns_enough_tasks():
    tasks = ta.build_atlassian_tasks()
    assert 190 <= len(tasks) <= 240

def test_all_tasks_have_required_fields():
    for t in ta.build_atlassian_tasks():
        for f in ("id", "category", "difficulty", "prompt", "expect_tools", "check", "setup", "negative"):
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
    positives = sum(1 for t in tasks if t["category"] == "vault_gated" and not t["negative"])
    assert 8 <= positives <= 12

def test_category_coverage_matches_design():
    from collections import Counter
    tasks = ta.build_atlassian_tasks()
    c = Counter(t["category"] for t in tasks)
    assert c["chain"] >= 30, c
    assert c["conf_read"] >= 12, c
    assert c["conf_search"] >= 12, c
    assert c["conf_write"] >= 2, c
    assert 190 <= len(tasks) <= 240, len(tasks)

def test_negative_tasks_present():
    tasks = ta.build_atlassian_tasks()
    negs = [t for t in tasks if t["negative"]]
    assert len(negs) >= 38, len(negs)
    # cubren varias familias
    cats = {t["category"] for t in negs}
    assert {"jira_search", "jira_read", "conf_search", "conf_read", "vault_gated"} <= cats

def test_feature_tasks_present():
    ids = {t["id"] for t in ta.build_atlassian_tasks()}
    assert {"ft_jql_in", "ft_author", "ft_paginate"} <= ids

# ---- grader-precision helpers (mejora #1) ----
def test_status_ok_rejects_filler_and_conflicts():
    assert ta._status_ok("The status is In Progress.", "In Progress")
    assert ta._status_ok("I'm done reading; status: Done", "Done")
    # 'done' de relleno pero el status correcto es otro y aparece -> conflicto -> falla
    assert not ta._status_ok("done reading; the status is Blocked", "Done")
    assert not ta._status_ok("It is To Do", "Done")

def test_mentions_space_word_boundary():
    assert ta._mentions_space("It lives in the HR space", "HR")
    assert not ta._mentions_space("data flows through the pipeline", "HR")

def test_negative_helpers():
    assert ta._no_issue_key("no matching issue was found")
    assert not ta._no_issue_key("the closest is OPS-142")
    assert ta._no_page_id("nothing matched")
    assert not ta._no_page_id("maybe page 10001")
    assert ta._says_none("that issue does not exist")
    assert not ta._says_none("the status is Done")
    assert ta._no_status("I could not find that issue")
    assert not ta._no_status("it is In Review")
