"""In-process mocks of the Jira / Confluence / Vault tools for the Advanced tier.

They replicate the SEARCH SEMANTICS and OUTPUT FORMAT of the real tools in
tools.py so the benchmark measures the agent + the tool DESCRIPTION, not a
divergent fake. Rebuilt fresh per task; writes mutate the in-memory store.
"""
import copy
import json
import re


# ----------------------------- JQL (subconjunto) -----------------------------
def _match_clause(issue, field, op, value):
    hay = {
        "project": issue["project"], "status": issue["status"],
        "assignee": issue.get("assignee") or "", "type": issue["type"],
        "issuetype": issue["type"],   # alias real de Jira (JQL usa 'issuetype')
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


def _mask_in_lists(expr):
    """Protege las listas de valores de `in (...)` del colapso de grupos de texto."""
    masks = []
    def repl(m):
        masks.append(m.group(0))
        return f"\x00{len(masks) - 1}\x00"
    return re.sub(r'\bin\s*\([^()]*\)', repl, expr, flags=re.IGNORECASE), masks


def _unmask(expr, masks):
    for i, orig in enumerate(masks):
        expr = expr.replace(f"\x00{i}\x00", orig)
    return expr


def _eval_jql(issue, jql):
    """Evalúa el subconjunto JQL. Soporta cláusulas field op value unidas por AND/OR
    y un único grupo entre paréntesis (el que genera la búsqueda por texto)."""
    # Normaliza el grupo (a OR b OR c): lo evaluamos aparte y sustituimos por True/False.
    masked, masks = _mask_in_lists(jql)
    def eval_group(m):
        return "TRUE" if _eval_flat(issue, _unmask(m.group(1), masks)) else "FALSE"
    flat = re.sub(r"\(([^()]*)\)", eval_group, masked)
    return _eval_flat(issue, _unmask(flat, masks))


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
    def __init__(self, issues, users=None):
        self._issues = {i["key"]: copy.deepcopy(i) for i in issues}
        self._users = list(users or [])

    def _resolve_user(self, who):
        w = (who or "").strip()
        for u in self._users:
            if w.lower() in (u["name"].lower(), u["email"].lower()):
                return u["email"]
        return w

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
        i["assignee"] = self._resolve_user(assignee)
        return f"Assigned {key.strip()} to {i['assignee']}."

    def comment(self, key="", body=""):
        i = self.issue(key)
        if not i:
            return f"ERROR: no issue {key}"
        if not body.strip():
            return "ERROR: provide both 'key' and 'body'."
        i["comments"].append(body.strip())
        return f"Comment added to {key.strip()}."


# ----------------------------- CQL (subconjunto) -----------------------------
def _page_hay(page, field):
    return {
        "title": page["title"],
        "text": page["title"] + " " + page["body"],
        "space": page["space"],
        "type": "page",
    }.get(field, "")


def _eval_cql(page, cql):
    # Reutiliza el evaluador de cláusulas, pero sobre campos de página.
    masked, masks = _mask_in_lists(cql)
    def eval_group(m):
        return "TRUE" if _eval_flat_page(page, _unmask(m.group(1), masks)) else "FALSE"
    flat = re.sub(r"\(([^()]*)\)", eval_group, masked)
    return _eval_flat_page(page, _unmask(flat, masks))


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
        if op == "in":
            vals = [v.strip().strip('"').lower() for v in value.strip("()").split(",")]
            return hay in vals
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
            if len(parts) == 1 and not author:   # author-only sí es una búsqueda válida
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
