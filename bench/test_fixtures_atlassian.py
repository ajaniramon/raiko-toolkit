import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fixtures_atlassian as fx

def test_jira_seed_size_and_keys():
    issues = fx.build_jira_seed()
    assert 110 <= len(issues) <= 145   # incluye needle + clústeres de casi-duplicados de HARD
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

def test_decoy_parts_never_name_users():
    """Lint anti-premisa-falsa: las páginas decoy '— part N' no pueden nombrar a ningún
    usuario. Con el antiguo 'Owner: {creator}' rotatorio el corpus fabricaba páginas que
    CONFIRMABAN las premisas falsas de F5/F5c ('Outage Playbook — part 8 ... Owner: Dan
    Poe'), castigando a los modelos que sí verificaban contra la fuente."""
    names = [u["name"] for u in fx.USERS]
    for p in fx.build_confluence_seed():
        if "— part" in p["title"]:
            for name in names:
                assert name not in p["body"], f"decoy '{p['title']}' nombra a {name}"


def test_false_premises_unsupported_by_corpus():
    """Las entidades premisadas en falso por las tasks F5b/F5c no pueden tener respaldo
    en NINGUNA página que comparta título con la fuente canónica."""
    for p in fx.build_confluence_seed():
        t = p["title"]
        if t.startswith("Outage Playbook") or t.startswith("PTO Policy"):
            assert "Dan Poe" not in p["body"], t          # fpc_confirm_oncall / fa_pto_contact
        if t.startswith("Deployment Guide") and t != "Deployment Guide":
            assert "Alice Ng" not in p["body"], t         # fpc_deploy_owner / fa_deploy_owner
        if t.startswith("Scaling Guide"):
            assert "20 replicas" not in p["body"], t      # fpc_confirm_replicas


def test_vault_seed_points_to_real_entities():
    issues = {i["key"] for i in fx.build_jira_seed()}
    vault = fx.build_vault_seed()
    assert 8 <= len(vault) <= 12
    # al menos un secreto referencia una key de issue existente
    referenced = [v.get("issue") for v in vault.values() if v.get("issue")]
    assert referenced and all(k in issues for k in referenced)
