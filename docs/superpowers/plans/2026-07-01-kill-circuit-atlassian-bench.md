# Retirar tier Circuit + batería Jira/Confluence mockeada — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminar el tier Circuit y su dependencia de infra, y construir dentro de Advanced una batería determinista (~150 tasks) de Jira/Confluence mockeados in-process + ~10 tasks gated por Vault mockeado.

**Architecture:** Mocks in-process (`MockJira`/`MockConfluence`/`MockVault`) reconstruidos por task desde una semilla determinista. Se inyectan en el arnés vía un `dispatch` por-task, sin tocar las implementaciones ni los schemas de producción en `tools.py` (así medir/mejorar descripciones = editar `tools.py` y re-correr). El grading de writes/chains verifica el efecto sobre el store mock (y el filesystem para chains con terminal de archivo).

**Tech Stack:** Python 3, pytest (dev), el arnés existente (`bench/harness.py`), OpenAI-compatible client.

## Global Constraints

- **Cero dependencias de infra/servidor.** Nada de binarios (Vault), servicios vivos (Jira CLI, Confluence Cloud) ni SSH. Todo mockeado in-process y determinista.
- **Determinismo:** las semillas NO usan `random` ni `datetime.now()`. Fechas y datos son literales o derivados de índices.
- **No tocar `tools.py` salvo:** (a) extender `call_tool` con un parámetro `dispatch` opcional; (b) eliminar `copy_file_to_mac` — que en realidad vive en `run_circuit.py`, no en `tools.py`. Los schemas y descripciones de las tools se conservan intactos.
- **Compatibilidad hacia atrás:** las tasks FS existentes de Advanced (y los tiers base/hard) deben seguir pasando sin cambios; los parámetros nuevos del arnés son opcionales con default = comportamiento actual.
- **Nombres fijos acordados:** proyectos Jira `OPS`, `WEB`, `DATA`; espacios Confluence `ENG`, `RUNBOOKS`, `HR`.
- **Tamaño objetivo:** ~150 tasks Atlassian + ~10 Vault. Cada task lleva `category` y `difficulty`.

---

## File Structure

- **Create** `bench/mock_atlassian.py` — `MockJira`, `MockConfluence`, `MockVault`, `AtlasCtx`, y `build_atlas_impls(jira, conf, vault)`.
- **Create** `bench/fixtures_atlassian.py` — `build_jira_seed()`, `build_confluence_seed()`, `build_vault_seed()`, `USERS`.
- **Create** `bench/tasks_atlassian.py` — `build_atlassian_tasks(jira, conf, vault)`.
- **Create** `bench/test_mock_atlassian.py` — tests unitarios de los mocks.
- **Create** `bench/test_fixtures_atlassian.py` — tests de determinismo de la semilla.
- **Modify** `bench/harness.py` — `run_task(..., dispatch=None, grader_ctx=None)`; usar dispatch por-task; grading con ctx.
- **Modify** `tools.py` — `call_tool(name, arguments, dispatch=None)`.
- **Modify** `bench/run_adv.py` — ramificar setup FS vs Atlassian; exponer tools; report por `category` + `difficulty`.
- **Delete** `bench/run_circuit.py`, `bench/tasks_circuit.py`, `bench/vaultsvc.py`.
- **Modify** `installers.py` — quitar `install_vault` y menciones.
- **Modify** `tui.py` — botón `dl_clis` a solo Jira ("Download Jira CLI").
- **Modify** `requirements.txt` — quitar `paramiko` si queda sin uso; añadir `pytest` como dev-opcional.
- **Modify** `README.md`, `docs/index.html`, `bench/make_charts.py`, `bench/combine_report.py` — quitar menciones al tier Circuit.

### Modelo de datos (contrato compartido entre fixtures, mocks y tasks)

**Jira issue** (dict):
```python
{
  "key": "OPS-101", "project": "OPS", "type": "Bug",       # Bug|Task|Story|Incident
  "status": "In Progress",                                  # To Do|In Progress|In Review|Done|Blocked
  "assignee": "alice@raiko.dev",                            # email o None
  "reporter": "bob@raiko.dev",
  "summary": "…", "description": "…",
  "comments": ["texto", …],
  "links": ["Outage Playbook"],                             # títulos de páginas Confluence
  "seq": 101,                                               # orden de creación (para "newest first")
}
```

**Confluence page** (dict):
```python
{
  "id": "10001", "space": "RUNBOOKS", "title": "Outage Playbook",
  "body": "texto plano con datos extraíbles…",
  "labels": ["runbook", "oncall"],
  "ancestors": ["Runbooks Home"],
  "creator": "Alice Ng", "created": "2026-03-01",
  "editor": "Bob Lee", "edited": "2026-05-02",
  "version": 3, "links": ["OPS-101"],
}
```

**Vault secret**: `{ "secret/data/<name>": {campo: valor, …} }`.

**USERS** (set fijo, en `fixtures_atlassian.py`):
```python
USERS = [
    {"name": "Alice Ng",   "email": "alice@raiko.dev",   "accountId": "acc-alice"},
    {"name": "Bob Lee",    "email": "bob@raiko.dev",     "accountId": "acc-bob"},
    {"name": "Carol Diaz", "email": "carol@raiko.dev",   "accountId": "acc-carol"},
    {"name": "Dan Poe",    "email": "dan@raiko.dev",     "accountId": "acc-dan"},
    {"name": "Erin Fox",   "email": "erin@raiko.dev",    "accountId": "acc-erin"},
]
```

---

### Task 1: Dispatch inyectable + grading por contexto en el arnés

**Files:**
- Modify: `tools.py` (`call_tool`, ~1281-1297)
- Modify: `bench/harness.py` (`run_task` firma ~57-65, call site ~147, grading ~166-176)
- Test: `bench/test_harness_dispatch.py` (create)

**Interfaces:**
- Produces:
  - `tools.call_tool(name: str, arguments: str | dict, dispatch: dict | None = None) -> str` — usa `dispatch` si se pasa, si no el `DISPATCH` global.
  - `harness.run_task(client, model_name, task, root, enable_thinking, tools=None, grader_root=False, system_prompt=None, max_iterations=None, dispatch=None, grader_ctx=None)` — cuando `dispatch` no es None, las tools se ejecutan contra él; cuando `grader_ctx` no es None, el grader se llama `check(answer, grader_ctx)`.

