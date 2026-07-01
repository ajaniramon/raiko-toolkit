# bench/test_mock_atlassian.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mock_atlassian import MockJira

ISSUES = [
    {"key": "OPS-1", "project": "OPS", "type": "Incident", "status": "Done",
     "assignee": "alice@raiko.dev", "reporter": "bob@raiko.dev",
     "summary": "database outage in production", "description": "the primary db went down",
     "comments": ["restarted the replica"], "links": ["Outage Playbook"], "seq": 1},
    {"key": "WEB-2", "project": "WEB", "type": "Bug", "status": "In Progress",
     "assignee": None, "reporter": "carol@raiko.dev",
     "summary": "login button misaligned", "description": "css regression on mobile",
     "comments": [], "links": [], "seq": 2},
]

def test_search_by_text_returns_matching_key():
    j = MockJira(ISSUES)
    out = j.search(query="outage")
    assert "OPS-1" in out
    assert "WEB-2" not in out

def test_search_newest_first():
    j = MockJira(ISSUES)
    # JQL that matches both issues; verifies ordering (seq DESC) independent of
    # the free-text word-length fallback (see NOTE below).
    out = j.search(jql='project = "OPS" OR project = "WEB"')
    lines = [l for l in out.splitlines() if l.strip()]
    assert lines[0].startswith("WEB-2")  # seq 2 primero

def test_search_project_filter():
    j = MockJira(ISSUES)
    out = j.search(query="regression", project="OPS")
    assert "No issues matched" in out

def test_search_jql_status():
    j = MockJira(ISSUES)
    out = j.search(jql='status = "In Progress"')
    assert "WEB-2" in out and "OPS-1" not in out

def test_get_shows_fields():
    j = MockJira(ISSUES)
    out = j.get("OPS-1")
    assert "database outage" in out and "alice@raiko.dev" in out and "Done" in out

def test_get_unknown_key():
    j = MockJira(ISSUES)
    assert "no details" in j.get("ZZ-9").lower() or "not found" in j.get("ZZ-9").lower()

def test_assign_mutates():
    j = MockJira(ISSUES)
    j.assign("WEB-2", "dan@raiko.dev")
    assert j.issue("WEB-2")["assignee"] == "dan@raiko.dev"

def test_comment_mutates():
    j = MockJira(ISSUES)
    j.comment("WEB-2", "fixed the CSS")
    assert "fixed the CSS" in j.issue("WEB-2")["comments"]
