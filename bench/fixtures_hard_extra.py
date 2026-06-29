"""Extra HARDCORE-tier content: more production-incident material for the
orders-service so the tier can pose 200+ strict, analysis-heavy tasks. All
ground truth is computed by parsing the written files. merge_into(root) writes
the files into the existing hard sandbox and returns the extra truth.
"""

import json
import re
from collections import Counter
from pathlib import Path

PATHS = ["/api/orders", "/api/orders/4471", "/health", "/api/login", "/static/app.js"]
METHODS = ["GET", "POST", "PUT", "DELETE"]


def _access2():
    """Bigger access log: 50 distinct client IPs with strictly increasing counts."""
    lines, idx = [], 0
    for i in range(50):
        ip = f"10.0.{i // 6}.{i % 6 + 1}"
        count = 5 + i
        for _ in range(count):
            status = 500 if idx % 9 == 0 else (404 if idx % 13 == 0 else 200)
            method = METHODS[idx % 4]
            path = PATHS[idx % 5]
            size = 200 + (idx * 7) % 5000
            hour = 10 + (idx // 100) % 12
            lines.append(f'{ip} - - [21/Jun/2026:{hour:02d}:{(idx // 60) % 60:02d}:{idx % 60:02d} +0000] '
                         f'"{method} {path} HTTP/1.1" {status} {size}')
            idx += 1
    return "\n".join(lines) + "\n"


_EVENT_TYPES = ["purchase", "refund", "chargeback", "payout", "fee"]


def _events_big():
    rows = ["ts,type,amount"]
    for i in range(40):
        t = _EVENT_TYPES[i % len(_EVENT_TYPES)]
        amount = 50 + (i * 17) % 400
        rows.append(f"10:{i % 60:02d},{t},{amount}")
    return "\n".join(rows) + "\n"


_SERVICES = ["gateway", "orders", "billing", "search", "notify"]


def _metrics():
    rows = ["service,latency_ms,errors"]
    for i in range(30):
        svc = _SERVICES[i % len(_SERVICES)]
        latency = 20 + (i * 13) % 300
        errors = (i * 3) % 7
        rows.append(f"{svc},{latency},{errors}")
    return "\n".join(rows) + "\n"


def _contacts_big():
    domains = ["nebula.io", "example.com", "acme.co", "contoso.com", "globex.net", "initech.org"]
    names = ["alice", "bob", "carol", "dan", "eve", "frank", "grace", "heidi",
             "ivan", "judy", "kim", "liam", "mona", "nora", "omar", "pia",
             "quinn", "rosa", "sam", "tina", "uma", "vic", "wes", "xena"]
    lines = []
    for i, n in enumerate(names):
        d = domains[i % len(domains)]
        lines.append(f"{n.title()} Person <{n}@{d}>")
    return "\n".join(lines) + "\n"


_REQS = [
    ("flask", "2.0.1"), ("requests", "2.25.0"), ("pyyaml", "5.4"), ("sqlalchemy", "1.4.0"),
    ("redis", "4.1.2"), ("celery", "5.2.3"), ("gunicorn", "20.1.0"), ("jinja2", "3.0.3"),
    ("click", "8.0.4"), ("boto3", "1.20.24"),
]


def _requirements_big():
    return "\n".join(f"{n}=={v}" for n, v in _REQS) + "\n"


CLUSTER = {
    "cluster": {"nodes": 12, "version_major": 1, "version_minor": 28},
    "namespaces": {"prod": {"pods": 48, "cpu_quota": 96, "mem_quota_gb": 384},
                   "staging": {"pods": 16, "cpu_quota": 32, "mem_quota_gb": 128}},
    "ingress": {"http_port": 80, "https_port": 443, "timeout_s": 60, "max_body_mb": 25},
    "autoscaler": {"min_replicas": 3, "max_replicas": 30, "target_cpu_pct": 70},
    "storage": {"pv_count": 20, "total_gb": 5000, "iops": 3000},
}

_CONFIG_STAGE = {"env": "staging", "db_port": 5432, "workers": 4, "debug": True,
                 "cache": {"ttl": 120, "enabled": True}, "feature_flags": ["x", "y"]}

# new src modules with known imports / defs / classes
_MODULES = {
    "src/handlers/orders_handler.py": (
        "from db import connect\n"
        "from orders import process_order\n\n"
        "def list_orders():\n    return []\n\n"
        "def get_order(oid):\n    return oid\n\n"
        "class OrderView:\n    def render(self):\n        return 'ok'\n"
    ),
    "src/handlers/auth_handler.py": (
        "from auth import verify\n\n"
        "def login(token):\n    return verify(token)\n\n"
        "def logout():\n    return True\n"
    ),
    "src/handlers/health.py": (
        "import os\n\n"
        "def healthcheck():\n    return {'status': 'ok'}\n"
    ),
    "src/lib/cache.py": (
        "import json\n\n"
        "class Cache:\n    def get(self, k):\n        return None\n\n"
        "    def set(self, k, v):\n        return True\n\n"
        "def make_key(*parts):\n    return ':'.join(parts)\n"
    ),
    "src/lib/retry.py": (
        "import time\n\n"
        "def retry(fn, times=3):\n    return fn()\n\n"
        "def backoff(n):\n    return 2 ** n\n"
    ),
}


_ORDER_REGIONS = ["north", "south", "east", "west"]
_ORDER_STATUS = ["paid", "pending", "cancelled", "refunded"]


def _orders():
    rows = ["id,region,status,amount"]
    for i in range(40):
        region = _ORDER_REGIONS[i % 4]
        status = _ORDER_STATUS[(i // 4) % 4]
        amount = 100 + (i * 23) % 900
        rows.append(f"{1000 + i},{region},{status},{amount}")
    return "\n".join(rows) + "\n"


def _files():
    f = {}
    f["logs/access2.log"] = _access2()
    f["data/events_big.csv"] = _events_big()
    f["data/metrics.csv"] = _metrics()
    f["data/contacts_big.txt"] = _contacts_big()
    f["data/orders.csv"] = _orders()
    f["requirements_full.txt"] = _requirements_big()
    f["config/cluster.json"] = json.dumps(CLUSTER, indent=2) + "\n"
    f["config/config.stage.json"] = json.dumps(_CONFIG_STAGE, indent=2) + "\n"
    f.update(_MODULES)
    return f


def merge_into(root: str) -> dict:
    root = Path(root)
    files = _files()
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return _truth(root)


def _truth(root: Path) -> dict:
    access = (root / "logs/access2.log").read_text(encoding="utf-8").splitlines()
    access = [l for l in access if l.strip()]
    ip_counts = Counter(l.split()[0] for l in access)
    status_counts = Counter(int(re.search(r'" (\d{3}) ', l).group(1)) for l in access)
    method_counts = Counter(l.split('"')[1].split()[0] for l in access)
    path_counts = Counter(l.split('"')[1].split()[1] for l in access)
    hour_counts = Counter(re.search(r":(\d{2}):\d{2}:\d{2} ", l).group(1) for l in access)
    total_bytes = sum(int(l.rsplit(" ", 1)[1]) for l in access)
    top_ip, top_n = ip_counts.most_common(1)[0]

    events = [l.split(",") for l in (root / "data/events_big.csv").read_text(encoding="utf-8").splitlines()[1:] if l.strip()]
    ev_total = {}
    ev_count = {}
    for _, t, a in events:
        ev_total[t] = ev_total.get(t, 0) + int(a)
        ev_count[t] = ev_count.get(t, 0) + 1
    ev_grand = sum(int(a) for _, _, a in events)
    ev_max = max(int(a) for _, _, a in events)
    ev_avg = {t: ev_total[t] // ev_count[t] for t in ev_total}

    metrics = [l.split(",") for l in (root / "data/metrics.csv").read_text(encoding="utf-8").splitlines()[1:] if l.strip()]
    lat_sum = sum(int(r[1]) for r in metrics)
    lat_max = max(int(r[1]) for r in metrics)
    lat_min = min(int(r[1]) for r in metrics)
    err_sum = sum(int(r[2]) for r in metrics)
    svc_err = {}
    svc_lat = {}
    for r in metrics:
        svc_err[r[0]] = svc_err.get(r[0], 0) + int(r[2])
        svc_lat[r[0]] = svc_lat.get(r[0], 0) + int(r[1])
    top_svc = max(svc_err, key=svc_err.get)

    # orders.csv aggregates
    orders = [l.split(",") for l in (root / "data/orders.csv").read_text(encoding="utf-8").splitlines()[1:] if l.strip()]
    ord_region = Counter(o[1] for o in orders)
    ord_status = Counter(o[2] for o in orders)
    ord_total = sum(int(o[3]) for o in orders)
    ord_paid_total = sum(int(o[3]) for o in orders if o[2] == "paid")
    ord_max = max(int(o[3]) for o in orders)
    ord_region_total = {}
    ord_status_total = {}
    for o in orders:
        ord_region_total[o[1]] = ord_region_total.get(o[1], 0) + int(o[3])
        ord_status_total[o[2]] = ord_status_total.get(o[2], 0) + int(o[3])
    svc_cnt = Counter(r[0] for r in metrics)
    svc_lat_avg = {s: svc_lat[s] // svc_cnt[s] for s in svc_lat}

    emails = re.findall(r"[\w.]+@[\w.]+", (root / "data/contacts_big.txt").read_text(encoding="utf-8"))
    dom_counts = Counter(e.split("@")[1] for e in emails)
    top_dom, _ = dom_counts.most_common(1)[0]

    dev = json.loads((root / "config/config.dev.json").read_text(encoding="utf-8"))
    prod = json.loads((root / "config/config.prod.json").read_text(encoding="utf-8"))
    stage = _CONFIG_STAGE
    diff_dev_stage = sorted(k for k in dev if dev.get(k) != stage.get(k))
    diff_prod_stage = sorted(k for k in prod if prod.get(k) != stage.get(k))

    import_db = []
    for f in (root / "src").rglob("*.py"):
        t = f.read_text(encoding="utf-8")
        if re.search(r"^\s*(from db import|import db)\b", t, re.MULTILINE):
            import_db.append(f.name)

    return {
        "h2_ip_counts": dict(ip_counts),
        "h2_status_counts": {str(k): v for k, v in status_counts.items()},
        "h2_method_counts": dict(method_counts),
        "h2_path_counts": dict(path_counts),
        "h2_total": len(access),
        "h2_total_bytes": total_bytes,
        "h2_top_ip": top_ip, "h2_top_ip_count": top_n,
        "h2_hour_counts": dict(hour_counts),
        "ev_total": ev_total, "ev_count": ev_count, "ev_grand": ev_grand, "ev_max": ev_max,
        "ev_avg": ev_avg,
        "lat_sum": lat_sum, "lat_max": lat_max, "lat_min": lat_min,
        "lat_avg": lat_sum // len(metrics), "err_sum": err_sum, "top_svc": top_svc,
        "svc_err": svc_err, "svc_lat": svc_lat, "svc_lat_avg": svc_lat_avg,
        "ord_region": dict(ord_region), "ord_status": dict(ord_status),
        "ord_region_total": ord_region_total, "ord_status_total": ord_status_total,
        "ord_total": ord_total, "ord_paid_total": ord_paid_total, "ord_max": ord_max,
        "email_count_big": len(emails), "domain_count_big": len(dom_counts),
        "dom_counts": dict(dom_counts), "top_dom": top_dom,
        "reqs": dict(_REQS), "reqs_count": len(_REQS),
        "cluster": CLUSTER,
        "diff_dev_stage": diff_dev_stage, "diff_prod_stage": diff_prod_stage,
        "import_db_all": sorted(import_db),
    }