- [ ] **Step 1: Write the failing test**

```python
# bench/test_harness_dispatch.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import call_tool

def test_call_tool_uses_injected_dispatch():
    calls = {}
    def fake(**kw):
        calls.update(kw)
        return "FAKE-OK"
    out = call_tool("jira_search", '{"query": "outage"}', dispatch={"jira_search": fake})
    assert out == "FAKE-OK"
    assert calls == {"query": "outage"}

def test_call_tool_unknown_in_injected_dispatch():
    out = call_tool("nope", "{}", dispatch={"jira_search": lambda **k: "x"})
    assert out.startswith("ERROR: unknown tool")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd bench && python -m pytest test_harness_dispatch.py -v`
Expected: FAIL con `TypeError: call_tool() got an unexpected keyword argument 'dispatch'`.

- [ ] **Step 3: Implement in `tools.py`**

Reemplaza la firma y el cuerpo de `call_tool` (líneas ~1281-1297):

```python
def call_tool(name: str, arguments: str | dict, dispatch: dict | None = None) -> str:
    table = dispatch if dispatch is not None else DISPATCH
    if name not in table:
        return f"ERROR: unknown tool {name}"
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else arguments
    except (json.JSONDecodeError, TypeError) as e:
        return (f"ERROR: invalid JSON in arguments for '{name}' ({e}). "
                f"Re-issue the call with VALID JSON — escape newlines as \\n and quotes as \\\".")
    if not isinstance(args, dict):
        return f"ERROR: arguments for '{name}' must be a JSON object."
    try:
        result = table[name](**args)
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"
    return result if isinstance(result, str) else json.dumps(result)
```

- [ ] **Step 4: Run the dispatch test to verify it passes**

Run: `cd bench && python -m pytest test_harness_dispatch.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Extend `run_task` in `bench/harness.py`**

Firma (líneas ~57-58):

```python
def run_task(client, model_name, task, root, enable_thinking, tools=None,
             grader_root=False, system_prompt=None, max_iterations=None,
             dispatch=None, grader_ctx=None):
```

Call site (línea ~147) — pasar el dispatch por-task:

```python
            result = call_tool(name, raw_args, dispatch=dispatch)
```

Grading (líneas ~166-173) — añadir la rama de contexto:

```python
    try:
        if grader_ctx is not None:
            correct = bool(task["check"](answer or "", grader_ctx))
        elif grader_root:
            correct = bool(task["check"](answer or "", root))
        else:
            correct = bool(task["check"](answer or ""))
    except Exception:
        correct = False
```

- [ ] **Step 6: Add a run_task ctx test**

Añade a `bench/test_harness_dispatch.py`:

```python
def test_run_task_passes_grader_ctx(monkeypatch):
    import harness
    # cliente falso: primera respuesta sin tool_calls, contenido = "done"
    class _Msg:
        content = "done"; tool_calls = []; reasoning_content = None
    class _Choice:
        message = _Msg()
    class _Resp:
        choices = [_Choice()]; usage = None
    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kw): return _Resp()
    seen = {}
    task = {"id": "t", "category": "c", "prompt": "p", "expect_tools": [],
            "check": lambda a, ctx: seen.setdefault("ctx", ctx) or (a == "done")}
    r = harness.run_task(_Client(), "m", task, root=".", enable_thinking=False,
                         tools=[], grader_ctx={"marker": 1})
    assert r["correct"] is True
    assert seen["ctx"] == {"marker": 1}
```

- [ ] **Step 7: Run all harness tests**

Run: `cd bench && python -m pytest test_harness_dispatch.py -v`
Expected: PASS (3 tests).

- [ ] **Step 8: Commit**

```bash
git add tools.py bench/harness.py bench/test_harness_dispatch.py
git commit -m "feat(bench): dispatch inyectable y grading por contexto en el arnés"
```

---

### Task 2: MockJira

**Files:**
- Create: `bench/mock_atlassian.py`
- Test: `bench/test_mock_atlassian.py`

**Interfaces:**
- Produces:
  - `MockJira(issues: list[dict])` — copia defensiva de los issues.
  - `.search(query="", jql="", limit=15, project="") -> str` (tabla `key\tstatus\tsummary`, newest-first por `seq`).
  - `.get(key: str) -> str` (detalles: summary/status/assignee/reporter/description/comments).
  - `.assign(key="", assignee="") -> str` (muta `assignee`).
  - `.comment(key="", body="") -> str` (añade a `comments`).
  - `.issue(key) -> dict | None` (acceso directo para graders).
- Consumes: nada (recibe la semilla ya construida).

Semántica de `search` (réplica de `tools.jira_search`): si `jql`, evaluar el subconjunto JQL; si no, partir `query` en palabras `\w+` con `len>=4` (si ninguna, todas), y hacer OR de `text ~ w` sobre `summary + description + comments`; `project` filtra por proyecto. Devolver hasta `limit` (cap 50) ordenado por `seq` DESC. Formato: `key\tstatus\tsummary` una por línea, sin cabeceras. Sin match: `f"No issues matched. (JQL: {jql_expr})"`.

Subconjunto JQL soportado: cláusulas unidas por ` AND ` / ` OR ` (evaluación izquierda-a-derecha, sin paréntesis anidados salvo el grupo `(a OR b OR c)` que genera la búsqueda por texto), con operadores por cláusula: `field = "v"`, `field != "v"`, `field ~ "v"` (contains), `field in (v1, v2)`. Campos: `project`, `status`, `assignee`, `type`, `text` (busca en summary+description+comments).

- [ ] **Step 1: Write the failing tests**

```python
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
    out = j.search(query="the")  # 'the' len<4 -> ignorada; sin palabras válidas -> todas
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd bench && python -m pytest test_mock_atlassian.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'mock_atlassian'`.

- [ ] **Step 3: Implement `MockJira` in `bench/mock_atlassian.py`**

```python
"""In-process mocks of the Jira / Confluence / Vault tools for the Advanced tier.

They replicate the SEARCH SEMANTICS and OUTPUT FORMAT of the real tools in
tools.py so the benchmark measures the agent + the tool DESCRIPTION, not a
divergent fake. Rebuilt fresh per task; writes mutate the in-memory store.
"""
import copy
import json
import re


