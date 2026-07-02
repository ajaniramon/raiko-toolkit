"""In-process mock of a read-only SQL database for the Frontier tier.

Mirrors the style of mock_k8s.py / mock_git.py: seeded, deterministic,
in-process state; the two public methods (`tables`, `query`) return plain
tool-result STRINGs; any failure (bad SQL, unknown table/column, or a
write attempt) returns an "ERROR: ..." string, never raises.

Unlike the other mocks this one is backed by a REAL sqlite3 database
(":memory:") so an agent's SQL is actually executed rather than pattern
matched. Only `SELECT` statements are allowed:

- the leading keyword is extracted after stripping whitespace and any
  leading `--`/`/* */` comments; anything other than SELECT is rejected
  before it ever reaches sqlite;
- `sqlite3.Connection.execute` already refuses to run more than one
  statement per call, so `SELECT ...; DROP TABLE ...` fails with a
  sqlite3 error, which is caught and turned into an "ERROR: ..." string;
- as defense in depth the connection is additionally put into
  `PRAGMA query_only = ON` mode after seeding, so even a SELECT-prefixed
  statement that sqlite would otherwise treat as mutating (there isn't
  one, but belt and suspenders) cannot alter the data.

Planted incident cross-reference (see mock_k8s.py / mock_git.py):
`deploys` row (31, checkout-api, 2.4.1, 2026-06-28T14:12, c9f2e41,
dan@raiko.dev) is the deploy that shipped the memory-limit regression;
`incidents` row (7, checkout-api, 2026-06-28T14:30, sev1, OPS-812) is the
resulting incident. `orders` carries the business-impact evidence: exactly
37 orders failed strictly after the incident's start time, built by
construction (see `_generate_orders` below) rather than by chance.
"""
import re
import sqlite3
from datetime import datetime, timedelta

MAX_RESULT_ROWS = 50

# The incident that "orders" evidence is built around (see mock_k8s.py /
# mock_git.py): checkout-api started crash-looping and failing checkouts at
# this instant.
INCIDENT_TS = "2026-06-28T14:30"


def _generate_orders():
    """Deterministically build the 200 `orders` rows with a plain loop.

    No randomness is used. Each order's timestamp is a fixed offset from a
    fixed start date, and its status is picked by simple index arithmetic
    (a range check plus `i % 3` for variety), so the result is identical on
    every call and across processes.

    Construction, by the numbers:
    - START = 2026-06-20T00:00, STEP_MINUTES = 90.
    - order i (0-indexed) gets created_at = START + i * 90 minutes.
    - minutes from START to the incident (2026-06-28T14:30) = 8*1440 + 870
      = 12390. Since 90 does not divide 12390 evenly (12390 / 90 =
      137.66...), no order lands exactly on the incident timestamp, so
      "> INCIDENT_TS" has a clean, unambiguous boundary:
        i=137 -> 90*137=12330 min -> 2026-06-28T13:30 (NOT after incident)
        i=138 -> 90*138=12420 min -> 2026-06-28T15:00 (first order after)
      So exactly the orders with index i >= 138 (i.e. i in 138..199, 62
      orders) have created_at > INCIDENT_TS.
    - Of those 62 post-incident orders, the first 37 (i = 138..174) are
      marked 'failed'; the remaining 25 (i = 175..199), like every
      pre-incident order (i = 0..137), are 'paid' or 'pending' via
      `i % 3 == 0 -> pending else paid`. No other row is ever 'failed'.
    - Therefore COUNT(status='failed' AND created_at > INCIDENT_TS) is
      exactly 37, by construction, and status='failed' never occurs
      before the incident.
    """
    start = datetime(2026, 6, 20, 0, 0)
    step_minutes = 90
    post_incident_start = 138  # first index whose created_at > INCIDENT_TS
    failed_count = 37

    rows = []
    for i in range(200):
        ts = start + timedelta(minutes=step_minutes * i)
        created_at = ts.strftime("%Y-%m-%dT%H:%M")
        if post_incident_start <= i < post_incident_start + failed_count:
            status = "failed"
        elif i % 3 == 0:
            status = "pending"
        else:
            status = "paid"
        # Deterministic, non-random amount derived from i via modular
        # arithmetic so SUM(amount_cents) is stable across instances.
        amount_cents = ((i * 137 + 599) % 9000) + 100
        rows.append((i + 1, created_at, status, amount_cents))
    return rows


SCHEMA = {
    "deploys": (
        "CREATE TABLE deploys ("
        "id INTEGER PRIMARY KEY, service TEXT, version TEXT, "
        "deployed_at TEXT, commit_hash TEXT, author TEXT)"
    ),
    "incidents": (
        "CREATE TABLE incidents ("
        "id INTEGER PRIMARY KEY, service TEXT, started_at TEXT, "
        "severity TEXT, jira_key TEXT)"
    ),
    "orders": (
        "CREATE TABLE orders ("
        "id INTEGER PRIMARY KEY, created_at TEXT, status TEXT, "
        "amount_cents INTEGER)"
    ),
}

