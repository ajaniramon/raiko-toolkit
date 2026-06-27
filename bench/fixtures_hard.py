"""HARDCORE fixture: an 'orders-service' microservice in production with an
incident. Inconsistent configs, a hardcoded secret, a real bug with a
self-test, structured logs with a traceback, and an Apache-style access.log.

Designed so that multi-step tasks with strict graders make small models
falter. The ground-truth is computed by parsing what was written.
"""

import json
import os
import re
import shutil
from pathlib import Path

SECRET_TOKEN = "sk-live-9f8e7d6c5b4a3210"

# deterministic distribution of IPs in the access.log (top = 10.0.0.7)
IP_COUNTS = [
    ("10.0.0.7", 80), ("10.0.0.3", 55), ("10.0.0.9", 40), ("192.168.1.5", 35),
    ("172.16.0.2", 30), ("10.0.0.11", 25), ("203.0.113.9", 20), ("198.51.100.4", 15),
]
PATHS = ["/api/orders", "/api/orders/4471", "/health", "/api/login", "/static/app.js"]


def _access_log():
    lines, idx = [], 0
    for ip, n in IP_COUNTS:
        for _ in range(n):
            sec = idx % 60
            status = 500 if idx % 9 == 0 else (404 if idx % 13 == 0 else 200)
            path = PATHS[idx % len(PATHS)]
            size = 200 + (idx * 7) % 5000
            lines.append(f'{ip} - - [20/Jun/2026:10:{(idx//60)%60:02d}:{sec:02d} +0000] '
                         f'"GET {path} HTTP/1.1" {status} {size}')
            idx += 1
    return "\n".join(lines) + "\n"


def _app_log():
    L = [
        "2026-06-20 10:00:01 INFO  starting orders-service v1.4.2",
        "2026-06-20 10:00:02 INFO  connected to db on port 5432",
        "2026-06-20 10:01:15 WARN  slow query took 1.2s",
        "2026-06-20 10:02:30 ERROR unhandled exception while processing order 4471",
        "Traceback (most recent call last):",
        '  File "src/app.py", line 31, in handle',
        "    result = process_order(order)",
        '  File "src/orders.py", line 4, in process_order',
        '    total = order["total"]',
        "KeyError: 'total'",
        "2026-06-20 10:03:10 ERROR retry failed for order 4471",
        "2026-06-20 10:03:45 WARN  cache miss rate high",
        "2026-06-20 10:04:50 ERROR db connection reset",
        "2026-06-20 10:06:05 ERROR timeout talking to payment gateway",
        "2026-06-20 10:07:00 INFO  recovered, queue drained",
        "2026-06-20 10:09:30 ERROR disk usage at 91%",
    ]
    return "\n".join(L) + "\n"


