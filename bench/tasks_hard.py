"""Tier HARDCORE: incidente real de dev/sysadmin sobre 'orders-service'.

~20 tareas multi-paso con graders ESTRICTOS: parsear tracebacks, correlacionar
configs, encontrar un secreto, ARREGLAR un bug y que pase su self-test (se ejecuta
de verdad), bumps de versión multi-fichero, análisis de access logs, etc.
Graders firman (answer, root).
"""

import subprocess
import sys
from pathlib import Path

from tasks import has_number, contains, contains_any


def rf(root, rel):
    p = Path(root) / rel
    return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else None


def _run_py(root, rel):
    try:
        r = subprocess.run([sys.executable, str(Path(root) / rel)],
                           capture_output=True, text=True, timeout=20, cwd=str(Path(root) / "src"))
        return r.returncode, (r.stdout + r.stderr)
    except Exception as e:
        return 1, str(e)


def build_tasks_hard(truth):
    T = truth
    tasks = []

    def add(id, category, prompt, expect, check, negative=False):
        tasks.append({"id": id, "category": category, "prompt": prompt,
                      "expect_tools": expect, "check": check, "negative": negative})

    READ = ["read_file", "head", "read_lines", "grep", "find_in_files"]

    # ---------- incidente / logs ----------
    add("incident_exc", "incident",
        "Read logs/app.log. The service threw one unhandled exception. Report three things: "
        "(a) the exception type, (b) the source file and the line number where it was raised, "
        "and (c) the name of the function it was raised in.",
        READ,
        lambda a, r: contains(a, "keyerror") and contains(a, "orders.py") and has_number(a, 4) and contains(a, "process_order"))

    add("err_window", "incident",
        "In logs/app.log, how many ERROR-level log lines happened between 10:00:00 and 10:05:00 "
        "(inclusive)? Answer with a single number.",
        READ + ["run_python"],
        lambda a, r: has_number(a, 3))

    add("err_total", "incident",
        "How many ERROR-level lines are there in total in logs/app.log? Answer with a number.",
        READ + ["run_python", "run_powershell"],
        lambda a, r: has_number(a, 5))

    add("rootcause_keys", "incident",
        "Using logs/app.log, identify the function where the crash happened, then open the "
        "source file that defines it and report the two keys it reads from the 'order' dict.",
        READ,
        lambda a, r: contains(a, "total") and contains(a, "discount"))

    # ---------- access log ----------
    add("count_500", "accesslog",
        "Using Python, parse logs/access.log (Apache common log format) and report how many "
        "requests returned HTTP status 500.",
        ["run_python"],
        lambda a, r: has_number(a, T["n_500"]))

    add("top_ip", "accesslog",
        "Analyze logs/access.log and report which client IP made the most requests and exactly "
        "how many requests it made.",
        ["run_python", "run_powershell"],
        lambda a, r: contains(a, T["top_ip"]) and has_number(a, T["top_ip_count"]))

    # ---------- seguridad / config ----------
    add("hardcoded_secret", "security",
        "There is a hardcoded API secret committed in the source code. Report the file name it "
        "lives in and the exact secret value.",
        ["grep", "find_in_files", "read_file"],
        lambda a, r: contains(a, "auth.py") and contains(a, T["secret"]))

    add("config_diff", "config",
        "Compare config/config.dev.json and config/config.prod.json and list every top-level "
        "key whose value differs between the two files.",
        READ + ["run_python"],
        lambda a, r: all(contains(a, k) for k in T["diff_keys"]))

    add("port_mismatch", "config",
        "The DB port in .env does not match the host port exposed for the 'db' service in "
        "config/docker-compose.yml. Report both port numbers.",
        READ,
        lambda a, r: has_number(a, T["env_db_port"]) and has_number(a, T["compose_host_port"]))

    # ---------- análisis de código ----------
    add("imports_db", "code",
        "Which Python files inside src/ import the 'db' module? List their file names.",
        ["grep", "find_in_files", "read_file"],
        lambda a, r: contains(a, "app.py") and contains(a, "orders.py") and "db.py" not in a.lower())

    add("db_connstr", "code",
        "Look at src/db.py. If the DB_PORT environment variable is NOT set, what exact postgres "
        "connection string does connect() return?",
        READ,
        lambda a, r: contains(a, "postgres://localhost:5432/orders"))

    add("req_audit", "code",
        "How many dependencies are pinned in requirements.txt, and which version of flask is pinned?",
        READ,
        lambda a, r: has_number(a, 4) and contains(a, "2.0.1"))

    # ---------- el bug real: arreglar y que pase el self-test ----------
    add("fix_parser", "fix",
        "src/parser.py has an off-by-one bug in the chunk() function. Fix it so that running the "
        "file executes its built-in self-test successfully and prints 'ok'. Do not change the test.",
        ["edit_file", "read_file", "run_python", "write_file"],
        lambda a, r: (lambda rc_out: rc_out[0] == 0 and "ok" in rc_out[1].lower())(_run_py(r, "src/parser.py")))

    # ---------- edición multi-fichero ----------
    add("version_bump", "edit",
        "Bump the project version from 1.4.2 to 1.5.0 in all three places: pyproject.toml, "
        "src/__init__.py, and CHANGELOG.md (add a new '## 1.5.0' heading at the top of the "
        "changelog). Keep each file valid.",
        ["edit_file", "read_file", "write_file"],
        lambda a, r: 'version = "1.5.0"' in (rf(r, "pyproject.toml") or "")
                     and '1.5.0' in (rf(r, "src/__init__.py") or "")
                     and "## 1.5.0" in (rf(r, "CHANGELOG.md") or "")
                     and 'version = "1.4.2"' not in (rf(r, "pyproject.toml") or ""))

    # ---------- python sobre el código ----------
    add("slugify_run", "python",
        "Using Python, import the slugify function from src/utils.py and report what "
        "slugify('  Hello World  ') returns.",
        ["run_python"],
        lambda a, r: contains(a, "hello-world"))

    add("purchases_total", "python",
        "In data/events.csv, what is the total 'amount' across rows whose type is 'purchase' only? "
        "Answer with the number.",
        ["run_python", "read_file"],
        lambda a, r: has_number(a, T["purchases_total"]))

    add("emails_domains", "python",
        "Extract all email addresses from data/contacts.txt and report how many UNIQUE domains "
        "appear. Answer with a number.",
        ["run_python", "grep"],
        lambda a, r: has_number(a, T["domain_count"]))

    # ---------- sysadmin / powershell ----------
    add("largest_file_ps", "powershell",
        "Using PowerShell, find the single largest file in the entire project tree and report its name.",
        ["run_powershell"],
        lambda a, r: contains(a, T["largest_file"]))

    add("py_lines_ps", "powershell",
        "Using PowerShell, count the TOTAL number of lines across all .py files under src/ "
        "(including subfolders) and report the number.",
        ["run_powershell"],
        lambda a, r: has_number(a, T["py_lines_total"]))

    # ---------- negativo / robustez ----------
    add("neg_tests", "negative",
        "Run the test suite located in the tests/ directory and report how many tests passed.",
        ["list_dir", "find_files", "run_python", "tree"],
        lambda a, r: contains_any(a, "no such", "does not exist", "doesn't exist", "no existe",
                                  "not found", "no tests", "there are no", "no hay", "empty", "couldn't find"),
        negative=True)

    return tasks
