"""Extra ADVANCED-tier tasks (write / edit / python / powershell), generated
from the fixture source data. Graders verify the resulting FILESYSTEM
(check(answer, root)). Every expected value is derived from the same source
structures the sandbox is built from, so the answers can't drift.

IDs are prefixed 'xa_' to never collide with tasks_advanced.py.
"""

from pathlib import Path

from tasks import has_number, contains
from tasks_advanced import rf, _json
from fixtures import USERS, SALES, INVENTORY
from fixtures_extra import EMPLOYEES, PRODUCTS

# ---- expected aggregates (computed once, deterministic) ----
USERS_SCORE = sum(u[4] for u in USERS)
USERS_AGE = sum(u[3] for u in USERS)
EMP_SALARY = sum(e[3] for e in EMPLOYEES)
EMP_YEARS = sum(e[4] for e in EMPLOYEES)
PROD_PRICE = sum(p[2] for p in PRODUCTS)
PROD_STOCK = sum(p[3] for p in PRODUCTS)
PROD_VALUE = sum(p[2] * p[3] for p in PRODUCTS)
SALES_TOTAL = sum(a for _, a in SALES)
INV_QTY = sum(q for _, q, _ in INVENTORY)
INV_VALUE = sum(q * pr for _, q, pr in INVENTORY)

WORD_BANK = [
    "nebula", "orion", "vega", "rigel", "sirius", "altair", "antares", "polaris",
    "atlas", "titan", "europa", "io", "ganymede", "callisto", "phobos", "deimos",
    "ceres", "vesta", "pallas", "juno", "hydra", "kraken", "phoenix", "draco",
    "lyra", "cygnus", "aquila", "corvus", "tucana", "pavo", "indus", "grus",
    "carina", "puppis", "vela", "norma", "ara", "lupus", "crux", "musca",
    "apus", "octans", "mensa", "dorado", "reticulum", "horologium", "caelum",
    "pictor", "volans", "chamaeleon", "tucan", "fornax", "sculptor", "phoenix2",
    "eridanus", "lepus", "monoceros", "canis", "puppis2", "antlia",
]