# ----------------------------- JQL (subconjunto) -----------------------------
def _split_top(expr, sep):
    """Parte por ` AND `/` OR ` respetando el grupo (a OR b) generado por la búsqueda."""
    out, depth, cur = [], 0, ""
    tokens = re.split(r"(\s+AND\s+|\s+OR\s+|\(|\))", expr)
    parts, buf, d = [], "", 0
    # Simplificación: soportamos como mucho un nivel de paréntesis para el grupo text.
    return None  # placeholder eliminado abajo


def _match_clause(issue, field, op, value):
    hay = {
        "project": issue["project"], "status": issue["status"],
        "assignee": issue.get("assignee") or "", "type": issue["type"],
        "text": " ".join([issue["summary"], issue["description"], " ".join(issue["comments"])]),
        "summary": issue["summary"],
    }.get(field, "")
    hay_l = hay.lower()
    if op == "~":
        return value.lower() in hay_l
    if op == "=":
        return hay_l == value.lower()
    if op == "!=":
        return hay_l != value.lower()
    if op == "in":
        vals = [v.strip().strip('"').lower() for v in value.strip("()").split(",")]
        return hay_l in vals
    return False


def _eval_jql(issue, jql):
    """Evalúa el subconjunto JQL. Soporta cláusulas field op value unidas por AND/OR
    y un único grupo entre paréntesis (el que genera la búsqueda por texto)."""
    # Normaliza el grupo (a OR b OR c): lo evaluamos aparte y sustituimos por True/False.
    def eval_group(m):
        inner = m.group(1)
        return "TRUE" if _eval_flat(issue, inner) else "FALSE"
    flat = re.sub(r"\(([^()]*)\)", eval_group, jql)
    return _eval_flat(issue, flat)


_CLAUSE = re.compile(r'(\w+)\s*(=|!=|~|\bin\b)\s*(\([^)]*\)|"[^"]*"|\S+)', re.IGNORECASE)


def _eval_flat(issue, expr):
    """Evalúa una expresión sin paréntesis: cláusulas / TRUE / FALSE unidas por AND/OR."""
    tokens = re.split(r'\s+(AND|OR)\s+', expr.strip(), flags=re.IGNORECASE)
    if not tokens or not tokens[0]:
        return True
    def val(tok):
        tok = tok.strip()
        if tok == "TRUE":
            return True
        if tok == "FALSE":
            return False
        m = _CLAUSE.match(tok)
        if not m:
            return False
        field, op, value = m.group(1).lower(), m.group(2).lower(), m.group(3).strip('"')
        return _match_clause(issue, field, op, value)
    result = val(tokens[0])
    i = 1
    while i + 1 < len(tokens):
        joiner, term = tokens[i].upper(), val(tokens[i + 1])
        result = (result and term) if joiner == "AND" else (result or term)
        i += 2
    return result


class MockJira:
    def __init__(self, issues):
        self._issues = {i["key"]: copy.deepcopy(i) for i in issues}

    def issue(self, key):
        return self._issues.get((key or "").strip())

    def search(self, query="", jql="", limit=15, project=""):
        try:
            n = max(1, min(int(limit or 15), 50))
        except (TypeError, ValueError):
            n = 15
        if jql:
            jql_expr = jql
        elif query:
            words = [w for w in re.findall(r"\w+", query, re.UNICODE) if len(w) >= 4]
            if not words:
                words = re.findall(r"\w+", query, re.UNICODE) or [query]
            clauses = " OR ".join(f'text ~ "{w}"' for w in words)
            jql_expr = f"({clauses})"
            if project:
                jql_expr = f'project = "{project}" AND {jql_expr}'
        else:
            return "ERROR: provide either `query` (free text) or `jql`."
        matched = [i for i in self._issues.values() if _eval_jql(i, jql_expr)]
        matched.sort(key=lambda i: i["seq"], reverse=True)
        matched = matched[:n]
        if not matched:
            return f"No issues matched. (JQL: {jql_expr})"
        return "\n".join(f'{i["key"]}\t{i["status"]}\t{i["summary"]}' for i in matched)

    def get(self, key):
        i = self.issue(key)
        if not i:
            return f"(no details returned for {key})"
        lines = [f'{i["key"]}: {i["summary"]}',
                 f'Type: {i["type"]}   Status: {i["status"]}',
                 f'Assignee: {i.get("assignee") or "Unassigned"}   Reporter: {i["reporter"]}',
                 "", i["description"]]
        if i["comments"]:
            lines += ["", "Comments:"] + [f"- {c}" for c in i["comments"]]
        if i.get("links"):
            lines += ["", "Linked pages: " + ", ".join(i["links"])]
        return "\n".join(lines)

    def assign(self, key="", assignee=""):
        i = self.issue(key)
        if not i:
            return f"ERROR: no issue {key}"
        if not assignee.strip():
            return "ERROR: provide both 'key' and 'assignee'."
        i["assignee"] = assignee.strip()
        return f"Assigned {key.strip()} to {assignee.strip()}."

    def comment(self, key="", body=""):
        i = self.issue(key)
        if not i:
            return f"ERROR: no issue {key}"
        if not body.strip():
            return "ERROR: provide both 'key' and 'body'."
        i["comments"].append(body.strip())
        return f"Comment added to {key.strip()}."
```

Nota: elimina la función placeholder `_split_top` (quedó como resto de diseño; no se usa).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd bench && python -m pytest test_mock_atlassian.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add bench/mock_atlassian.py bench/test_mock_atlassian.py
git commit -m "feat(bench): MockJira con búsqueda/JQL, get, assign y comment"
```

---

### Task 3: MockConfluence

**Files:**
- Modify: `bench/mock_atlassian.py`
- Test: `bench/test_mock_atlassian.py` (añadir)

