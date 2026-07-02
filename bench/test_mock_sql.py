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
