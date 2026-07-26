import json

from engine import store


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
