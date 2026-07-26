import json
import os

from engine import store


def _save(tmp_path, sid, cwd=None, updated="2026-01-01T00:00:00"):
    sess = {"id": sid, "provider": "openai", "model": "gpt-test",
            "updated": updated, "messages": [{"role": "user", "content": sid}]}
    if cwd is not None:
        sess["cwd"] = str(cwd)
    assert store.write_session(sess)
    return sess


def test_session_ids_reject_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "SESSIONS_DIR", str(tmp_path))
    outside = tmp_path.parent / "outside.json"
    outside.write_text('{"id":"outside"}', encoding="utf-8")
    assert store.load_session("../outside") is None
    assert not store.delete_session("../outside")
    assert not store.write_session({"id": "../outside", "messages": []})
    assert outside.exists()


def test_write_session_is_atomic_and_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "SESSIONS_DIR", str(tmp_path))
    session = {
        "id": "safe-session_01",
        "provider": "openai",
        "model": "gpt-test",
        "messages": [{"role": "user", "content": "hola"}],
    }
    assert store.write_session(session)
    assert store.load_session(session["id"]) == session
    assert list(tmp_path.glob("*.tmp")) == []
    assert json.loads((tmp_path / "safe-session_01.json").read_text()) == session


# ---------- per-folder sessions ----------

def test_list_and_last_session_are_scoped_to_their_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "SESSIONS_DIR", str(tmp_path / "sessions"))
    alpha, beta = tmp_path / "alpha", tmp_path / "beta"
    alpha.mkdir(), beta.mkdir()
    _save(tmp_path, "alpha-old", alpha, updated="2026-01-01T10:00:00")
    _save(tmp_path, "alpha-new", alpha, updated="2026-01-02T10:00:00")
    _save(tmp_path, "beta-one", beta, updated="2026-01-03T10:00:00")

    assert [s["id"] for s in store.list_sessions(alpha)] == ["alpha-new", "alpha-old"]
    assert [s["id"] for s in store.list_sessions(beta)] == ["beta-one"]
    assert store.last_session(alpha)["id"] == "alpha-new"     # not beta's newer one
    assert store.last_session(beta)["id"] == "beta-one"
    assert len(store.list_sessions()) == 3                    # unfiltered = everything
    assert store.list_sessions(tmp_path / "gamma") == []


def test_cwd_matching_ignores_case_and_dot_segments(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "SESSIONS_DIR", str(tmp_path / "sessions"))
    project = tmp_path / "Project"
    (project / "sub").mkdir(parents=True)
    _save(tmp_path, "in-project", project)

    detour = project / "sub" / ".." / "."
    assert store.last_session(detour)["id"] == "in-project"
    if os.name == "nt":     # Windows paths differ only in case all the time
        assert store.last_session(str(project).upper())["id"] == "in-project"


def test_sessions_saved_before_cwd_existed_still_load(tmp_path, monkeypatch):
    """Soft migration: no cwd key = 'global'. Listed and resumable as always,
    just never matched by a folder filter."""
    monkeypatch.setattr(store, "SESSIONS_DIR", str(tmp_path / "sessions"))
    legacy = _save(tmp_path, "legacy-session")            # written with no cwd
    _save(tmp_path, "modern-session", tmp_path / "here")
    (tmp_path / "here").mkdir()

    assert store.load_session("legacy-session") == legacy
    assert "cwd" not in legacy
    assert store.session_cwd(legacy) == ""
    assert "legacy-session" in [s["id"] for s in store.list_sessions()]
    assert [s["id"] for s in store.list_sessions(tmp_path / "here")] == ["modern-session"]
