import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def read_file(path: str) -> str:
    p = Path(path)
    if not p.is_file():
        return f"ERROR: not a file: {path}"
    return p.read_text(encoding="utf-8", errors="replace")


def write_file(path: str, content: str) -> str:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"OK: wrote {len(content)} chars to {path}"


def list_dir(path: str = ".") -> str:
    p = Path(path)
    if not p.is_dir():
        return f"ERROR: not a directory: {path}"
    entries = []
    for child in sorted(p.iterdir()):
        if child.is_dir():
            # Count total recursive size of the directory
            total = sum(f.stat().st_size for f in child.rglob("*") if f.is_file())
            size_str = _format_size(total)
            entries.append(f"DIR  {child.name:<30} {size_str}")
        else:
            size = child.stat().st_size
            size_str = _format_size(size)
            entries.append(f"FILE {child.name:<30} {size_str}")
    return "\n".join(entries) if entries else "(empty)"


def get_current_directory() -> str:
    return os.getcwd()


def grep(pattern: str, path: str = ".", glob: str = "**/*") -> str:
    p = Path(path)
    if not p.exists():
        return f"ERROR: path does not exist: {path}"
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return f"ERROR: bad regex: {e}"

    targets = [p] if p.is_file() else [f for f in p.glob(glob) if f.is_file()]
    hits = []
    for f in targets:
        try:
            for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{f}:{i}: {line}")
                    if len(hits) >= 200:
                        hits.append("... (truncated at 200 matches)")
                        return "\n".join(hits)
        except (OSError, UnicodeDecodeError):
            continue
    return "\n".join(hits) if hits else "(no matches)"


def list_models() -> str:
    """List all models (subdirectories) in F:\\models."""
    p = Path("F:/models")
    if not p.is_dir():
        return f"ERROR: F:\\models does not exist or is not a directory"
    entries = []
    for child in sorted(p.iterdir()):
        if child.is_dir():
            size = sum(f.stat().st_size for f in child.rglob("*") if f.is_file())
            size_str = _format_size(size)
            entries.append(f"DIR  {child.name}  ({size_str})")
        else:
            entries.append(f"FILE {child.name}")
    return "\n".join(entries) if entries else "(empty — no models found)"


def _format_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.2f} PB"


# ---------------------------------------------------------------------------
# Additional tools, all READ-ONLY (they write nothing).
# ---------------------------------------------------------------------------

def find_files(name_glob: str, path: str = ".") -> str:
    """Find files matching a glob pattern (e.g. **/*.py) under a directory."""
    p = Path(path)
    if not p.is_dir():
        return f"ERROR: not a directory: {path}"
    matches = [str(f) for f in sorted(p.glob(name_glob)) if f.is_file()]
    if not matches:
        return "(no files match)"
    if len(matches) > 200:
        matches = matches[:200] + [f"... (truncated, {len(matches) - 200} more)"]
    return "\n".join(matches)


def read_lines(path: str, start: int, end: int) -> str:
    """Read lines [start, end] (1-indexed, inclusive) of a text file, numbered."""
    p = Path(path)
    if not p.is_file():
        return f"ERROR: not a file: {path}"
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, start)
    chunk = lines[start - 1:end]
    if not chunk:
        return f"(no lines in range {start}-{end}; file has {len(lines)} lines)"
    return "\n".join(f"{i}\t{line}" for i, line in enumerate(chunk, start))


def head(path: str, n: int = 20) -> str:
    """Return the first N lines of a text file (numbered)."""
    p = Path(path)
    if not p.is_file():
        return f"ERROR: not a file: {path}"
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(f"{i}\t{line}" for i, line in enumerate(lines[:n], 1)) or "(empty file)"


def tail(path: str, n: int = 20) -> str:
    """Return the last N lines of a text file (numbered)."""
    p = Path(path)
    if not p.is_file():
        return f"ERROR: not a file: {path}"
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(0, len(lines) - n)
    return "\n".join(f"{i}\t{line}" for i, line in enumerate(lines[start:], start + 1)) or "(empty file)"


def count_lines(path: str) -> str:
    """Count lines, words and bytes of a text file (wc-style)."""
    p = Path(path)
    if not p.is_file():
        return f"ERROR: not a file: {path}"
    data = p.read_text(encoding="utf-8", errors="replace")
    return f"lines={len(data.splitlines())} words={len(data.split())} bytes={p.stat().st_size}"


def stat_path(path: str) -> str:
    """Return type, size, modified time and (for dirs) child count of a path."""
    p = Path(path)
    if not p.exists():
        return f"ERROR: path does not exist: {path}"
    st = p.stat()
    mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    if p.is_dir():
        children = list(p.iterdir())
        total = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        return f"type=dir children={len(children)} total_size={_format_size(total)} mtime={mtime}"
    return f"type=file size={_format_size(st.st_size)} bytes={st.st_size} mtime={mtime}"


