"""Construye un sandbox rico con contenido conocido y calcula TODO el ground-truth.

El sandbox simula un proyecto real (código multi-módulo, CSVs numéricos, JSON
anidado, logs con niveles, docs) para poder plantear ~100 tareas: lecturas,
conteos, agregaciones, navegación, cadenas multi-paso, comparaciones cross-file,
razonamiento condicional y negativos.

Todo el ground-truth se calcula aquí (de las estructuras en memoria y escaneando
los ficheros escritos) para que las respuestas correctas nunca se desincronicen
del contenido.
"""

import json
import os
import re
import shutil
from pathlib import Path

# ---------------------------------------------------------------------------
# Datos fuente (de aquí salen tanto los ficheros como los agregados de verdad)
# ---------------------------------------------------------------------------

USERS = [  # id, name, role, age, score
    (1, "alice", "admin", 34, 88),
    (2, "bob", "user", 28, 72),
    (3, "carol", "user", 41, 95),
    (4, "dave", "admin", 37, 64),
    (5, "erin", "user", 23, 80),
    (6, "frank", "user", 52, 77),
    (7, "grace", "admin", 29, 91),
    (8, "heidi", "user", 45, 69),
]

SALES = [  # region, amount
    ("north", 1200), ("south", 800), ("east", 1500),
    ("west", 950), ("north", 300), ("east", 200),
]

INVENTORY = [  # item, qty, price
    ("widget", 10, 5), ("gadget", 4, 20), ("gizmo", 7, 12), ("doohickey", 3, 50),
]

SETTINGS = {
    "app": {"name": "Nebula", "version": "3.1.0", "debug": False},
    "server": {"port": 9090, "workers": 4, "timeout": 60},
    "features": {"auth": True, "cache": False, "beta": ["alpha", "beta", "gamma"]},
    "limits": {"max_users": 1000, "rate_per_sec": 50},
    "owners": ["alice", "bob", "carol"],
}

NOTES_MARKERS = {5: "ALPHA_MARK", 12: "BETA_MARK", 18: "GAMMA_MARK"}
BIGLOG_LINES = 2000
OMEGA_LINE = 1234


def _csv(header, rows):
    out = [",".join(header)]
    for r in rows:
        out.append(",".join(str(x) for x in r))
    return "\n".join(out) + "\n"


def _notes_text():
    lines = []
    for i in range(1, 21):
        if i in NOTES_MARKERS:
            lines.append(f"Line {i}: {NOTES_MARKERS[i]} appears here.")
        else:
            lines.append(f"Line {i}: filler note number {i}.")
    return "\n".join(lines) + "\n"


def _log_text(errors, warns, infos, special=None, special_at=1):
    """Genera un log con counts exactos de cada nivel."""
    lines = []
    idx = 1
    def emit(level, n):
        nonlocal idx
        for _ in range(n):
            tag = f"{level} event {idx}"
            if special and idx == special_at and level == "ERROR":
                tag += f" {special}"
            lines.append(f"2026-06-27 00:00:{idx % 60:02d} {level} {tag}")
            idx += 1
    emit("ERROR", errors)
    emit("WARN", warns)
    emit("INFO", infos)
    return "\n".join(lines) + "\n"


def _big_log():
    lines = []
    for i in range(1, BIGLOG_LINES + 1):
        if i == OMEGA_LINE:
            lines.append(f"log line {i}: OMEGA_TOKEN special marker here.")
        else:
            lines.append(f"log line {i}: routine event, nothing to see here.")
    return "\n".join(lines) + "\n"


