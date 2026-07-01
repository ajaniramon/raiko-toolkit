import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fixtures_atlassian as fx

def test_jira_seed_size_and_keys():
    issues = fx.build_jira_seed()
    assert 110 <= len(issues) <= 130
    projects = {i["project"] for i in issues}
    assert projects == {"OPS", "WEB", "DATA"}
    seqs = [i["seq"] for i in issues]
    assert len(seqs) == len(set(seqs))  # seq únicos

def test_jira_seed_deterministic():
    assert fx.build_jira_seed() == fx.build_jira_seed()

def test_conf_seed_size_and_spaces():
    pages = fx.build_confluence_seed()
    assert 70 <= len(pages) <= 90
    assert {p["space"] for p in pages} == {"ENG", "RUNBOOKS", "HR"}
    ids = [p["id"] for p in pages]
    assert len(ids) == len(set(ids))

def test_conf_seed_deterministic():
    assert fx.build_confluence_seed() == fx.build_confluence_seed()

def test_long_page_for_pagination():
    pages = fx.build_confluence_seed()
    lp = [p for p in pages if p["title"] == "Long Runbook Archive"]
    assert len(lp) == 1
    p = lp[0]
    assert len(p["body"]) > 12000                      # fuerza paginación en confluence_get
    assert "OMEGA-CODEWORD-42" in p["body"]
    assert p["body"].index("OMEGA-CODEWORD-42") > 12000  # el codeword va tras la 1ª ventana

def test_needle_issue_present():
    issues = fx.build_jira_seed()
    needles = [i for i in issues if "NEBULA7788" in (i["summary"] + i["description"])]
    assert len(needles) == 1

def test_cross_link_outage_playbook():
    issues = fx.build_jira_seed()
    pages = fx.build_confluence_seed()
    assert any("Outage Playbook" in i["links"] for i in issues)
    assert any(p["title"] == "Outage Playbook" for p in pages)

def test_vault_seed_points_to_real_entities():
    issues = {i["key"] for i in fx.build_jira_seed()}
    vault = fx.build_vault_seed()
    assert 8 <= len(vault) <= 12
    # al menos un secreto referencia una key de issue existente
    referenced = [v.get("issue") for v in vault.values() if v.get("issue")]
    assert referenced and all(k in issues for k in referenced)
