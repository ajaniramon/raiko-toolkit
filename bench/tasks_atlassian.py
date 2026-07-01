"""Batería Atlassian (Jira + Confluence + Vault mockeados) para el tier Advanced.

Cada task lleva setup="atlassian": el runner reconstruye mocks frescos por task,
inyecta el dispatch y pasa un AtlasCtx como grader_ctx. Los checks tienen firma
check(answer, ctx).
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tasks import contains, has_number      # helpers de grading reutilizados
from tasks_advanced import rf                # lectura de ficheros del sandbox

CATEGORIES = {"jira_search", "conf_search", "jira_read", "conf_read",
              "jira_write", "conf_write", "chain", "vault_gated"}


def build_atlassian_tasks():
    tasks = []

    def add(id, category, difficulty, prompt, expect, check):
        tasks.append({"id": id, "category": category, "difficulty": difficulty,
                      "prompt": prompt, "expect_tools": expect, "check": check,
                      "negative": False, "setup": "atlassian"})

    # ---------------- Jira: búsqueda / recall ----------------
    add("js_needle", "jira_search", "easy",
        "Use jira_search to find the issue that mentions the token 'NEBULA7788' and report its key.",
        ["jira_search"], lambda a, c: contains(a, "OPS-777"))
    add("js_outage_owner", "jira_search", "medium",
        "Search Jira for the database outage incident in the OPS project and report its issue key.",
        ["jira_search"], lambda a, c: contains(a, "OPS-"))
    add("js_count_inprogress_web", "jira_search", "hard",
        "Using jira_search with JQL, how many WEB issues are In Progress? Answer with a number.",
        ["jira_search"],
        lambda a, c: has_number(a, sum(1 for i in _all_issues(c)
                                       if i["project"] == "WEB" and i["status"] == "In Progress")))

    # Generador de recall por needle-topics (cada proyecto tiene topics buscables):
    for idx, (proj, kw) in enumerate([
        ("OPS", "certificate renewal"), ("WEB", "checkout timeout"),
        ("DATA", "duplicate rows"), ("OPS", "kubernetes pod crashloop"),
        ("WEB", "slow search endpoint"), ("DATA", "schema drift"),
    ]):
        add(f"js_recall_{idx}", "jira_search", "medium",
            f"Search Jira for the issue about '{kw}' and report its key.",
            ["jira_search"], (lambda p: lambda a, c: contains(a, p + "-"))(proj))

    # ---------------- Confluence: búsqueda ----------------
    add("cs_playbook", "conf_search", "easy",
        "Use confluence_search to find the page titled 'Outage Playbook' and report its id.",
        ["confluence_search"], lambda a, c: contains(a, "10001"))
    add("cs_space_filter", "conf_search", "medium",
        "Search Confluence in the RUNBOOKS space for the page about backup recovery and report its title.",
        ["confluence_search"], lambda a, c: contains(a, "Backup Recovery"))

    # ---------------- Jira: lectura / extracción ----------------
    add("jr_assignee", "jira_read", "easy",
        "Use jira_get to open OPS-100 and report who it is assigned to.",
        ["jira_get"],
        lambda a, c: contains(a, (c.jira.issue("OPS-100")["assignee"] or "unassigned").split("@")[0]))
    add("jr_status", "jira_read", "easy",
        "Open WEB-100 with jira_get and report its status.",
        ["jira_get"], lambda a, c: contains(a, c.jira.issue("WEB-100")["status"]))

    # ---------------- Confluence: lectura / extracción ----------------
    add("cr_port", "conf_read", "medium",
        "Open the 'Outage Playbook' page and report the port number to restart the db replica on.",
        ["confluence_get"], lambda a, c: has_number(a, 5432))
    add("cr_pto_days", "conf_read", "medium",
        "Read the 'PTO Policy' page and report how many PTO days employees accrue per year.",
        ["confluence_get"], lambda a, c: has_number(a, 25))
    add("cr_deploy_owner", "conf_read", "medium",
        "Open the 'Deployment Guide' and report the name of the rollout owner.",
        ["confluence_get"], lambda a, c: contains(a, "Bob Lee"))

    # ---------------- Escrituras atómicas ----------------
    add("jw_assign", "jira_write", "medium",
        "Assign issue WEB-100 to dan@raiko.dev using jira_assign.",
        ["jira_assign"], lambda a, c: c.jira.issue("WEB-100")["assignee"] == "dan@raiko.dev")
    add("jw_comment", "jira_write", "medium",
        "Add a comment with the exact text 'root cause identified' to OPS-100.",
        ["jira_comment"],
        lambda a, c: any("root cause identified" in x for x in c.jira.issue("OPS-100")["comments"]))
    add("cw_create", "conf_write", "medium",
        "Create a Confluence page in the ENG space titled 'Release Notes 2026-07' "
        "with a body describing the release. ",
        ["confluence_create"], lambda a, c: c.conf.page_by_title("Release Notes 2026-07") is not None)
    add("cw_comment", "conf_write", "medium",
        "Add a comment 'reviewed and approved' to the page with id 10002.",
        ["confluence_comment"],
        lambda a, c: any("reviewed and approved" in x for x in c.conf.comments("10002")))

    # ---------------- Cadenas multitool cross-tool ----------------
    add("ch_incident_to_page", "chain", "hard",
        "Find the Jira incident that mentions 'NEBULA7788', open the Confluence runbook it links to, "
        "and create a Confluence page in ENG titled 'Postmortem OPS-777' whose body includes the "
        "on-call owner named in that runbook.",
        ["jira_search", "confluence_get", "confluence_create"],
        lambda a, c: (c.conf.page_by_title("Postmortem OPS-777") is not None
                      and "Alice Ng" in (c.conf.page_by_title("Postmortem OPS-777") or {}).get("body", "")))
    add("ch_runbook_to_file", "chain", "hard",
        "Open the 'Outage Playbook' page, find the port to restart the replica on, and write that "
        "number to a local file named 'port.txt'.",
        ["confluence_get", "write_file"],
        lambda a, c: "5432" in (rf(c.root, "port.txt") or ""))
    add("ch_assign_from_page", "chain", "hard",
        "Read the 'Outage Playbook' to find the on-call owner, then assign OPS-100 to that person "
        "(their email is <firstname>@raiko.dev).",
        ["confluence_get", "jira_assign"],
        lambda a, c: c.jira.issue("OPS-100")["assignee"] == "alice@raiko.dev")

    # ---------------- Vault-gated (~10) ----------------
    add("vg_assign_oncall", "vault_gated", "hard",
        "Read the Vault secret at 'secret/data/oncall'. It contains an 'issue' key and an 'assignee'. "
        "Assign that issue to that assignee in Jira.",
        ["vault_get_secret", "jira_assign"],
        lambda a, c: c.jira.issue("OPS-777")["assignee"] == "alice@raiko.dev")
    add("vg_comment_deploy", "vault_gated", "hard",
        "Read 'secret/data/deploy' from Vault; it has an 'issue'. Add the comment 'deploy verified' "
        "to that Jira issue.",
        ["vault_get_secret", "jira_comment"],
        lambda a, c: any("deploy verified" in x for x in c.jira.issue("WEB-100")["comments"]))
    add("vg_comment_etl", "vault_gated", "hard",
        "Read the Vault secret at 'secret/data/etl', which has an 'issue'. Add the comment "
        "'pipeline verified' to that Jira issue.",
        ["vault_get_secret", "jira_comment"],
        lambda a, c: any("pipeline verified" in x for x in c.jira.issue("DATA-100")["comments"]))
    add("vg_comment_backup", "vault_gated", "hard",
        "Read the Vault secret at 'secret/data/backup', which has an 'issue'. Add the comment "
        "'backup restored' to that Jira issue.",
        ["vault_get_secret", "jira_comment"],
        lambda a, c: any("backup restored" in x for x in c.jira.issue("OPS-104")["comments"]))
    add("vg_comment_web_owner", "vault_gated", "hard",
        "Read the Vault secret at 'secret/data/web-owner', which has an 'issue'. Add the comment "
        "'owner notified' to that Jira issue.",
        ["vault_get_secret", "jira_comment"],
        lambda a, c: any("owner notified" in x for x in c.jira.issue("WEB-101")["comments"]))

    # ---- Generadores para llegar a la distribución objetivo ----
    _extend_generated(add)
    return tasks


def _all_issues(ctx):
    return list(ctx.jira._issues.values())


def _extend_generated(add):
    import fixtures_atlassian as fx
    issues = fx.build_jira_seed()
    vault = fx.build_vault_seed()

    # Recall: para 45 issues variados, pedir su key por su summary exacto.
    picks = [i for i in issues if i["key"] != "OPS-777"][::5][:25]
    for i in picks:
        key = i["key"]; summ = i["summary"]
        add(f"gjs_{key}", "jira_search", "easy",
            f"Search Jira for the issue whose summary is '{summ}' and report its key.",
            ["jira_search"], (lambda k: lambda a, c: contains(a, k))(key))

    # Lectura: para 35 issues, reportar el status via jira_get.
    for i in [x for x in issues if x["key"] != "OPS-777"][::6][:20]:
        key = i["key"]
        add(f"gjr_{key}", "jira_read", "easy",
            f"Use jira_get to open {key} and report its status.",
            ["jira_get"], (lambda k: lambda a, c: contains(a, c.jira.issue(k)["status"]))(key))

    # Escritura: para 35 issues, asignar a un usuario fijo y verificar el store.
    targets = ["alice@raiko.dev", "bob@raiko.dev", "carol@raiko.dev", "dan@raiko.dev", "erin@raiko.dev"]
    for n, i in enumerate([x for x in issues if x["key"] != "OPS-777"][::7][:20]):
        key = i["key"]; who = targets[n % len(targets)]
        add(f"gjw_{key}", "jira_write", "medium",
            f"Assign Jira issue {key} to {who}.",
            ["jira_assign"], (lambda k, w: lambda a, c: c.jira.issue(k)["assignee"] == w)(key, who))

    # Chains vault-gated adicionales: assign desde cada secreto que tenga issue+assignee.
    for path, data in vault.items():
        if "issue" in data and "assignee" in data:
            iss, who = data["issue"], data["assignee"]
            add(f"gvg_{iss}", "vault_gated", "hard",
                f"Read the Vault secret at '{path}', which has 'issue' and 'assignee'. "
                f"Assign that issue to that assignee in Jira.",
                ["vault_get_secret", "jira_assign"],
                (lambda k, w: lambda a, c: c.jira.issue(k)["assignee"] == w)(iss, who))

    pages = fx.build_confluence_seed()
    clean_pages = [p for p in pages if "—" not in p["title"]]   # 12 páginas de título limpio
    _OVERRIDE = {"Outage Playbook", "Deployment Guide", "PTO Policy"}
    generic_pages = [p for p in clean_pages if p["title"] not in _OVERRIDE]

    def _email(name):
        return name.split()[0].lower() + "@raiko.dev"

    # conf_search: buscar página por la palabra más larga de su título y reportar su id.
    for p in clean_pages:
        kw = max(p["title"].split(), key=len)
        add(f"gcs_{p['id']}", "conf_search", "medium",
            f"Search Confluence for the page about '{kw}' and report its page id.",
            ["confluence_search"], (lambda i: lambda a, c: contains(a, i))(p["id"]))

    # conf_read: abrir página por título y reportar su espacio.
    for p in clean_pages:
        add(f"gcr_{p['id']}", "conf_read", "easy",
            f"Use confluence_get to open the page titled '{p['title']}' and report which space it is in.",
            ["confluence_get"], (lambda s: lambda a, c: contains(a, s))(p["space"]))

    # chain A: jira_search -> write_file (escribe la key encontrada en un archivo).
    for i in [x for x in issues if x["key"] != "OPS-777"][::4][:16]:
        key, summ = i["key"], i["summary"]
        add(f"gch_a_{key}", "chain", "hard",
            f"Find the Jira issue whose summary is '{summ}', then write ONLY its issue key "
            f"to a file named 'found.txt'.",
            ["jira_search", "write_file"],
            (lambda k: lambda a, c: k in (rf(c.root, "found.txt") or ""))(key))

    # chain B: confluence_get -> write_file (escribe el space de la página en un archivo).
    for p in clean_pages:
        add(f"gch_b_{p['id']}", "chain", "hard",
            f"Open the Confluence page titled '{p['title']}' and write the space key it belongs to "
            f"into a file named 'space.txt'.",
            ["confluence_get", "write_file"],
            (lambda s: lambda a, c: s in (rf(c.root, "space.txt") or ""))(p["space"]))

    # chain C: confluence_get -> jira_assign (owner de la página -> asignar un issue a ese owner).
    assign_issues = [x for x in issues if x["key"] != "OPS-777"][2::9][:12]
    for n, i in enumerate(assign_issues):
        p = generic_pages[n % len(generic_pages)]
        key, mail = i["key"], _email(p["creator"])
        add(f"gch_c_{key}", "chain", "hard",
            f"Read the Confluence page titled '{p['title']}' to find its owner, then assign Jira "
            f"issue {key} to that person (their email is <firstname>@raiko.dev, all lowercase).",
            ["confluence_get", "jira_assign"],
            (lambda k, e: lambda a, c: c.jira.issue(k)["assignee"] == e)(key, mail))