FILES = {
    "pyproject.toml": (
        "[project]\n"
        'name = "orders-service"\n'
        'version = "1.4.2"\n'
        'requires-python = ">=3.10"\n'
    ),
    "CHANGELOG.md": "# Changelog\n\n## 1.4.2\n- baseline release\n",
    "requirements.txt": (
        "flask==2.0.1\n"
        "requests==2.25.0\n"
        "pyyaml==5.4\n"
        "sqlalchemy==1.4.0\n"
    ),
    ".env": (
        "DB_HOST=localhost\n"
        "DB_PORT=5432\n"
        "DB_PASSWORD=s3cr3t-db-pass\n"
        "API_KEY=sk-env-key-001\n"
    ),
    "config/config.dev.json": json.dumps(
        {"env": "dev", "db_port": 5432, "workers": 2, "debug": True,
         "cache": {"ttl": 60, "enabled": False}, "feature_flags": ["x", "y"]}, indent=2) + "\n",
    "config/config.prod.json": json.dumps(
        {"env": "prod", "db_port": 5432, "workers": 8, "debug": False,
         "cache": {"ttl": 300, "enabled": True}, "feature_flags": ["x", "y", "z"]}, indent=2) + "\n",
    "config/docker-compose.yml": (
        "services:\n"
        "  db:\n"
        "    image: postgres:14\n"
        "    ports:\n"
        '      - "5433:5432"\n'
        "  web:\n"
        "    build: .\n"
        "    ports:\n"
        '      - "8000:8000"\n'
    ),
    "src/__init__.py": '__version__ = "1.4.2"\n',
    "src/app.py": (
        "from db import connect\n"
        "from auth import verify\n"
        "from orders import process_order, summary\n\n"
        "def handle(order, token):\n"
        "    if not verify(token):\n"
        "        raise PermissionError('bad token')\n"
        "    result = process_order(order)\n"
        "    return result\n"
    ),
    "src/db.py": (
        "import json, os\n\n"
        "def connect():\n"
        "    port = int(os.environ.get('DB_PORT', 5432))\n"
        "    return f'postgres://localhost:{port}/orders'\n"
    ),
    "src/auth.py": (
        "import os\n\n"
        "# FIXME: do not hardcode credentials in source\n"
        f'API_TOKEN = "{SECRET_TOKEN}"\n\n'
        "def verify(token):\n"
        "    return token == API_TOKEN\n"
    ),
    "src/orders.py": (
        "from db import connect\n\n"
        "def process_order(order):\n"
        '    total = order["total"]\n'
        '    discount = order["discount"]\n'
        "    return total - discount\n\n"
        "def summary(orders):\n"
        "    return sum(process_order(o) for o in orders)\n"
    ),
    # off-by-one bug in chunk(): takes size-1 elements but advances by size
    "src/parser.py": (
        "def chunk(items, size):\n"
        "    out = []\n"
        "    i = 0\n"
        "    while i < len(items):\n"
        "        out.append(items[i:i + size - 1])\n"
        "        i += size\n"
        "    return out\n\n"
        'if __name__ == "__main__":\n'
        "    expected = [[1, 2, 3], [4, 5, 6], [7, 8, 9], [10]]\n"
        "    got = chunk(list(range(1, 11)), 3)\n"
        "    assert got == expected, f'FAIL: {got}'\n"
        '    print("ok")\n'
    ),
    "src/utils.py": (
        "def slugify(s):\n"
        "    return s.strip().lower().replace(' ', '-')\n"
    ),
    "data/contacts.txt": (
        "Alice Smith <alice@nebula.io>\n"
        "Bob Jones bob@example.com\n"
        "Carol White carol@nebula.io\n"
        "Dan Brown dan@acme.co\n"
        "Eve Black eve@example.com\n"
        "Frank Green frank@nebula.io\n"
        "Grace Hopper grace@acme.co\n"
        "Heidi Klum heidi@contoso.com\n"
    ),
    "data/events.csv": (
        "ts,type,amount\n"
        "10:00,purchase,120\n"
        "10:01,refund,30\n"
        "10:02,purchase,200\n"
        "10:03,purchase,50\n"
        "10:04,refund,20\n"
        "10:05,purchase,80\n"
        "10:06,chargeback,150\n"
    ),
    "logs/app.log": _app_log(),
    "logs/access.log": _access_log(),
}


def build_sandbox(base_dir: str) -> dict:
    root = Path(base_dir) / "hardsandbox"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    for rel, content in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return {"root": str(root), "truth": _truth(root)}


def _truth(root: Path) -> dict:
    access = (root / "logs/access.log").read_text(encoding="utf-8").splitlines()
    from collections import Counter
    ips = Counter(l.split()[0] for l in access if l.strip())
    top_ip, top_n = ips.most_common(1)[0]
    n_500 = sum(1 for l in access if re.search(r'" 500 ', l))

    emails = re.findall(r"[\w.]+@[\w.]+", (root / "data/contacts.txt").read_text(encoding="utf-8"))
    domains = {e.split("@")[1] for e in emails}

    dev = json.loads((root / "config/config.dev.json").read_text(encoding="utf-8"))
    prod = json.loads((root / "config/config.prod.json").read_text(encoding="utf-8"))
    diff_keys = {k for k in dev if dev.get(k) != prod.get(k)}

    # files that import 'db' (import db / from db import)
    import_db = []
    for f in (root / "src").glob("*.py"):
        t = f.read_text(encoding="utf-8")
        if re.search(r"^\s*(from db import|import db)\b", t, re.MULTILINE):
            import_db.append(f.name)

    events = [l.split(",") for l in (root / "data/events.csv").read_text(encoding="utf-8").splitlines()[1:] if l.strip()]
    purchases_total = sum(int(a) for _, t, a in events if t == "purchase")

    all_files = [f for f in root.rglob("*") if f.is_file()]
    largest_file = max(all_files, key=lambda f: f.stat().st_size).name
    py_lines_total = sum(len(f.read_text(encoding="utf-8").splitlines()) for f in (root / "src").rglob("*.py"))

    return {
        "top_ip": top_ip, "top_ip_count": top_n, "n_500": n_500,
        "total_requests": len([l for l in access if l.strip()]),
        "email_count": len(emails), "domain_count": len(domains),
        "diff_keys": sorted(diff_keys),
        "import_db_files": sorted(import_db),
        "secret": SECRET_TOKEN,
        "purchases_total": purchases_total,
        "env_db_port": 5432, "compose_host_port": 5433,
        "largest_file": largest_file, "py_lines_total": py_lines_total,
    }


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    print(json.dumps(build_sandbox(here)["truth"], indent=2, ensure_ascii=False))