def build_extra_adv() -> list:
    tasks = []

    def add(id, category, prompt, expect_tools, check):
        tasks.append({"id": id, "category": category, "prompt": prompt,
                      "expect_tools": expect_tools, "check": check, "negative": False})

    WRITE = ["write_file", "run_python", "run_powershell"]
    EDIT = ["edit_file", "write_file", "run_python", "run_powershell"]
    PY = ["run_python"]
    PS = ["run_powershell"]

    # ---------- write a file with exact text (60) ----------
    for i in range(60):
        w = WORD_BANK[i % len(WORD_BANK)] + (str(i) if i >= len(WORD_BANK) else "")
        fn = f"gen_{i:02d}.txt"
        add(f"xa_text_{i:02d}", "write",
            f"Create a new file named {fn} containing exactly this text: {w}",
            WRITE, (lambda fn, w: lambda a, r: (rf(r, fn) or "").strip() == w)(fn, w))

    # ---------- write a JSON object (14) ----------
    for i in range(14):
        label = WORD_BANK[i]
        num = (i + 1) * 3
        fn = f"obj_{i:02d}.json"
        add(f"xa_json_{i:02d}", "write",
            f'Create a file {fn} containing a JSON object with key "label" set to '
            f'"{label}" and key "num" set to {num}.',
            WRITE,
            (lambda fn, label, num: lambda a, r: (lambda d: bool(d) and str(d.get("label")).lower() == label and int(d.get("num", -1)) == num)(_json(r, fn)))(fn, label, num))

    # ---------- write a multiline list (12) ----------
    for i in range(12):
        trio = [WORD_BANK[(i * 3 + k) % len(WORD_BANK)] for k in range(3)]
        fn = f"list_{i:02d}.txt"
        add(f"xa_list_{i:02d}", "write",
            f"Create a file {fn} with exactly three lines, in this order: "
            f"{trio[0]}, {trio[1]}, {trio[2]} (one per line).",
            WRITE,
            (lambda fn, trio: lambda a, r: [l.strip() for l in (rf(r, fn) or "").splitlines() if l.strip()][:3] == trio)(fn, trio))

    # ---------- edit config.ini port (12) ----------
    for i, port in enumerate([3000, 3100, 4200, 4300, 5400, 5500, 6600, 6700, 7700, 7100, 2200, 2300]):
        add(f"xa_eport_{i:02d}", "edit",
            f"In config.ini, change the server port value from 8080 to {port}, leaving everything else unchanged.",
            EDIT,
            (lambda port: lambda a, r: (lambda c: bool(c) and f"port={port}" in c.replace(" ", "") and "8080" not in c)(rf(r, "config.ini") or ""))(port))

    # ---------- edit settings.json server.port (6) ----------
    for i, port in enumerate([7001, 7002, 7003, 7004, 7005, 7006]):
        add(f"xa_setport_{i:02d}", "edit",
            f"In settings.json, change server.port from 9090 to {port}. Keep the file valid JSON.",
            EDIT,
            (lambda port: lambda a, r: (lambda d: bool(d) and d.get("server", {}).get("port") == port)(_json(r, "settings.json")))(port))

    # ---------- edit services.json replicas (6) ----------
    for i, n in enumerate([2, 3, 8, 10, 12, 16]):
        add(f"xa_svcrep_{i:02d}", "edit",
            f"In config/services.json, change the 'replicas' value from 6 to {n}. Keep it valid JSON.",
            EDIT,
            (lambda n: lambda a, r: (lambda d: bool(d) and d.get("replicas") == n)(_json(r, "config/services.json")))(n))

    # ---------- edit README version (6) ----------
    for i, v in enumerate(["2.4.2", "2.5.0", "3.0.0", "2.4.5", "2.6.1", "2.9.9"]):
        add(f"xa_ver_{i:02d}", "edit",
            f"In README.md, update the version number from 2.4.1 to {v}.",
            EDIT,
            (lambda v: lambda a, r: (lambda c: bool(c) and v in c and "2.4.1" not in c)(rf(r, "README.md") or ""))(v))

    # ---------- edit utils MAGIC (6) ----------
    for i, n in enumerate([7, 13, 50, 99, 123, 256]):
        add(f"xa_magic_{i:02d}", "edit",
            f"In src/utils.py, change the MAGIC constant value from 42 to {n}.",
            EDIT,
            (lambda n: lambda a, r: (lambda c: bool(c) and (f"MAGIC = {n}" in c or f"MAGIC={n}" in c) and "42" not in c.split("def")[0])(rf(r, "src/utils.py") or ""))(n))

    # ---------- python: compute an aggregate, report the number (varied) ----------
    py_specs = [
        ("the sum of the 'salary' column in data/employees.csv", EMP_SALARY),
        ("the sum of the 'years' column in data/employees.csv", EMP_YEARS),
        ("the sum of the 'price' column in data/products.csv", PROD_PRICE),
        ("the sum of the 'stock' column in data/products.csv", PROD_STOCK),
        ("the total inventory value (sum of price*stock) in data/products.csv", PROD_VALUE),
        ("the sum of the 'amount' column in data/sales.csv", SALES_TOTAL),
        ("the sum of the 'score' column in data/users.csv", USERS_SCORE),
        ("the sum of the 'age' column in data/users.csv", USERS_AGE),
        ("the sum of the 'qty' column in data/inventory.csv", INV_QTY),
        ("the total inventory value (sum of qty*price) in data/inventory.csv", INV_VALUE),
        ("the number of employees in the 'eng' department in data/employees.csv", sum(1 for e in EMPLOYEES if e[2] == "eng")),
        ("the number of products in the 'tools' category in data/products.csv", sum(1 for p in PRODUCTS if p[1] == "tools")),
        ("the highest salary in data/employees.csv", max(e[3] for e in EMPLOYEES)),
        ("the lowest salary in data/employees.csv", min(e[3] for e in EMPLOYEES)),
        ("the number of employees earning more than 60000 in data/employees.csv", sum(1 for e in EMPLOYEES if e[3] > 60000)),
        ("the number of products that are out of stock (stock 0) in data/products.csv", sum(1 for p in PRODUCTS if p[3] == 0)),
        ("the highest price in data/products.csv", max(p[2] for p in PRODUCTS)),
        ("the number of users older than 40 in data/users.csv", sum(1 for u in USERS if u[3] > 40)),
        ("the number of admin users in data/users.csv", sum(1 for u in USERS if u[2] == "admin")),
        ("the highest score in data/users.csv", max(u[4] for u in USERS)),
        ("the average (floor) salary in data/employees.csv", EMP_SALARY // len(EMPLOYEES)),
        ("the number of rows (excluding header) in data/products.csv", len(PRODUCTS)),
        ("the number of rows (excluding header) in data/employees.csv", len(EMPLOYEES)),
        ("the difference between the highest and lowest salary in data/employees.csv", max(e[3] for e in EMPLOYEES) - min(e[3] for e in EMPLOYEES)),
    ]
    for i, (desc, val) in enumerate(py_specs):
        add(f"xa_py_{i:02d}", "python",
            f"Using Python, compute {desc} and report the resulting number.",
            PY, (lambda val: lambda a, r: has_number(a, val))(val))

    # ---------- python: compute and write the number to a file (12) ----------
    for i, (desc, val) in enumerate(py_specs[:12]):
        fn = f"out_{i:02d}.txt"
        add(f"xa_pyw_{i:02d}", "python",
            f"Using Python, compute {desc} and write ONLY the resulting number into a new file named {fn}.",
            PY, (lambda fn, val: lambda a, r: has_number(rf(r, fn) or "", val))(fn, val))

    # ---------- powershell: count files by extension (6) ----------
    for ext in [".py", ".csv", ".log", ".md", ".json", ".txt"]:
        add(f"xa_ps_ext_{ext[1:]}", "powershell",
            f"Using PowerShell, count how many {ext} files exist in the whole project tree "
            f"(including subfolders) and report the number.",
            PS, (lambda ext: lambda a, r: has_number(a, sum(1 for _ in Path(r).rglob(f"*{ext}"))))(ext))

    # ---------- powershell: count lines of a file (10) ----------
    ps_line_files = ["big.log", "data/users.csv", "data/employees.csv", "data/products.csv",
                     "logs/app.log", "logs/api.log", "data/notes.txt", "config/services.json",
                     "src/main.py", "docs/usage.md"]
    for i, rel in enumerate(ps_line_files):
        add(f"xa_ps_lines_{i:02d}", "powershell",
            f"Using PowerShell, report how many lines the file {rel} has.",
            PS, (lambda rel: lambda a, r: has_number(a, len((rf(r, rel) or "").splitlines())))(rel))

    # ---------- powershell: read a config value (4) ----------
    add("xa_ps_port", "powershell",
        "Using PowerShell, read config.ini and report the configured server port number.",
        PS, lambda a, r: has_number(a, 8080))
    add("xa_ps_timeout", "powershell",
        "Using PowerShell, read config.ini and report the timeout value.",
        PS, lambda a, r: has_number(a, 30))
    add("xa_ps_httport", "powershell",
        "Using PowerShell, read config/services.json and report ports.http.",
        PS, lambda a, r: has_number(a, 8088))
    add("xa_ps_replicas", "powershell",
        "Using PowerShell, read config/services.json and report the number of replicas.",
        PS, lambda a, r: has_number(a, 6))

    # ---------- append rows / lines (12) ----------
    new_users = [(9, "ivan"), (10, "judy"), (11, "kim"), (12, "liam"), (13, "mona")]
    for i, (uid, name) in enumerate(new_users):
        add(f"xa_appuser_{i:02d}", "edit",
            f"Append a new row to data/users.csv for a user with id {uid}, name {name}, role user, "
            f"age 30, score 75, in the same comma-separated format.",
            EDIT,
            (lambda uid, name: lambda a, r: any(
                [x.strip() for x in line.split(",")] == [str(uid), name, "user", "30", "75"]
                for line in (rf(r, "data/users.csv") or "").splitlines()))(uid, name))

    new_prods = [("zeta", "tech", 33, 9), ("omega", "tools", 21, 4), ("sigma", "food", 12, 7)]
    for i, (nm, cat, pr, st) in enumerate(new_prods):
        add(f"xa_appprod_{i:02d}", "edit",
            f"Append a new row to data/products.csv for a product named {nm}, category {cat}, "
            f"price {pr}, stock {st}, in the same comma-separated format.",
            EDIT,
            (lambda nm, cat, pr, st: lambda a, r: any(
                [x.strip() for x in line.split(",")] == [nm, cat, str(pr), str(st)]
                for line in (rf(r, "data/products.csv") or "").splitlines()))(nm, cat, pr, st))

    for i, text in enumerate(["ERROR injected one", "WARN injected two", "ERROR injected three", "INFO injected four"]):
        add(f"xa_applog_{i:02d}", "edit",
            f"Append a new line with exactly this text to the end of logs/app.log: {text}",
            EDIT,
            (lambda text: lambda a, r: text.lower() in (rf(r, "logs/app.log") or "").lower())(text))

    return tasks