def tree(path: str = ".", depth: int = 2) -> str:
    """Recursive indented listing of a directory up to `depth` levels deep."""
    p = Path(path)
    if not p.is_dir():
        return f"ERROR: not a directory: {path}"
    lines = []

    def walk(d: Path, prefix: str, level: int):
        if level > depth or len(lines) >= 300:
            return
        for child in sorted(d.iterdir(), key=lambda c: (c.is_file(), c.name)):
            if len(lines) >= 300:
                return
            tag = "DIR " if child.is_dir() else "FILE"
            lines.append(f"{prefix}{tag} {child.name}")
            if child.is_dir():
                walk(child, prefix + "  ", level + 1)

    walk(p, "", 1)
    if len(lines) >= 300:
        lines.append("... (truncated at 300 entries)")
    return "\n".join(lines) if lines else "(empty)"


def find_in_files(pattern: str, path: str = ".", glob: str = "**/*") -> str:
    """Regex search; return only the files that match, with their match counts."""
    p = Path(path)
    if not p.exists():
        return f"ERROR: path does not exist: {path}"
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return f"ERROR: bad regex: {e}"
    targets = [p] if p.is_file() else [f for f in p.glob(glob) if f.is_file()]
    results = []
    for f in targets:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue
        count = len(rx.findall(text))
        if count:
            results.append((count, str(f)))
    if not results:
        return "(no files match)"
    results.sort(key=lambda x: (-x[0], x[1]))
    return "\n".join(f"{fpath}: {count} match(es)" for count, fpath in results)


# ---------------------------------------------------------------------------
# WRITE / EXECUTION tools (semi-destructive, with guards).
# Designed to run inside a sandbox; they have a denylist + timeout.
# ---------------------------------------------------------------------------

# Only clearly DESTRUCTIVE/irreversible operations are blocked. System introspection
# (socket, platform, subprocess) and read-only network access ARE allowed — it's your machine.
_DANGER = [
    r"Remove-Item\b[^\n]*-Recurse", r"\brmdir\b[^\n]*/s", r"\bdel\b[^\n]*/s",
    r"\brm\b[^\n]*-rf\b", r"shutil\.rmtree", r"os\.removedirs",
    r"\bformat\b\s+[a-zA-Z]:", r"\bmkfs\b", r"\bdiskpart\b",
    r"shutdown\b", r"Stop-Computer", r"Restart-Computer", r"\breboot\b",
    r"reg\s+delete", r"Remove-Item[^\n]*HK(LM|CU)", r"Set-ItemProperty[^\n]*HKLM",
]
_DANGER_RX = re.compile("|".join(_DANGER), re.IGNORECASE)


def _danger_check(code: str):
    m = _DANGER_RX.search(code or "")
    if m:
        return f"ERROR: blocked potentially destructive/unsafe operation: '{m.group(0)}'"
    return None


def danger_match(text: str):
    """Returns the detected dangerous fragment (or None). So the UI layer
    can ask for permission instead of blocking outright."""
    m = _DANGER_RX.search(text or "")
    return m.group(0) if m else None


def edit_file(path: str, old_string: str, new_string: str) -> str:
    """Replace an exact, unique snippet inside a file (targeted edit)."""
    p = Path(path)
    if not p.is_file():
        return f"ERROR: not a file: {path}"
    text = p.read_text(encoding="utf-8", errors="replace")
    n = text.count(old_string)
    if n == 0:
        return f"ERROR: old_string not found in {path}"
    if n > 1:
        return f"ERROR: old_string is not unique ({n} matches); add more context"
    p.write_text(text.replace(old_string, new_string), encoding="utf-8")
    return f"OK: edited {path} (replaced 1 occurrence)"


def run_python(code: str, allow_unsafe: bool = False) -> str:
    """Run a short Python snippet in a subprocess (cwd = current dir), 20s timeout.
    Stdout+stderr returned. Destructive ops are blocked unless allow_unsafe=True
    (the UI sets that after the user approves a permission prompt)."""
    if not allow_unsafe:
        blocked = _danger_check(code)
        if blocked:
            return blocked
    try:
        proc = subprocess.run([sys.executable, "-c", code],
                              capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        return "ERROR: python execution timed out (20s)"
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if len(out) > 4000:
        out = out[:4000] + "\n... (truncated)"
    return out or "(no output)"


def run_powershell(command: str, allow_unsafe: bool = False) -> str:
    """Run a PowerShell command (cwd = current dir), 25s timeout. Stdout+stderr
    returned. Destructive ops are blocked unless allow_unsafe=True (set by the UI
    after the user approves a permission prompt)."""
    if not allow_unsafe:
        blocked = _danger_check(command)
        if blocked:
            return blocked
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=25)
    except subprocess.TimeoutExpired:
        return "ERROR: powershell execution timed out (25s)"
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if len(out) > 4000:
        out = out[:4000] + "\n... (truncated)"
    return out or "(no output)"


def run_bash(command: str, allow_unsafe: bool = False) -> str:
    """Run a bash/sh shell command on Linux/macOS (cwd = current dir), 25s timeout.
    Stdout+stderr returned. Destructive ops are blocked unless allow_unsafe=True
    (set by the UI after the user approves a permission prompt)."""
    if not allow_unsafe:
        blocked = _danger_check(command)
        if blocked:
            return blocked
    import shutil
    sh = shutil.which("bash") or shutil.which("zsh") or shutil.which("sh")
    if not sh:
        return "ERROR: no bash/sh shell found (this tool is for Linux/macOS)."
    try:
        proc = subprocess.run([sh, "-c", command],
                              capture_output=True, text=True, timeout=25)
    except subprocess.TimeoutExpired:
        return "ERROR: bash execution timed out (25s)"
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if len(out) > 4000:
        out = out[:4000] + "\n... (truncated)"
    return out or "(no output)"