**Interfaces:**
- Produces:
  - `MockConfluence(pages: list[dict], users: list[dict])`.
  - `.search(query="", cql="", limit=15, space="", author="") -> str` (líneas `id\ttype\t[space]\ttitle\tby creator (date) · last edit editor (date)`; cabecera `# CQL: …`).
  - `.user(query="") -> str` (`accountId\tname\temail`).
  - `.get(page_id="", title="", space="", max_chars=12000, start=0) -> str` (cabecera `# title` + `ID:`/`Space:` + cuerpo).
  - `.create(space="", title="", body="") -> str` (muta; id nuevo secuencial; `Created page '…' (id X) in SPACE: …`).
  - `.comment(page_id="", body="") -> str` (muta `._comments[page_id]`).
  - `.page(page_id) -> dict | None`, `.page_by_title(title) -> dict | None`, `.comments(page_id) -> list[str]`.

Semántica de `search`: réplica de `confluence_search` — palabras `\w+` `len>=4` OR de `text ~ w` sobre `title + body`; filtro `space`; filtro `author` por nombre de creador (resuelto contra `users`). Si `cql`, evaluar el mismo subconjunto que Jira sobre campos `title`/`text`/`space`/`type`. Formato de salida idéntico al real.

- [ ] **Step 1: Write the failing tests** (añadir a `bench/test_mock_atlassian.py`)

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd bench && python -m pytest test_mock_atlassian.py -k conf -v`
Expected: FAIL con `ImportError: cannot import name 'MockConfluence'`.

- [ ] **Step 3: Implement `MockConfluence` (añadir a `bench/mock_atlassian.py`)**

```python
def _page_hay(page, field):
    return {
        "title": page["title"],
        "text": page["title"] + " " + page["body"],
        "space": page["space"],
        "type": "page",
    }.get(field, "")


def _eval_cql(page, cql):
    # Reutiliza el evaluador de cláusulas, pero sobre campos de página.
    def eval_group(m):
        return "TRUE" if _eval_flat_page(page, m.group(1)) else "FALSE"
    flat = re.sub(r"\(([^()]*)\)", eval_group, cql)
    return _eval_flat_page(page, flat)


def _eval_flat_page(page, expr):
    tokens = re.split(r'\s+(AND|OR)\s+', expr.strip(), flags=re.IGNORECASE)
    if not tokens or not tokens[0]:
        return True
    def val(tok):
        tok = tok.strip()
        if tok in ("TRUE", "FALSE"):
            return tok == "TRUE"
        m = _CLAUSE.match(tok)
        if not m:
            return True  # p.ej. "type = page" siempre cierto en nuestro store
        field, op, value = m.group(1).lower(), m.group(2).lower(), m.group(3).strip('"')
        if field == "type":
            return True
        hay = _page_hay(page, field).lower()
        if op == "~":
            return value.lower() in hay
        if op == "=":
            return hay == value.lower()
        if op == "!=":
            return hay != value.lower()
        return False
    result = val(tokens[0]); i = 1
    while i + 1 < len(tokens):
        joiner, term = tokens[i].upper(), val(tokens[i + 1])
        result = (result and term) if joiner == "AND" else (result or term)
        i += 2
    return result


class MockConfluence:
    def __init__(self, pages, users):
        self._pages = {p["id"]: copy.deepcopy(p) for p in pages}
        self._users = list(users)
        self._comments = {}
        self._next_id = 20001

    def page(self, page_id):
        return self._pages.get((page_id or "").strip())

    def page_by_title(self, title):
        t = (title or "").strip().lower()
        for p in self._pages.values():
            if p["title"].lower() == t:
                return p
        return None

    def comments(self, page_id):
        return self._comments.get((page_id or "").strip(), [])

    def search(self, query="", cql="", limit=15, space="", author=""):
        try:
            n = max(1, min(int(limit or 15), 50))
        except (TypeError, ValueError):
            n = 15
        if cql:
            q = cql
        else:
            parts = ["type = page"]
            if query:
                words = [w for w in re.findall(r"\w+", query, re.UNICODE) if len(w) >= 4] \
                    or re.findall(r"\w+", query, re.UNICODE) or [query]
                parts.append("(" + " OR ".join(f'text ~ "{w}"' for w in words) + ")")
            if space:
                parts.append(f'space = "{space}"')
            if author:
                names = [u["name"] for u in self._users if author.lower() in u["name"].lower()]
                if not names:
                    return (f"No Confluence user matched '{author}'. Use confluence_user to "
                            f"find the exact name / accountId, then retry.")
            if len(parts) == 1:
                return "ERROR: provide `query`, `author`, `space`, or a raw `cql`."
            q = " AND ".join(parts)
        matched = [p for p in self._pages.values() if _eval_cql(p, q)]
        if author:
            matched = [p for p in matched if author.lower() in p["creator"].lower()]
        matched.sort(key=lambda p: p["id"])
        matched = matched[:n]
        if not matched:
            return f"No pages matched. (CQL: {q})"
        lines = [f"# CQL: {q}"]
        for p in matched:
            lines.append(f'{p["id"]}\tpage\t[{p["space"]}]\t{p["title"]}\tby {p["creator"]} '
                         f'({p["created"]}) · last edit {p["editor"]} ({p["edited"]})')
        return "\n".join(lines)

    def user(self, query=""):
        if not query:
            return "ERROR: provide a name (or part of it) in 'query'."
        rows = [u for u in self._users if query.lower() in u["name"].lower()]
        if not rows:
            return f"No user matched '{query}'."
        return "\n".join(f'{u["accountId"]}\t{u["name"]}\t{u["email"]}' for u in rows)

    def get(self, page_id="", title="", space="", max_chars=12000, start=0):
        try:
            max_chars = max(500, min(int(max_chars or 12000), 40000))
        except (TypeError, ValueError):
            max_chars = 12000
        try:
            start = max(0, int(start or 0))
        except (TypeError, ValueError):
            start = 0
        p = self.page(page_id)
        if not p and title.strip():
            for cand in sorted(self._pages.values(), key=lambda x: x["id"]):
                if title.strip().lower() in cand["title"].lower() and \
                        (not space or cand["space"] == space):
                    p = cand
                    break
        if not p:
            return (f"No page found with title ~ '{title}'." if title
                    else "ERROR: provide 'page_id' or 'title' (optionally with 'space').")
        body = p["body"]
        header = f'# {p["title"]}\nID: {p["id"]}  ·  Space: {p["space"]}\nURL: /spaces/{p["space"]}/pages/{p["id"]}\n\n'
        total = len(body)
        chunk = body[start:start + max_chars]
        out = header + chunk
        shown = start + len(chunk)
        if shown < total:
            out += (f"\n\n… [showing chars {start}–{shown} of {total}. "
                    f"Call confluence_get again with start={shown} to continue.]")
        elif start:
            out += f"\n\n[end of page — {total} chars total]"
        return out

    def create(self, space="", title="", body=""):
        if not (space.strip() and title.strip() and body.strip()):
            return "ERROR: provide 'title', 'body', and a 'space' key."
        pid = str(self._next_id); self._next_id += 1
        self._pages[pid] = {
            "id": pid, "space": space.strip(), "title": title.strip(), "body": body,
            "labels": [], "ancestors": [], "creator": "raiko-agent",
            "created": "2026-07-01", "editor": "raiko-agent", "edited": "2026-07-01",
            "version": 1, "links": [],
        }
        return f"Created page '{title.strip()}' (id {pid}) in {space.strip()}: /spaces/{space.strip()}/pages/{pid}"

    def comment(self, page_id="", body=""):
        if not (page_id.strip() and body.strip()):
            return "ERROR: provide 'page_id' and 'body'."
        if not self.page(page_id):
            return f"ERROR: no page {page_id}"
        self._comments.setdefault(page_id.strip(), []).append(body.strip())
        return f"Comment added to page {page_id.strip()}."
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd bench && python -m pytest test_mock_atlassian.py -v`
Expected: PASS (15 tests: 8 Jira + 7 Confluence).

- [ ] **Step 5: Commit**

```bash
git add bench/mock_atlassian.py bench/test_mock_atlassian.py
git commit -m "feat(bench): MockConfluence con search/CQL, get, user, create y comment"
```

---

### Task 4: MockVault, AtlasCtx y build_atlas_impls

**Files:**
- Modify: `bench/mock_atlassian.py`
- Test: `bench/test_mock_atlassian.py` (añadir)

**Interfaces:**
- Produces:
  - `MockVault(secrets: dict)` con `.get(path: str) -> str` (JSON de los campos; error si no existe).
  - `class AtlasCtx` con atributos `.jira`, `.conf`, `.vault`, `.root` (str path del sandbox de FS por-task).
  - `build_atlas_impls(jira: MockJira, conf: MockConfluence, vault: MockVault) -> dict` → mapa `{nombre_tool: callable}` para los 9 tools Atlassian + `vault_get_secret`.

- [ ] **Step 1: Write the failing tests** (añadir a `bench/test_mock_atlassian.py`)

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd bench && python -m pytest test_mock_atlassian.py -k "vault or impls or ctx" -v`
Expected: FAIL con `ImportError`.

