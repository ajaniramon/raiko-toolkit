"""Extra deterministic content for the BASIC tier, kept separate from
fixtures.py so the original sandbox/truth stays untouched and the expansion is
easy to review.

Everything here is generated from in-code source structures and all ground
truth is computed by reading the written files back, so the correct answers can
never drift from the content. `merge_into()` lays the files down inside an
existing sandbox root and returns the extra truth dict.
"""

import json
from pathlib import Path

# ---------------------------------------------------------------------------
# Source data (literal & deterministic — no randomness)
# ---------------------------------------------------------------------------

_EMP_NAMES = ["ana", "ben", "cleo", "dan", "ella", "finn", "gia", "hugo", "iris",
              "jack", "kara", "leo", "mia", "nico", "ona", "pau", "quinn", "rosa",
              "sam", "tina", "uma", "vito", "wes", "xena"]
_DEPTS = ["eng", "sales", "hr", "ops", "finance", "support"]
# id, name, dept, salary, years
EMPLOYEES = [(i + 1, _EMP_NAMES[i], _DEPTS[i % len(_DEPTS)],
              40000 + i * 2500, (i % 8) + 1) for i in range(len(_EMP_NAMES))]

_PROD_NAMES = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf",
               "hotel", "india", "juliet", "kilo", "lima", "mike", "november",
               "oscar", "papa"]
_CATS = ["tools", "food", "toys", "books", "tech"]
# name, category, price, stock
PRODUCTS = [(_PROD_NAMES[i], _CATS[i % len(_CATS)], 5 + (i * 3) % 50, (i * 7) % 40)
            for i in range(len(_PROD_NAMES))]

SERVICES = {
    "name": "atlas-gateway",
    "version": "5.2.3",
    "replicas": 6,
    "ports": {"http": 8088, "grpc": 9099, "metrics": 9100},
    "limits": {"cpu": 4, "memory_mb": 2048, "max_conns": 512},
    "regions": ["eu-west", "us-east", "ap-south"],
    "flags": {"tls": True, "tracing": False, "ratelimit": True},
    "deps": ["postgres", "redis", "kafka", "vault"],
}

# Each unique token lives in exactly ONE file (used by 'locate' tasks).
LOCATE_TOKENS = {
    "ZEPHYR_KEY": "src/lib/strings.py",
    "VORTEX_ID": "src/services/billing.py",
    "NIMBUS_TAG": "docs/spec.md",
    "COBALT_REF": "src/services/notify.py",
    "PHANTOM_SIG": "src/lib/mathx.py",
    "HALCYON_NOTE": "docs/usage.md",
    "OBSIDIAN_MARK": "data/ledger.csv",
    "SABLE_TOKEN": "src/api/admin.py",
    "EMBER_CODE": "logs/api.log",
    "QUARTZ_PIN": "notes/scratch.txt",
}

# new logs: name -> (errors, warns, infos). Kept <5 errors so logs/app.log (5)
# from the base fixture stays the single most-errors log (also computed live).
_NEW_LOGS = {
    "api.log": (4, 6, 12),
    "worker.log": (2, 3, 8),
    "auth.log": (1, 7, 5),
    "cron.log": (0, 1, 9),
}


def _csv(header, rows):
    out = [",".join(header)]
    for r in rows:
        out.append(",".join(str(x) for x in r))
    return "\n".join(out) + "\n"


def _mklog(errors, warns, infos, special=None):
    lines, idx = [], 1
    def emit(level, n):
        nonlocal idx
        for _ in range(n):
            tag = f"{level} event {idx}"
            if special and level == "ERROR" and idx == 1:
                tag += f" {special}"
            lines.append(f"2026-06-28 09:00:{idx % 60:02d} {level} {tag}")
            idx += 1
    emit("ERROR", errors)
    emit("WARN", warns)
    emit("INFO", infos)
    return "\n".join(lines) + "\n"


def _files():
    f = {}
    f["data/employees.csv"] = _csv(["id", "name", "dept", "salary", "years"], EMPLOYEES)
    f["data/products.csv"] = _csv(["name", "category", "price", "stock"], PRODUCTS)
    # a small ledger carrying one unique token in a comment-ish trailing row
    ledger = [("2026-01", 1200), ("2026-02", 1800), ("2026-03", 1500),
              ("2026-04", 2100), ("2026-05", 1700)]
    f["data/ledger.csv"] = (_csv(["month", "revenue"], ledger)
                            + "# OBSIDIAN_MARK end-of-ledger\n")
    f["config/services.json"] = json.dumps(SERVICES, indent=2) + "\n"

    for name, (e, w, i) in _NEW_LOGS.items():
        special = "EMBER_CODE" if name == "api.log" else None
        f[f"logs/{name}"] = _mklog(e, w, i, special=special)

    f["src/lib/strings.py"] = (
        "# ZEPHYR_KEY unique marker\n"
        "def upper(s):\n"
        "    return s.upper()\n\n"
        "def reverse(s):\n"
        "    return s[::-1]\n\n"
        "def repeat(s, n):\n"
        "    return s * n\n"
    )
    f["src/lib/mathx.py"] = (
        "# PHANTOM_SIG unique marker\n"
        "import math\n\n"
        "def square(x):\n"
        "    return x * x\n\n"
        "def cube(x):\n"
        "    return x * x * x\n"
    )
    f["src/services/billing.py"] = (
        'VORTEX_ID = "billing-unique"\n\n'
        "import os\n\n"
        "class Invoice:\n"
        "    def total(self):\n"
        "        return 0\n\n"
        "def charge(amount):\n"
        "    return amount\n"
    )
    f["src/services/notify.py"] = (
        "# COBALT_REF unique marker\n"
        "import sys\n\n"
        "def send_email(to, body):\n"
        "    return True\n\n"
        "def send_sms(to, body):\n"
        "    return True\n"
    )
    f["src/api/admin.py"] = (
        "# SABLE_TOKEN unique marker\n"
        "class AdminPanel:\n"
        "    def __init__(self):\n"
        "        self.users = []\n\n"
        "    def ban(self, uid):\n"
        "        return uid\n"
    )
    f["docs/usage.md"] = (
        "# Usage\n\n"
        "Run the gateway with the default profile. HALCYON_NOTE applies here.\n"
        "See the spec for the full list of options and limits.\n"
    )
    f["docs/spec.md"] = (
        "# Spec\n\n"
        "The atlas-gateway exposes http, grpc and metrics ports. NIMBUS_TAG section.\n"
        "Replicas scale horizontally behind the load balancer.\n"
    )
    f["docs/glossary.md"] = (
        "# Glossary\n\n"
        "Gateway: the entry point. Replica: one running copy. Region: a deploy zone.\n"
    )
    f["notes/scratch.txt"] = (
        "scratch note one\n"
        "scratch note two with QUARTZ_PIN inside\n"
        "scratch note three\n"
    )
    return f