def vault_get_secret(path: str) -> str:
    """Read a secret from HashiCorp Vault over its HTTP API.
    Uses VAULT_ADDR / VAULT_TOKEN from the environment. For KV v2 the read path
    is like 'secret/data/<name>'. Returns the secret's key/value data as JSON."""
    import requests
    addr = os.environ.get("VAULT_ADDR", "http://127.0.0.1:8200")
    token = os.environ.get("VAULT_TOKEN", "")
    try:
        r = requests.get(f"{addr}/v1/{path.lstrip('/')}",
                         headers={"X-Vault-Token": token}, timeout=10)
    except Exception as e:
        return f"ERROR: cannot reach Vault: {e}"
    if r.status_code != 200:
        return f"ERROR: Vault returned {r.status_code}: {r.text[:200]}"
    body = r.json().get("data", {})
    secret = body.get("data", body)  # KV v2 nests under data.data
    return json.dumps(secret)


def web_search(query: str, max_results: int = 5) -> str:
    """Search the web via the Tavily API. Returns ranked results (title, URL,
    snippet) plus a short synthesized answer when available. Needs TAVILY_API_KEY
    in the environment (free key at tavily.com)."""
    import urllib.request
    import urllib.error
    key = os.environ.get("TAVILY_API_KEY", "")
    if not key:
        return ("ERROR: web search is not configured — set TAVILY_API_KEY "
                "(get a free key at https://tavily.com).")
    try:
        n = max(1, min(int(max_results or 5), 10))
    except (TypeError, ValueError):
        n = 5
    payload = json.dumps({
        "api_key": key,
        "query": query,
        "max_results": n,
        "search_depth": "advanced",   # deeper crawl → better snippets
        "include_answer": True,
    }).encode("utf-8")
    req = urllib.request.Request("https://api.tavily.com/search", data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            detail = ""
        return f"ERROR: Tavily returned {e.code}: {detail}"
    except Exception as e:
        return f"ERROR: web search failed: {type(e).__name__}: {e}"
    out = []
    answer = (data.get("answer") or "").strip()
    if answer:
        out.append(f"Answer: {answer}\n")
    for i, r in enumerate(data.get("results", []), 1):
        title = (r.get("title") or "").strip()
        url = r.get("url", "")
        snippet = " ".join((r.get("content") or "").split())
        if len(snippet) > 500:
            snippet = snippet[:500] + "…"
        out.append(f"{i}. {title}\n   {url}\n   {snippet}")
    return "\n".join(out) if out else "(no results)"


def web_fetch(url: str, max_chars: int = 8000) -> str:
    """Fetch a web page and return its main text content (cleaned), via the Tavily
    Extract API. Needs TAVILY_API_KEY in the environment. Use after web_search to
    read a result in full, or to read the contents of a known URL."""
    import urllib.request
    import urllib.error
    key = os.environ.get("TAVILY_API_KEY", "")
    if not key:
        return ("ERROR: web fetch is not configured — set TAVILY_API_KEY "
                "(get a free key at https://tavily.com).")
    try:
        limit = max(500, min(int(max_chars or 8000), 20000))
    except (TypeError, ValueError):
        limit = 8000
    payload = json.dumps({
        "api_key": key,
        "urls": [url],
        "extract_depth": "advanced",
    }).encode("utf-8")
    req = urllib.request.Request("https://api.tavily.com/extract", data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            detail = ""
        return f"ERROR: Tavily returned {e.code}: {detail}"
    except Exception as e:
        return f"ERROR: web fetch failed: {type(e).__name__}: {e}"
    results = data.get("results") or []
    if not results:
        failed = data.get("failed_results") or []
        why = f" ({failed[0].get('error', '')})" if failed else ""
        return f"ERROR: could not extract content from {url}{why}"
    content = (results[0].get("raw_content") or "").strip()
    if len(content) > limit:
        content = content[:limit] + f"\n… (truncated, {len(content) - limit} more chars)"
    return content or "(no content extracted)"


def _jira_bin() -> str:
    """Locate the Jira CLI binary (ankitpokhrel/jira-cli)."""
    import shutil
    return (os.environ.get("JIRA_CLI")
            or shutil.which("jira")
            or r"C:\utils\jira\bin\jira.exe")


def _run_jira(args: list) -> tuple:
    """Run the jira CLI with the given args. Returns (ok, output)."""
    bin_path = _jira_bin()
    if not (os.path.isfile(bin_path) or __import__("shutil").which(bin_path)):
        return False, ("ERROR: Jira CLI not found. Install ankitpokhrel/jira-cli "
                       "and/or set the JIRA_CLI environment variable to its path.")
    try:
        proc = subprocess.run([bin_path] + args, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=40)
    except subprocess.TimeoutExpired:
        return False, "ERROR: jira command timed out (40s)"
    except Exception as e:
        return False, f"ERROR: {type(e).__name__}: {e}"
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    out = re.sub(r"\x1b\[[0-9;]*m", "", out)  # strip ANSI colour codes
    if proc.returncode != 0 and not proc.stdout:
        return False, f"ERROR: jira exited {proc.returncode}: {out[:500] or '(no output)'}"
    return True, out


def jira_search(query: str = "", jql: str = "", limit: int = 15, project: str = "") -> str:
    """Search Jira issues. Pass free text in `query` (matched against summary,
    description and comments) or a raw `jql` expression for full control. Returns a
    plain table of key · status · summary, newest first."""
    try:
        n = max(1, min(int(limit or 15), 50))
    except (TypeError, ValueError):
        n = 15
    if jql:
        jql_expr = jql
    elif query:
        # In JQL, `text ~ "a b c"` requires ALL terms (so a natural phrase with one
        # absent word matches nothing). Split into words and OR them for a forgiving
        # search that ranks the relevant issue into the list.
        words = [w for w in re.findall(r"\w+", query, re.UNICODE) if len(w) >= 4]
        if not words:
            words = re.findall(r"\w+", query, re.UNICODE) or [query]
        clauses = " OR ".join(f'text ~ "{w}"' for w in words)
        jql_expr = f"({clauses})"
        if project:
            jql_expr = f"project = {project} AND {jql_expr}"
        # NOTE: don't append ORDER BY here — the CLI applies its own --order-by
        # (created, DESC) and a second ORDER BY clause makes the JQL invalid.
    else:
        return "ERROR: provide either `query` (free text) or `jql`."
    ok, out = _run_jira(["issue", "list", "--jql", jql_expr, "--plain", "--no-headers",
                         "--columns", "key,status,summary", "--paginate", f"0:{n}"])
    if not ok:
        return out
    if not out:
        return f"No issues matched. (JQL: {jql_expr})"
    if len(out) > 4000:
        out = out[:4000] + "\n… (truncated)"
    return out


def jira_get(key: str) -> str:
    """Fetch a single Jira issue by its key (e.g. 'PROJ-1234') and return its
    details (summary, status, assignee, description) in plain text."""
    if not key or not key.strip():
        return "ERROR: provide an issue key, e.g. 'PROJ-1234'."
    ok, out = _run_jira(["issue", "view", key.strip(), "--plain"])
    if not ok:
        return out
    if len(out) > 6000:
        out = out[:6000] + "\n… (truncated)"
    return out or f"(no details returned for {key})"


def jira_assign(key: str = "", assignee: str = "") -> str:
    """Assign a Jira issue to a user. `assignee` is an email or an EXACT display
    name; use 'me' for yourself, 'default' for the project default, or 'x' to
    unassign. (Write operation — the TUI asks for permission first.)"""
    if not key.strip() or not assignee.strip():
        return "ERROR: provide both 'key' (e.g. 'PROJ-1') and 'assignee'."
    who = assignee.strip()
    if who.lower() in ("me", "self", "myself"):
        ok, mine = _run_jira(["me"])
        if not ok:
            return mine
        who = (mine.strip().splitlines() or [""])[-1].strip()
        if not who:
            return "ERROR: could not resolve the current user via `jira me`."
    ok, out = _run_jira(["issue", "assign", key.strip(), who])
    if not ok:
        return out
    return out or f"Assigned {key.strip()} to {who}."


def jira_comment(key: str = "", body: str = "") -> str:
    """Add a comment to a Jira issue. (Write operation — the TUI asks for
    permission first.)"""
    if not key.strip() or not body.strip():
        return "ERROR: provide both 'key' (e.g. 'PROJ-1') and 'body'."
    ok, out = _run_jira(["issue", "comment", "add", key.strip(), body, "--no-input"])
    if not ok:
        return out
    return out or f"Comment added to {key.strip()}."


# --------------------------- Confluence (REST API) ---------------------------
# Confluence Cloud reuses the account-scoped Atlassian API token (the same one the
# Jira tools use), so no extra binary/CLI is needed — we hit the REST API directly
# with HTTP Basic auth (email:token).

def _confluence_ctx():
    base = (os.environ.get("CONFLUENCE_BASE_URL") or "").rstrip("/")
    email = os.environ.get("CONFLUENCE_EMAIL") or ""
    token = os.environ.get("JIRA_API_TOKEN") or os.environ.get("ATLASSIAN_API_TOKEN") or ""
    if not (base and email and token):
        return None, ("ERROR: Confluence is not configured — set confluence.base_url and "
                      "confluence.email in tui_config.json (the Atlassian token is reused "
                      "from jira.api_token / JIRA_API_TOKEN).")
    return (base, email, token), None


def _html_to_text(html_str: str) -> str:
    import html as _html
    t = re.sub(r"(?i)<br\s*/?>", "\n", html_str or "")
    t = re.sub(r"(?i)</(p|div|h[1-6]|li|tr)>", "\n", t)
    t = re.sub(r"(?i)<li[^>]*>", "• ", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = _html.unescape(t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def _xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _text_to_storage(body: str) -> str:
    """Wrap plain text into Confluence 'storage' (XHTML) paragraphs."""
    paras = [p.strip() for p in (body or "").split("\n\n") if p.strip()]
    return "".join(f"<p>{_xml_escape(p).replace(chr(10), '<br/>')}</p>" for p in paras) or "<p></p>"


def _confluence_user_ids(base, email, token, name, limit=5):
    """Resolve a person's name (or part of it) to their Atlassian accountId(s).
    Returns a list of (accountId, displayName). CQL `creator`/`contributor` need an
    accountId — a display name is not accepted — so author searches go through here.
    Uses /rest/api/search/user, the only endpoint that still honours user fields."""
    import requests
    try:
        r = requests.get(f"{base}/rest/api/search/user",
                         params={"cql": f'user.fullname ~ "{name}"', "limit": limit},
                         auth=(email, token), timeout=20)
        if r.status_code != 200:
            return []
        out = []
        for it in r.json().get("results", []):
            u = it.get("user") or {}
            if u.get("accountId"):
                out.append((u["accountId"], u.get("displayName") or "?"))
        return out
    except Exception:
        return []


def confluence_search(query: str = "", cql: str = "", limit: int = 15,
                      space: str = "", author: str = "") -> str:
    """Search Confluence pages. Free text in `query`, an `author` name to restrict to
    that person's pages, an optional `space` key, or a raw `cql` expression. Each result
    shows id · type · [space] · title · creator (date) · last editor (date)."""
    ctx, err = _confluence_ctx()
    if err:
        return err
    base, email, token = ctx
    import requests
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
            users = _confluence_user_ids(base, email, token, author)
            if not users:
                return (f"No Confluence user matched '{author}'. Use confluence_user to "
                        f"find the exact name / accountId, then retry.")
            ids = ", ".join(f'"{aid}"' for aid, _ in users)
            parts.append(f"creator in ({ids})")
        if len(parts) == 1:   # only "type = page" — nothing to actually search on
            return "ERROR: provide `query`, `author`, `space`, or a raw `cql`."
        q = " AND ".join(parts)

    # Content search (/rest/api/content/search) still honours `creator`/`contributor`
    # — unlike /rest/api/search — and lets us expand the author + dates.
    try:
        r = requests.get(f"{base}/rest/api/content/search",
                         params={"cql": q, "limit": n, "expand": "history,version,space"},
                         auth=(email, token), timeout=25)
    except Exception as e:
        return f"ERROR: cannot reach Confluence: {e}"
    if r.status_code != 200:
        return f"ERROR: Confluence returned {r.status_code}: {r.text[:200]}  (CQL: {q})"
    results = r.json().get("results", [])
    if not results:
        return f"No pages matched. (CQL: {q})"
    lines = [f"# CQL: {q}"]
    for c in results:
        cid = c.get("id", "?")
        typ = c.get("type", "?")
        title = c.get("title") or "(untitled)"
        sp = (c.get("space") or {}).get("key", "")
        hist = c.get("history") or {}
        creator = ((hist.get("createdBy") or {}).get("displayName")) or "?"
        created = (hist.get("createdDate") or "")[:10]
        ver = c.get("version") or {}
        editor = ((ver.get("by") or {}).get("displayName")) or "?"
        edited = (ver.get("when") or "")[:10]
        lines.append(f"{cid}\t{typ}\t[{sp}]\t{title}\tby {creator} ({created})"
                     f" · last edit {editor} ({edited})")
    out = "\n".join(lines)
    return out[:4000] + "\n… (truncated)" if len(out) > 4000 else out


def confluence_user(query: str = "") -> str:
    """Resolve a Confluence/Atlassian person by name (or part of it) to their accountId.
    Returns accountId · display name · email (when visible). Use this to get the
    accountId for an author search when a name alone isn't matching."""
    ctx, err = _confluence_ctx()
    if err:
        return err
    base, email, token = ctx
    if not query:
        return "ERROR: provide a name (or part of it) in 'query'."
    users = _confluence_user_ids(base, email, token, query, limit=10)
    if not users:
        return f"No user matched '{query}'."
    # _confluence_user_ids drops email; re-query once to surface it when visible
    import requests
    try:
        r = requests.get(f"{base}/rest/api/search/user",
                         params={"cql": f'user.fullname ~ "{query}"', "limit": 10},
                         auth=(email, token), timeout=20)
        rows = r.json().get("results", []) if r.status_code == 200 else []
    except Exception:
        rows = []
    if rows:
        lines = []
        for it in rows:
            u = it.get("user") or {}
            if u.get("accountId"):
                lines.append(f'{u["accountId"]}\t{u.get("displayName","?")}\t{u.get("email","") or ""}')
        return "\n".join(lines) if lines else f"No user matched '{query}'."
    return "\n".join(f"{aid}\t{name}" for aid, name in users)


def confluence_get(page_id: str = "", title: str = "", space: str = "",
                   max_chars: int = 12000, start: int = 0) -> str:
    """Fetch a Confluence page by `page_id` (or by `title`, optionally scoped to a
    `space` key) and return its title, space, URL and body as readable text.

    Long pages are paginated, not silently cut: at most `max_chars` of body are
    returned starting at character offset `start`. When more remains, the footer says
    exactly how many chars were shown out of the total and the `start` to pass on the
    next call to continue — so the whole page is reachable across calls."""
    ctx, err = _confluence_ctx()
    if err:
        return err
    base, email, token = ctx
    import requests
    try:
        max_chars = max(500, min(int(max_chars or 12000), 40000))
    except (TypeError, ValueError):
        max_chars = 12000
    try:
        start = max(0, int(start or 0))
    except (TypeError, ValueError):
        start = 0
    pid = (page_id or "").strip()
    if not pid:
        if not title.strip():
            return "ERROR: provide 'page_id' or 'title' (optionally with 'space')."
        cql = f'type = page AND title ~ "{title}"' + (f' AND space = "{space}"' if space else "")
        try:
            rr = requests.get(f"{base}/rest/api/search", params={"cql": cql, "limit": 1},
                              auth=(email, token), timeout=25)
        except Exception as e:
            return f"ERROR: cannot reach Confluence: {e}"
        res = rr.json().get("results", []) if rr.status_code == 200 else []
        if not res:
            return f"No page found with title ~ '{title}'."
        pid = (res[0].get("content", {}) or {}).get("id", "")
    try:
        # body.view is rendered HTML (macros expanded) — far cleaner to read than
        # body.storage, which carries raw <ac:…> macro markup. Keep storage as a
        # fallback for the rare page where view comes back empty.
        r = requests.get(f"{base}/rest/api/content/{pid}",
                         params={"expand": "body.view,body.storage,space"},
                         auth=(email, token), timeout=25)
    except Exception as e:
        return f"ERROR: cannot reach Confluence: {e}"
    if r.status_code != 200:
        return f"ERROR: Confluence returned {r.status_code}: {r.text[:200]}"
    d = r.json()
    sp = (d.get("space", {}) or {}).get("key", "")
    bodies = d.get("body", {}) or {}
    raw = (bodies.get("view", {}) or {}).get("value", "") \
        or (bodies.get("storage", {}) or {}).get("value", "")
    body = _html_to_text(raw)
    url = f"{base}/spaces/{sp}/pages/{pid}"
    header = f"# {d.get('title', '')}\nID: {pid}  ·  Space: {sp}\nURL: {url}\n\n"
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


def confluence_create(space: str = "", title: str = "", body: str = "") -> str:
    """Create a new Confluence page in `space` (a space KEY) with `title` and `body`
    (plain text; blank lines become paragraphs). Write operation — gated by the TUI."""
    ctx, err = _confluence_ctx()
    if err:
        return err
    base, email, token = ctx
    space = (space or os.environ.get("CONFLUENCE_SPACE", "")).strip()  # default space (config)
    if not (space and title.strip() and body.strip()):
        return "ERROR: provide 'title', 'body', and a 'space' key (or set a default confluence.space)."
    import requests
    payload = {"type": "page", "title": title.strip(), "space": {"key": space},
               "body": {"storage": {"value": _text_to_storage(body), "representation": "storage"}}}
    try:
        r = requests.post(f"{base}/rest/api/content", json=payload, auth=(email, token), timeout=25)
    except Exception as e:
        return f"ERROR: cannot reach Confluence: {e}"
    if r.status_code not in (200, 201):
        return f"ERROR: Confluence returned {r.status_code}: {r.text[:300]}"
    pid = r.json().get("id", "?")
    return f"Created page '{title.strip()}' (id {pid}) in {space.strip()}: {base}/spaces/{space.strip()}/pages/{pid}"


def confluence_comment(page_id: str = "", body: str = "") -> str:
    """Add a comment to a Confluence page by id. Write operation — gated by the TUI."""
    ctx, err = _confluence_ctx()
    if err:
        return err
    base, email, token = ctx
    if not (page_id.strip() and body.strip()):
        return "ERROR: provide 'page_id' and 'body'."
    import requests
    payload = {"type": "comment", "container": {"id": page_id.strip(), "type": "page"},
               "body": {"storage": {"value": _text_to_storage(body), "representation": "storage"}}}
    try:
        r = requests.post(f"{base}/rest/api/content", json=payload, auth=(email, token), timeout=25)
    except Exception as e:
        return f"ERROR: cannot reach Confluence: {e}"
    if r.status_code not in (200, 201):
        return f"ERROR: Confluence returned {r.status_code}: {r.text[:300]}"
    return f"Comment added to page {page_id.strip()}."


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 text file and return its contents.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Path to the file."}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write text to a file, creating parent dirs. Overwrites existing files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List entries in a directory (one level, not recursive) with file and folder sizes.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "default": "."}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_directory",
            "description": "Return the current working directory.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Regex search across files. Returns 'path:line: text' matches, up to 200.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Python regex."},
                    "path": {"type": "string", "default": ".", "description": "File or directory."},
                    "glob": {"type": "string", "default": "**/*", "description": "Glob filter when path is a dir."},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_models",
            "description": "List all models (directories) in F:\\models, showing each folder name and its total size.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_files",
            "description": "Find files matching a glob pattern (e.g. '**/*.py') under a directory. Returns matching paths.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name_glob": {"type": "string", "description": "Glob pattern, e.g. '**/*.txt' or 'src/*.py'."},
                    "path": {"type": "string", "default": ".", "description": "Directory to search in."},
                },
                "required": ["name_glob"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_lines",
            "description": "Read a range of lines [start, end] (1-indexed, inclusive) from a text file. Output is line-numbered.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start": {"type": "integer", "description": "First line (1-indexed)."},
                    "end": {"type": "integer", "description": "Last line (inclusive)."},
                },
                "required": ["path", "start", "end"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "head",
            "description": "Return the first N lines of a text file (numbered). Default N=20.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "n": {"type": "integer", "default": 20},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tail",
            "description": "Return the last N lines of a text file (numbered). Default N=20.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "n": {"type": "integer", "default": 20},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "count_lines",
            "description": "Count lines, words and bytes of a text file (like 'wc'). Returns 'lines=.. words=.. bytes=..'.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stat_path",
            "description": "Get metadata of a file or directory: type, size, modified time, and child count for directories.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tree",
            "description": "Recursive indented listing of a directory up to `depth` levels deep. Default depth=2.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "default": "."},
                    "depth": {"type": "integer", "default": 2},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_in_files",
            "description": "Regex search that returns only the FILES containing the pattern plus how many matches each has (not the lines). Use to locate which file contains something.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Python regex."},
                    "path": {"type": "string", "default": ".", "description": "File or directory."},
                    "glob": {"type": "string", "default": "**/*", "description": "Glob filter when path is a dir."},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Make a targeted edit to a file by replacing an exact, unique text snippet with a new one. The old_string must appear exactly once. Use this to change a specific value/section without rewriting the whole file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string", "description": "Exact text to replace (must be unique in the file)."},
                    "new_string": {"type": "string", "description": "Replacement text."},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_python",
            "description": "Run a Python 3 snippet and get its printed output. This is your GO-TO tool for ANY computation, data processing, or logic — math, counting, summing/averaging, parsing or aggregating CSV/JSON, regex extraction, date/time, and reading/transforming/writing files programmatically. Whenever a question needs a calculation or data manipulation, use this instead of guessing or doing it in your head. If the user explicitly says to 'use Python' (or run/execute Python), ALWAYS call this tool. Remember to print() the result. Working dir = current directory, 20s timeout. Keep snippets short; for long scripts, write_file first then run it.",
            "parameters": {
                "type": "object",
                "properties": {"code": {"type": "string", "description": "Python source to run with `python -c`."}},
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_powershell",
            "description": "Execute a PowerShell command in the current directory and return its output. Use for system/filesystem inspection or scripting on Windows. 25s timeout. Destructive operations are blocked.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string", "description": "PowerShell command to run."}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_bash",
            "description": "Execute a bash/sh shell command in the current directory and return its output. Use this for shell/system tasks on Linux or macOS (the equivalent of run_powershell on Windows). 25s timeout. Destructive operations are blocked.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string", "description": "bash/sh command to run."}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_get_secret",
            "description": "Read a secret from HashiCorp Vault. For KV v2 the path looks like 'secret/data/<name>'. Returns the secret's fields as a JSON object (e.g. host, port, username, password).",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Vault read path, e.g. 'secret/data/mac'."}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web and get ranked results (title, URL, snippet) plus a short synthesized answer when available. Use this for current events, recent facts, prices, documentation, or anything outside your training knowledge — don't guess.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query."},
                    "max_results": {"type": "integer", "description": "How many results to return (1-10, default 5)."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "jira_search",
            "description": "Search Jira issues. Give free text in 'query' (matched against summary, description and comments) or a raw JQL expression in 'jql' for full control. Returns a plain list of matching issues (key, status, summary), newest first. Use this to find an issue when you don't know its key.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Free-text search, e.g. 'progressives lose decimals'."},
                    "jql": {"type": "string", "description": "Raw JQL, e.g. 'project = ABC AND status = Open'. Overrides 'query'."},
                    "limit": {"type": "integer", "description": "Max issues to return (1-50, default 15)."},
                    "project": {"type": "string", "description": "Optional project key to scope a free-text query."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "jira_get",
            "description": "Fetch a single Jira issue by its key (e.g. 'PROJ-1234') and return its full details: summary, status, assignee, reporter and description.",
            "parameters": {
                "type": "object",
                "properties": {"key": {"type": "string", "description": "The issue key, e.g. 'PROJ-1234'."}},
                "required": ["key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "jira_assign",
            "description": "Assign a Jira issue to a user. The assignee is an email or an EXACT display name; use 'me' for yourself, 'default' for the project default, or 'x' to unassign. This MODIFIES the issue.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "The issue key, e.g. 'PROJ-1234'."},
                    "assignee": {"type": "string", "description": "Email, exact display name, 'me', 'default', or 'x' (unassign)."},
                },
                "required": ["key", "assignee"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "jira_comment",
            "description": "Add a comment to a Jira issue. This MODIFIES the issue (posts a visible comment).",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "The issue key, e.g. 'PROJ-1234'."},
                    "body": {"type": "string", "description": "The comment text. Supports newlines."},
                },
                "required": ["key", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "confluence_search",
            "description": "Search Confluence pages by free text in 'query', and/or by 'author' (a person's name — restricts results to pages that person created), optionally scoped to a 'space' key. Or pass a raw CQL expression in 'cql'. Each result line includes the page id, type, space, title, creator (+date) and last editor (+date), so you can tell who wrote a page. To find pages someone authored, pass their name in 'author' (it is resolved to an accountId automatically); if the name doesn't match, use confluence_user first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Free-text search, e.g. 'release checklist'."},
                    "author": {"type": "string", "description": "A person's name (or part of it). Restricts to pages CREATED by that person. Resolved to an accountId via the user directory."},
                    "cql": {"type": "string", "description": "Raw CQL, e.g. 'space = ENG AND title ~ \"runbook\"'. Overrides query/author/space."},
                    "limit": {"type": "integer", "description": "Max pages to return (1-50, default 15)."},
                    "space": {"type": "string", "description": "Optional space key to scope the search."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "confluence_user",
            "description": "Resolve a Confluence/Atlassian person by name (or part of it) to their accountId. Returns accountId, display name and email (when visible). Use this when an author search by name isn't matching, to get the exact accountId.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "A person's name or part of it, e.g. 'Ramon'."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "confluence_get",
            "description": "Fetch a Confluence page by 'page_id' (or by 'title', optionally scoped to a 'space' key) and return its title, space, URL and body as readable text. Long pages are paginated: at most 'max_chars' of body are returned from offset 'start', and the footer tells you how many chars remain and the 'start' to pass next. To read a whole long page, keep calling with the 'start' from the previous footer until it says end of page — do NOT guess or invent the missing content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "page_id": {"type": "string", "description": "The page id, e.g. '123456'."},
                    "title": {"type": "string", "description": "Page title to look up if you don't have the id."},
                    "space": {"type": "string", "description": "Optional space key to disambiguate a title lookup."},
                    "max_chars": {"type": "integer", "description": "Max body chars to return per call (500-40000, default 12000)."},
                    "start": {"type": "integer", "description": "Body character offset to start from (default 0). Use the value from the previous call's footer to continue a long page."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "confluence_create",
            "description": "Create a NEW Confluence page. Defaults to the configured space if 'space' is omitted. This WRITES to Confluence (publishes a page).",
            "parameters": {
                "type": "object",
                "properties": {
                    "space": {"type": "string", "description": "Space KEY (e.g. 'SI'). Optional — defaults to the configured space."},
                    "title": {"type": "string", "description": "The page title."},
                    "body": {"type": "string", "description": "Page body as plain text; blank lines become paragraphs."},
                },
                "required": ["title", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "confluence_comment",
            "description": "Add a comment to a Confluence page by id. This WRITES to Confluence (posts a visible comment).",
            "parameters": {
                "type": "object",
                "properties": {
                    "page_id": {"type": "string", "description": "The page id, e.g. '123456'."},
                    "body": {"type": "string", "description": "The comment text. Supports newlines."},
                },
                "required": ["page_id", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch a URL and return its main page text (cleaned). Use to read a full page — e.g. a result returned by web_search, or a known link. Returns up to max_chars characters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to fetch."},
                    "max_chars": {"type": "integer", "description": "Max characters of content to return (default 8000)."},
                },
                "required": ["url"],
            },
        },
    },
]


DISPATCH = {
    "read_file": read_file,
    "write_file": write_file,
    "list_dir": list_dir,
    "get_current_directory": get_current_directory,
    "grep": grep,
    "list_models": list_models,
    "find_files": find_files,
    "read_lines": read_lines,
    "head": head,
    "tail": tail,
    "count_lines": count_lines,
    "stat_path": stat_path,
    "tree": tree,
    "find_in_files": find_in_files,
    "edit_file": edit_file,
    "run_python": run_python,
    "run_powershell": run_powershell,
    "run_bash": run_bash,
    "vault_get_secret": vault_get_secret,
    "web_search": web_search,
    "web_fetch": web_fetch,
    "jira_search": jira_search,
    "jira_get": jira_get,
    "jira_assign": jira_assign,
    "jira_comment": jira_comment,
    "confluence_search": confluence_search,
    "confluence_user": confluence_user,
    "confluence_get": confluence_get,
    "confluence_create": confluence_create,
    "confluence_comment": confluence_comment,
}


def call_tool(name: str, arguments: str | dict) -> str:
    if name not in DISPATCH:
        return f"ERROR: unknown tool {name}"
    # Malformed JSON in the arguments must NOT crash the turn: we return a
    # recoverable error so the model retries with valid JSON.
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else arguments
    except (json.JSONDecodeError, TypeError) as e:
        return (f"ERROR: invalid JSON in arguments for '{name}' ({e}). "
                f"Re-issue the call with VALID JSON — escape newlines as \\n and quotes as \\\".")
    if not isinstance(args, dict):
        return f"ERROR: arguments for '{name}' must be a JSON object."
    try:
        result = DISPATCH[name](**args)
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"
    return result if isinstance(result, str) else json.dumps(result)
