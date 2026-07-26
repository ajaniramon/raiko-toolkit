"""raiko web contract tests (in-process ASGI): auth, session API, WS turn flow,
permission round-trip over the socket, exec gating and web write-confirmation."""

import json
import os
import time
import uuid

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from conftest import fake_client, text_delta, tool_delta, usage_chunk

import web.server as srv
from engine import store
from context import ContextTracker

AUTH = {"Authorization": "Bearer secreto"}


@pytest.fixture
def client(cfg):
    cfg["web"]["token"] = "secreto"
    return TestClient(srv.build_app(cfg))


def make_ws_session(client, script, provider="openai", model="gpt-test"):
    r = client.post("/api/sessions", headers=AUTH,
                    json={"provider": provider, "model": model})
    assert r.status_code == 201, r.text
    sid = r.json()["session_id"]
    live = srv.STATE.sessions[sid].session
    live.client = fake_client(script)
    live.tracker = ContextTracker(model)
    return sid, r.json()


def drive_turn(ws, decision="allow_once"):
    """Send nothing; read events until turn_done. Answers permissions with `decision`."""
    perms, results, texts = [], [], []
    while True:
        e = ws.receive_json()
        if e["type"] == "telemetry":
            continue
        if e["type"] == "text_delta":
            texts.append(e["text"])
        if e["type"] == "permission_required":
            perms.append(e)
            ws.send_json({"type": "permission_response",
                          "perm_id": e["perm_id"], "decision": decision})
        if e["type"] == "tool_call_result":
            results.append(e)
        if e["type"] == "turn_done":
            return e, perms, results, "".join(texts)


def test_auth_required(client):
    assert client.get("/api/sessions").status_code == 401
    assert client.get("/api/sessions", headers=AUTH).status_code == 200


def test_ws_rejects_query_string_token(client):
    sid, _ = make_ws_session(client, [])
    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect(f"/ws/{sid}?token=secreto") as ws:
            ws.receive_json()
    assert caught.value.code == 4401


def test_create_and_ws_turn(client):
    script = [[text_delta("hola "), text_delta("panel"), usage_chunk(20, 4)]]
    sid, info = make_ws_session(client, script)
    assert info["protocol_version"] and info["exec_enabled"] is False
    with client.websocket_connect(f"/ws/{sid}", headers=AUTH) as ws:
        started = ws.receive_json()
        assert started["type"] == "session_started"
        assert started["protocol_version"] == "1.1"
        snapshot = ws.receive_json()
        assert snapshot["type"] == "session_snapshot"
        assert snapshot["session_id"] == sid and snapshot["messages"][0]["role"] == "system"
        ws.send_json({"type": "send", "text": "saluda"})
        done, perms, results, texts = drive_turn(ws)
        assert texts == "hola panel" and done["reason"] == "completed"


def test_permission_roundtrip_pauses_turn(client, cfg):
    outside = os.path.join(os.path.dirname(cfg["permissions"]["workspace"]), "w.txt")
    script = [
        [tool_delta(0, "c1", "write_file",
                    json.dumps({"path": outside, "content": "web\n"})),
         usage_chunk(30, 6)],
        [text_delta("escrito"), usage_chunk(40, 3)],
    ]
    sid, _ = make_ws_session(client, script)
    with client.websocket_connect(f"/ws/{sid}", headers=AUTH) as ws:
        ws.receive_json()
        ws.send_json({"type": "send", "text": "escribe"})
        done, perms, results, _ = drive_turn(ws)
        assert perms and perms[0]["tool"] == "write_file"
        assert results[0]["ok"] and results[0]["diff"]
        assert open(outside).read() == "web\n"


