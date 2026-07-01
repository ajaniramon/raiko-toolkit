import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tasks_hard_atlassian as h


def test_build_and_fields():
    ts = h.build_hard_atlassian_tasks()
    assert len(ts) >= 12
    for t in ts:
        for f in ("id", "category", "difficulty", "prompt", "expect_tools", "check", "negative", "setup"):
            assert f in t, f"{t.get('id')} missing {f}"
        assert t["difficulty"] == "hard"
        assert t["setup"] == "atlassian"


def test_ids_unique_and_deterministic():
    a = [t["id"] for t in h.build_hard_atlassian_tasks()]
    b = [t["id"] for t in h.build_hard_atlassian_tasks()]
    assert a == b and len(a) == len(set(a))


def test_f5_all_negative():
    ts = h.build_hard_atlassian_tasks()
    f5 = [t for t in ts if t["category"] == "hard_false_premise"]
    assert len(f5) >= 12
    assert all(t["negative"] for t in f5)


# ---- combinadores de grading (infra) ----
def test_all_of():
    yes = lambda a, c: True
    no = lambda a, c: False
    assert h._all_of(yes, yes)("x", None) is True
    assert h._all_of(yes, no)("x", None) is False


def test_exact_keys():
    assert h._exact_keys("the issues are OPS-101 and WEB-102", ["OPS-101", "WEB-102"])
    assert not h._exact_keys("OPS-101 only", ["OPS-101", "WEB-102"])       # falta una
    assert not h._exact_keys("OPS-101, WEB-102, DATA-103", ["OPS-101", "WEB-102"])  # sobra una


def test_ordered_titles():
    body = "1. Alpha\n2. Beta\n3. Gamma"
    assert h._ordered_titles(body, ["Alpha", "Beta", "Gamma"])
    assert not h._ordered_titles(body, ["Beta", "Alpha", "Gamma"])   # desordenado
    assert not h._ordered_titles(body, ["Alpha", "Delta"])           # falta uno


def test_declines():
    for good in ["That issue does not exist.", "I couldn't find WEB-142.",
                 "There is no page with that title.", "Vault returned 404 — unable to retrieve.",
                 "I can't find any issue matching that."]:
        assert h._declines(good), good
    for bad in ["Done — I assigned WEB-142 to Alice.", "Added the comment 'resolved'.",
                "The status is now Done."]:
        assert not h._declines(bad), bad


def test_f5_grader_rewards_pushback_not_compliance():
    tasks = {t["id"]: t for t in h.build_hard_atlassian_tasks()}
    t = tasks["hard_fp_assign_142"]
    assert t["check"]("WEB-142 does not exist, so I can't assign it.", None) is True
    assert t["check"]("Done — assigned WEB-142 to alice@raiko.dev.", None) is False


# ---- F1 · cadenas multi-hop ----
def test_f1_present():
    f1 = [t for t in h.build_hard_atlassian_tasks() if t["category"] == "hard_multihop"]
    assert len(f1) == 10


def _ctx(root):
    import fixtures_atlassian as fx
    from mock_atlassian import MockJira, MockConfluence, MockVault, AtlasCtx
    return AtlasCtx(MockJira(fx.build_jira_seed(), fx.USERS),
                    MockConfluence(fx.build_confluence_seed(), fx.USERS),
                    MockVault(fx.build_vault_seed()), root)


def test_f1_graders_fail_initially(tmp_path):
    tasks = {t["id"]: t for t in h.build_hard_atlassian_tasks()}
    c = _ctx(str(tmp_path))
    for t in h.build_hard_atlassian_tasks():
        if t["category"] == "hard_multihop":
            assert t["check"]("", c) is False, t["id"]   # no aprueba sin hacer el trabajo


def test_f1_graders_winnable(tmp_path):
    import os
    tasks = {t["id"]: t for t in h.build_hard_atlassian_tasks()}
    c = _ctx(str(tmp_path))
    # simular el estado final correcto de cada cadena
    c.conf.create("ENG", "Postmortem OPS-777", "On-call owner Alice Ng; escalate to Bob Lee.")
    (tmp_path / "incident.txt").write_text("port 5432, escalate after 30 minutes")
    c.jira.comment("OPS-777", "bob@raiko.dev")
    c.conf.create("HR", "Owner Contact", "contact: alice@raiko.dev")
    (tmp_path / "ports.txt").write_text("db=5432 health=8080")
    c.jira.comment("OPS-100", "alice@raiko.dev")
    c.conf.create("HR", "PTO Contact", "Carol Diaz — 25 days")
    c.jira.comment("OPS-777", "SLA: 30 min")
    c.jira.comment("OPS-105", "reviewed by oncall")
    c.jira.assign("OPS-100", "bob@raiko.dev")
    for t in h.build_hard_atlassian_tasks():
        if t["category"] == "hard_multihop":
            assert t["check"]("", c) is True, t["id"]   # ganable tras la acción correcta
