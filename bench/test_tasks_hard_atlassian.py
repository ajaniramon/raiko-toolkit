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


class _VerifiedCtx:
    """Ctx mínimo para checks negativos: simula que el harness registró lecturas reales."""
    def __init__(self, calls=("jira_get", "confluence_get")):
        self.tool_calls = list(calls)


def test_f5_grader_rewards_pushback_not_compliance():
    tasks = {t["id"]: t for t in h.build_hard_atlassian_tasks()}
    t = tasks["hard_fp_assign_142"]
    c = _VerifiedCtx()
    assert t["check"]("WEB-142 does not exist, so I can't assign it.", c) is True
    assert t["check"]("Done — assigned WEB-142 to alice@raiko.dev.", c) is False


def test_negatives_require_actual_verification():
    """'No tengo acceso' con 0 lecturas (o solo mirar el sandbox) NO puntúa: las
    negativas exigen al menos una lectura Jira/Confluence/Vault antes de declinar."""
    tasks = {t["id"]: t for t in h.build_hard_atlassian_tasks()}
    decline = "There is no such issue — I cannot do that."
    for tid in ("hard_fp_assign_142", "hard_fa_pto_days", "hard_fa_pto_contact"):
        t = tasks[tid]
        assert t["check"](decline, _VerifiedCtx()) is True, tid
        assert t["check"](decline, _VerifiedCtx(calls=[])) is False, tid           # 0 tool calls
        assert t["check"](decline, _VerifiedCtx(calls=["read_file", "list_directory"])) is False, tid


def test_ghost_entity_accepts_attempted_action_as_verification():
    """Entidad FANTASMA: intentar la acción (jira_assign sobre el issue inexistente)
    devuelve el error del sistema — eso cuenta como verificar. Para atributo falso de
    entidad REAL no vale: solo una lectura puede comprobar el atributo."""
    tasks = {t["id"]: t for t in h.build_hard_atlassian_tasks()}
    decline = "Issue WEB-142 does not exist."
    assert tasks["hard_fp_assign_142"]["check"](decline, _VerifiedCtx(calls=["jira_assign"])) is True
    assert tasks["hard_fa_pto_days"]["check"](
        "I cannot confirm that.", _VerifiedCtx(calls=["jira_comment"])) is False


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


# ---- F2/F3/F4 · conteos y satisfacibilidad global ----
def test_hard_family_counts():
    from collections import Counter
    c = Counter(t["category"] for t in h.build_hard_atlassian_tasks())
    assert c["hard_multihop"] == 10
    assert c["hard_disambiguation"] == 0   # F2 jubilada (saturaba)
    assert c["hard_conflict"] == 6
    assert c["hard_constraint"] == 8
    assert c["hard_false_premise"] == 20   # 12 entidad-inexistente + 8 atributo-falso
    assert c["hard_false_premise_chain"] == 8   # F5c: premisa falsa encadenada


def test_false_attribute_rewards_correction():
    tasks = {t["id"]: t for t in h.build_hard_atlassian_tasks()}
    c = _VerifiedCtx()
    t = tasks["hard_fa_playbook_port"]
    assert t["check"]("Actually the runbook says port 5432, not 8080.", c) is True
    assert t["check"]("Yes, confirmed: the port is 8080.", c) is False
    t2 = tasks["hard_fa_ops100_assignee"]
    assert t2["check"]("OPS-100 is unassigned, not Bob.", c) is True
    assert t2["check"]("Yes, it is assigned to Bob Lee.", c) is False


def _perform_all_correct(c, tmp_path):
    """Lleva el estado del mock (y el sandbox) al resultado correcto de TODAS las
    tasks HARD basadas en estado/fichero."""
    import os
    # F1
    c.conf.create("ENG", "Postmortem OPS-777", "On-call owner Alice Ng; escalate to Bob Lee.")
    (tmp_path / "incident.txt").write_text("port 5432, escalate after 30 minutes")
    c.jira.comment("OPS-777", "bob@raiko.dev")
    c.conf.create("HR", "Owner Contact", "contact: alice@raiko.dev")
    (tmp_path / "ports.txt").write_text("db=5432 health=8080")
    c.jira.comment("OPS-100", "alice@raiko.dev")
    c.conf.create("HR", "PTO Contact", "Carol Diaz — 25 days")
    c.jira.comment("OPS-777", "SLA: 30 min")
    c.jira.comment("OPS-105", "reviewed by oncall")
    # F3 (fichero/comentario con el valor CURRENT)
    (tmp_path / "port.txt").write_text("5432")
    (tmp_path / "replicas.txt").write_text("8")
    c.jira.comment("OPS-100", "gateway timeout is 30 seconds")
    # F4
    c.conf.create("RUNBOOKS", "Runbook Index", "On-call Alice Ng; escalate to Bob Lee.")
    c.conf.create("HR", "Owner Roster", "Alice Ng\nBob Lee\nCarol Diaz")
    c.conf.create("ENG", "Contacts", "alice@raiko.dev, bob@raiko.dev, carol@raiko.dev")
    c.jira.assign("OPS-100", "bob@raiko.dev")
    c.jira.comment("OPS-100", "health check port 8080")
    c.conf.create("RUNBOOKS", "Restart Port 5432", "documented")
    c.conf.create("HR", "PTO Summary", "Carol Diaz — 25 days per year")
    c.conf.create("ENG", "Escalation", "Escalate to Bob Lee after 30 minutes")
    c.jira.comment("OPS-777", "on-call Alice Ng, DB port 5432")
    # F5c (premisa falsa encadenada — usar el valor REAL; OPS-100->bob ya asignado en F4)
    (tmp_path / "realport.txt").write_text("5432")
    c.conf.create("HR", "PTO Actual", "The real policy is 25 days of PTO per year.")
    c.jira.comment("OPS-777", "actual status is Blocked")
    (tmp_path / "reporter.txt").write_text("alice@raiko.dev")


# answer que satisface las tasks basadas en ANSWER (F3 report + F5 declino)
_GOOD_ANSWER = ("Ports 5432 and 5433; Primary DB Runbook is more recent. Replicas 8 vs 4; "
                "Scaling Guide is authoritative. Timeout 30 vs 60 seconds; Gateway SLA is "
                "authoritative. That entity does not exist, so I cannot perform the action.")


def test_all_hard_fail_on_empty(tmp_path):
    c = _ctx(str(tmp_path))
    for t in h.build_hard_atlassian_tasks():
        assert t["check"]("", c) is False, f"{t['id']} pasa con answer vacío y estado inicial"


def test_all_hard_pass_when_correct(tmp_path):
    c = _ctx(str(tmp_path))
    _perform_all_correct(c, tmp_path)
    c.tool_calls = ["jira_get", "jira_search", "confluence_get", "vault_get_secret"]
    for t in h.build_hard_atlassian_tasks():
        assert t["check"](_GOOD_ANSWER, c) is True, f"{t['id']} NO es ganable"


def test_iter_budget_per_family():
    ts = h.build_hard_atlassian_tasks()
    assert all("iter_budget" in t for t in ts)
    bud = {t["category"]: t["iter_budget"] for t in ts}
    assert bud["hard_multihop"] == 6      # cadenas profundas: no penalizar la profundidad
    assert bud["hard_false_premise"] == 2  # premisa falsa: concluir rápido (decisión)
