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
