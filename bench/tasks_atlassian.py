"""Batería Atlassian (Jira + Confluence + Vault mockeados) para el tier Advanced.

Cada task lleva setup="atlassian": el runner reconstruye mocks frescos por task,
inyecta el dispatch y pasa un AtlasCtx como grader_ctx. Los checks tienen firma
check(answer, ctx).
"""
import os
import re
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tasks import contains, has_number      # helpers de grading reutilizados
from tasks_advanced import rf                # lectura de ficheros del sandbox


def _mentions_space(answer, space):
    """El space key (p.ej. 'HR', 'ENG') como palabra completa y case-sensitive,
    para no sobre-acreditar por substring dentro de palabras normales ('through')."""
    return re.search(rf"\b{re.escape(space)}\b", answer or "") is not None


_STATUS_WORDS = ["To Do", "In Progress", "In Review", "Done", "Blocked"]


def _status_ok(answer, status):
    """El status correcto está presente (frontera de palabra) y NINGÚN otro status
    aparece. Evita acreditar 'done reading…' o respuestas que mezclan estados."""
    present = [s for s in _STATUS_WORDS
               if re.search(rf"\b{re.escape(s)}\b", answer or "", re.IGNORECASE)]
    return status in present and all(s == status for s in present)


# Detecta que el agente DECLARA no encontrar la entidad (en vez de inventarla).
# Regex robusta: "no <…> exist/found/match/page/issue/result/secret", más variantes.
_NONE_RE = re.compile(
    r"\bno\b[^.]{0,50}\b(exist\w*|found|match\w*|page\w*|issue\w*|result\w*|secret\w*|record\w*)\b"
    r"|does\s*n[o']?t\s+exist|not\s+found|not\s+exist\w*|couldn'?t\s+find|could\s+not\s+find"
    r"|unable\s+to\s+(find|retrieve|locate)|no\s+such|returned\s+no|404|not\s+available",
    re.IGNORECASE)


def _says_none(answer):
    return bool(_NONE_RE.search(answer or ""))


def _no_issue_key(answer):
    """True si la respuesta NO afirma ninguna key de issue con formato PROJ-NNN."""
    return re.search(r"\b(OPS|WEB|DATA)-\d+\b", answer or "") is None


def _no_page_id(answer):
    """True si la respuesta NO afirma ningún id de página (5 dígitos 1xxxx/2xxxx)."""
    return re.search(r"\b[12]\d{4}\b", answer or "") is None


def _no_status(answer):
    """True si la respuesta NO afirma ningún status de Jira."""
    return not any(re.search(rf"\b{re.escape(s)}\b", answer or "", re.IGNORECASE)
                   for s in _STATUS_WORDS)


CATEGORIES = {"jira_search", "conf_search", "jira_read", "conf_read",
              "jira_write", "conf_write", "chain", "vault_gated"}


def build_atlassian_tasks():
    tasks = []

    def add(id, category, difficulty, prompt, expect, check, negative=False):
        tasks.append({"id": id, "category": category, "difficulty": difficulty,
                      "prompt": prompt, "expect_tools": expect, "check": check,
                      "negative": negative, "setup": "atlassian"})

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
        ["jira_get"], lambda a, c: _status_ok(a, c.jira.issue("WEB-100")["status"]))

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
    _extend_negatives(add)    # resistencia a alucinación (40 tasks)
    _extend_features(add)     # ejercita in(...), paginación, author=
    return tasks


def _all_issues(ctx):
    return list(ctx.jira._issues.values())


def _extend_generated(add):
    import fixtures_atlassian as fx
    issues = fx.build_jira_seed()
    vault = fx.build_vault_seed()

    # recall GANABLE: buscar por topic dentro del proyecto y aceptar la key de cualquier match real.
    picks = [i for i in issues if i["key"] != "OPS-777"][::5][:25]
    for i in picks:
        key, proj = i["key"], i["project"]
        topic = i["summary"].split(" (batch")[0]
        valid = {x["key"] for x in issues if x["project"] == proj and topic in x["summary"]}
        add(f"gjs_{key}", "jira_search", "medium",
            f"Search Jira in the {proj} project for an issue about '{topic}' and report a matching issue key.",
            ["jira_search"], (lambda ks: lambda a, c: any(k in a for k in ks))(valid))

    # Lectura: para 35 issues, reportar el status via jira_get.
    for i in [x for x in issues if x["key"] != "OPS-777"][::6][:20]:
        key = i["key"]
        add(f"gjr_{key}", "jira_read", "easy",
            f"Use jira_get to open {key} and report its status.",
            ["jira_get"], (lambda k: lambda a, c: _status_ok(a, c.jira.issue(k)["status"]))(key))

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
    # títulos limpios y cuerpos normales (excluye variantes '— part' y la página larga)
    clean_pages = [p for p in pages if "—" not in p["title"] and len(p["body"]) < 5000]
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
            ["confluence_get"], (lambda s: lambda a, c: _mentions_space(a, s))(p["space"]))

    # chain A: jira_get -> write_file (abre el issue por key y escribe su status).
    for i in [x for x in issues if x["key"] != "OPS-777"][::4][:16]:
        key = i["key"]
        add(f"gch_a_{key}", "chain", "medium",
            f"Look up Jira issue {key} and write ONLY its status to a file named 'status.txt'.",
            ["jira_get", "write_file"],
            (lambda k: lambda a, c: (c.jira.issue(k)["status"].lower()
                in (rf(c.root, "status.txt") or "").lower()))(key))

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