DEFAULT_SEED = {
    "deploys": [
        (31, "checkout-api", "2.4.1", "2026-06-28T14:12", "c9f2e41", "dan@raiko.dev"),
        (30, "checkout-api", "2.3.9", "2026-06-20T10:03", "b4e9c77", "alice@raiko.dev"),
        (32, "payments-api", "1.9.2", "2026-06-29T09:41", "77aa210", "carol@raiko.dev"),
        # Older filler rows for other services (not payments-api 3.0.0).
        (27, "search-api", "3.0.5", "2026-06-10T09:00", "1a2b3c4", "erin@raiko.dev"),
        (28, "billing-api", "1.2.0", "2026-06-12T11:15", "5d6e7f8", "frank@raiko.dev"),
        (29, "notifications-api", "0.9.1", "2026-06-15T08:45", "9c8b7a6", "grace@raiko.dev"),
        # Round-2 hardening: two more decoy rows, neither for checkout-api, so
        # "latest checkout-api deploy" (id 31, c9f2e41) is unaffected.
        (33, "orders-api", "5.2.1", "2026-06-27T11:00", "9e2f4c1", "erin@raiko.dev"),
        (34, "search-api", "3.1.0", "2026-06-26T16:20", "f7d3b19", "erin@raiko.dev"),
    ],
    "incidents": [
        (7, "checkout-api", "2026-06-28T14:30", "sev1", "OPS-812"),
        (6, "search-api", "2026-05-11T08:00", "sev3", "OPS-640"),
    ],
    "orders": _generate_orders(),
}

_READ_ONLY_ERROR = "ERROR: read-only: only SELECT is allowed"


def _leading_keyword(sql):
    """Return the first SQL keyword after stripping whitespace/comments."""
    s = sql.strip()
    while True:
        if s.startswith("--"):
            nl = s.find("\n")
            s = s[nl + 1:] if nl != -1 else ""
        elif s.startswith("/*"):
            end = s.find("*/")
            s = s[end + 2:] if end != -1 else ""
        else:
            break
        s = s.lstrip()
    m = re.match(r"[A-Za-z]+", s)
    return m.group(0).upper() if m else ""


def _format_result(columns, rows):
    lines = [" | ".join(columns)]
    shown = rows[:MAX_RESULT_ROWS]
    for r in shown:
        lines.append(" | ".join("" if v is None else str(v) for v in r))
    if len(rows) > MAX_RESULT_ROWS:
        lines.append(f"... ({len(rows) - MAX_RESULT_ROWS} more rows)")
    return "\n".join(lines)


class MockSQL:
    """Deterministic, in-process, read-only sqlite database.

    Rebuilt fresh per instance from `seed_rows` (defaults to DEFAULT_SEED,
    matching the Seed Reference exactly). Every method returns a plain
    string; nothing ever raises out of `query`.
    """

    def __init__(self, seed_rows=None):
        seed = seed_rows or DEFAULT_SEED
        self._conn = sqlite3.connect(":memory:")
        cur = self._conn.cursor()
        for ddl in SCHEMA.values():
            cur.execute(ddl)
        cur.executemany(
            "INSERT INTO deploys VALUES (?,?,?,?,?,?)", seed["deploys"]
        )
        cur.executemany(
            "INSERT INTO incidents VALUES (?,?,?,?,?)", seed["incidents"]
        )
        cur.executemany(
            "INSERT INTO orders VALUES (?,?,?,?)", seed["orders"]
        )
        self._conn.commit()
        # Defense in depth: even if the keyword check were ever bypassed,
        # the connection itself refuses to write.
        self._conn.execute("PRAGMA query_only = ON")

    def tables(self):
        """Return a compact schema listing for all tables."""
        lines = ["Tables:"]
        lines.extend(SCHEMA.values())
        return "\n".join(lines)

    def query(self, sql):
        """Run a read-only SQL query and return a formatted result table.

        Non-SELECT statements are rejected before touching sqlite. Any
        sqlite-level failure (bad syntax, unknown table/column, an
        attempted multi-statement chain, etc.) is caught and returned as
        an "ERROR: ..." string instead of raising.
        """
        if _leading_keyword(sql) != "SELECT":
            return _READ_ONLY_ERROR
        try:
            cur = self._conn.execute(sql)
            rows = cur.fetchall()
            columns = [d[0] for d in cur.description] if cur.description else []
            return _format_result(columns, rows)
        except Exception as e:
            return f"ERROR: {e}"
