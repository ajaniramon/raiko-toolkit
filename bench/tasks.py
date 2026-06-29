"""Suite of ~100 benchmark tasks + programmatic graders.

Categories: reads, counts, aggregations (CSV), nested JSON, navigation,
grep/find, head/tail/ranges, stat, multistep, cross-file comparisons,
conditional reasoning, negatives/robustness and list_models.

Each task: id, category, prompt, expect_tools, negative, check(answer)->bool.
Graders are format-tolerant (case-insensitive, they look for the key datum) but
strict about the correct value.
"""

import re


def _numbers(text: str):
    return [int(n) for n in re.findall(r"-?\d+", text.replace(",", "").replace(".", " "))]


def has_number(text: str, n: int) -> bool:
    return n in _numbers(text)


def contains(text: str, *subs) -> bool:
    low = text.lower()
    return all(s.lower() in low for s in subs)


def contains_any(text: str, *subs) -> bool:
    low = text.lower()
    return any(s.lower() in low for s in subs)


def yes(text: str) -> bool:
    return contains_any(text, "yes", "sí", "si,", " si ", "true", "correct", "afirmativ")


def no(text: str) -> bool:
    return contains_any(text, "no", "false", "not ", "isn't", "ningun", "neither")


NOT_FOUND = ["no existe", "no se encontr", "no encontr", "not exist", "doesn't exist",
             "does not exist", "not found", "no such", "cannot find", "can't find",
             "unable to", "not a file", "no hay", "no pude", "couldn't find",
             "could not find", "out of range", "fuera de rango", "no existe"]


def says_not_found(text: str) -> bool:
    return contains_any(text, *NOT_FOUND)