- [ ] **Step 3: Implement (añadir a `bench/mock_atlassian.py`)**

```python
class MockVault:
    def __init__(self, secrets):
        self._secrets = {k: dict(v) for k, v in secrets.items()}

    def get(self, path):
        p = (path or "").strip()
        if p not in self._secrets:
            return f"ERROR: Vault returned 404: no secret at {p}"
        return json.dumps(self._secrets[p])


class AtlasCtx:
    def __init__(self, jira, conf, vault, root):
        self.jira = jira
        self.conf = conf
        self.vault = vault
        self.root = root


def build_atlas_impls(jira, conf, vault):
    return {
        "jira_search": lambda **kw: jira.search(**kw),
        "jira_get": lambda **kw: jira.get(**kw),
        "jira_assign": lambda **kw: jira.assign(**kw),
        "jira_comment": lambda **kw: jira.comment(**kw),
        "confluence_search": lambda **kw: conf.search(**kw),
        "confluence_user": lambda **kw: conf.user(**kw),
        "confluence_get": lambda **kw: conf.get(**kw),
        "confluence_create": lambda **kw: conf.create(**kw),
        "confluence_comment": lambda **kw: conf.comment(**kw),
        "vault_get_secret": lambda **kw: vault.get(**kw),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd bench && python -m pytest test_mock_atlassian.py -v`
Expected: PASS (19 tests).

- [ ] **Step 5: Commit**

```bash
git add bench/mock_atlassian.py bench/test_mock_atlassian.py
git commit -m "feat(bench): MockVault, AtlasCtx y build_atlas_impls"
```

---

### Task 5: Semilla determinista (fixtures_atlassian.py)

**Files:**
- Create: `bench/fixtures_atlassian.py`
- Test: `bench/test_fixtures_atlassian.py`

**Interfaces:**
- Produces:
  - `USERS: list[dict]` (ver Global/Modelo de datos).
  - `build_jira_seed() -> list[dict]` (~120 issues, keys `OPS-*`/`WEB-*`/`DATA-*`, `seq` único creciente).
  - `build_confluence_seed() -> list[dict]` (~80 páginas en `ENG`/`RUNBOOKS`/`HR`).
  - `build_vault_seed() -> dict` (~10 secretos que apuntan a keys/emails/page-ids existentes).
  - Incluye "needles" con tokens únicos y cross-links coherentes (issue OPS ↔ página "Outage Playbook").
- Consumes: nada.

Determinismo: todo derivado de índices y listas literales; **prohibido** `random`/`datetime.now()`.

- [ ] **Step 1: Write the failing tests**

```python
# bench/test_fixtures_atlassian.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd bench && python -m pytest test_fixtures_atlassian.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'fixtures_atlassian'`.

- [ ] **Step 3: Implement `bench/fixtures_atlassian.py`**

```python
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
            body = bodies.get(title, f"{title}: reference notes for the {space} space. "
                                     f"Owner: {creator}. See related runbooks.")
            links = ["OPS-777"] if title == "Outage Playbook" else []
            pages.append({
                "id": str(pid), "space": space, "title": title, "body": body,
                "labels": [space.lower()], "ancestors": [titles[0]] if k else [],
                "creator": creator, "created": f"2026-0{1 + (k % 6)}-01",
                "editor": editor, "edited": f"2026-0{1 + ((k + 1) % 6)}-15",
                "version": 1 + (k % 5), "links": links,
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
```

