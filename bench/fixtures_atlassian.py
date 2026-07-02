"""Semilla determinista para la batería Atlassian del tier Advanced.

Sin random ni datetime.now(): todo se deriva de índices y listas literales,
para que build_*_seed() sea reproducible byte a byte entre corridas.
"""

USERS = [
    {"name": "Alice Ng",   "email": "alice@raiko.dev", "accountId": "acc-alice"},
    {"name": "Bob Lee",    "email": "bob@raiko.dev",   "accountId": "acc-bob"},
    {"name": "Carol Diaz", "email": "carol@raiko.dev", "accountId": "acc-carol"},
    {"name": "Dan Poe",    "email": "dan@raiko.dev",   "accountId": "acc-dan"},
    {"name": "Erin Fox",   "email": "erin@raiko.dev",  "accountId": "acc-erin"},
]
_EMAILS = [u["email"] for u in USERS]

_PROJECTS = ["OPS", "WEB", "DATA"]
_TYPES = ["Bug", "Task", "Story", "Incident"]
_STATUSES = ["To Do", "In Progress", "In Review", "Done", "Blocked"]

# Frases base por proyecto para dar variedad de vocabulario (y solapes buscables).
_TOPICS = {
    "OPS": ["database outage", "disk pressure on the node", "certificate renewal",
            "backup job failure", "kubernetes pod crashloop", "on-call rotation"],
    "WEB": ["login button misaligned", "checkout timeout", "broken image on the landing page",
            "slow search endpoint", "cookie banner regression", "dark mode contrast"],
    "DATA": ["ETL pipeline stalled", "duplicate rows in the warehouse", "schema drift in events",
             "dashboard shows stale numbers", "late-arriving partition", "PII masking gap"],
}


def build_jira_seed():
    issues = []
    seq = 0
    for pi, project in enumerate(_PROJECTS):
        topics = _TOPICS[project]
        for k in range(40):  # 40 por proyecto -> 120 total
            seq += 1
            topic = topics[k % len(topics)]
            typ = _TYPES[k % len(_TYPES)]
            status = _STATUSES[k % len(_STATUSES)]
            assignee = _EMAILS[k % len(_EMAILS)] if k % 3 else None
            reporter = _EMAILS[(k + 1) % len(_EMAILS)]
            num = 100 + k
            issues.append({
                "key": f"{project}-{num}", "project": project, "type": typ,
                "status": status, "assignee": assignee, "reporter": reporter,
                "summary": f"{topic} (batch {k})",
                "description": f"Issue about {topic} in {project}. Steps to reproduce documented.",
                "comments": [f"triaged by {reporter}"] if k % 2 == 0 else [],
                "links": [], "seq": seq,
            })
    # Needle con token único (para test de recall de búsqueda exacta):
    seq += 1
    issues.append({
        "key": "OPS-777", "project": "OPS", "type": "Incident", "status": "Blocked",
        "assignee": None, "reporter": "alice@raiko.dev",
        "summary": "NEBULA7788 payload corruption incident",
        "description": "Unique token NEBULA7788. The Outage Playbook covers the recovery.",
        "comments": [], "links": ["Outage Playbook"], "seq": seq,
    })
    return issues


# F2 (desambiguación) fue jubilada — saturaba y no discriminaba. Se retiraron sus clústeres
# de casi-duplicados de la semilla. HARD_ONLY_KEYS queda vacío (el floor no excluye jira).
HARD_ONLY_KEYS = []


