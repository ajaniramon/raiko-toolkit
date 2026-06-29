"""Extra BASIC-tier tasks, generated from the extra ground truth produced by
fixtures_extra.py. All graders derive their expected value from computed truth,
so they are correct by construction. IDs are prefixed 'x_' to never collide with
the originals in tasks.py.
"""

from tasks import has_number, contains, contains_any, says_not_found

READ = ["read_file", "head", "read_lines", "grep"]
LISTING = ["list_dir", "tree", "find_files"]
GREP = ["grep", "find_in_files", "read_file"]


def build_extra_basic(truth) -> list:
    T = truth
    tasks = []

    def add(id, category, prompt, expect_tools, check, negative=False):
        tasks.append({"id": id, "category": category, "prompt": prompt,
                      "expect_tools": expect_tools, "check": check, "negative": negative})

    # ---- per-file line counts (generated) ----
    for rel, n in T["x_line_counts"].items():
        slug = rel.replace("/", "_").replace(".", "_")
        add(f"x_lines_{slug}", "count_lines",
            f"How many lines does {rel} have? Answer with a number.",
            ["count_lines", "read_file"], (lambda n: lambda a: has_number(a, n))(n))

    # ---- per-file word counts (generated) ----
    for rel, n in T["x_word_counts"].items():
        slug = rel.replace("/", "_").replace(".", "_")
        add(f"x_words_{slug}", "count_lines",
            f"How many words are in {rel}? Answer with a number.",
            ["count_lines", "read_file"], (lambda n: lambda a: has_number(a, n))(n))

    # ---- locate unique tokens (generated) ----
    for token, rel in T["locate_tokens"].items():
        base = rel.split("/")[-1]
        add(f"x_loc_{token.lower()}", "locate",
            f"Which file contains the token {token}? Give its file name.",
            ["find_in_files", "grep"], (lambda b: lambda a: contains(a, b))(base))

    # ---- employees.csv aggregations ----
    add("x_emp_count", "csv_emp",
        "How many employees are listed in data/employees.csv? Answer with a number.",
        READ + ["count_lines"], (lambda n: lambda a: has_number(a, n))(T["emp_count"]))
    for dept, n in T["emp_dept_counts"].items():
        add(f"x_emp_dept_{dept}", "csv_emp",
            f"How many employees in data/employees.csv work in the '{dept}' department? Answer with a number.",
            READ + ["find_in_files"], (lambda n: lambda a: has_number(a, n))(n))
    add("x_emp_total_salary", "csv_emp",
        "What is the sum of all salaries in data/employees.csv? Answer with a number.",
        READ, (lambda n: lambda a: has_number(a, n))(T["emp_salary_total"]))
    add("x_emp_max_salary", "csv_emp",
        "What is the highest salary in data/employees.csv? Answer with a number.",
        READ, (lambda n: lambda a: has_number(a, n))(T["emp_salary_max"]))
    add("x_emp_min_salary", "csv_emp",
        "What is the lowest salary in data/employees.csv? Answer with a number.",
        READ, (lambda n: lambda a: has_number(a, n))(T["emp_salary_min"]))
    add("x_emp_top_name", "csv_emp",
        "Which employee has the highest salary in data/employees.csv? Give the name.",
        READ, (lambda s: lambda a: contains(a, s))(T["emp_top_name"]))
    add("x_emp_low_name", "csv_emp",
        "Which employee has the lowest salary in data/employees.csv? Give the name.",
        READ, (lambda s: lambda a: contains(a, s))(T["emp_low_name"]))
    add("x_emp_over60k", "csv_emp",
        "How many employees in data/employees.csv earn more than 60000? Answer with a number.",
        READ, (lambda n: lambda a: has_number(a, n))(T["emp_over_60k"]))
    for dept, tot in T["emp_dept_salary"].items():
        add(f"x_emp_salsum_{dept}", "csv_emp",
            f"What is the total salary of all employees in the '{dept}' department in data/employees.csv? Answer with a number.",
            READ, (lambda n: lambda a: has_number(a, n))(tot))

    # ---- products.csv aggregations ----
    add("x_prod_count", "csv_prod",
        "How many products are listed in data/products.csv? Answer with a number.",
        READ + ["count_lines"], (lambda n: lambda a: has_number(a, n))(T["prod_count"]))
    for cat, n in T["prod_cat_counts"].items():
        add(f"x_prod_cat_{cat}", "csv_prod",
            f"How many products in data/products.csv are in the '{cat}' category? Answer with a number.",
            READ + ["find_in_files"], (lambda n: lambda a: has_number(a, n))(n))
    add("x_prod_value", "csv_prod",
        "In data/products.csv, what is the total inventory value (sum of price*stock across all products)? Answer with a number.",
        READ, (lambda n: lambda a: has_number(a, n))(T["prod_value"]))
    add("x_prod_stock", "csv_prod",
        "What is the total stock across all products in data/products.csv? Answer with a number.",
        READ, (lambda n: lambda a: has_number(a, n))(T["prod_stock_total"]))
    add("x_prod_priciest", "csv_prod",
        "Which product has the highest price in data/products.csv? Give its name.",
        READ, (lambda s: lambda a: contains(a, s))(T["prod_priciest"]))
    add("x_prod_cheapest", "csv_prod",
        "Which product has the lowest price in data/products.csv? Give its name.",
        READ, (lambda s: lambda a: contains(a, s))(T["prod_cheapest"]))
    add("x_prod_maxprice", "csv_prod",
        "What is the highest price in data/products.csv? Answer with a number.",
        READ, (lambda n: lambda a: has_number(a, n))(T["prod_max_price"]))
    add("x_prod_oos", "csv_prod",
        "How many products in data/products.csv are out of stock (stock equal to 0)? Answer with a number.",
        READ, (lambda n: lambda a: has_number(a, n))(T["prod_oos_count"]))

    # ---- config/services.json (nested) ----
    svc = [
        ("x_svc_version", "What is the 'version' in config/services.json?", T["svc_version"], "s"),
        ("x_svc_replicas", "How many 'replicas' are configured in config/services.json? Answer with a number.", T["svc_replicas"], "n"),
        ("x_svc_http", "What is ports.http in config/services.json?", T["svc_http_port"], "n"),
        ("x_svc_grpc", "What is ports.grpc in config/services.json?", T["svc_grpc_port"], "n"),
        ("x_svc_metrics", "What is ports.metrics in config/services.json?", T["svc_metrics_port"], "n"),
        ("x_svc_maxconns", "What is limits.max_conns in config/services.json?", T["svc_max_conns"], "n"),
        ("x_svc_memory", "What is limits.memory_mb in config/services.json?", T["svc_memory"], "n"),
        ("x_svc_cpu", "What is limits.cpu in config/services.json?", T["svc_cpu"], "n"),
        ("x_svc_regions", "How many regions are listed in config/services.json? Answer with a number.", T["svc_region_count"], "n"),
        ("x_svc_deps", "How many dependencies are listed under 'deps' in config/services.json? Answer with a number.", T["svc_dep_count"], "n"),
    ]
    for id, prompt, val, kind in svc:
        if kind == "n":
            add(id, "json2", prompt, READ, (lambda v: lambda a: has_number(a, v))(val))
        else:
            add(id, "json2", prompt, READ, (lambda v: lambda a: contains(a, v))(val))

    # ---- new logs: per-level counts ----
    for name, lv in T["x_log_levels"].items():
        add(f"x_err_{name.split('.')[0]}", "grep",
            f"How many ERROR lines are in logs/{name}? Answer with a number.",
            GREP, (lambda n: lambda a: has_number(a, n))(lv["error"]))
        add(f"x_warn_{name.split('.')[0]}", "grep",
            f"How many WARN lines are in logs/{name}? Answer with a number.",
            GREP, (lambda n: lambda a: has_number(a, n))(lv["warn"]))

    # ---- grep over the new modules ----
    add("x_def_new", "grep",
        "How many function definitions (lines starting with 'def ') are there across "
        "src/lib and src/services combined? Answer with a number.",
        GREP, (lambda n: lambda a: has_number(a, n))(T["def_new"]))
    add("x_class_new", "grep",
        "How many class definitions are there in src/services and src/api/admin.py combined? Answer with a number.",
        GREP, (lambda n: lambda a: has_number(a, n))(T["class_new"]))

    # ---- find_files by extension (whole tree) ----
    for ext in [".py", ".csv", ".log", ".md", ".json", ".txt"]:
        n = T["by_ext"].get(ext, 0)
        add(f"x_ext_{ext[1:]}", "find_files",
            f"How many {ext} files are there in the whole project? Answer with a number.",
            ["find_files", "tree"], (lambda n: lambda a: has_number(a, n))(n))

    # ---- head / tail / read_lines on new files ----
    add("x_head_emp", "head",
        "What is the very first line (the header) of data/employees.csv?",
        ["head", "read_lines", "read_file"], lambda a: contains(a, "id,name,dept"))
    add("x_head_strings", "head",
        "What is the first line of src/lib/strings.py?",
        ["head", "read_lines", "read_file"], lambda a: contains(a, "zephyr_key"))
    add("x_head_billing", "head",
        "What is the first line of src/services/billing.py?",
        ["head", "read_lines", "read_file"], lambda a: contains(a, "vortex_id"))
    add("x_tail_scratch", "tail",
        "What is the last non-empty line of notes/scratch.txt?",
        ["tail", "read_lines", "read_file"], lambda a: contains(a, "scratch note three"))
    add("x_rl_products", "read_lines",
        "What is the text on line 2 of data/products.csv (the first data row)?",
        ["read_lines", "read_file", "head"], lambda a: contains(a, "alpha"))
    add("x_head_usage", "head",
        "What is the first heading line of docs/usage.md?",
        ["head", "read_lines", "read_file"], lambda a: contains(a, "usage"))

    # ---- a few reasoning / cross tasks ----
    add("x_emp_avg", "reason",
        "What is the integer average (floor) of all salaries in data/employees.csv? Answer with a number.",
        READ + ["run_python"], (lambda n: lambda a: has_number(a, n))(T["emp_avg_salary"]))

    # ---- negatives / robustness ----
    add("x_neg_dept", "negative",
        "How many employees in data/employees.csv work in the 'marketing' department? Answer with a number.",
        READ + ["find_in_files"],
        lambda a: has_number(a, 0) or contains_any(a, "none", "zero", "no ", "ningun"), negative=True)
    add("x_neg_token", "negative",
        "Which file contains the token NONEXISTENT_XYZ? If none, say so.",
        ["find_in_files", "grep"],
        lambda a: says_not_found(a) or contains_any(a, "none", "no file", "ningun"), negative=True)
    add("x_neg_product", "negative",
        "What is the price of the product named 'zeppelin' in data/products.csv?",
        READ, lambda a: says_not_found(a) or contains_any(a, "no such", "not in", "ningun", "no existe"),
        negative=True)

    return tasks