Nota: `OPS-777` es el needle; sus links apuntan a "Outage Playbook", cuyo `links` apunta de vuelta a `OPS-777` (cross-link coherente).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd bench && python -m pytest test_fixtures_atlassian.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add bench/fixtures_atlassian.py bench/test_fixtures_atlassian.py
git commit -m "feat(bench): semilla determinista Jira/Confluence/Vault"
```

---

### Task 6: Tasks Atlassian (tasks_atlassian.py)

**Files:**
- Create: `bench/tasks_atlassian.py`
- Test: `bench/test_tasks_atlassian.py`

**Interfaces:**
- Consumes: `mock_atlassian.MockJira/MockConfluence/MockVault/AtlasCtx`, `fixtures_atlassian.build_*_seed`, helpers `tasks.contains/has_number`.
- Produces:
  - `build_atlassian_tasks() -> list[dict]` — cada task: `id, category, difficulty, prompt, expect_tools, check, negative=False, setup="atlassian"`.
  - Los `check` de estas tasks tienen firma `check(answer, ctx)` donde `ctx` es un `AtlasCtx`.
  - `CATEGORIES = {"jira_search","conf_search","jira_read","conf_read","jira_write","conf_write","chain","vault_gated"}`.

Los graders leen del `ctx` reconstruido tras la corrida:
- lectura/recall: `contains(answer, "OPS-777")`, `has_number(answer, 5432)`, etc.
- write jira: `ctx.jira.issue("WEB-100")["assignee"] == "dan@raiko.dev"`.
- write conf: `ctx.conf.page_by_title("Postmortem OPS-777") is not None`.
- chain con terminal de archivo: `rf(ctx.root, "answer.txt")` contiene el valor.

Distribución objetivo (~160): jira_search+conf_search ≈40, jira_read+conf_read ≈35, writes ≈30, chain ≈45, vault_gated ≈10.

- [ ] **Step 1: Write the failing tests**

```python
# bench/test_tasks_atlassian.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd bench && python -m pytest test_tasks_atlassian.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'tasks_atlassian'`.

- [ ] **Step 3: Implement `bench/tasks_atlassian.py`**

Estructura: un `add()` helper + familias curadas (recall/extracción/write/chain/vault) y bucles generadores para llegar a escala. Los `check` reciben `(answer, ctx)`.

```python
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
    for idx, (proj, kw, key_prefix) in enumerate([
        ("OPS", "certificate renewal", "OPS-"),
        ("WEB", "checkout timeout", "WEB-"),
        ("DATA", "duplicate rows", "DATA-"),
        ("OPS", "kubernetes pod crashloop", "OPS-"),
        ("WEB", "slow search endpoint", "WEB-"),
        ("DATA", "schema drift", "DATA-"),
    ]):
        add(f"js_recall_{idx}", "jira_search", "medium",
            f"Search Jira for the issue about '{kw}' and report its key.",
            ["jira_search"],
            (lambda p: lambda a, c: any(p in a for p in [p]) or contains(a, p))(key_prefix))
        # nota: el check real usa el prefijo de proyecto; ver refinamiento en Step 4.

    # ---------------- Confluence: búsqueda ----------------
    add("cs_playbook", "conf_search", "easy",
        "Use confluence_search to find the page titled 'Outage Playbook' and report its id.",
        ["confluence_search"], lambda a, c: contains(a, "10001"))
    add("cs_space_filter", "conf_search", "medium",
        "Search Confluence in the RUNBOOKS space for pages about backups and report a matching title.",
        ["confluence_search"], lambda a, c: contains(a, "Backup"))

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

    # ---- Generadores para llegar a la distribución objetivo ----
    _extend_generated(add)
    return tasks
```

Y el generador (mismo archivo), que produce familias parametrizadas para completar los conteos (recall por issue, extracción de owner por página, assign/comment por issue, y chains vault-gated por secreto):

```python
def _all_issues(ctx):
    return list(ctx.jira._issues.values())


def _extend_generated(add):
    import fixtures_atlassian as fx
    issues = fx.build_jira_seed()
    vault = fx.build_vault_seed()

    # Recall: para 25 issues variados, pedir su key por su summary exacto.
    picks = [i for i in issues if i["key"] != "OPS-777"][::5][:25]
    for i in picks:
        key = i["key"]; summ = i["summary"]
        add(f"gjs_{key}", "jira_search", "easy",
            f"Search Jira for the issue whose summary is '{summ}' and report its key.",
            ["jira_search"], (lambda k: lambda a, c: contains(a, k))(key))

    # Lectura: para 20 issues, reportar el status via jira_get.
    for i in [x for x in issues if x["key"] != "OPS-777"][::6][:20]:
        key = i["key"]
        add(f"gjr_{key}", "jira_read", "easy",
            f"Use jira_get to open {key} and report its status.",
            ["jira_get"], (lambda k: lambda a, c: contains(a, c.jira.issue(k)["status"]))(key))

    # Escritura: para 20 issues, asignar a un usuario fijo y verificar el store.
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
```

- [ ] **Step 4: Refinar el check de recall generado (eliminar el patrón dudoso)**

El bucle `js_recall_*` del Step 3 tiene un check enrevesado; sustitúyelo por uno directo que compruebe el prefijo de proyecto correcto. Reemplaza ese bloque por:

```python
    for idx, (proj, kw) in enumerate([
        ("OPS", "certificate renewal"), ("WEB", "checkout timeout"),
        ("DATA", "duplicate rows"), ("OPS", "kubernetes pod crashloop"),
        ("WEB", "slow search endpoint"), ("DATA", "schema drift"),
    ]):
        add(f"js_recall_{idx}", "jira_search", "medium",
            f"Search Jira for the issue about '{kw}' and report its key.",
            ["jira_search"], (lambda p: lambda a, c: contains(a, p + "-"))(proj))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd bench && python -m pytest test_tasks_atlassian.py -v`
Expected: PASS (5 tests). Si el conteo cae fuera de 150-175, ajusta los slices (`[::5]`, `[::6]`, `[::7]`) de `_extend_generated`.

- [ ] **Step 6: Commit**