def build_confluence_seed():
    pages = []
    pid = 10000
    spaces = {
        "RUNBOOKS": ["Outage Playbook", "Backup Recovery", "On-call Handbook", "Incident Severity"],
        "ENG":      ["Deployment Guide", "Service Catalog", "API Style Guide", "Postmortem Index"],
        "HR":       ["Onboarding Checklist", "PTO Policy", "Expense Policy", "Org Chart"],
    }
    # Datos extraíbles concretos por página "clave" (para tasks de extracción):
    bodies = {
        "Outage Playbook": "On-call owner is Alice Ng. Restart the primary db replica on port 5432. "
                           "Escalate to Bob Lee after 30 minutes.",
        "Deployment Guide": "Deploy with the blue-green strategy. The rollout owner is Bob Lee. "
                            "Health check path is /healthz on port 8080.",
        "PTO Policy": "Employees accrue 25 days of PTO per year. Requests go to Carol Diaz.",
    }
    for space, titles in spaces.items():
        for k in range(27):  # 27*3 = 81 páginas
            pid += 1
            base_title = titles[k % len(titles)]
            title = base_title if k < len(titles) else f"{base_title} — part {k}"
            creator = USERS[k % len(USERS)]["name"]
            editor = USERS[(k + 2) % len(USERS)]["name"]
            if title in bodies:
                body = bodies[title]
            elif k < len(titles):
                body = (f"{title}: reference notes for the {space} space. "
                        f"Owner: {creator}. See related runbooks.")
            else:
                # Las páginas decoy "— part N" NUNCA nombran usuarios: con el antiguo
                # "Owner: {creator}" rotatorio el corpus fabricaba páginas que CONFIRMABAN
                # premisas falsas de F5/F5c (p. ej. 'Outage Playbook — part 8 ... Owner:
                # Dan Poe'), castigando a los modelos que sí verificaban.
                body = (f"{title}: archived reference notes for the {space} space. "
                        f"See the main '{base_title}' page for current guidance.")
            links = ["OPS-777"] if title == "Outage Playbook" else []
            pages.append({
                "id": str(pid), "space": space, "title": title, "body": body,
                "labels": [space.lower()], "ancestors": [titles[0]] if k else [],
                "creator": creator, "created": f"2026-0{1 + (k % 6)}-01",
                "editor": editor, "edited": f"2026-0{1 + ((k + 1) % 6)}-15",
                "version": 1 + (k % 5), "links": links,
            })
    # Página larga (>12k chars) para ejercitar la paginación de confluence_get:
    # el codeword va al final, fuera de la primera ventana de 12000 chars.
    long_body = ("Runbook operational notes. " * 620) + \
                "\nFINAL LINE: the codeword is OMEGA-CODEWORD-42."
    pages.append({
        "id": "10099", "space": "RUNBOOKS", "title": "Long Runbook Archive",
        "body": long_body, "labels": ["runbook", "archive"], "ancestors": ["Outage Playbook"],
        "creator": "Alice Ng", "created": "2026-01-01",
        "editor": "Alice Ng", "edited": "2026-06-30", "version": 9, "links": [],
    })
    # Pares en conflicto (F3): dos páginas discrepan en un valor; la fecha de "verificado"
    # va en el CUERPO (confluence_get muestra el body) para poder razonar cuál es más reciente.
    conflicts = [
        # (id, title, space, valor, fecha_verificado)
        ("10101", "Primary DB Runbook", "RUNBOOKS", "the DB port is 5432", "2026-06-30"),
        ("10102", "Legacy DB Notes",    "RUNBOOKS", "the DB port is 5433", "2026-01-15"),
        ("10103", "Scaling Guide",      "ENG",      "run 8 replicas",      "2026-05-20"),
        ("10104", "Old Capacity Plan",  "ENG",      "run 4 replicas",      "2026-02-10"),
        ("10105", "Gateway SLA",        "ENG",      "the gateway timeout is 30 seconds", "2026-06-01"),
        ("10106", "Draft Timeout Spec", "ENG",      "the gateway timeout is 60 seconds", "2026-03-01"),
    ]
    for cid, title, space, value, verified in conflicts:
        pages.append({
            "id": cid, "space": space, "title": title,
            "body": f"{title}: {value}. Last verified {verified}.",
            "labels": [space.lower(), "config"], "ancestors": [], "creator": "Bob Lee",
            "created": "2026-01-01", "editor": "Bob Lee", "edited": verified,
            "version": 2, "links": [],
        })
    return pages


def build_vault_seed():
    # Secretos que GATEAN acciones Atlassian: devuelven una key/email/page-id reales.
    return {
        "secret/data/oncall":   {"issue": "OPS-777", "assignee": "alice@raiko.dev"},
        "secret/data/deploy":   {"issue": "WEB-100", "owner": "bob@raiko.dev"},
        "secret/data/etl":      {"issue": "DATA-100", "assignee": "carol@raiko.dev"},
        "secret/data/runbook":  {"page_id": "10001", "space": "RUNBOOKS"},
        "secret/data/postmortem": {"space": "ENG", "reporter": "dan@raiko.dev"},
        "secret/data/backup":   {"issue": "OPS-104", "assignee": "dan@raiko.dev"},
        "secret/data/web-owner": {"assignee": "erin@raiko.dev", "issue": "WEB-101"},
        "secret/data/data-owner": {"assignee": "bob@raiko.dev", "issue": "DATA-101"},
        "secret/data/sev":      {"page_id": "10004", "space": "RUNBOOKS"},
        "secret/data/hr":       {"space": "HR", "owner": "carol@raiko.dev"},
    }