def merge_into(root: str) -> dict:
    """Writes the extra files into an existing sandbox root and returns extra truth."""
    root = Path(root)
    files = _files()
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return _truth(root, list(files))


def _count(path: Path, token: str) -> int:
    return sum(1 for ln in path.read_text(encoding="utf-8").splitlines() if token in ln)


def _truth(root: Path, rels) -> dict:
    # per-file line & word counts, read back from disk (exact)
    line_counts = {rel: len((root / rel).read_text(encoding="utf-8").splitlines()) for rel in rels}
    word_counts = {rel: len((root / rel).read_text(encoding="utf-8").split())
                   for rel in rels if rel.endswith((".md", ".txt"))}

    # employees aggregates
    dept_counts = {}
    for _, _, dept, _, _ in EMPLOYEES:
        dept_counts[dept] = dept_counts.get(dept, 0) + 1
    salaries = [s for _, _, _, s, _ in EMPLOYEES]
    top_emp = max(EMPLOYEES, key=lambda e: e[3])
    low_emp = min(EMPLOYEES, key=lambda e: e[3])
    dept_salary = {}
    for _, _, dept, sal, _ in EMPLOYEES:
        dept_salary[dept] = dept_salary.get(dept, 0) + sal

    # products aggregates
    cat_counts = {}
    for _, cat, _, _ in PRODUCTS:
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
    prod_value = sum(p * s for _, _, p, s in PRODUCTS)
    stock_total = sum(s for _, _, _, s in PRODUCTS)
    priciest = max(PRODUCTS, key=lambda p: p[2])
    cheapest = min(PRODUCTS, key=lambda p: p[2])
    oos = [n for n, _, _, s in PRODUCTS if s == 0]

    # new log level counts (read back)
    log_levels = {}
    for name in _NEW_LOGS:
        p = root / "logs" / name
        log_levels[name] = {
            "error": _count(p, "ERROR"), "warn": _count(p, "WARN"), "info": _count(p, "INFO")}

    # grep over the new code modules
    new_py = ["src/lib/strings.py", "src/lib/mathx.py", "src/services/billing.py",
              "src/services/notify.py", "src/api/admin.py"]
    def_new = sum(_count(root / r, "def ") for r in new_py)
    class_new = sum(1 for r in new_py
                    for ln in (root / r).read_text(encoding="utf-8").splitlines()
                    if ln.lstrip().startswith("class "))

    # whole-tree extension counts (recomputed live so they include base + extra)
    by_ext = {}
    for f in root.rglob("*"):
        if f.is_file():
            by_ext[f.suffix] = by_ext.get(f.suffix, 0) + 1

    return {
        "x_line_counts": line_counts,
        "x_word_counts": word_counts,
        "locate_tokens": LOCATE_TOKENS,
        # employees
        "emp_count": len(EMPLOYEES),
        "emp_dept_counts": dept_counts,
        "emp_salary_total": sum(salaries),
        "emp_salary_max": max(salaries),
        "emp_salary_min": min(salaries),
        "emp_top_name": top_emp[1],
        "emp_low_name": low_emp[1],
        "emp_dept_salary": dept_salary,
        "emp_over_60k": sum(1 for s in salaries if s > 60000),
        "emp_avg_salary": sum(salaries) // len(salaries),
        # products
        "prod_count": len(PRODUCTS),
        "prod_cat_counts": cat_counts,
        "prod_value": prod_value,
        "prod_stock_total": stock_total,
        "prod_priciest": priciest[0],
        "prod_cheapest": cheapest[0],
        "prod_oos_count": len(oos),
        "prod_max_price": priciest[2],
        # services.json
        "svc_version": SERVICES["version"],
        "svc_replicas": SERVICES["replicas"],
        "svc_http_port": SERVICES["ports"]["http"],
        "svc_grpc_port": SERVICES["ports"]["grpc"],
        "svc_metrics_port": SERVICES["ports"]["metrics"],
        "svc_max_conns": SERVICES["limits"]["max_conns"],
        "svc_memory": SERVICES["limits"]["memory_mb"],
        "svc_cpu": SERVICES["limits"]["cpu"],
        "svc_region_count": len(SERVICES["regions"]),
        "svc_dep_count": len(SERVICES["deps"]),
        # logs / grep
        "x_log_levels": log_levels,
        "def_new": def_new,
        "class_new": class_new,
        # extensions
        "by_ext": by_ext,
    }