def build_tasks(truth: dict) -> list:
    T = truth
    tasks = []

    def add(id, category, prompt, expect_tools, check, negative=False):
        tasks.append({"id": id, "category": category, "prompt": prompt,
                      "expect_tools": expect_tools, "check": check, "negative": negative})

    READ = ["read_file", "head", "read_lines", "grep"]
    LISTING = ["list_dir", "tree", "find_files"]

    # ---- cwd / navigation ----
    add("cwd", "cwd", "What is the current working directory? Answer with the path.",
        ["get_current_directory"], lambda a: contains(a, T["root_name"]))
    add("top_count", "navigate",
        "How many entries (files and folders) are in the current directory, top level only? Answer with a number.",
        LISTING, lambda a: has_number(a, T["top_entries_count"]))
    add("top_dirs", "navigate",
        "Name all the top-level subdirectories in the current directory.",
        LISTING, lambda a: all(contains(a, d) for d in T["top_dirs"]))
    add("empty_dir", "navigate", "Is the directory named 'empty' actually empty? Answer yes or no.",
        LISTING + ["stat_path"], lambda a: yes(a))
    add("src_subdirs", "navigate",
        "How many subdirectories does the 'src' directory contain? Answer with a number.",
        LISTING, lambda a: has_number(a, T["src_subdir_count"]))
    add("api_files", "navigate", "List the Python files inside the src/api directory.",
        LISTING, lambda a: all(contains(a, f) for f in T["src_api_files"]))
    add("data_children", "navigate",
        "How many files are inside the 'data' directory? Answer with a number.",
        LISTING + ["stat_path"], lambda a: has_number(a, T["data_children"]))

    # ---- sizes ----
    add("largest", "size", "Which single file in the whole project tree is the largest? Give its file name.",
        LISTING + ["stat_path"], lambda a: contains(a, T["largest_file"]))
    add("smallest", "size", "Which single file in the whole project tree is the smallest? Give its file name.",
        LISTING + ["stat_path"], lambda a: contains(a, T["smallest_file"]))
    add("config_size", "size", "What is the size in bytes of config.ini?",
        ["stat_path", "count_lines"], lambda a: has_number(a, T["config_bytes"]))
    add("settings_size", "size", "What is the size in bytes of settings.json?",
        ["stat_path", "count_lines"], lambda a: has_number(a, T["settings_bytes"]))

    # ---- README ----
    add("readme_version", "read_file", "What version number is listed in README.md?",
        READ, lambda a: contains(a, T["readme_version"]))
    add("readme_secret", "read_file", "What is the secret access code mentioned in README.md?",
        READ, lambda a: contains(a, T["secret"].lower()))
    add("readme_license", "read_file", "What license does README.md state?",
        READ, lambda a: contains(a, T["license"]))
    add("readme_email", "read_file", "What contact email is given in README.md?",
        READ, lambda a: contains(a, T["email"]))

    # ---- config.ini ----
    add("config_port", "config", "What port is configured in config.ini?",
        READ, lambda a: has_number(a, T["config_port"]))
    add("config_timeout", "config", "What is the timeout value in config.ini?",
        READ, lambda a: has_number(a, T["config_timeout"]))
    add("config_retries", "config", "What is the max_retries value in config.ini?",
        READ, lambda a: has_number(a, T["config_retries"]))

    # ---- settings.json (nested) ----
    add("set_version", "json", "What is app.version in settings.json?",
        READ, lambda a: contains(a, T["settings_version"]))
    add("set_port", "json", "What is server.port in settings.json?",
        READ, lambda a: has_number(a, T["settings_port"]))
    add("set_workers", "json", "How many server.workers are configured in settings.json?",
        READ, lambda a: has_number(a, T["settings_workers"]))
    add("set_beta", "json", "How many items are in the features.beta list in settings.json? Answer with a number.",
        READ, lambda a: has_number(a, T["beta_count"]))
    add("set_maxusers", "json", "What is limits.max_users in settings.json?",
        READ, lambda a: has_number(a, T["max_users"]))
    add("set_owners", "json", "How many owners are listed in settings.json? Answer with a number.",
        READ, lambda a: has_number(a, T["owners_count"]))
    add("set_rate", "json", "What is limits.rate_per_sec in settings.json?",
        READ, lambda a: has_number(a, T["rate"]))

    # ---- users.csv aggregations ----
    add("u_admins", "csv_users", "How many users in data/users.csv have the role 'admin'? Answer with a number.",
        READ + ["find_in_files"], lambda a: has_number(a, T["admin_count"]))
    add("u_users", "csv_users", "How many users in data/users.csv have the role 'user'? Answer with a number.",
        READ + ["find_in_files"], lambda a: has_number(a, T["user_count"]))
    add("u_oldest", "csv_users", "In data/users.csv, what is the name of the oldest user?",
        READ, lambda a: contains(a, T["oldest_name"]))
    add("u_maxage", "csv_users", "In data/users.csv, what is the highest age? Answer with a number.",
        READ, lambda a: has_number(a, T["max_age"]))
    add("u_youngest", "csv_users", "In data/users.csv, what is the name of the youngest user?",
        READ, lambda a: contains(a, T["youngest_name"]))
    add("u_over40", "csv_users", "How many users in data/users.csv are older than 40? Answer with a number.",
        READ, lambda a: has_number(a, T["over40_count"]))
    add("u_scoresum", "csv_users", "What is the sum of all 'score' values in data/users.csv? Answer with a number.",
        READ, lambda a: has_number(a, T["score_sum"]))
    add("u_maxscore", "csv_users", "What is the highest score in data/users.csv? Answer with a number.",
        READ, lambda a: has_number(a, T["max_score"]))
    add("u_bestname", "csv_users", "Which user has the highest score in data/users.csv?",
        READ, lambda a: contains(a, T["best_name"]))
    add("u_minscore", "csv_users", "What is the lowest score in data/users.csv? Answer with a number.",
        READ, lambda a: has_number(a, T["min_score"]))

    # ---- sales.csv / inventory.csv aggregations ----
    add("s_total", "csv_agg", "What is the total of all 'amount' values in data/sales.csv? Answer with a number.",
        READ, lambda a: has_number(a, T["sales_total"]))
    add("s_rows", "csv_agg", "How many data rows (excluding header) does data/sales.csv have? Answer with a number.",
        READ + ["count_lines"], lambda a: has_number(a, T["sales_rows"]))
    add("s_north", "csv_agg", "How many rows in data/sales.csv are for the 'north' region? Answer with a number.",
        READ + ["find_in_files"], lambda a: has_number(a, T["north_count"]))
    add("s_maxsingle", "csv_agg", "What is the single largest amount in data/sales.csv? Answer with a number.",
        READ, lambda a: has_number(a, T["max_single_amount"]))
    add("s_regionmax", "csv_agg", "Which region has the highest total amount across data/sales.csv?",
        READ, lambda a: contains(a, T["region_max_total"]))
    add("i_value", "csv_agg",
        "In data/inventory.csv, what is the total inventory value (sum of qty*price across all items)? Answer with a number.",
        READ, lambda a: has_number(a, T["inv_value"]))
    add("i_count", "csv_agg", "How many distinct items are listed in data/inventory.csv? Answer with a number.",
        READ + ["count_lines"], lambda a: has_number(a, T["inv_count"]))
    add("i_maxqty", "csv_agg", "Which item has the highest qty in data/inventory.csv?",
        READ, lambda a: contains(a, T["inv_max_qty_item"]))
    add("i_totalqty", "csv_agg", "What is the total qty across all items in data/inventory.csv? Answer with a number.",
        READ, lambda a: has_number(a, T["inv_total_qty"]))

    # ---- count_lines / word counts ----
    for rel, n in T["line_counts"].items():
        add(f"lines_{rel.split('/')[-1]}", "count_lines",
            f"How many lines does {rel} have? Answer with a number.",
            ["count_lines", "read_file"], (lambda n: lambda a: has_number(a, n))(n))
    for rel, n in T["word_counts"].items():
        add(f"words_{rel.split('/')[-1]}", "count_lines",
            f"How many words are in {rel}? Answer with a number.",
            ["count_lines", "read_file"], (lambda n: lambda a: has_number(a, n))(n))

    # ---- grep counts ----
    add("g_defsrc", "grep",
        "How many function definitions (lines starting with 'def ') are there in total across the src directory? Answer with a number.",
        ["grep", "find_in_files", "read_file"], lambda a: has_number(a, T["def_count_src"]))
    add("g_classsrc", "grep",
        "How many class definitions (lines starting with 'class ') are there across the src directory? Answer with a number.",
        ["grep", "find_in_files", "read_file"], lambda a: has_number(a, T["class_count_src"]))
    add("g_import_main", "grep", "How many import statements are in src/main.py? Answer with a number.",
        ["grep", "read_file", "count_lines"], lambda a: has_number(a, T["import_main"]))
    add("g_err_app", "grep", "How many ERROR lines are in logs/app.log? Answer with a number.",
        ["grep", "find_in_files", "read_file"], lambda a: has_number(a, T["err_app"]))
    add("g_warn_app", "grep", "How many WARN lines are in logs/app.log? Answer with a number.",
        ["grep", "find_in_files", "read_file"], lambda a: has_number(a, T["warn_app"]))
    add("g_err_total", "grep", "How many ERROR lines are there across all files in the logs directory? Answer with a number.",
        ["grep", "find_in_files"], lambda a: has_number(a, T["err_total"]))
    add("g_warn_total", "grep", "How many WARN lines are there across the whole logs directory? Answer with a number.",
        ["grep", "find_in_files"], lambda a: has_number(a, T["warn_total"]))
    add("g_todo", "grep", "How many TODO comments are there in the entire project? Answer with a number.",
        ["grep", "find_in_files"], lambda a: has_number(a, T["todo_count"]))
    add("g_dunder", "grep", "How many lines in the entire project mention the literal token __main__? Answer with a number.",
        ["grep", "find_in_files"], lambda a: has_number(a, T["main_lines"]))
    add("g_filesdef", "grep", "Across the whole project, how many files contain the word 'def'? Answer with a number.",
        ["find_in_files", "grep"], lambda a: has_number(a, T["files_with_def"]))

    # ---- find_in_files: locate unique tokens ----
    add("loc_qelor", "locate", "Which file contains the token QELOR_TOKEN? Give its file name.",
        ["find_in_files", "grep"], lambda a: contains(a, "queries.py"))
    add("loc_alpha", "locate", "Which file contains the token ALPHA_MARK? Give its file name.",
        ["find_in_files", "grep"], lambda a: contains(a, "notes.txt"))
    add("loc_deadbeef", "locate", "Which file contains the token DEADBEEF? Give its file name.",
        ["find_in_files", "grep"], lambda a: contains(a, "app.log"))
    add("loc_omega", "locate", "Which file contains the token OMEGA_TOKEN? Give its file name.",
        ["find_in_files", "grep"], lambda a: contains(a, "big.log"))
    add("loc_ztoken", "locate", "Which file contains the token ZTOKEN_HANDLER? Give its file name.",
        ["find_in_files", "grep"], lambda a: contains(a, "handlers.py"))

    # ---- grep line numbers ----
    for line, mark in T["notes_markers"].items():
        add(f"line_{mark.lower()}", "grep",
            f"On which line number of data/notes.txt does {mark} appear? Answer with the number.",
            ["grep", "read_lines", "read_file"], (lambda l: lambda a: has_number(a, l))(line))
    add("line_omega", "grep", "On which line number of big.log does OMEGA_TOKEN appear? Answer with the number.",
        ["grep", "read_lines"], lambda a: has_number(a, T["omega_line"]))

    # ---- find_files ----
    add("ff_py", "find_files", "How many .py files are inside the src directory (including subfolders)? Answer with a number.",
        ["find_files", "tree", "list_dir"], lambda a: has_number(a, T["py_src_count"]))
    add("ff_md", "find_files", "List the names of all Markdown (.md) files in the project.",
        ["find_files", "tree", "grep"], lambda a: contains(a, "guide.md") and contains(a, "changelog.md"))
    add("ff_csv", "find_files", "How many .csv files are in the project? Answer with a number.",
        ["find_files", "tree"], lambda a: has_number(a, T["csv_count"]))
    add("ff_log", "find_files", "How many .log files are in the project? Answer with a number.",
        ["find_files", "tree"], lambda a: has_number(a, T["log_count"]))

    # ---- head / tail / read_lines ----
    add("head_main", "head", "What is the very first line of src/main.py?",
        ["head", "read_lines", "read_file"], lambda a: contains(a, "import os"))
    add("tail_app", "tail", "What is the last non-empty line of src/app.py?",
        ["tail", "read_lines", "read_file"], lambda a: contains(a, "app running"))
    add("rl_utils", "read_lines", "Show the content of lines 3 to 4 of src/utils.py.",
        ["read_lines", "head", "read_file"], lambda a: contains(a, "def helper"))
    add("rl_notes5", "read_lines", "What is the exact text on line 5 of data/notes.txt?",
        ["read_lines", "read_file", "head", "grep"], lambda a: contains(a, "alpha_mark"))
    add("rl_notes12", "read_lines", "What is the exact text on line 12 of data/notes.txt?",
        ["read_lines", "read_file", "grep"], lambda a: contains(a, "beta_mark"))
    add("head_guide", "head", "What is the first heading line of docs/guide.md?",
        ["head", "read_lines", "read_file"], lambda a: contains(a, "guide"))

    # ---- chained multistep ----
    add("ms_helper", "multistep",
        "Find which Python file contains a function called 'helper', then tell me what numeric value it multiplies its argument by.",
        ["grep", "find_in_files", "find_files", "read_file"], lambda a: has_number(a, 42))
    add("ms_alpha", "multistep",
        "Locate the file containing ALPHA_MARK, then report which line number it is on.",
        ["grep", "find_in_files", "read_lines", "read_file"], lambda a: has_number(a, 5))
    add("ms_qelor_lines", "multistep",
        "Find the file that contains QELOR_TOKEN, then report how many lines that file has.",
        ["grep", "find_in_files", "count_lines", "read_file"],
        lambda a: has_number(a, len("QELOR_TOKEN = \"unique\"\n\ndef select_all(table):\n    return table\n\ndef count_rows(table):\n    return 0\n".splitlines())))
    add("ms_mostlines", "multistep",
        "Which .py file in the src directory has the most lines? Give its file name.",
        ["find_files", "count_lines", "tree", "read_file"], lambda a: contains(a, T["most_lines_py"]))
    add("ms_biggest_csv", "multistep",
        "Find the largest .csv file in data, then report how many lines it has.",
        ["find_files", "stat_path", "count_lines", "list_dir"],
        lambda a: has_number(a, T["line_counts"]["data/users.csv"]))

    # ---- cross-file comparisons ----
    add("cmp_ports", "compare",
        "Does the port in config.ini match server.port in settings.json? Answer yes or no.",
        READ, lambda a: no(a))
    add("cmp_port_bigger", "compare",
        "Between the port in config.ini and server.port in settings.json, which number is larger? Answer with the number.",
        READ, lambda a: has_number(a, max(T["config_port"], T["settings_port"])))
    add("cmp_versions", "compare",
        "Does the version in README.md match app.version in settings.json? Answer yes or no.",
        READ, lambda a: no(a))
    add("cmp_logerrors", "compare", "Which log file in the logs directory has the most ERROR lines? Give its name.",
        ["grep", "find_in_files", "read_file"], lambda a: contains(a, T["log_most_errors"]))
    add("cmp_mainutils", "compare",
        "Which file has more lines, src/main.py or src/utils.py? Give the file name.",
        ["count_lines", "read_file"],
        lambda a: contains(a, "main.py" if T["line_counts"]["src/main.py"] > T["line_counts"]["src/utils.py"] else "utils.py"))

    # ---- conditional reasoning ----
    add("cond_admins", "reason", "Are there more than 2 admin users in data/users.csv? Answer yes or no.",
        READ, lambda a: yes(a))
    add("cond_moreusers", "reason",
        "Are there more 'user' rows than 'admin' rows in data/users.csv? Answer yes or no.",
        READ, lambda a: yes(a))
    add("cond_score90", "reason", "Does any user in data/users.csv have a score above 90? Answer yes or no.",
        READ, lambda a: yes(a))
    add("cond_debug", "reason", "Is debug mode enabled in settings.json? Answer yes or no.",
        READ, lambda a: no(a))
    add("cond_cache", "reason", "Is the 'cache' feature enabled in settings.json? Answer yes or no.",
        READ, lambda a: no(a))

    # ---- negatives / robustness ----
    add("neg_file", "negative",
        "Read the file 'database.sqlite' in the current directory and tell me which tables it defines.",
        READ + ["find_files", "list_dir"], lambda a: says_not_found(a), negative=True)
    add("neg_word", "negative",
        "How many times does the word 'kubernetes' appear anywhere in the project? Answer with a number.",
        ["grep", "find_in_files"],
        lambda a: has_number(a, 0) or contains_any(a, "none", "zero", "ningun", "doesn't appear", "does not appear", "no aparece"),
        negative=True)
    add("neg_dir", "negative", "List the contents of the directory 'node_modules' in this project.",
        LISTING + ["stat_path"], lambda a: says_not_found(a), negative=True)
    add("neg_todo_docs", "negative",
        "How many TODO comments are there in the docs directory? Answer with a number.",
        ["grep", "find_in_files"],
        lambda a: has_number(a, 0) or contains_any(a, "none", "zero", "no todo", "ningun"), negative=True)
    add("neg_outrange", "negative", "What is the text on line 999 of src/utils.py?",
        ["read_lines", "read_file"],
        lambda a: says_not_found(a) or contains_any(a, "only", "no line", "fewer", "solo", "no hay"), negative=True)

    # ---- list_models ----
    if T["model_entries_count"] > 0:
        add("lm_count", "list_models",
            "Using the model-listing tool, how many entries are in F:\\models? Answer with a number.",
            ["list_models"], lambda a: has_number(a, T["model_entries_count"]))
        add("lm_name", "list_models", "Using the model-listing tool, name at least one entry found in F:\\models.",
            ["list_models"], lambda a: contains_any(a, *T["model_entries"][:12]))
        add("lm_check", "list_models",
            "Using the model-listing tool, is there a folder named 'checkpoints' in F:\\models? Answer yes or no.",
            ["list_models"], lambda a: yes(a) if "checkpoints" in T["model_entries"] else no(a))

    # extra generated families (employees/products/services/extra logs & modules)
    if "x_line_counts" in T:
        from tasks_extra import build_extra_basic
        tasks.extend(build_extra_basic(T))

    return tasks
