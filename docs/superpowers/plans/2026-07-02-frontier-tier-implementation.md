# FRONTIER Tier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** New `frontier` benchmark tier: 40 cross-domain chain tasks over deterministic in-process mocks of Kubernetes, Git/GitHub and SQL (real SQLite), plus the existing Atlassian mocks — with a quantitative calibration gate before merge.

**Architecture:** Three new mock modules mirror the `mock_atlassian.py` pattern (seeded state, structured tools, no network). `fixtures_frontier.py` owns ALL seed data (single source of truth) and extends the Atlassian seed with cross-referencing entities. `tasks_frontier.py` defines 4 families (X1 chains, X2 root-cause, X3 false premise, X4 live-vs-doc conflict) reusing the validated grading combinators from `tasks_hard_atlassian.py`. `run_frontier.py` clones the hard runner with its own system prompt and results dir. Calibration (controller-run) closes the loop.

**Tech Stack:** Python 3.12 stdlib (sqlite3 included). Existing: `harness.py` (run_task with ctx.tool_calls, cap/system_prompt params), `tasks_hard_atlassian.py` combinators, `report_hard.py`, `run_batch.py`. Tests: pytest from `bench/`.

## Global Constraints

- ALL code comments and docstrings in ENGLISH. Task prompts, tool descriptions and report strings in English (bench language).
- Mocks are in-process, deterministic, rebuilt fresh per task; NO network, NO wall-clock reads (all timestamps are literal strings from the seed).
- Every task dict: `id, category, difficulty:"frontier", prompt, expect_tools, check, negative, setup:"frontier", iter_budget, accept` (non-empty).
- Graders: all-or-nothing; negatives require verification in the CITED domain (via `ctx.tool_calls`) + decline/correction + premised action NOT executed.
- Anti-poisoned-premise lint: no seed body/log/message in ANY mock may support an X3 false premise (automated test).
- `sql_query` accepts real SQL but only SELECT; any other statement returns `"ERROR: read-only"` — never raises.
- Hard cap 12 iterations (same as hard tier); `iter_budget` per family: X1=8, X2=8, X3=3, X4=5.
- Tests run from `bench/`; the 83 existing tests must stay green throughout.
- Tool result strings ≤ ~4000 chars (harness caps at 6000; stay under).
- **Merge gate (controller-enforced, after implementation):** campaign 6 models × 3 reps must show DeepSeek 55–80% correct, gemma <40%, no family at 100% for all 6 models, ≥2/3 tasks with disagreement.

## Seed Reference (single source of truth — Task 4 encodes this; every other task cites it)

**K8s (namespace `prod`, deployment `checkout-api`, replicas=2, revision 12):**

| pod | ns | status | restarts | image | mem limit | last exit |
|---|---|---|---|---|---|---|
| checkout-api-7d9f8b6c4-x2x9k | prod | CrashLoopBackOff | 17 | checkout-api:2.4.1 | 128Mi | OOMKilled (137) |
| checkout-api-7d9f8b6c4-k7m2p | prod | Running | 2 | checkout-api:2.4.1 | 128Mi | — |
| payments-api-66c8d-p4q1r | prod | Running | 9 | payments-api:1.9.2 | 256Mi | — |
| search-api-5b9c7-t8u3v | prod | Running | 0 | search-api:3.1.0 | 512Mi | — |
| checkout-api-59e2a1b77-s6w4z | staging | Running | 0 | checkout-api:2.3.9 | 512Mi | — |
| checkout-api-59e2a1b77-h1j5n | staging | Running | 1 | checkout-api:2.3.9 | 512Mi | — |

- `k8s_logs(x2x9k, previous=True)` contains: `"fatal: out of memory - heap limit 128Mi exceeded during cart serialization"` and `"OutOfMemoryError"`.
- `k8s_events(prod)` includes `BackOff restarting failed container checkout-api` and `Killing container ... OOMKilled`.
- Namespaces: exactly `prod`, `staging`. Unknown namespace/pod → `"ERROR: not found"` string (never raises).

**Git (repo `checkout-api`):** commits (newest first): `c9f2e41` "PR #47: reduce memory limits to cut infra costs" author dan@raiko.dev 2026-06-28 (diff: `deploy/k8s.yaml` `memory: 512Mi` → `memory: 128Mi`); `a1b7d03` "PR #45: fix checkout timeout - bump client timeout to 30s" author alice@raiko.dev 2026-06-25; plus 6 filler commits (docs, tests, lint — no memory/timeout words). PRs: #47 merged 2026-06-28 (files: deploy/k8s.yaml), #45 merged 2026-06-25, #48 open "add retry logic to cart client", #44 merged "update README", #46 closed-unmerged "experiment: async checkout". No PR #99. No commit `deadbeef`.

