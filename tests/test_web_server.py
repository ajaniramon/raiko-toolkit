"""raiko web contract tests (in-process ASGI): auth, session API, WS turn flow,
permission round-trip over the socket, exec gating and web write-confirmation."""

import json
import os

import pytest
from starlette.testclient import TestClient

from conftest import fake_client, text_delta, tool_delta, usage_chunk

import web.server as srv
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


def test_create_and_ws_turn(client):
    script = [[text_delta("hola "), text_delta("panel"), usage_chunk(20, 4)]]
    sid, info = make_ws_session(client, script)
    assert info["protocol_version"] and info["exec_enabled"] is False
    with client.websocket_connect(f"/ws/{sid}?token=secreto") as ws:
        assert ws.receive_json()["type"] == "session_started"
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
    with client.websocket_connect(f"/ws/{sid}?token=secreto") as ws:
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
    with client.websocket_connect(f"/ws/{sid}?token=secreto") as ws:
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
    with client.websocket_connect(f"/ws/{sid}?token=secreto") as ws:
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
    with client.websocket_connect(f"/ws/{sid}?token=secreto") as ws:
        ws.receive_json()
        ws.send_json({"type": "send", "text": "escribe dentro"})
        done, perms, results, _ = drive_turn(ws, decision="deny")
        assert perms and perms[0]["tool"] == "write_file"
        assert not results[0]["ok"] and "DENIED" in results[0]["result"]
        assert not os.path.exists(inside)
