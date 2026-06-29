"""Extra HARDCORE-tier tasks: analysis-heavy questions over the enriched
orders-service incident data (big access log, metrics, events, cluster config,
extra modules). All expected values come from computed truth, so graders are
correct by construction. IDs are prefixed 'xh_'.
"""

from tasks import has_number, contains, contains_any

READ = ["read_file", "head", "read_lines", "grep", "find_in_files"]
PY = ["run_python", "read_file"]
PYPS = ["run_python", "run_powershell", "read_file"]


def build_extra_hard(truth) -> list:
    T = truth
    tasks = []

    def add(id, category, prompt, expect, check, negative=False):
        tasks.append({"id": id, "category": category, "prompt": prompt,
                      "expect_tools": expect, "check": check, "negative": negative})

    def num(id, cat, prompt, val, expect=PY):
        add(id, cat, prompt, expect, (lambda v: lambda a, r: has_number(a, v))(val))

    def txt(id, cat, prompt, s, expect=PY):
        add(id, cat, prompt, expect, (lambda s: lambda a, r: contains(a, s))(s))

    # ---- access2.log: per-IP request counts ----
    for i, (ip, cnt) in enumerate(sorted(T["h2_ip_counts"].items())):
        num(f"xh_ip_{i:02d}", "accesslog",
            f"In logs/access2.log (Apache common log format), how many requests were made by "
            f"the client IP {ip}? Answer with a number.", cnt, PYPS)

    # ---- access2.log: status / method / path / totals ----
    for st, cnt in sorted(T["h2_status_counts"].items()):
        num(f"xh_status_{st}", "accesslog",
            f"In logs/access2.log, how many requests returned HTTP status {st}? Answer with a number.", cnt)
    for m, cnt in sorted(T["h2_method_counts"].items()):
        num(f"xh_method_{m.lower()}", "accesslog",
            f"In logs/access2.log, how many requests used the {m} HTTP method? Answer with a number.", cnt)
    for j, (path, cnt) in enumerate(sorted(T["h2_path_counts"].items())):
        num(f"xh_path_{j:02d}", "accesslog",
            f"In logs/access2.log, how many requests targeted the path {path}? Answer with a number.", cnt)
    num("xh_total", "accesslog",
        "How many request lines are there in total in logs/access2.log? Answer with a number.", T["h2_total"])
    num("xh_bytes", "accesslog",
        "Using Python, sum the response size (last field) of every line in logs/access2.log and report the total bytes.",
        T["h2_total_bytes"])
    txt("xh_topip", "accesslog",
        "Which client IP made the most requests in logs/access2.log? Report the IP.", T["h2_top_ip"])
    num("xh_topip_n", "accesslog",
        "How many requests did the busiest client IP make in logs/access2.log? Answer with a number.",
        T["h2_top_ip_count"])

    # ---- access2.log: per-hour request counts ----
    for hour, cnt in sorted(T["h2_hour_counts"].items()):
        num(f"xh_hour_{hour}", "accesslog",
            f"In logs/access2.log, how many requests happened during the {hour}:00 hour "
            f"(timestamps with :{hour}: as the hour)? Answer with a number.", cnt)

    # ---- events_big.csv: totals / counts / averages per type ----
    for t, tot in sorted(T["ev_total"].items()):
        num(f"xh_evtot_{t}", "events",
            f"In data/events_big.csv, what is the total 'amount' across rows whose type is '{t}'? Answer with a number.", tot)
    for t, cnt in sorted(T["ev_count"].items()):
        num(f"xh_evcnt_{t}", "events",
            f"In data/events_big.csv, how many rows have type '{t}'? Answer with a number.", cnt)
    num("xh_evgrand", "events",
        "In data/events_big.csv, what is the grand total of the 'amount' column? Answer with a number.", T["ev_grand"])
    num("xh_evmax", "events",
        "In data/events_big.csv, what is the single largest 'amount'? Answer with a number.", T["ev_max"])
    for t, avg in sorted(T["ev_avg"].items()):
        num(f"xh_evavg_{t}", "events",
            f"In data/events_big.csv, what is the integer average (floor) 'amount' for rows of type '{t}'? Answer with a number.", avg)

    # ---- orders.csv aggregates ----
    for reg, cnt in sorted(T["ord_region"].items()):
        num(f"xh_ord_reg_{reg}", "orders",
            f"In data/orders.csv, how many orders are from the '{reg}' region? Answer with a number.", cnt)
    for st, cnt in sorted(T["ord_status"].items()):
        num(f"xh_ord_st_{st}", "orders",
            f"In data/orders.csv, how many orders have status '{st}'? Answer with a number.", cnt)
    num("xh_ord_total", "orders", "In data/orders.csv, what is the total of the 'amount' column? Answer with a number.", T["ord_total"])
    num("xh_ord_paid", "orders", "In data/orders.csv, what is the total 'amount' across orders whose status is 'paid'? Answer with a number.", T["ord_paid_total"])
    num("xh_ord_max", "orders", "In data/orders.csv, what is the single largest 'amount'? Answer with a number.", T["ord_max"])
    for reg, tot in sorted(T["ord_region_total"].items()):
        num(f"xh_ord_regtot_{reg}", "orders",
            f"In data/orders.csv, what is the total 'amount' for orders from the '{reg}' region? Answer with a number.", tot)
    for st, tot in sorted(T["ord_status_total"].items()):
        num(f"xh_ord_sttot_{st}", "orders",
            f"In data/orders.csv, what is the total 'amount' for orders with status '{st}'? Answer with a number.", tot)

    # ---- metrics.csv ----
    num("xh_lat_sum", "metrics", "In data/metrics.csv, what is the sum of the 'latency_ms' column? Answer with a number.", T["lat_sum"])
    num("xh_lat_max", "metrics", "In data/metrics.csv, what is the maximum 'latency_ms'? Answer with a number.", T["lat_max"])
    num("xh_lat_min", "metrics", "In data/metrics.csv, what is the minimum 'latency_ms'? Answer with a number.", T["lat_min"])
    num("xh_lat_avg", "metrics", "In data/metrics.csv, what is the integer average (floor) of 'latency_ms'? Answer with a number.", T["lat_avg"])
    num("xh_err_sum", "metrics", "In data/metrics.csv, what is the total number of 'errors' across all rows? Answer with a number.", T["err_sum"])
    txt("xh_top_svc", "metrics", "In data/metrics.csv, which service has the most total errors? Report the service name.", T["top_svc"])
    for svc, errs in sorted(T["svc_err"].items()):
        num(f"xh_svcerr_{svc}", "metrics",
            f"In data/metrics.csv, what is the total 'errors' for the '{svc}' service? Answer with a number.", errs)
    for svc, lat in sorted(T["svc_lat"].items()):
        num(f"xh_svclat_{svc}", "metrics",
            f"In data/metrics.csv, what is the total 'latency_ms' for the '{svc}' service? Answer with a number.", lat)
    for svc, avg in sorted(T["svc_lat_avg"].items()):
        num(f"xh_svclatavg_{svc}", "metrics",
            f"In data/metrics.csv, what is the integer average (floor) 'latency_ms' for the '{svc}' service? Answer with a number.", avg)

    # ---- contacts_big.txt ----
    num("xh_email_count", "extract", "How many email addresses are in data/contacts_big.txt? Answer with a number.", T["email_count_big"], ["run_python", "grep"])
    num("xh_domain_count", "extract", "How many UNIQUE email domains appear in data/contacts_big.txt? Answer with a number.", T["domain_count_big"], ["run_python", "grep"])
    txt("xh_top_domain", "extract", "Which email domain is the most common in data/contacts_big.txt? Report the domain.", T["top_dom"], ["run_python", "grep"])
    for dom, cnt in sorted(T["dom_counts"].items()):
        num(f"xh_dom_{dom.replace('.', '_')}", "extract",
            f"How many email addresses in data/contacts_big.txt use the domain '{dom}'? Answer with a number.", cnt, ["run_python", "grep"])

    # ---- requirements_full.txt ----
    num("xh_reqcount", "deps", "How many dependencies are pinned in requirements_full.txt? Answer with a number.", T["reqs_count"], READ)
    for pkg, ver in sorted(T["reqs"].items()):
        txt(f"xh_req_{pkg}", "deps",
            f"Which version of '{pkg}' is pinned in requirements_full.txt? Report the version.", ver, READ)

    # ---- cluster.json field lookups ----
    C = T["cluster"]
    leaves = [
        ("xh_cl_nodes", "How many nodes are in the cluster according to config/cluster.json?", C["cluster"]["nodes"]),
        ("xh_cl_vmaj", "What is cluster.version_major in config/cluster.json?", C["cluster"]["version_major"]),
        ("xh_cl_vmin", "What is cluster.version_minor in config/cluster.json?", C["cluster"]["version_minor"]),
        ("xh_cl_prodpods", "How many pods does the 'prod' namespace have in config/cluster.json?", C["namespaces"]["prod"]["pods"]),
        ("xh_cl_prodcpu", "What is namespaces.prod.cpu_quota in config/cluster.json?", C["namespaces"]["prod"]["cpu_quota"]),
        ("xh_cl_prodmem", "What is namespaces.prod.mem_quota_gb in config/cluster.json?", C["namespaces"]["prod"]["mem_quota_gb"]),
        ("xh_cl_stagepods", "How many pods does the 'staging' namespace have in config/cluster.json?", C["namespaces"]["staging"]["pods"]),
        ("xh_cl_stagecpu", "What is namespaces.staging.cpu_quota in config/cluster.json?", C["namespaces"]["staging"]["cpu_quota"]),
        ("xh_cl_stagemem", "What is namespaces.staging.mem_quota_gb in config/cluster.json?", C["namespaces"]["staging"]["mem_quota_gb"]),
        ("xh_cl_http", "What is ingress.http_port in config/cluster.json?", C["ingress"]["http_port"]),
        ("xh_cl_https", "What is ingress.https_port in config/cluster.json?", C["ingress"]["https_port"]),
        ("xh_cl_timeout", "What is ingress.timeout_s in config/cluster.json?", C["ingress"]["timeout_s"]),
        ("xh_cl_body", "What is ingress.max_body_mb in config/cluster.json?", C["ingress"]["max_body_mb"]),
        ("xh_cl_minrep", "What is autoscaler.min_replicas in config/cluster.json?", C["autoscaler"]["min_replicas"]),
        ("xh_cl_maxrep", "What is autoscaler.max_replicas in config/cluster.json?", C["autoscaler"]["max_replicas"]),
        ("xh_cl_cpu", "What is autoscaler.target_cpu_pct in config/cluster.json?", C["autoscaler"]["target_cpu_pct"]),
        ("xh_cl_pv", "What is storage.pv_count in config/cluster.json?", C["storage"]["pv_count"]),
        ("xh_cl_gb", "What is storage.total_gb in config/cluster.json?", C["storage"]["total_gb"]),
        ("xh_cl_iops", "What is storage.iops in config/cluster.json?", C["storage"]["iops"]),
    ]
    for id, prompt, val in leaves:
        num(id, "config", prompt, val, READ)

    # ---- config diffs (dev/stage, prod/stage) ----
    add("xh_diff_devstage", "config",
        "Compare config/config.dev.json and config/config.stage.json and list every top-level key whose value differs.",
        READ + ["run_python"],
        (lambda keys: lambda a, r: all(contains(a, k) for k in keys))(T["diff_dev_stage"]))
    add("xh_diff_prodstage", "config",
        "Compare config/config.prod.json and config/config.stage.json and list every top-level key whose value differs.",
        READ + ["run_python"],
        (lambda keys: lambda a, r: all(contains(a, k) for k in keys))(T["diff_prod_stage"]))
    num("xh_stage_workers", "config", "What is the 'workers' value in config/config.stage.json?", 4, READ)
    num("xh_stage_ttl", "config", "What is cache.ttl in config/config.stage.json?", 120, READ)

    # ---- code analysis over the extra modules ----
    add("xh_imports_db", "code",
        "Which Python files under src/ import the 'db' module (directly via 'import db' or "
        "'from db import')? List their file names.",
        ["grep", "find_in_files", "read_file"],
        (lambda names: lambda a, r: all(contains(a, n) for n in names))(T["import_db_all"]))
    txt("xh_cache_class", "code",
        "What is the name of the class defined in src/lib/cache.py?", "Cache",
        ["grep", "read_file"])
    txt("xh_retry_fn", "code",
        "In src/lib/retry.py, what does the backoff(n) function compute? Report the Python expression.",
        "2", ["read_file"])

    return tasks