```bash
git add bench/tasks_atlassian.py bench/test_tasks_atlassian.py
git commit -m "feat(bench): batería de tasks Jira/Confluence/Vault (~150+10)"
```

---

### Task 7: Cablear la batería en run_adv.py

**Files:**
- Modify: `bench/run_adv.py` (imports ~25-30; `FULL_TOOLS` ~44; `run_suite` ~97-126; `write_report` ~129-151)
- Test: manual smoke (abajo)

**Interfaces:**
- Consumes: `build_atlassian_tasks`, `build_jira_seed/build_confluence_seed/build_vault_seed`, `USERS`, `MockJira/MockConfluence/MockVault/AtlasCtx/build_atlas_impls`, `harness.run_task(..., dispatch=, grader_ctx=)`, `tools.DISPATCH`.
- Produces: el suite de Advanced ahora incluye las tasks Atlassian; el report desglosa por `category` y por `difficulty`.

- [ ] **Step 1: Imports y tool set (top de `run_adv.py`)**

Tras `from tasks_advanced import build_tasks_adv` (línea ~30) añade:

```python
from tasks_atlassian import build_atlassian_tasks
import fixtures_atlassian as fx
from mock_atlassian import (MockJira, MockConfluence, MockVault, AtlasCtx,
                            build_atlas_impls)
from tools import DISPATCH
```

- [ ] **Step 2: Ramificar el setup por task en `run_suite`**

Reemplaza el cuerpo del `for i, task in ...` (líneas ~103-123) para ramificar según `task.get("setup")`:

```python
        for i, task in enumerate(tasks, 1):
            if task["id"] in done:
                results.append(done[task["id"]])
                console.print(f"  [{label}] {i:>2}/{len(tasks)} {task['id']:<20} [dim]cached[/]")
                continue
            os.chdir(SCRATCH)
            base = os.path.join(SCRATCH, "advtmp", f"{label}_{i}")
            if task.get("setup") == "atlassian":
                # sandbox de FS vacío (para chains con write_file) + mocks frescos
                os.makedirs(base, exist_ok=True)
                root = base
                os.chdir(root)
                jira = MockJira(fx.build_jira_seed())
                conf = MockConfluence(fx.build_confluence_seed(), fx.USERS)
                vault = MockVault(fx.build_vault_seed())
                disp = dict(DISPATCH); disp.update(build_atlas_impls(jira, conf, vault))
                ctx = AtlasCtx(jira, conf, vault, root)
                r = run_task(client, alias, task, root, enable_thinking,
                             tools=FULL_TOOLS, dispatch=disp, grader_ctx=ctx)
            else:
                root = fixtures.build_sandbox(base)["root"]
                os.chdir(root)
                r = run_task(client, alias, task, root, enable_thinking,
                             tools=FULL_TOOLS, grader_root=True)
            results.append(r)
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            f.flush(); os.fsync(f.fileno())
            mark = "[green]OK [/]" if r["correct"] else "[red]FAIL[/]"
            tool = "" if r["tool_ok"] else "[yellow](tool?)[/]"
            console.print(f"  [{label}] {i:>2}/{len(tasks)} {task['id']:<20} {mark} {tool} "
                          f"score={r['score']:.2f} iters={r['iterations']} {r['latency_s']}s [dim]{r['status']}[/]")
```

- [ ] **Step 3: Combinar las tasks en `main()`**

Localiza `tasks = build_tasks_adv()` (línea ~169) y sustitúyela por:

```python
    tasks = build_tasks_adv() + build_atlassian_tasks()
```

- [ ] **Step 4: Desglose por dificultad en el report**

En `write_report` (tras el bloque "Accuracy by category", línea ~148) añade una tabla por dificultad. Antes de `lines += ["", f"Transcripts…"]` inserta:

```python
    diffs = ["easy", "medium", "hard"]
    lines += ["", "## Accuracy by difficulty (%)", "",
              "| Difficulty | " + " | ".join(r["label"] for r in rows) + " |",
              "|---|" + "---|" * len(rows)]
    for d in diffs:
        cells = []
        for r in rows:
            per = r["agg"].get("by_difficulty", {})
            cells.append(str(per.get(d, "—")))
        lines.append(f"| {d} | " + " | ".join(cells) + " |")
```

Y añade `by_difficulty` al agregado: en `bench/harness.py` `aggregate` (tras `by_category`, línea ~237) inserta:

```python
    diffs = {}
    for r in results:
        d = r.get("difficulty")
        if d:
            diffs.setdefault(d, []).append(r["correct"])
    by_difficulty = {d: round(100 * sum(v) / len(v)) for d, v in sorted(diffs.items())}
```

y añádelo al dict devuelto (junto a `by_category`): `"by_difficulty": by_difficulty,`.

Para que `difficulty` llegue al resultado, en `run_task` (dict de retorno, ~184) añade tras `"category": task["category"],`:

```python
        "difficulty": task.get("difficulty"),
```

- [ ] **Step 5: Smoke run (sin modelo real: verificación de setup)**

Verifica que el suite se construye y que un dispatch mock responde, sin llamar a un LLM:

```bash
cd bench && python -c "
import fixtures_atlassian as fx
from mock_atlassian import MockJira, MockConfluence, MockVault, build_atlas_impls
from tasks_atlassian import build_atlassian_tasks
from tools import DISPATCH, call_tool
tasks = build_atlassian_tasks()
print('atlassian tasks:', len(tasks))
j=MockJira(fx.build_jira_seed()); c=MockConfluence(fx.build_confluence_seed(), fx.USERS); v=MockVault(fx.build_vault_seed())
disp=dict(DISPATCH); disp.update(build_atlas_impls(j,c,v))
print(call_tool('jira_search', '{\"query\": \"NEBULA7788\"}', dispatch=disp))
print(call_tool('confluence_get', '{\"page_id\": \"10001\"}', dispatch=disp)[:120])
"
```
Expected: imprime `atlassian tasks: 15x`, una línea con `OPS-777` y el header de la página 10001.

- [ ] **Step 6: Run the full test suite**

Run: `cd bench && python -m pytest -v`
Expected: PASS (todos: harness, mocks, fixtures, tasks).

- [ ] **Step 7: Commit**

