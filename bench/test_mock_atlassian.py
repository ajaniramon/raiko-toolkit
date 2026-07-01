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

def test_search_jql_issuetype_alias():
    # JQL real de Jira usa 'issuetype'; el mock debe aceptarlo como alias de 'type'.
    j = MockJira(ISSUES)
    out = j.search(jql='issuetype = "Bug"')
    assert "WEB-2" in out and "OPS-1" not in out   # WEB-2 es Bug, OPS-1 es Incident

def test_search_jql_in_operator():
    j = MockJira(ISSUES)
    out = j.search(jql='status in ("In Progress", "Done")')
    assert "OPS-1" in out and "WEB-2" in out   # Done + In Progress both matched
    out2 = j.search(jql='assignee in ("alice@raiko.dev")')
    assert "OPS-1" in out2 and "WEB-2" not in out2

def test_assign_resolves_display_name_to_email():
    users = [{"name": "Alice Ng", "email": "alice@raiko.dev", "accountId": "acc-alice"}]
    j = MockJira(ISSUES, users)
    j.assign("WEB-2", "Alice Ng")
    assert j.issue("WEB-2")["assignee"] == "alice@raiko.dev"

def test_assign_email_unchanged_and_no_users_ok():
    j = MockJira(ISSUES)          # sin tabla de usuarios
    j.assign("WEB-2", "dan@raiko.dev")
    assert j.issue("WEB-2")["assignee"] == "dan@raiko.dev"


from mock_atlassian import MockConfluence

PAGES = [
    {"id": "10001", "space": "RUNBOOKS", "title": "Outage Playbook",
     "body": "On-call owner is Alice Ng. Restart the replica on port 5432.",
     "labels": ["runbook"], "ancestors": ["Runbooks Home"],
     "creator": "Alice Ng", "created": "2026-03-01", "editor": "Bob Lee",
     "edited": "2026-05-02", "version": 3, "links": ["OPS-1"]},
    {"id": "10002", "space": "ENG", "title": "Deployment Guide",
     "body": "Deploy with the blue-green strategy. Contact Bob Lee.",
     "labels": ["eng"], "ancestors": [], "creator": "Bob Lee", "created": "2026-02-10",
     "editor": "Bob Lee", "edited": "2026-04-01", "version": 1, "links": []},
]
USERS_T = [{"name": "Alice Ng", "email": "alice@raiko.dev", "accountId": "acc-alice"},
           {"name": "Bob Lee", "email": "bob@raiko.dev", "accountId": "acc-bob"}]

def test_conf_search_text():
    c = MockConfluence(PAGES, USERS_T)
    out = c.search(query="playbook")
    assert "10001" in out and "Outage Playbook" in out

def test_conf_search_space_filter():
    c = MockConfluence(PAGES, USERS_T)
    out = c.search(query="deploy", space="RUNBOOKS")
    assert "No pages matched" in out

def test_conf_get_body():
    c = MockConfluence(PAGES, USERS_T)
    out = c.get(page_id="10001")
    assert "On-call owner is Alice Ng" in out and "port 5432" in out

def test_conf_get_by_title():
    c = MockConfluence(PAGES, USERS_T)
    out = c.get(title="Deployment Guide")
    assert "blue-green" in out

def test_conf_user():
    c = MockConfluence(PAGES, USERS_T)
    out = c.user("Alice")
    assert "acc-alice" in out and "Alice Ng" in out

def test_conf_search_author_only():
    c = MockConfluence(PAGES, USERS_T)
    out = c.search(author="Alice")          # solo autor, sin query/space
    assert "10001" in out and "10002" not in out   # 10001 es de Alice Ng, 10002 de Bob Lee
    assert c.search(author="Zoe Nobody").startswith("No Confluence user matched")

def test_conf_create_mutates():
    c = MockConfluence(PAGES, USERS_T)
    out = c.create(space="ENG", title="Postmortem OPS-1", body="root cause: bad deploy")
    assert "Created page" in out
    p = c.page_by_title("Postmortem OPS-1")
    assert p is not None and "root cause" in p["body"] and p["space"] == "ENG"

def test_conf_comment_mutates():
    c = MockConfluence(PAGES, USERS_T)
    c.comment(page_id="10001", body="resolved on 2026-05-03")
    assert any("resolved" in x for x in c.comments("10001"))

def test_conf_search_cql_in_operator():
    c = MockConfluence(PAGES, USERS_T)
    out = c.search(cql='space in ("RUNBOOKS", "ENG")')
    assert "10001" in out and "10002" in out


from mock_atlassian import MockVault, build_atlas_impls, AtlasCtx

def test_vault_get_returns_json():
    import json
    v = MockVault({"secret/data/mac": {"host": "h", "port": "22"}})
    out = v.get("secret/data/mac")
    assert json.loads(out) == {"host": "h", "port": "22"}

def test_vault_unknown_path():
    v = MockVault({})
    assert "ERROR" in v.get("secret/data/nope")

def test_build_atlas_impls_keys():
    j = MockJira(ISSUES); c = MockConfluence(PAGES, USERS_T); v = MockVault({})
    impls = build_atlas_impls(j, c, v)
    assert set(impls) == {
        "jira_search", "jira_get", "jira_assign", "jira_comment",
        "confluence_search", "confluence_user", "confluence_get",
        "confluence_create", "confluence_comment", "vault_get_secret",
    }

def test_atlas_ctx_holds_stores():
    j = MockJira(ISSUES); c = MockConfluence(PAGES, USERS_T); v = MockVault({})
    ctx = AtlasCtx(j, c, v, "/tmp/x")
    assert ctx.jira is j and ctx.conf is c and ctx.vault is v and ctx.root == "/tmp/x"