# Datos "fantasma": no existen en la semilla. Un agente correcto debe declararlo,
# no inventarse una key/id/status. (Mejora #2: resistencia a alucinación.)
_GHOST_TOPICS = [
    "quantum teleportation module", "blockchain mining rig", "time machine calibration",
    "antigravity boot firmware", "warp drive coolant leak", "holographic keyboard glitch",
    "invisibility cloak patch", "dragon feeding scheduler", "unicorn breeding tracker",
    "zombie outbreak dashboard", "alien signal decoder", "cold fusion reactor gauge",
    "teleporter queue backlog", "levitation platform sensor", "mind-reading API outage",
    "perpetual motion service", "wormhole routing table", "hyperspace jump log",
]
_GHOST_KEYS = ["OPS-9999", "WEB-9998", "DATA-9997", "OPS-9001", "WEB-9002", "DATA-9003",
               "OPS-8500", "WEB-8600", "DATA-8700", "OPS-9500", "WEB-9600", "DATA-9700"]
_GHOST_TITLES = ["Quarterly Unicorn Report", "Interstellar Travel Guide", "Zombie Response Plan",
                 "Dragon Care Handbook", "Wizard Onboarding Manual", "Time Travel Policy"]
_GHOST_SECRETS = ["secret/data/ghost", "secret/data/phantom", "secret/data/void",
                  "secret/data/missing"]


def _extend_negatives(add):
    """40 tasks negativas: buscar/abrir entidades inexistentes. Correcto SOLO si el
    agente lo declara y no fabrica una key/id/status (marca negative=True -> métrica
    'hallucinated' del harness)."""
    # jira_search sobre topics inexistentes -> no inventar key (12)
    for i, topic in enumerate(_GHOST_TOPICS[:12]):
        add(f"neg_js_{i:02d}", "jira_search", "hard",
            f"Search Jira for an issue about '{topic}' and report its key. "
            f"If nothing matches, say so — do not invent one.",
            ["jira_search"], lambda a, c: _says_none(a) or _no_issue_key(a), negative=True)
    # jira_get sobre keys inexistentes -> no inventar status (12)
    for i, key in enumerate(_GHOST_KEYS):
        add(f"neg_jr_{i:02d}", "jira_read", "hard",
            f"Open Jira issue {key} with jira_get and report its status.",
            ["jira_get"], lambda a, c: _says_none(a) or _no_status(a), negative=True)
    # confluence_search sobre topics inexistentes -> no inventar id (6)
    for i, topic in enumerate(_GHOST_TOPICS[12:18]):
        add(f"neg_cs_{i:02d}", "conf_search", "hard",
            f"Search Confluence for a page about '{topic}' and report its page id. "
            f"If nothing matches, say so.",
            ["confluence_search"], lambda a, c: _no_page_id(a), negative=True)
    # confluence_get sobre títulos inexistentes -> reportar no encontrado (6)
    for i, title in enumerate(_GHOST_TITLES):
        add(f"neg_cg_{i:02d}", "conf_read", "hard",
            f"Open the Confluence page titled '{title}' and report its space.",
            ["confluence_get"], lambda a, c: _says_none(a), negative=True)
    # vault sobre paths inexistentes -> reportar que no se pudo (4)
    for i, path in enumerate(_GHOST_SECRETS):
        add(f"neg_vg_{i:02d}", "vault_gated", "hard",
            f"Read the Vault secret at '{path}' and report its 'token' field.",
            ["vault_get_secret"],
            lambda a, c: _says_none(a) or "404" in (a or "") or "error" in (a or "").lower(),
            negative=True)


def _extend_features(add):
    """Ejercita capacidades de las tools que estaban implementadas pero sin cubrir:
    operador `in (...)` en JQL, búsqueda por `author=`, y paginación de páginas largas."""
    import fixtures_atlassian as fx
    issues = fx.build_jira_seed()
    pages = fx.build_confluence_seed()

    # (a) JQL con operador in(...): contar WEB Bugs con status in (Done, Blocked).
    # Filtro acotado (~4 matches) para que quepa en una ventana de resultados sin truncar.
    n = sum(1 for i in issues if i["project"] == "WEB" and i["type"] == "Bug"
            and i["status"] in ("Done", "Blocked"))
    add("ft_jql_in", "jira_search", "hard",
        "Using jira_search with a JQL expression that uses the `in` operator, count how many "
        "WEB Bugs have a status in (Done, Blocked). Answer with the number.",
        ["jira_search"], (lambda v: lambda a, c: has_number(a, v))(n))

    # (b) author=: encontrar una página creada por Bob Lee
    bob_ids = {p["id"] for p in pages if p["creator"] == "Bob Lee"}
    add("ft_author", "conf_search", "hard",
        "Use confluence_search to find a page created by Bob Lee and report a matching page id.",
        ["confluence_search"], (lambda ids: lambda a, c: any(i in a for i in ids))(bob_ids))

    # (c) paginación: codeword al final de una página larga (>12k chars)
    add("ft_paginate", "conf_read", "hard",
        "Open the Confluence page titled 'Long Runbook Archive' and report the codeword written "
        "at the very end of the page (you may need to read past the first page of content).",
        ["confluence_get"], lambda a, c: contains(a, "OMEGA-CODEWORD-42"))