```bash
git add bench/run_adv.py bench/harness.py
git commit -m "feat(bench): integrar batería Atlassian en el runner Advanced + report por dificultad"
```

---

### Task 8: Demolición del tier Circuit y limpieza de infra

**Files:**
- Delete: `bench/run_circuit.py`, `bench/tasks_circuit.py`, `bench/vaultsvc.py`
- Modify: `installers.py` (quitar `install_vault` y menciones), `tui.py` (~1044-1046, ~1099, ~1106), `requirements.txt`, `README.md`, `docs/index.html`, `bench/make_charts.py`, `bench/combine_report.py`

**Interfaces:** ninguna nueva; se elimina superficie.

- [ ] **Step 1: Borrar los archivos del tier Circuit**

```bash
cd /home/ramon.martinez@corp.zitrogames.com/raiko-toolkit
git rm bench/run_circuit.py bench/tasks_circuit.py bench/vaultsvc.py
```

- [ ] **Step 2: Quitar `install_vault` de `installers.py`**

Elimina la función `install_vault` (líneas ~113-136) y cualquier mención a Vault en el docstring del módulo (líneas ~1-10) y en `describe()`. Verifica que no quede referencia:

Run: `grep -ni vault installers.py`
Expected: sin resultados.

- [ ] **Step 3: Botón del `--configure` a solo Jira (`tui.py`)**

- Línea ~1045: `yield Static("Jira + Vault CLIs", classes="clabel")` → `yield Static("Jira CLI", classes="clabel")`.
- Línea ~1046: cambia el label del botón: `yield Button("Download Jira CLI", id="dl_clis", variant="primary")`.
- Línea ~1099: `self._status("[cyan]Downloading Jira CLI + Vault… (this can take a minute)[/]")` → `self._status("[cyan]Downloading Jira CLI… (this can take a minute)[/]")`.
- Línea ~1106: `for name, fn in [("jira", installers.install_jira_cli), ("vault", installers.install_vault)]:` → `for name, fn in [("jira", installers.install_jira_cli)]:`.

Verifica:
Run: `grep -ni "install_vault\|Jira + Vault\|+ Vault" tui.py`
Expected: sin resultados.

- [ ] **Step 4: Depurar `requirements.txt`**

Comprueba si `paramiko` queda sin uso tras borrar el tier Circuit:

Run: `grep -rn "import paramiko\|paramiko" --include=*.py . | grep -v __pycache__`
- Si NO hay resultados: elimina la línea `paramiko>=3.0 …` de `requirements.txt`.
- Actualiza el comentario de `requests>=2.28` si mencionaba solo vault (queda en uso por `web_*`, no lo borres).

Añade pytest como dependencia de desarrollo (opcional) al final de `requirements.txt`:

```
# dev — para la suite de tests del benchmark:
# pytest>=8.0
```

- [ ] **Step 5: Limpiar menciones al tier Circuit en docs y charts**

Run: `grep -rniE "circuit" README.md docs/index.html bench/make_charts.py bench/combine_report.py`

Para cada resultado, elimina la fila/sección/entrada del tier Circuit (leaderboards, listas de tiers, generación de charts). No inventes texto nuevo; solo suprime lo relativo a Circuit. Si `make_charts.py`/`combine_report.py` iteran sobre una lista de tiers que incluye `"circuit"`, quita ese elemento.

Verifica que no quedan referencias colgando:
Run: `grep -rniE "run_circuit|tasks_circuit|vaultsvc|copy_file_to_mac|circuit tier" . --include=*.py --include=*.md --include=*.html | grep -v __pycache__`
Expected: sin resultados.

- [ ] **Step 6: Verificar que nada importa lo borrado**

Run: `cd bench && python -c "import run_adv, harness, tasks_advanced, tasks_atlassian, mock_atlassian, fixtures_atlassian; print('imports OK')"`
Expected: `imports OK`.

Run: `python -c "import tui, installers; print('tui/installers OK')"`
Expected: `tui/installers OK` (desde la raíz del repo).

- [ ] **Step 7: Suite completa de tests**

Run: `cd bench && python -m pytest -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "chore: retirar tier Circuit y toda dependencia de infra (Vault binario, SSH al Mac)"
```

---

## Self-Review

**1. Spec coverage:**
- Demolición Circuit (archivos, copy_to_mac, vault binario, botón configure) → Task 8. ✓
- Mocks in-process con fidelidad de semántica/formato → Tasks 2-4. ✓
- Inyección sin tocar schemas → Task 1 (dispatch) + Task 7 (composición por-task). ✓
- Semilla determinista con cross-links y needles → Task 5. ✓
- Familias de tasks (search/read/write/chain/vault) con dificultad → Task 6. ✓
- Grading contra store mock y filesystem → Task 1 (grader_ctx) + Task 6 (checks) + Task 7 (AtlasCtx). ✓
- Report por category + difficulty → Task 7. ✓
- Vault tool conservado en producción → no se toca en `tools.py` (solo `call_tool`); Task 8 solo quita instalador/botón. ✓
- Follow-up de descripciones → fuera de alcance, documentado en spec. ✓

**2. Placeholder scan:** El único literal "placeholder" es la función `_split_top` en el Step 3 de Task 2, señalada explícitamente para eliminar (nota al final del step) — sustituida por el evaluador real `_eval_jql`/`_eval_flat`. Sin TBD/TODO en pasos ejecutables.

**3. Type consistency:** `build_atlas_impls(jira, conf, vault)` (Task 4) coincide con su uso en Task 7. `AtlasCtx(jira, conf, vault, root)` con atributos `.jira/.conf/.vault/.root` usados por los checks de Task 6. `MockJira.issue(key)`, `MockConfluence.page_by_title/comments/page`, `MockVault.get` usados consistentemente en checks. `run_task(..., dispatch=, grader_ctx=)` y `call_tool(..., dispatch=)` coherentes entre Task 1 y Task 7. `difficulty` propagado run_task→aggregate→report.

Riesgo conocido a validar en ejecución: los conteos de `_extend_generated` (slices `[::5]/[::6]/[::7]`) deben dejar el total en 150-175; Task 6 Step 5 lo verifica y da la palanca de ajuste.