FILES = {
    "README.md": (
        "# Project Nebula\n\n"
        "version: 2.4.1\n"
        "maintainer: ramon\n"
        "license: MIT\n\n"
        "Nebula is a sample project used for benchmarking tool use.\n"
        "The secret access code is ORION-7788.\n"
        "Contact: admin@nebula.example\n"
    ),
    "config.ini": (
        "[server]\n"
        "port=8080\n"
        "host=0.0.0.0\n"
        "timeout=30\n\n"
        "[auth]\n"
        "enabled=true\n"
        "max_retries=5\n"
    ),
    "settings.json": json.dumps(SETTINGS, indent=2) + "\n",
    "data/users.csv": _csv(["id", "name", "role", "age", "score"], USERS),
    "data/sales.csv": _csv(["region", "amount"], SALES),
    "data/inventory.csv": _csv(["item", "qty", "price"], INVENTORY),
    "data/notes.txt": _notes_text(),
    "logs/app.log": _log_text(5, 9, 20, special="DEADBEEF", special_at=1),
    "logs/server.log": _log_text(3, 4, 10),
    "logs/debug.log": _log_text(0, 2, 15),
    "src/main.py": (
        "import os\n"
        "import sys\n"
        "from utils import helper\n\n"
        "def main():\n"
        "    # TODO: refactor this\n"
        '    print("hello from main")\n'
        "    return helper(2)\n\n"
        'if __name__ == "__main__":\n'
        "    main()\n"
    ),
    "src/utils.py": (
        "MAGIC = 42\n\n"
        "def helper(x):\n"
        "    return x * MAGIC\n\n"
        "def format_name(name):\n"
        "    return name.strip().lower()\n"
    ),
    "src/app.py": (
        "def run_app():\n"
        '    print("app running")\n'
    ),
    "src/models.py": (
        "class User:\n"
        "    def __init__(self, name):\n"
        "        self.name = name\n\n"
        "class Product:\n"
        "    def __init__(self, sku):\n"
        "        self.sku = sku\n"
    ),
    "src/api/routes.py": (
        "def get_users():\n"
        "    return []\n\n"
        "def get_user(uid):\n"
        "    return uid\n\n"
        "def create_user(data):\n"
        "    return data\n"
    ),
    "src/api/handlers.py": (
        "# ZTOKEN_HANDLER unique marker\n"
        "def handle_request(req):\n"
        "    return req\n"
    ),
    "src/db/schema.py": (
        "class Schema:\n"
        "    version = 1\n"
    ),
    "src/db/queries.py": (
        'QELOR_TOKEN = "unique"\n\n'
        "def select_all(table):\n"
        "    return table\n\n"
        "def count_rows(table):\n"
        "    return 0\n"
    ),
    "docs/guide.md": (
        "# Guide\n\n"
        "This guide explains how to use Nebula step by step.\n"
        "Read the README first for the version and access code.\n"
    ),
    "docs/api.md": (
        "# API\n\n"
        "The API exposes endpoints that return the project status.\n"
    ),
    "docs/faq.md": (
        "# FAQ\n\n"
        "Q: Is this real? A: No, it is a benchmark fixture.\n"
    ),
    "docs/changelog.md": (
        "# Changelog\n\n"
        "- 3.1.0: current settings version\n"
        "- 2.4.1: README version\n"
    ),
    "big.log": _big_log(),
}

EMPTY_DIRS = ["empty"]


def build_sandbox(base_dir: str) -> dict:
    root = Path(base_dir) / "sandbox"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    for rel, content in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    for d in EMPTY_DIRS:
        (root / d).mkdir(parents=True, exist_ok=True)
    return {"root": str(root), "truth": _compute_truth(root)}


def _count_in_file(path: Path, pattern: str) -> int:
    rx = re.compile(pattern)
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if rx.search(line))


