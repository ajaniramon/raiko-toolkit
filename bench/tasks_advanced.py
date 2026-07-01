"""ADVANCED tier of the benchmark: the model writes files, edits the specific
chunk it needs to, uses Python and uses PowerShell. The graders verify the
resulting FILESYSTEM (check(answer, root)), not just the text.

Each task runs on a freshly rebuilt sandbox (writes mutate the tree), so the
tasks don't interfere with each other.
"""

import json
import platform
from pathlib import Path

from tasks import has_number, contains, contains_any

# OS-adaptive shell: PowerShell on Windows, bash elsewhere. Graders verify the
# filesystem effect (OS-agnostic), so the same task is fair on any platform.
_IS_WIN = platform.system() == "Windows"
SHELL_TOOL = "run_powershell" if _IS_WIN else "run_bash"
SHELL_NAME = "PowerShell" if _IS_WIN else "bash"


def rf(root, rel):
    p = Path(root) / rel
    return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else None


def _json(root, rel):
    c = rf(root, rel)
    try:
        return json.loads(c)
    except Exception:
        return None


def build_tasks_adv():
    tasks = []

    def add(id, category, prompt, expect_tools, check):
        tasks.append({"id": id, "category": category, "prompt": prompt,
                      "expect_tools": expect_tools, "check": check, "negative": False})

    WRITE = ["write_file", "run_python", SHELL_TOOL]
    EDIT = ["edit_file", "write_file", "run_python", SHELL_TOOL]

    # ---------------- write files ----------------
    add("w_create", "write",
        "Create a new file named output.txt containing exactly this text: Hello Nebula",
        WRITE, lambda a, r: (rf(r, "output.txt") or "").strip() == "Hello Nebula")
    add("w_md", "write",
        "Create a file summary.md with a level-1 Markdown heading that says 'Summary', and on the next line the text 'status: done'.",
        WRITE, lambda a, r: (lambda c: bool(c) and contains(c, "# summary") and contains(c, "status: done"))(rf(r, "summary.md") or ""))
    add("w_json", "write",
        "Create a file meta.json containing a JSON object with two keys: \"name\" set to \"nebula\" and \"count\" set to 3.",
        WRITE, lambda a, r: (lambda d: bool(d) and str(d.get("name")).lower() == "nebula" and int(d.get("count", -1)) == 3)(_json(r, "meta.json")))
    add("w_multiline", "write",
        "Create a file lines.txt with exactly three lines, in this order: alpha, beta, gamma (one per line).",
        WRITE, lambda a, r: [l.strip() for l in (rf(r, "lines.txt") or "").splitlines() if l.strip()][:3] == ["alpha", "beta", "gamma"])

    # ---------------- edit the specific chunk ----------------
    add("e_port", "edit",
        "In config.ini, change the server port value from 8080 to 9999, leaving everything else unchanged.",
        EDIT, lambda a, r: (lambda c: bool(c) and "9999" in c and "8080" not in c and "timeout=30" in c)(rf(r, "config.ini") or ""))
    add("e_debug", "edit",
        "In settings.json, change the value of app.debug from false to true. Keep the file valid JSON.",
        EDIT, lambda a, r: (lambda d: bool(d) and d.get("app", {}).get("debug") is True)(_json(r, "settings.json")))
    add("e_version", "edit",
        "In README.md, update the version number from 2.4.1 to 2.5.0.",
        EDIT, lambda a, r: (lambda c: bool(c) and "2.5.0" in c and "2.4.1" not in c)(rf(r, "README.md") or ""))
    add("e_magic", "edit",
        "In src/utils.py, change the MAGIC constant value from 42 to 100.",
        EDIT, lambda a, r: (lambda c: bool(c) and ("MAGIC = 100" in c or "MAGIC=100" in c) and "42" not in c.split("def")[0])(rf(r, "src/utils.py") or ""))
    add("e_rename", "edit",
        "In src/app.py, rename the function 'run_app' to 'start_app' (keep its body).",
        EDIT, lambda a, r: (lambda c: bool(c) and "start_app" in c and "run_app" not in c)(rf(r, "src/app.py") or ""))
    add("e_append_user", "edit",
        "Append a new row to data/users.csv for a user with id 9, name ivan, role user, age 30, score 75, in the same comma-separated format.",
        EDIT, lambda a, r: any(
            [x.strip() for x in line.split(",")] == ["9", "ivan", "user", "30", "75"]
            for line in (rf(r, "data/users.csv") or "").splitlines()))

    # ---------------- Python ----------------
    add("p_sum", "python",
        "Using Python, compute the sum of all values in the 'score' column of data/users.csv and report the resulting number.",
        ["run_python"], lambda a, r: has_number(a, 636))
    add("p_squares", "python",
        "Using Python, create a file squares.txt containing the squares of the integers 1 to 10, one per line.",
        ["run_python"], lambda a, r: [l.strip() for l in (rf(r, "squares.txt") or "").splitlines() if l.strip()] == [str(i * i) for i in range(1, 11)])
    add("p_transform", "python",
        "Using Python, read data/sales.csv and write the total of its 'amount' column into a new file named sales_total.txt.",
        ["run_python"], lambda a, r: has_number(rf(r, "sales_total.txt") or "", 4950))
    add("p_words", "python",
        "Using Python, count how many words are in docs/guide.md and report the number.",
        ["run_python"], lambda a, r: has_number(a, len((rf(r, "docs/guide.md") or "").split())))
    add("p_jsonfield", "python",
        "Using Python, read settings.json and report the value of limits.max_users.",
        ["run_python"], lambda a, r: has_number(a, 1000))
    add("p_combined", "python",
        "Read the MAGIC constant from src/utils.py, then use Python to compute MAGIC multiplied by 10, and write the result into a file named magic10.txt.",
        ["run_python", "read_file", "grep"], lambda a, r: has_number(rf(r, "magic10.txt") or "", 420))

    # ---------------- Shell (OS-adaptive: PowerShell on Windows, bash elsewhere) ----------------
    add("sh_countpy", "shell",
        f"Using {SHELL_NAME}, count how many .py files exist under the src directory (including subfolders) and report the number.",
        [SHELL_TOOL], lambda a, r: has_number(a, len(list((Path(r) / "src").rglob("*.py")))))
    add("sh_write", "shell",
        f"Using {SHELL_NAME}, create a file named sh_out.txt containing the text: from shell",
        [SHELL_TOOL], lambda a, r: "from shell" in (rf(r, "sh_out.txt") or "").lower())
    add("sh_lines", "shell",
        f"Using {SHELL_NAME}, report how many lines the file big.log has.",
        [SHELL_TOOL], lambda a, r: has_number(a, len((rf(r, "big.log") or "").splitlines())))
    add("sh_listdir", "shell",
        f"Using {SHELL_NAME}, list the names of the files in the data directory.",
        [SHELL_TOOL], lambda a, r: contains(a, "users.csv") and contains(a, "sales.csv"))
    add("sh_port", "shell",
        f"Using {SHELL_NAME}, read config.ini and report the configured port number.",
        [SHELL_TOOL], lambda a, r: has_number(a, 8080))

    # ---------------- mixed / multistep ----------------
    add("m_edit_verify", "mixed",
        "Change the timeout value in config.ini from 30 to 45, then read the file back to confirm the new value and report it.",
        EDIT, lambda a, r: "timeout=45" in (rf(r, "config.ini") or ""))
    add("m_py_dirfile", "mixed",
        "Using Python, create a new folder named 'out' and inside it a file result.txt containing the text 'ok'.",
        ["run_python"], lambda a, r: "ok" in (rf(r, "out/result.txt") or "").lower())
    add("m_append_log", "mixed",
        "Append a new line with the text 'ERROR new failure' to the end of logs/app.log.",
        EDIT, lambda a, r: "error new failure" in (rf(r, "logs/app.log") or "").lower())

    # extra generated families (write/edit/python/powershell at scale)
    from tasks_advanced_extra import build_extra_adv
    tasks.extend(build_extra_adv())

    return tasks
