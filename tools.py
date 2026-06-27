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
            "name": "vault_get_secret",
            "description": "Read a secret from HashiCorp Vault. For KV v2 the path looks like 'secret/data/<name>'. Returns the secret's fields as a JSON object (e.g. host, port, username, password).",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Vault read path, e.g. 'secret/data/mac'."}},
                "required": ["path"],
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
    "vault_get_secret": vault_get_secret,
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