def _compute_truth(root: Path) -> dict:
    top_entries = sorted(p.name for p in root.iterdir())
    top_dirs = sorted(p.name for p in root.iterdir() if p.is_dir())
    all_files = [f for f in root.rglob("*") if f.is_file()]
    by_size = sorted(all_files, key=lambda f: f.stat().st_size)

    src = root / "src"
    py_src = sorted(src.rglob("*.py"))
    def_count = sum(1 for f in py_src
                    for ln in f.read_text(encoding="utf-8").splitlines()
                    if ln.lstrip().startswith("def "))
    class_count = sum(1 for f in py_src
                      for ln in f.read_text(encoding="utf-8").splitlines()
                      if ln.lstrip().startswith("class "))
    files_with_def = sum(1 for f in all_files
                         if re.search(r"\bdef\b", f.read_text(encoding="utf-8", errors="replace")))

    md_all = sorted(f.name for f in root.rglob("*.md"))
    csv_all = sorted(f.name for f in root.rglob("*.csv"))
    log_all = sorted(f.name for f in root.rglob("*.log"))

    # agregados CSV
    admin = sum(1 for u in USERS if u[2] == "admin")
    user = sum(1 for u in USERS if u[2] == "user")
    oldest = max(USERS, key=lambda u: u[3])
    youngest = min(USERS, key=lambda u: u[3])
    over40 = sum(1 for u in USERS if u[3] > 40)
    score_sum = sum(u[4] for u in USERS)
    best = max(USERS, key=lambda u: u[4])
    worst = min(USERS, key=lambda u: u[4])

    sales_total = sum(a for _, a in SALES)
    north_count = sum(1 for r, _ in SALES if r == "north")
    max_single = max(a for _, a in SALES)
    region_tot = {}
    for r, a in SALES:
        region_tot[r] = region_tot.get(r, 0) + a
    region_max = max(region_tot, key=region_tot.get)

    inv_value = sum(q * p for _, q, p in INVENTORY)
    inv_total_qty = sum(q for _, q, _ in INVENTORY)
    inv_max = max(INVENTORY, key=lambda x: x[1])[0]

    # logs
    err_app = _count_in_file(root / "logs/app.log", r"\bERROR\b")
    warn_app = _count_in_file(root / "logs/app.log", r"\bWARN\b")
    err_total = sum(_count_in_file(root / "logs" / n, r"\bERROR\b")
                    for n in ["app.log", "server.log", "debug.log"])
    warn_total = sum(_count_in_file(root / "logs" / n, r"\bWARN\b")
                     for n in ["app.log", "server.log", "debug.log"])

    # conteos de líneas por fichero usados por las tareas
    line_counts = {rel: len((root / rel).read_text(encoding="utf-8").splitlines())
                   for rel in ["data/users.csv", "data/notes.txt", "src/main.py",
                               "src/utils.py", "docs/guide.md", "big.log"]}
    word_counts = {rel: len((root / rel).read_text(encoding="utf-8").split())
                   for rel in ["docs/guide.md", "docs/faq.md"]}

    main_lines = sum(_count_in_file(f, "__main__") for f in all_files)
    todo_count = sum(_count_in_file(f, r"\bTODO\b") for f in all_files)
    import_main = _count_in_file(root / "src/main.py", r"\bimport\b")

    # fichero .py de src con más líneas
    most_lines_py = max(py_src, key=lambda f: len(f.read_text(encoding="utf-8").splitlines()))

    models_dir = Path("F:/models")
    model_entries = sorted(p.name for p in models_dir.iterdir()) if models_dir.is_dir() else []

    return {
        "root_name": root.name,
        "top_entries_count": len(top_entries),
        "top_dirs": top_dirs,
        "largest_file": by_size[-1].name,
        "smallest_file": by_size[0].name,
        "src_subdir_count": len([p for p in src.iterdir() if p.is_dir()]),
        "src_api_files": sorted(f.name for f in (src / "api").glob("*.py")),
        "data_children": len(list((root / "data").iterdir())),
        "py_src_count": len(py_src),
        "def_count_src": def_count,
        "class_count_src": class_count,
        "files_with_def": files_with_def,
        "md_all": md_all, "md_count": len(md_all),
        "csv_all": csv_all, "csv_count": len(csv_all),
        "log_all": log_all, "log_count": len(log_all),
        "most_lines_py": most_lines_py.name,
        # README / config / settings
        "readme_version": "2.4.1", "secret": "ORION-7788", "license": "MIT",
        "email": "admin@nebula.example",
        "config_port": 8080, "config_timeout": 30, "config_retries": 5,
        "settings_version": SETTINGS["app"]["version"],
        "settings_port": SETTINGS["server"]["port"],
        "settings_workers": SETTINGS["server"]["workers"],
        "beta_count": len(SETTINGS["features"]["beta"]),
        "max_users": SETTINGS["limits"]["max_users"],
        "owners_count": len(SETTINGS["owners"]),
        "rate": SETTINGS["limits"]["rate_per_sec"],
        "debug_enabled": SETTINGS["app"]["debug"],
        # users
        "admin_count": admin, "user_count": user,
        "oldest_name": oldest[1], "max_age": oldest[3], "youngest_name": youngest[1],
        "over40_count": over40, "score_sum": score_sum,
        "max_score": best[4], "best_name": best[1], "min_score": worst[4],
        # sales / inventory
        "sales_total": sales_total, "sales_rows": len(SALES), "north_count": north_count,
        "max_single_amount": max_single, "region_max_total": region_max,
        "inv_value": inv_value, "inv_count": len(INVENTORY),
        "inv_max_qty_item": inv_max, "inv_total_qty": inv_total_qty,
        # logs
        "err_app": err_app, "warn_app": warn_app, "err_total": err_total,
        "warn_total": warn_total, "log_most_errors": "app.log",
        # markers / lines
        "notes_markers": NOTES_MARKERS, "omega_line": OMEGA_LINE,
        "line_counts": line_counts, "word_counts": word_counts,
        "main_lines": main_lines, "todo_count": todo_count, "import_main": import_main,
        "config_bytes": (root / "config.ini").stat().st_size,
        "settings_bytes": (root / "settings.json").stat().st_size,
        # models
        "model_entries": model_entries, "model_entries_count": len(model_entries),
    }


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    print(json.dumps(build_sandbox(here)["truth"], indent=2, ensure_ascii=False))