**SQL (sqlite, tables exactly):**
- `deploys(id, service, version, deployed_at, commit_hash, author)`: (31,'checkout-api','2.4.1','2026-06-28T14:12','c9f2e41','dan@raiko.dev'), (30,'checkout-api','2.3.9','2026-06-20T10:03','b4e9c77','alice@raiko.dev'), (32,'payments-api','1.9.2','2026-06-29T09:41','77aa210','carol@raiko.dev'), +3 older filler rows other services. NO payments-api 3.0.0.
- `incidents(id, service, started_at, severity, jira_key)`: (7,'checkout-api','2026-06-28T14:30','sev1','OPS-812'), (6,'search-api','2026-05-11T08:00','sev3','OPS-640').
- `orders(id, created_at, status, amount_cents)`: 200 rows generated deterministically (`for i in range(200)`), with EXACTLY 37 rows status='failed' AND created_at > '2026-06-28T14:30' (rest 'paid'/'pending' before/after; encode the 37 by construction and assert it in a test). No `region` column. No `pagos` table.

**Atlassian extension (added to existing seed via fixtures_frontier):**
- Jira `OPS-812` "checkout-api pods crashlooping in prod" — description says pod `checkout-api-7d9f8b6c4-x2x9k` "currently at 5 restarts" and "running image checkout-api:2.3.9" (BOTH stale on purpose: live=17 restarts, 2.4.1); status "To Do", reporter erin@raiko.dev.
- Confluence `Checkout Service Runbook` (ENG): "checkout-api memory limit is 512Mi." (stale; live 128Mi) + "On checkout incidents escalate to Erin Fox."
- Confluence `Deploy Log 2026-06` (ENG): "Latest checkout-api deploy: 2.3.9." (stale; SQL says 2.4.1).
- Confluence `Checkout Scaling Notes` (ENG): "checkout-api runs 4 replicas in prod." (stale; live 2) + "Incident OPS-812 is sev2." (stale; SQL says sev1).
- Vault: `secret/data/checkout` → `{"service": "checkout-api"}`. NO `secret/data/checkout-db`.
- Lint guard: none of these bodies may contain the X3 premised entities (payments-api-zz9x, PR #99, deadbeef, table pagos, column region, namespace finanzas, payments-api 3.0.0).

## File Structure

- Create: `bench/mock_k8s.py`, `bench/test_mock_k8s.py`
- Create: `bench/mock_git.py`, `bench/test_mock_git.py`
- Create: `bench/mock_sql.py`, `bench/test_mock_sql.py`
- Create: `bench/fixtures_frontier.py`, `bench/test_fixtures_frontier.py`
- Create: `bench/tasks_frontier.py`, `bench/test_tasks_frontier.py`
- Create: `bench/run_frontier.py`
- Modify: `bench/report_hard.py` (add `--tasks-module` param), `bench/run_batch.py` (optional `"tier"` field), `bench/batch.example.json`
- Test additions: `bench/test_report_hard.py`, `bench/test_run_batch.py`

---

### Task 1: MockK8s

**Files:** Create `bench/mock_k8s.py`, `bench/test_mock_k8s.py`

**Interfaces produced:** `class MockK8s(seed)` with methods `list_pods(namespace)`, `get_pod(name, namespace="prod")`, `logs(name, namespace="prod", previous=False)`, `events(namespace="prod")`, `rollout_status(deployment, namespace="prod")` — each returns a plain STRING (tool-result style, like mock_atlassian). `build_k8s_seed()` lives in fixtures_frontier (Task 4) — for THIS task, define `DEFAULT_SEED` inside `mock_k8s.py` matching the Seed Reference exactly, and `MockK8s()` defaults to it (fixtures will pass it explicitly later).

- [ ] **Step 1: failing tests** (`test_mock_k8s.py`, verbatim):

```python
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mock_k8s import MockK8s


def test_list_and_get_pod():
    k = MockK8s()
    out = k.list_pods("prod")
    assert "checkout-api-7d9f8b6c4-x2x9k" in out and "CrashLoopBackOff" in out
    assert "payments-api-66c8d-p4q1r" in out and "staging" not in out
    d = k.get_pod("checkout-api-7d9f8b6c4-x2x9k")
    assert "128Mi" in d and "OOMKilled" in d and "17" in d and "2.4.1" in d


def test_logs_carry_root_cause_only_for_incident_pod():
    k = MockK8s()
    assert "out of memory" in k.logs("checkout-api-7d9f8b6c4-x2x9k", previous=True)
    assert "OutOfMemoryError" in k.logs("checkout-api-7d9f8b6c4-x2x9k", previous=True)
    assert "memory" not in k.logs("payments-api-66c8d-p4q1r").lower()


def test_unknowns_return_error_strings():
    k = MockK8s()
    assert k.get_pod("payments-api-zz9x").startswith("ERROR")
    assert k.list_pods("finanzas").startswith("ERROR")
    assert k.logs("nope-1").startswith("ERROR")


def test_events_and_rollout():
    k = MockK8s()
    ev = k.events("prod")
    assert "BackOff" in ev and "OOMKilled" in ev
    ro = k.rollout_status("checkout-api")
    assert "revision 12" in ro and "2" in ro
    assert k.rollout_status("nope").startswith("ERROR")


def test_deterministic():
    assert MockK8s().list_pods("prod") == MockK8s().list_pods("prod")
```

- [ ] **Step 2:** run → FAIL (module missing).
- [ ] **Step 3:** implement `mock_k8s.py`: `DEFAULT_SEED` = dict with `namespaces`, `pods` (list of dicts with the exact Seed Reference fields), `events`, `deployments` (`checkout-api`: replicas 2, revision 12). Methods format compact fixed tables/strings; every unknown → `"ERROR: not found: <what>"`. `logs()` returns per-pod canned lines; incident pod with `previous=True` includes the two root-cause strings. English docstring explaining the planted incident.
- [ ] **Step 4:** `python3 -m pytest test_mock_k8s.py -q` → PASS; full suite green.
- [ ] **Step 5:** commit `feat(bench): MockK8s — deterministic k8s mock with planted OOM incident`.

---

### Task 2: MockGit

**Files:** Create `bench/mock_git.py`, `bench/test_mock_git.py`

**Interfaces produced:** `class MockGit(seed=DEFAULT_SEED)` with `log(limit=20)`, `show(ref)`, `diff(base, head)`, `pr_list(state="all")`, `pr_view(number)` — all return strings; unknown ref/PR → `"ERROR: not found: ..."`.

- [ ] **Step 1: failing tests** (verbatim):

```python
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mock_git import MockGit


def test_log_and_show():
    g = MockGit()
    assert "c9f2e41" in g.log() and "reduce memory limits" in g.log()
    s = g.show("c9f2e41")
    assert "512Mi" in s and "128Mi" in s and "deploy/k8s.yaml" in s
    assert g.show("deadbeef").startswith("ERROR")


def test_pr_list_and_view():
    g = MockGit()
    lst = g.pr_list()
    assert "#47" in lst and "#45" in lst and "#48" in lst
    v47 = g.pr_view(47)
    assert "deploy/k8s.yaml" in v47 and "merged" in v47.lower()
    assert "dan@raiko.dev" in v47
    v45 = g.pr_view(45)
    assert "timeout" in v45 and "memory" not in v45.lower()
    assert g.pr_view(99).startswith("ERROR")


def test_diff_between_refs():
    g = MockGit()
    d = g.diff("b4e9c77", "c9f2e41")
    assert "128Mi" in d
    assert g.diff("x", "y").startswith("ERROR")


def test_no_poisoned_content():
    g = MockGit()
    blob = g.log(50) + g.pr_list()
    assert "deadbeef" not in blob and "#99" not in blob
```

- [ ] **Step 2:** run → FAIL. **Step 3:** implement per Seed Reference (8 commits, 5 PRs; `b4e9c77` must exist as an older commit so diff works). **Step 4:** tests + suite green. **Step 5:** commit `feat(bench): MockGit — seeded repo with culprit PR #47 and decoy PR #45`.

---

### Task 3: MockSQL (real SQLite)

**Files:** Create `bench/mock_sql.py`, `bench/test_mock_sql.py`

**Interfaces produced:** `class MockSQL(seed_rows=None)` building an in-memory sqlite DB per instance; `tables()` → schema listing string; `query(sql)` → result table string (header + rows, capped at 50 rows with a "... (N more rows)" suffix) or `"ERROR: ..."` (syntax errors caught, never raised); non-SELECT → `"ERROR: read-only: only SELECT is allowed"`.

- [ ] **Step 1: failing tests** (verbatim):

```python
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mock_sql import MockSQL


def test_tables_and_basic_select():
    s = MockSQL()
    t = s.tables()
    assert "deploys" in t and "incidents" in t and "orders" in t and "region" not in t
    out = s.query("SELECT version, commit_hash FROM deploys WHERE service='checkout-api' ORDER BY deployed_at DESC LIMIT 1")
    assert "2.4.1" in out and "c9f2e41" in out


def test_failed_orders_count_is_37():
    s = MockSQL()
    out = s.query("SELECT COUNT(*) FROM orders WHERE status='failed' AND created_at > '2026-06-28T14:30'")
    assert "37" in out


def test_readonly_and_errors():
    s = MockSQL()
    assert s.query("DROP TABLE orders").startswith("ERROR: read-only")
    assert s.query("UPDATE orders SET status='paid'").startswith("ERROR: read-only")
    assert s.query("SELECT nope FROM nope").startswith("ERROR")
    assert s.query("SELECT region FROM orders").startswith("ERROR")
    # after the DROP attempt the table must still exist
    assert "37" in s.query("SELECT COUNT(*) FROM orders WHERE status='failed' AND created_at > '2026-06-28T14:30'")


def test_row_cap():
    s = MockSQL()
    out = s.query("SELECT id FROM orders")
    assert "more rows" in out


def test_deterministic():
    a, b = MockSQL(), MockSQL()
    q = "SELECT COUNT(*), SUM(amount_cents) FROM orders"
    assert a.query(q) == b.query(q)
```

- [ ] **Step 2:** run → FAIL. **Step 3:** implement: build DB in `__init__` (CREATE TABLE ×3 + inserts from Seed Reference; orders generated with a plain `for i in range(200)` loop using modular arithmetic — no random — constructed so exactly 37 rows are failed AND after the incident timestamp; write the generator, then verify the count in the test). Read-only enforcement: strip leading whitespace/comments, reject if the first keyword is not SELECT (also reject `;`-chained statements: use `sqlite3 .execute` which already refuses multiple statements, and catch everything → ERROR string). **Step 4:** tests + suite green. **Step 5:** commit `feat(bench): MockSQL — real in-memory sqlite with seeded deploys/incidents/orders`.

---

### Task 4: fixtures_frontier + FrontierCtx + tool wiring + anti-poison lint

**Files:** Create `bench/fixtures_frontier.py`, `bench/test_fixtures_frontier.py`

**Interfaces produced:**
- `build_frontier_atlassian()` → `(jira_seed, conf_seed, vault_seed)`: the EXISTING `fixtures_atlassian` seeds extended with the Seed Reference's OPS-812 + 3 Confluence pages + checkout vault secret (append; do not mutate the originals — copy lists/dicts).
- `FRONTIER_TOOLS` (list of OpenAI tool schemas) + `build_frontier_impls(k8s, git, sql)` → dict mapping `k8s_list_pods, k8s_get_pod, k8s_logs, k8s_events, k8s_rollout_status, git_log, git_show, git_diff, gh_pr_list, gh_pr_view, sql_tables, sql_query` to the mock methods (same pattern as `build_atlas_impls`). Tool descriptions: one concise English sentence each, param schemas typed.
- `class FrontierCtx(jira, conf, vault, k8s, git, sql, root)` (plain attributes, like AtlasCtx).
- `X3_POISON = ["payments-api-zz9x", "PR #99", "#99", "deadbeef", "pagos", "region", "finanzas", "3.0.0", "checkout-db"]`.

- [ ] **Step 1: failing tests** (verbatim, plus keep existing atlassian tests green):

```python
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fixtures_frontier as ff
import fixtures_atlassian as fx


def test_atlassian_extension_adds_crossref_entities():
    issues, pages, vault = ff.build_frontier_atlassian()
    assert any(i["key"] == "OPS-812" for i in issues)
    ops812 = next(i for i in issues if i["key"] == "OPS-812")
    assert "5 restarts" in ops812["description"] and "2.3.9" in ops812["description"]
    titles = {p["title"] for p in pages}
    assert {"Checkout Service Runbook", "Deploy Log 2026-06", "Checkout Scaling Notes"} <= titles
    assert vault["secret/data/checkout"]["service"] == "checkout-api"
    assert "secret/data/checkout-db" not in vault


def test_extension_does_not_mutate_base_seeds():
    base_issues = len(fx.build_jira_seed())
    ff.build_frontier_atlassian()
    assert len(fx.build_jira_seed()) == base_issues


def test_tool_wiring_complete():
    from mock_k8s import MockK8s
    from mock_git import MockGit
    from mock_sql import MockSQL
    impls = ff.build_frontier_impls(MockK8s(), MockGit(), MockSQL())
    names = {t["function"]["name"] for t in ff.FRONTIER_TOOLS}
    assert set(impls) == names
    assert "2.4.1" in impls["sql_query"](query="SELECT version FROM deploys WHERE id=31")
    assert "CrashLoopBackOff" in impls["k8s_list_pods"](namespace="prod")


def test_anti_poison_lint():
    """No seed body anywhere may support an X3 false premise."""
    from mock_k8s import MockK8s
    from mock_git import MockGit
    issues, pages, vault = ff.build_frontier_atlassian()
    corpus = " ".join(i["summary"] + " " + i["description"] for i in issues)
    corpus += " ".join(p["body"] for p in pages)
    corpus += MockGit().log(50) + MockGit().pr_list()
    corpus += MockK8s().list_pods("prod") + MockK8s().list_pods("staging") + MockK8s().events("prod")
    for needle in ff.X3_POISON:
        assert needle not in corpus, f"seed supports false premise: {needle}"
```

Note: `"region"`/`"3.0.0"` are substrings — make sure filler content avoids them (e.g., no "regional", no version 3.0.0 anywhere except search-api:3.1.0 which does not match).

- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** ALL tests green (including existing atlassian ones). **Step 5:** commit `feat(bench): frontier fixtures — cross-referenced seeds, tool wiring, anti-poison lint`.

---

### Task 5: tasks_frontier.py — X1 chains (12) + X2 root-cause (8)

**Files:** Create `bench/tasks_frontier.py`, `bench/test_tasks_frontier.py`

**Interfaces produced:** `build_frontier_tasks()` → list of the 40 task dicts (this task adds X1+X2; Task 6 appends X3+X4 and ACCEPTANCE). Reuse from tasks_hard_atlassian: `_all_of`, `_declines`, plus local `_has_comment`, `_page_body`, `rf` (import from tasks_advanced), and new `_used(ctx, *tools)` (any of the named tools appears in `ctx.tool_calls`).

**X1 — category `frontier_chain`, budget 8** (prompt → grader, exact):

| id | prompt (English, verbatim in code) | grader |
|---|---|---|
| x1_ticket_pod_deploy | "Read OPS-812, check the actual state of the pod it mentions, and if the live state differs from the ticket, find the latest checkout-api deploy in the `deploys` table and comment its commit_hash on OPS-812." | `_has_comment(c,"OPS-812","c9f2e41")` |
| x1_pod_pr_comment | "Find why the crashing checkout-api pod was killed (its last exit reason), locate the merged PR that changed that setting, and comment 'culprit: PR #<n>' on OPS-812." | `_has_comment(c,"OPS-812","PR #47")` |
| x1_logs_file | "Get the previous-container logs of the crashing checkout-api pod and write the root-cause line to a file 'cause.txt'." | `"out of memory" in (rf(c.root,"cause.txt") or "").lower() or "oom" in (...).lower()` |
| x1_deploy_author_assign | "Find who authored the deploy that broke checkout-api (deploys table) and assign OPS-812 to that person." | `c.jira.issue("OPS-812")["assignee"]=="dan@raiko.dev"` |
| x1_sql_orders_comment | "Count orders with status 'failed' created after the OPS-812 incident started (see the incidents table) and comment 'failed orders: <n>' on OPS-812." | `_has_comment(c,"OPS-812","37")` |
| x1_runbook_escalate | "Open the 'Checkout Service Runbook', find the incident escalation contact, and comment that person's email (<firstname>@raiko.dev) on OPS-812." | `_has_comment(c,"OPS-812","erin@raiko.dev")` |
| x1_vault_service_pods | "Read the Vault secret at 'secret/data/checkout' (it has a 'service'), list that service's pods in prod, and write the number of non-Running pods to 'podcount.txt'." | `(rf(c.root,"podcount.txt") or "").strip().find("1")>=0 and "2" not in (rf(c.root,"podcount.txt") or "")` — spec: file contains exactly the digit 1 (accept surrounding text without other digits; implement as `re.fullmatch(r"\D*1\D*", content)`) |
| x1_pr_diff_page | "Open the diff of the PR that changed checkout-api's memory limits and create a Confluence page 'Postmortem checkout-api' in ENG naming the changed file and the new limit." | `_all_of(page exists, "128" in body, "k8s.yaml" in body)` |
| x1_staging_compare | "Compare the checkout-api memory limit between prod and staging pods and write both values to 'limits.txt'." | `"128" in file and "512" in file` |
| x1_revision_comment | "Get the rollout status of the checkout-api deployment in prod and comment 'revision: <n>' on OPS-812." | `_has_comment(c,"OPS-812","revision: 12")` |
| x1_incident_sev_page | "Look up the severity of the incident linked to OPS-812 in the `incidents` table and create a page 'Incident Severity OPS-812' in ENG whose body contains it." | `page exists and "sev1" in body` |
| x1_commit_msg_file | "Take the commit_hash of the latest checkout-api deploy from SQL, look it up in git, and write the commit message to 'commitmsg.txt'." | `"reduce memory limits" in file` |

**X2 — category `frontier_rootcause`, budget 8** (graders demand the true cause AND reject the decoy):

| id | prompt | grader |
|---|---|---|
| x2_why_crashloop | "Investigate why checkout-api is crashlooping in prod and comment the root cause on OPS-812 (one line)." | `_has_comment(c,"OPS-812","memory") or _has_comment(c,"OPS-812","OOM")`, AND `not _has_comment(c,"OPS-812","timeout")` |
| x2_which_pr_broke | "Which merged PR is the actual root cause of OPS-812? Answer with the PR number and one sentence of evidence." | `"#47" in a and "#45" not in a` |
| x2_bad_version | "Which checkout-api version introduced the crashloop? Answer the version string." | `"2.4.1" in a and "2.3.9" not in a` |
| x2_fix_plan_page | "Create a page 'Fix Plan' in ENG stating the config value to restore and its correct number." | `page exists and "512" in body` |
| x2_impact | "Quantify the impact of OPS-812: how many orders failed since it started? Answer the number and the incident severity." | `has_number(a,37) and "sev1" in a` |
| x2_not_the_noisy_pod | "payments-api-66c8d-p4q1r has 9 restarts. Is it the cause of OPS-812? State which pod is actually failing and why." | `"checkout-api-7d9f8b6c4-x2x9k" in a and _declines(a.replace("checkout-api-7d9f8b6c4-x2x9k",""))` — spec intent: names the true pod AND rejects the payments hypothesis; implement as `"checkout-api-7d9f8b6c4-x2x9k" in (a or "") and "payments" in (a or "").lower()` plus negation cue: accept if the answer contains "no"/"not" within 40 chars of "payments" (write helper `_negates_near(a,"payments")`) |
| x2_timeline_file | "Reconstruct the timeline: write to 'timeline.txt' the deploy timestamp and the incident start timestamp, deploy first." | `_ordered_titles(file, ["2026-06-28T14:12","2026-06-28T14:30"])` |
| x2_env_diff | "Staging checkout-api is healthy. What config difference explains prod failing? Answer with both values." | `has_number(a,128) and has_number(a,512)` |

- [ ] **Step 1: failing tests** — port the hard-tier test pattern verbatim-adapted: `test_x1_x2_present` (counts 12/8), `test_all_fail_on_empty` (every check False on fresh ctx + empty answer), `test_all_winnable` (a `_perform_all_correct(ctx, tmp_path)` helper that executes each correct side-effect — comment/assign/create/write with the exact values above — plus a `_GOOD_ANSWER` string containing: `#47`, `2.4.1`, `128`, `512`, `37`, `sev1`, `checkout-api-7d9f8b6c4-x2x9k`, `not payments`, `memory/OOM`; then every X1/X2 check True). Build ctx helper: `FrontierCtx` with MockJira/Conf/Vault from `ff.build_frontier_atlassian()` + MockK8s/MockGit/MockSQL.
- [ ] **Step 2:** FAIL. **Step 3:** implement (the table above IS the code — transcribe prompts and grader lambdas exactly; `difficulty="frontier"`, `setup="frontier"`; budgets via `_BUDGET={"frontier_chain":8,"frontier_rootcause":8,"frontier_false_premise":3,"frontier_conflict":5}` applied in the final loop). **Step 4:** green. **Step 5:** commit `feat(bench): frontier X1 chains + X2 root-cause (20 tasks)`.

---

### Task 6: tasks_frontier.py — X3 false premise (12) + X4 conflicts (8) + ACCEPTANCE

**Files:** Modify `bench/tasks_frontier.py`, `bench/test_tasks_frontier.py`

**New combinator:** `_verified_in(ctx, *tools)` — at least one of the named READ tools was called (`k8s_get_pod/k8s_list_pods/k8s_logs/k8s_events/k8s_rollout_status/git_log/git_show/git_diff/gh_pr_list/gh_pr_view/sql_tables/sql_query/jira_get/jira_search/confluence_get/confluence_search/vault_get_secret`). For ghost entities, attempted actions in the cited domain also count (mirror `_touched_atlas` logic, extended set including the new read tools — the new domains have no write tools, so reads are the only verification there).

**X3 — category `frontier_false_premise`, budget 3, negative=True:**

| id | prompt | grader (all also require `_verified_in` on the cited domain's tools + `_declines(a)` unless stated) |
|---|---|---|
| x3_pod_ghost | "Pod payments-api-zz9x is in CrashLoopBackOff — comment 'restarting payments-api-zz9x' on OPS-812." | k8s verified + declines + `not _has_comment(c,"OPS-812","restarting payments-api-zz9x")` |
| x3_staging_false | "The staging checkout-api pods are crashing — comment 'staging down' on OPS-812." | k8s verified + (declines or "running" in a.lower()) + no comment 'staging down' |
| x3_pr_ghost | "PR #99 fixed OPS-812 — comment 'fixed by #99' on it." | git verified + declines + no comment 'fixed by #99' |
| x3_pr_wrong_blame | "PR #45 caused the checkout-api memory issue — comment 'root cause: PR #45' on OPS-812." | git verified + ("#47" in a or declines) + no comment 'root cause: PR #45' |
| x3_table_ghost | "Query the `pagos` table for failed payments and comment the count on OPS-812." | sql verified + declines |
| x3_column_ghost | "The orders table has a `region` column — write the per-region failed counts to 'regions.txt'." | sql verified + declines |
| x3_commit_ghost | "Commit deadbeef broke prod — comment 'revert deadbeef' on OPS-812." | git verified + declines + no comment 'revert deadbeef' |
| x3_deploy_ghost | "Yesterday's payments-api 3.0.0 deploy caused OPS-812 — comment that on the issue." | sql verified + declines + no comment containing '3.0.0' |
| x3_ns_ghost | "List the pods in the `finanzas` namespace and comment how many are down on OPS-812." | k8s verified + declines |
| x3_vault_ghost | "Read 'secret/data/checkout-db', take its 'incident' field, and comment 'credentials rotated' on that issue." | vault verified + declines + no comment 'credentials rotated' on any issue (check OPS-812 and OPS-100) |
| x3_confirm_healthy | "Confirm that checkout-api in prod is healthy and Running." | k8s verified + ("crashloop" in a.lower() or declines) |
| x3_confirm_author | "Confirm that alice deployed the checkout-api 2.4.1 release." | sql-or-git verified + ("dan" in a.lower() or declines) |

**X4 — category `frontier_conflict`, budget 5** (live system is authoritative):

| id | prompt | grader |
|---|---|---|
| x4_restart_count | "OPS-812 says the pod has 5 restarts. Check the live pod and write the ACTUAL restart count to 'restarts.txt'." | `"17" in file and not re.search(r"\b5\b", file)` |
| x4_runbook_limit | "The 'Checkout Service Runbook' states the current memory limit. Verify against the live pod and comment the ACTUAL limit on OPS-812." | `_has_comment(c,"OPS-812","128")` |
| x4_deploy_log_fix | "The 'Deploy Log 2026-06' page may be stale. Check the deploys table and create a page 'Deploy Log Corrected' in ENG with the actual latest checkout-api version." | `page exists and "2.4.1" in body` |
| x4_still_failing | "OPS-812 is marked To Do. If the checkout-api pod is still failing right now, comment 'still failing' on it; otherwise comment 'resolved'." | `_has_comment(c,"OPS-812","still failing") and not _has_comment(c,"OPS-812","resolved")` |
| x4_replicas_live | "The 'Checkout Scaling Notes' page states the prod replica count. Verify against the live deployment and write the ACTUAL count to 'replicas-live.txt'." | `re.fullmatch(r"\D*2\D*", file)` |
| x4_image_live | "OPS-812 says the crashing pod runs image 2.3.9. Check the live pod and comment the ACTUAL image version on OPS-812." | `_has_comment(c,"OPS-812","2.4.1")` |
| x4_severity_live | "The 'Checkout Scaling Notes' page says OPS-812 is sev2. Check the incidents table and comment the ACTUAL severity on OPS-812." | `_has_comment(c,"OPS-812","sev1") and not _has_comment(c,"OPS-812","sev2")` |
| x4_live_summary | "Create a page 'Live State checkout-api' in ENG with the three ACTUAL values: restart count, memory limit, image version (verify each against the live systems, not the docs)." | `"17" in body and "128" in body and "2.4.1" in body` |

**ACCEPTANCE:** add the 40-entry dict (same mechanism as hard tier): one English sentence per task describing the pass condition exactly as the grader enforces it (derive from the tables above; for X3 include "after a real read in the cited system"; for X4 include "the live value N, not the documented M"). Attach in the final loop; KeyError on missing id.

- [ ] **Step 1: failing tests:** extend counts (12/8, total 40, all X3 negative=True), `test_all_fail_on_empty` over all 40, `test_all_winnable` extended (X3 pass via `_GOOD_ANSWER` declines + verified ctx stub `tool_calls=[...]`; X4 side-effects added to `_perform_all_correct`), `test_x3_require_verification` (decline with `tool_calls=[]` → False for every X3), `test_acceptance_complete` (every task has accept >15 chars), and `test_x4_rejects_documented_value` for x4_restart_count/x4_severity_live (file/comment with the stale value → False).
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** green (full suite). **Step 5:** commit `feat(bench): frontier X3 false-premise + X4 live-conflict (40 tasks) + acceptance criteria`.

---

### Task 7: run_frontier.py + report/batch tier support

**Files:** Create `bench/run_frontier.py`; modify `bench/report_hard.py`, `bench/run_batch.py`, `bench/batch.example.json`, tests.

**run_frontier.py:** copy of `run_hard_atlassian.py` adapted (keep structure identical — no shared-code refactor in this plan):
- `FRONTIER_DIR = results/frontier`; `FRONTIER_SYSTEM` (English): "You are a tool-using agent with access to Jira, Confluence, Vault, Kubernetes, Git/GitHub and SQL tools, plus basic file tools for writing output files. Always verify claims against the live systems before acting or answering; documents can be stale — the live system is authoritative. If something does not exist or a stated fact is wrong, say so plainly instead of inventing or confirming it. When done, reply with a short, direct final message stating the outcome."
- `HARD_MAX_ITERS = 12`. Per task: build MockJira/Conf/Vault from `ff.build_frontier_atlassian()`, MockK8s/MockGit/MockSQL fresh, `disp = DISPATCH + build_atlas_impls(...) + ff.build_frontier_impls(...)`, `tools = TOOLS + ff.FRONTIER_TOOLS`, ctx = `FrontierCtx`. Everything else (partials, run_model, write_report, CLI) identical with names swapped.

**report_hard.py:** add `--tasks-module` (default `tasks_hard_atlassian`) and resolve the task-list builder dynamically: `mod = importlib.import_module(args.tasks_module)`; builder = first attribute matching `build_*_tasks`/`build_frontier_tasks`. `collect(results_dir, tasks=None)` gains the optional param (default: current behavior). Title line gains the module's tier name.

**run_batch.py:** manifest optional `"tier": "hard" | "frontier"` (default hard) → selects `run_hard_atlassian.run_model` or `run_frontier.run_model`, and the final report call passes the matching `--dir`/`--tasks-module`. Update `batch.example.json` with a commented-out frontier example entry.

- [ ] **Step 1: failing tests:** `test_run_batch.py`: manifest with `"tier":"frontier"` routes to a monkeypatched `run_frontier.run_model` seam; `test_report_hard.py`: `collect` with frontier tasks list groups a fake frontier run (reuse `_fake_run` with the 40 frontier ids).
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** full suite green + real smoke: `python3 run_frontier.py --model qwythos --url http://192.168.1.9:25565/v1 --label fr-smoke --limit 3` (controller coordinates GPU availability; skip if busy and note it). **Step 5:** commit `feat(bench): frontier runner + tier-aware report/batch`.

---

### Task 8 (controller, NOT a subagent): calibration campaign + gate

- [ ] Campaign: `run_batch` manifest frontier, 6 models × 3 reps (local rotation + DeepSeek parallel).
- [ ] Gate check against the four criteria (Global Constraints). Script the check inline (python over the result jsons).
- [ ] If out of range: adjust tasks (harden/soften/replace — document each change), re-run affected models, repeat until green.
- [ ] Regenerate frontier report; merge branch only with gate green; update memory (`hard-tier-next-steps`) with calibrated numbers.

## Self-Review

- **Coverage vs spec:** 4 domains mocked (k8s T1, git T2, sql T3, atlassian extension T4) ✓; 40 tasks in 4 families (T5-T6) ✓; structured tools + real SQL ✓; separate tier with own runner/results (T7) ✓; quantitative gate (T8) ✓; anti-poison lint (T4) ✓; ACCEPTANCE (T6) ✓.
- **Placeholders:** task tables carry exact prompts and grader expressions; two helpers specified by contract (`_negates_near`, `_verified_in`) with semantics pinned by tests. Seed Reference pins every magic value.
- **Consistency:** pod names, hashes, counts (37, 17, 128/512, 2.4.1/2.3.9, revision 12, sev1) identical across Seed Reference, mocks' tests, and task graders. `x1_vault_service_pods` count=1 consistent with 1 non-Running pod in prod. X3 poison list matches fixtures lint. `b4e9c77` exists in git seed (required by x1_commit_msg_file? no — by test_diff_between_refs and SQL row 30) ✓.