def test_exec_blocked_by_default(client):
    script = [
        [tool_delta(0, "c1", "run_python", json.dumps({"code": "print(1)"})),
         usage_chunk(10, 5)],
        [text_delta("no pude"), usage_chunk(12, 3)],
    ]
    sid, _ = make_ws_session(client, script)
    with client.websocket_connect(f"/ws/{sid}", headers=AUTH) as ws:
        ws.receive_json()
        ws.send_json({"type": "send", "text": "ejecuta"})
        done, perms, results, _ = drive_turn(ws)
        assert not results[0]["ok"]
        assert "disabled on this interface" in results[0]["result"]


def test_exec_confirms_every_call_when_enabled(cfg):
    cfg["web"]["token"] = "secreto"
    cfg["web"]["allow_exec"] = True
    cfg["permissions"]["allow"] = ["run_python"]     # allowlist must NOT bypass web confirm
    client = TestClient(srv.build_app(cfg))
    script = [
        [tool_delta(0, "c1", "run_python", json.dumps({"code": "print(2+2)"})),
         usage_chunk(10, 5)],
        [text_delta("4"), usage_chunk(12, 2)],
    ]
    sid, info = make_ws_session(client, script)
    assert info["exec_enabled"] is True
    with client.websocket_connect(f"/ws/{sid}", headers=AUTH) as ws:
        ws.receive_json()
        ws.send_json({"type": "send", "text": "2+2"})
        done, perms, results, _ = drive_turn(ws, decision="allow_once")
        assert perms and perms[0]["tool"] == "run_python"
        assert results[0]["ok"] and "4" in results[0]["result"]


def test_deny_over_web_blocks_allowlisted_write(client, cfg):
    inside = os.path.join(cfg["permissions"]["workspace"], "in.txt")
    cfg["permissions"]["allow"] = ["write_file"]
    script = [
        [tool_delta(0, "c1", "write_file",
                    json.dumps({"path": inside, "content": "x\n"})),
         usage_chunk(10, 5)],
        [text_delta("ok"), usage_chunk(12, 2)],
    ]
    sid, _ = make_ws_session(client, script)
    with client.websocket_connect(f"/ws/{sid}", headers=AUTH) as ws:
        ws.receive_json()
        ws.send_json({"type": "send", "text": "escribe dentro"})
        done, perms, results, _ = drive_turn(ws, decision="deny")
        assert perms and perms[0]["tool"] == "write_file"
        assert perms[0]["allowed_decisions"] == ["allow_once", "deny"]
        assert not results[0]["ok"] and "DENIED" in results[0]["result"]
        assert not os.path.exists(inside)


def test_health_and_capabilities_do_not_leak_secrets(client, cfg):
    cfg["openai"]["api_key"] = "never-return-this"
    health = client.get("/api/health", headers=AUTH)
    assert health.status_code == 200
    assert health.json()["protocol_version"] == "1.1"
    capabilities = client.get("/api/capabilities", headers=AUTH)
    assert capabilities.status_code == 200
    raw = capabilities.text
    assert "never-return-this" not in raw
    assert "base_url" not in raw
    assert any(item["id"] == "openai" for item in capabilities.json()["providers"])


def test_resume_snapshot_and_session_detail(client):
    session_id = f"test-{uuid.uuid4().hex}"
    saved = {
        "id": session_id,
        "title": "Recovered",
        "updated": "2026-07-26T00:00:00",
        "provider": "openai",
        "model": "gpt-test",
        "ctx_window": 4096,
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "before refresh"},
            {"role": "assistant", "content": "still here"},
        ],
    }
    assert store.write_session(saved)
    try:
        detail = client.get(f"/api/sessions/{session_id}", headers=AUTH)
        assert detail.status_code == 200
        assert detail.json()["messages"][-1]["content"] == "still here"
        created = client.post(
            "/api/sessions",
            headers=AUTH,
            json={"resume": session_id},
        )
        assert created.status_code == 201
        sid = created.json()["session_id"]
        with client.websocket_connect(f"/ws/{sid}", headers=AUTH) as ws:
            ws.receive_json()
            snapshot = ws.receive_json()
            assert snapshot["type"] == "session_snapshot"
            assert snapshot["engine_session_id"] == session_id
            assert snapshot["messages"][-2]["content"] == "before refresh"
    finally:
        store.delete_session(session_id)


