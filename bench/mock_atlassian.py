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