def test_resume_rejects_path_traversal(client):
    response = client.post(
        "/api/sessions",
        headers=AUTH,
        json={"resume": "../outside"},
    )
    assert response.status_code == 400


def test_only_one_socket_owns_a_live_session(client):
    sid, _ = make_ws_session(client, [[]])
    with client.websocket_connect(f"/ws/{sid}", headers=AUTH) as first:
        first.receive_json()
        first.receive_json()
        with pytest.raises(WebSocketDisconnect) as caught:
            with client.websocket_connect(f"/ws/{sid}", headers=AUTH):
                pass
        assert caught.value.code == 4409


def test_disconnect_denies_pending_permission_immediately(client, cfg):
    outside = os.path.join(os.path.dirname(cfg["permissions"]["workspace"]), "disconnect.txt")
    script = [
        [
            tool_delta(
                0,
                "c1",
                "write_file",
                json.dumps({"path": outside, "content": "must-not-exist\n"}),
            ),
            usage_chunk(10, 2),
        ],
        [text_delta("denied"), usage_chunk(12, 2)],
    ]
    sid, _ = make_ws_session(client, script)
    with client.websocket_connect(f"/ws/{sid}", headers=AUTH) as ws:
        ws.receive_json()
        ws.receive_json()
        ws.send_json({"type": "send", "text": "write"})
        while True:
            event = ws.receive_json()
            if event["type"] == "permission_required":
                break
    live = srv.STATE.sessions[sid]
    deadline = time.monotonic() + 1
    while live.busy and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not live.busy
    assert not os.path.exists(outside)


def test_swap_model_emits_no_second_session_started(client):
    sid, _ = make_ws_session(client, [[]])
    with client.websocket_connect(f"/ws/{sid}", headers=AUTH) as ws:
        ws.receive_json()
        ws.receive_json()
        ws.send_json({"type": "swap_model", "provider": "openai", "model": "gpt-next"})
        seen = []
        while "session_snapshot" not in seen:
            event = ws.receive_json()
            if event["type"] != "telemetry":
                seen.append(event["type"])
        assert seen == ["model_swapped", "session_snapshot"]


def test_live_session_limit_and_delete(cfg):
    cfg["web"]["token"] = "secreto"
    cfg["web"]["max_live_sessions"] = 1
    client = TestClient(srv.build_app(cfg))
    first = client.post(
        "/api/sessions",
        headers=AUTH,
        json={"provider": "openai", "model": "gpt-test"},
    )
    assert first.status_code == 201
    second = client.post(
        "/api/sessions",
        headers=AUTH,
        json={"provider": "openai", "model": "gpt-test"},
    )
    assert second.status_code == 429
    deleted = client.delete(f"/api/sessions/{first.json()['session_id']}", headers=AUTH)
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "kind": "live"}


def test_mcp_tools_are_loaded_when_configured(cfg, monkeypatch):
    cfg["web"]["token"] = "secreto"
    cfg["mcp"] = {
        "enabled": True,
        "servers": [{"name": "mock", "url": "http://mcp.invalid", "prefix": "x_"}],
    }

    def fake_load(self):
        self.mcp_tools = [{"type": "function", "function": {"name": "x_demo"}}]
        return [("mock", 1, "x_", "http://mcp.invalid")]

    monkeypatch.setattr("engine.session.Session.load_mcp_tools", fake_load)
    client = TestClient(srv.build_app(cfg))
    response = client.post(
        "/api/sessions",
        headers=AUTH,
        json={"provider": "openai", "model": "gpt-test"},
    )
    assert response.status_code == 201
    assert response.json()["mcp_tools"] == 1
    assert response.json()["mcp_servers"] == 1
