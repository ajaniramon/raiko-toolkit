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
from engine.config import _app_home
from context import ContextTracker

AUTH = {"Authorization": "Bearer secreto"}


def _write_skill(root, dirname, frontmatter_lines, body="Do the thing.\n"):
    """Create <root>/<dirname>/SKILL.md, mirroring tests/test_skills.py's helper."""
    d = os.path.join(root, dirname)
    os.makedirs(d, exist_ok=True)
    fm = "\n".join(frontmatter_lines)
    content = f"---\n{fm}\n---\n{body}"
    with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(content)
    return d


@pytest.fixture
def client(cfg):
    cfg["web"]["token"] = "secreto"
    return TestClient(srv.build_app(cfg))


def make_ws_session(
    client,
    script,
    provider="openai",
    model="gpt-test",
    permission_mode="ask",
    max_iterations=None,
):
    body = {
        "provider": provider,
        "model": model,
        "permission_mode": permission_mode,
    }
    if max_iterations is not None:
        body["max_iterations"] = max_iterations
    r = client.post("/api/sessions", headers=AUTH, json=body)
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
    assert info["permission_mode"] == "ask"
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


def test_exec_stays_blocked_in_yolo_when_server_policy_disables_it(client):
    script = [
        [tool_delta(0, "c1", "run_python", json.dumps({"code": "print(1)"})),
         usage_chunk(10, 5)],
        [text_delta("no pude"), usage_chunk(12, 3)],
    ]
    sid, info = make_ws_session(client, script, permission_mode="yolo")
    assert info["permission_mode"] == "yolo"
    with client.websocket_connect(f"/ws/{sid}", headers=AUTH) as ws:
        ws.receive_json()
        ws.send_json({"type": "send", "text": "ejecuta"})
        _, perms, results, _ = drive_turn(ws)
        assert perms == []
        assert not results[0]["ok"]
        assert "disabled on this interface" in results[0]["result"]


def test_exec_policy_can_be_enabled_from_environment(monkeypatch):
    monkeypatch.setenv("RAIKO_WEB_ALLOW_EXEC", "1")
    assert srv._env_flag("RAIKO_WEB_ALLOW_EXEC", False) is True
    monkeypatch.setenv("RAIKO_WEB_ALLOW_EXEC", "off")
    assert srv._env_flag("RAIKO_WEB_ALLOW_EXEC", True) is False
    monkeypatch.setenv("RAIKO_WEB_ALLOW_EXEC", "invalid")
    assert srv._env_flag("RAIKO_WEB_ALLOW_EXEC", False) is False


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


def test_yolo_exec_runs_without_permission_event_when_enabled(cfg):
    cfg["web"]["token"] = "secreto"
    cfg["web"]["allow_exec"] = True
    client = TestClient(srv.build_app(cfg))
    script = [
        [tool_delta(0, "c1", "run_python", json.dumps({"code": "print(6*7)"})),
         usage_chunk(10, 5)],
        [text_delta("42"), usage_chunk(12, 2)],
    ]
    sid, info = make_ws_session(client, script, permission_mode="yolo")
    assert info["permission_mode"] == "yolo"
    with client.websocket_connect(f"/ws/{sid}", headers=AUTH) as ws:
        ws.receive_json()
        ws.send_json({"type": "send", "text": "6*7"})
        _, perms, results, _ = drive_turn(ws)
        assert perms == []
        assert results[0]["ok"] and "42" in results[0]["result"]


def test_yolo_does_not_override_explicit_denylist(cfg):
    cfg["web"]["token"] = "secreto"
    cfg["web"]["allow_exec"] = True
    cfg["permissions"]["deny"] = ["run_python"]
    client = TestClient(srv.build_app(cfg))
    script = [
        [tool_delta(0, "c1", "run_python", json.dumps({"code": "print(42)"})),
         usage_chunk(10, 5)],
        [text_delta("denied"), usage_chunk(12, 2)],
    ]
    sid, _ = make_ws_session(client, script, permission_mode="yolo")
    with client.websocket_connect(f"/ws/{sid}", headers=AUTH) as ws:
        ws.receive_json()
        ws.send_json({"type": "send", "text": "run"})
        _, perms, results, _ = drive_turn(ws)
        assert perms == []
        assert not results[0]["ok"] and "DENIED" in results[0]["result"]


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
    payload = capabilities.json()
    assert any(item["id"] == "openai" for item in payload["providers"])
    assert payload["permission_modes"] == ["ask", "yolo"]
    assert payload["default_permission_mode"] == "ask"


def test_create_session_rejects_unknown_permission_mode(client):
    response = client.post(
        "/api/sessions",
        headers=AUTH,
        json={
            "provider": "openai",
            "model": "gpt-test",
            "permission_mode": "maybe",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "permission_mode must be 'ask' or 'yolo'"


def test_model_catalog_requires_auth_and_configured_provider(cfg, monkeypatch):
    cfg["web"]["token"] = "secreto"
    cfg["nano"]["api_key"] = "configured-secret"
    client = TestClient(srv.build_app(cfg))
    assert client.get("/api/providers/nano/models").status_code == 401

    async def catalog_payload(provider):
        return {
            "provider": provider,
            "models": [{"id": "nano/model", "input_price": 0.1, "output_price": 0.5}],
            "count": 1,
            "cache": "hit",
        }

    monkeypatch.setattr(srv.STATE.model_catalog, "get", catalog_payload)
    response = client.get("/api/providers/nano/models", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["models"][0]["input_price"] == 0.1

    response = client.get("/api/providers/openai/models", headers=AUTH)
    assert response.status_code == 409
    assert response.json()["error"] == "provider is not configured"


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


def test_list_skills_returns_discovered(client):
    skills_dir = os.path.join(_app_home(), "skills")
    _write_skill(skills_dir, "webpanel-demo",
                 ['name: webpanel-demo', 'description: "Demo skill for the web API."'])
    response = client.get("/api/skills", headers=AUTH)
    assert response.status_code == 200
    found = {s["name"]: s for s in response.json()["skills"]}
    assert "webpanel-demo" in found
    entry = found["webpanel-demo"]
    assert entry["description"] == "Demo skill for the web API."
    assert entry["source"] == "raiko"
    assert entry["path"].endswith("SKILL.md")


def test_list_skills_requires_auth(client):
    assert client.get("/api/skills").status_code == 401


def test_skill_detail_returns_full_markdown(client):
    skills_dir = os.path.join(_app_home(), "skills")
    _write_skill(skills_dir, "webpanel-detail",
                 ['name: webpanel-detail', 'description: "Detail skill."'],
                 body="Step 1. Do the thing.\nStep 2. Done.\n")
    response = client.get("/api/skills/webpanel-detail", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "webpanel-detail"
    assert body["description"] == "Detail skill."
    assert body["source"] == "raiko"
    assert body["path"].endswith("SKILL.md")
    assert body["content"].startswith("---\n")
    assert "name: webpanel-detail" in body["content"]
    assert "Step 1. Do the thing." in body["content"]


def test_skill_detail_requires_auth(client):
    skills_dir = os.path.join(_app_home(), "skills")
    _write_skill(skills_dir, "webpanel-auth",
                 ['name: webpanel-auth', 'description: "Auth skill."'])
    assert client.get("/api/skills/webpanel-auth").status_code == 401


def test_skill_detail_unknown_name_returns_404(client):
    response = client.get("/api/skills/does-not-exist", headers=AUTH)
    assert response.status_code == 404
    assert response.json() == {"error": "unknown skill"}


def test_capabilities_includes_skills(client):
    skills_dir = os.path.join(_app_home(), "skills")
    _write_skill(skills_dir, "webpanel-cap",
                 ['name: webpanel-cap', 'description: "Capabilities skill."'])
    response = client.get("/api/capabilities", headers=AUTH)
    assert response.status_code == 200
    found = {s["name"]: s for s in response.json()["skills"]}
    assert "webpanel-cap" in found
    assert found["webpanel-cap"] == {
        "name": "webpanel-cap",
        "description": "Capabilities skill.",
    }


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


# ---------------------------------------------------------------------------
# Per-project working directories (web.project_roots)
# ---------------------------------------------------------------------------

@pytest.fixture
def rooted(cfg, tmp_path, monkeypatch):
    """A server whose sessions may only be rooted under <tmp>/roots, plus an
    isolated session store."""
    monkeypatch.setattr(store, "SESSIONS_DIR", str(tmp_path / "sessions"))
    root = tmp_path / "roots"
    (root / "alpha").mkdir(parents=True)
    (root / "beta").mkdir()
    (tmp_path / "outside").mkdir()
    cfg["web"]["token"] = "secreto"
    cfg["web"]["project_roots"] = [str(root)]
    cfg["permissions"]["workspace"] = ""   # no global confinement: the project is it
    return TestClient(srv.build_app(cfg)), root, tmp_path


def test_projects_lists_roots_and_children(rooted):
    client, root, _ = rooted
    assert client.get("/api/projects").status_code == 401
    body = client.get("/api/projects", headers=AUTH).json()
    assert body["roots"] == [os.path.realpath(str(root))]
    paths = [p["path"] for p in body["projects"]]
    assert os.path.realpath(str(root / "alpha")) in paths
    assert os.path.realpath(str(root / "beta")) in paths
    assert all(p["path"].startswith(os.path.realpath(str(root))) for p in body["projects"])


@pytest.mark.parametrize("bad", ["traversal", "empty", "unknown-relative"])
def test_create_session_rejects_a_cwd_outside_the_roots(rooted, bad):
    client, root, _ = rooted
    candidate = {"traversal": str(root / ".." / "outside"),
                 "empty": "",
                 "unknown-relative": "relative/but/unknown"}[bad]
    response = client.post("/api/sessions", headers=AUTH,
                           json={"provider": "openai", "model": "gpt-test",
                                 "cwd": candidate})
    assert response.status_code == 400, response.text
    assert "cwd" in response.json()["error"]


def test_create_session_rejects_an_absolute_cwd_outside_the_roots(rooted):
    client, _, tmp_path = rooted
    response = client.post("/api/sessions", headers=AUTH,
                           json={"provider": "openai", "model": "gpt-test",
                                 "cwd": str(tmp_path / "outside")})
    assert response.status_code == 400
    assert response.json()["error"] == "cwd is outside the configured project roots"


def test_create_session_with_no_roots_configured_accepts_no_cwd(client, tmp_path):
    response = client.post("/api/sessions", headers=AUTH,
                           json={"provider": "openai", "model": "gpt-test",
                                 "cwd": str(tmp_path)})
    assert response.status_code == 400
    assert "project_roots" in response.json()["error"]


def test_create_session_roots_the_agent_in_the_requested_project(rooted):
    client, root, _ = rooted
    response = client.post("/api/sessions", headers=AUTH,
                           json={"provider": "openai", "model": "gpt-test",
                                 "cwd": str(root / "alpha")})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["cwd"] == os.path.realpath(str(root / "alpha"))
    live = srv.STATE.sessions[body["session_id"]].session
    assert live.cwd == os.path.realpath(str(root / "alpha"))
    assert live.workspace() == live.cwd      # writes confined to the project


def test_two_web_sessions_keep_separate_working_directories(rooted):
    """One process, two projects: a relative write from each must land in its
    own folder (no shared chdir)."""
    client, root, _ = rooted
    ids = {}
    for name in ("alpha", "beta"):
        # yolo: these turns run without a socket attached, so nobody could
        # answer the write confirmation that 'ask' mode would raise.
        r = client.post("/api/sessions", headers=AUTH,
                        json={"provider": "openai", "model": "gpt-test",
                              "permission_mode": "yolo",
                              "cwd": str(root / name)})
        assert r.status_code == 201, r.text
        ids[name] = r.json()["session_id"]

    for name, sid in ids.items():
        live = srv.STATE.sessions[sid].session
        live.tracker = ContextTracker("gpt-test")
        live.client = fake_client([
            [tool_delta(0, "c1", "write_file",
                        json.dumps({"path": "out.txt", "content": "soy " + name})),
             usage_chunk(10, 5)],
            [text_delta("ok"), usage_chunk(12, 2)],
        ])
        live.run_turn("escribe out.txt")

    assert (root / "alpha" / "out.txt").read_text(encoding="utf-8") == "soy alpha"
    assert (root / "beta" / "out.txt").read_text(encoding="utf-8") == "soy beta"


def test_sessions_expose_and_filter_by_cwd(rooted):
    client, root, _ = rooted
    store.write_session({"id": "alpha-1", "provider": "openai", "model": "gpt-test",
                         "updated": "2026-01-01T10:00:00", "title": "en alpha",
                         "cwd": str(root / "alpha"), "messages": []})
    store.write_session({"id": "beta-1", "provider": "openai", "model": "gpt-test",
                         "updated": "2026-01-02T10:00:00", "title": "en beta",
                         "cwd": str(root / "beta"), "messages": []})
    store.write_session({"id": "legacy-1", "provider": "openai", "model": "gpt-test",
                         "updated": "2026-01-03T10:00:00", "title": "sin carpeta",
                         "messages": []})
    live_id = client.post("/api/sessions", headers=AUTH,
                          json={"provider": "openai", "model": "gpt-test",
                                "cwd": str(root / "alpha")}).json()["session_id"]

    everything = client.get("/api/sessions", headers=AUTH).json()
    assert {s["id"] for s in everything["saved"]} == {"alpha-1", "beta-1", "legacy-1"}
    assert next(s for s in everything["saved"] if s["id"] == "legacy-1")["cwd"] == ""

    scoped = client.get("/api/sessions", headers=AUTH,
                        params={"cwd": str(root / "alpha")}).json()
    assert [s["id"] for s in scoped["saved"]] == ["alpha-1"]
    assert [s["session_id"] for s in scoped["live"]] == [live_id]
    assert scoped["live"][0]["cwd"] == os.path.realpath(str(root / "alpha"))

    other = client.get("/api/sessions", headers=AUTH,
                       params={"cwd": str(root / "beta")}).json()
    assert [s["id"] for s in other["saved"]] == ["beta-1"]
    assert other["live"] == []


def test_resuming_a_saved_session_recovers_its_project(rooted):
    client, root, _ = rooted
    store.write_session({"id": "alpha-1", "provider": "openai", "model": "gpt-test",
                         "updated": "2026-01-01T10:00:00", "title": "en alpha",
                         "cwd": str(root / "alpha"),
                         "messages": [{"role": "user", "content": "hola"}]})
    body = client.post("/api/sessions", headers=AUTH,
                       json={"resume": "alpha-1"}).json()
    assert body["cwd"] == os.path.realpath(str(root / "alpha"))
    assert body["cwd_note"] is None


def test_resuming_a_session_saved_outside_the_roots_falls_back(rooted):
    """A session saved from the TUI anywhere on the machine must not hand the
    panel a working directory the roots would have rejected."""
    client, _, tmp_path = rooted
    store.write_session({"id": "wild-1", "provider": "openai", "model": "gpt-test",
                         "updated": "2026-01-01T10:00:00", "title": "fuera",
                         "cwd": str(tmp_path / "outside"),
                         "messages": [{"role": "user", "content": "hola"}]})
    body = client.post("/api/sessions", headers=AUTH, json={"resume": "wild-1"}).json()
    assert body["cwd"] == os.getcwd()
    assert "ignored" in body["cwd_note"]


def test_capabilities_reports_the_project_roots(rooted):
    client, root, _ = rooted
    body = client.get("/api/capabilities", headers=AUTH).json()
    assert body["project_roots"] == [os.path.realpath(str(root))]


# ---------------------------------------------------------------- max_iterations

def _rounds(n):
    """n tool-call rounds the engine will keep executing (exec is blocked over the
    web, which still counts as a round) plus a final text answer."""
    script = [[tool_delta(0, f"c{i}", "run_python", json.dumps({"code": "print(1)"})),
               usage_chunk(10, 5)] for i in range(n)]
    return script + [[text_delta("listo"), usage_chunk(12, 2)]]


def _drain_notice(ws):
    """Next notice on the socket (telemetry samples arrive interleaved)."""
    while True:
        event = ws.receive_json()
        if event["type"] == "notice":
            return event


def test_capabilities_expose_the_iteration_cap(client):
    body = client.get("/api/capabilities", headers=AUTH).json()
    assert body["default_max_iterations"] == srv.DEFAULT_WEB_MAX_ITERATIONS == 60
    assert body["max_iterations_limit"] == srv.MAX_ITERATIONS_LIMIT


def test_web_sessions_default_to_the_web_cap_not_the_tui_one(client, cfg):
    """The engine/TUI default (8) is too short for an unattended panel session."""
    body = client.post("/api/sessions", headers=AUTH,
                       json={"provider": "openai", "model": "gpt-test"}).json()
    assert cfg["max_iterations"] == 8          # config untouched
    assert body["max_iterations"] == 60
    assert srv.STATE.sessions[body["session_id"]].session.iteration_cap() == 60


def test_configured_web_cap_becomes_the_default(cfg):
    cfg["web"]["token"] = "secreto"
    cfg["web"]["max_iterations"] = 25
    client = TestClient(srv.build_app(cfg))
    assert client.get("/api/capabilities",
                      headers=AUTH).json()["default_max_iterations"] == 25
    body = client.post("/api/sessions", headers=AUTH,
                       json={"provider": "openai", "model": "gpt-test"}).json()
    assert body["max_iterations"] == 25


def test_create_session_accepts_a_per_session_cap(client):
    body = client.post("/api/sessions", headers=AUTH,
                       json={"provider": "openai", "model": "gpt-test",
                             "max_iterations": 120}).json()
    assert body["max_iterations"] == 120
    assert srv.STATE.sessions[body["session_id"]].session.iteration_cap() == 120


@pytest.mark.parametrize("bad", [0, -1, 501, "60", 12.5, True, [60]])
def test_create_session_rejects_an_invalid_cap(client, bad):
    response = client.post("/api/sessions", headers=AUTH,
                           json={"provider": "openai", "model": "gpt-test",
                                 "max_iterations": bad})
    assert response.status_code == 400, bad
    assert "max_iterations" in response.json()["error"]


def test_live_sessions_keep_independent_caps(client, cfg):
    caps = {}
    for wanted in (5, 300):
        body = client.post("/api/sessions", headers=AUTH,
                           json={"provider": "openai", "model": "gpt-test",
                                 "max_iterations": wanted}).json()
        caps[wanted] = srv.STATE.sessions[body["session_id"]].session.iteration_cap()
    assert caps == {5: 5, 300: 300}
    assert cfg["max_iterations"] == 8          # never written to the shared config
    listing = client.get("/api/sessions", headers=AUTH).json()
    assert sorted(s["max_iterations"] for s in listing["live"]) == [5, 300]


def test_resumed_session_keeps_its_saved_cap_unless_overridden(client):
    store.write_session({"id": "capped-1", "provider": "openai", "model": "gpt-test",
                         "updated": "2026-01-01T10:00:00", "title": "con tope",
                         "max_iterations": 90,
                         "messages": [{"role": "user", "content": "hola"}]})
    listed = client.get("/api/sessions", headers=AUTH).json()["saved"]
    assert next(s for s in listed if s["id"] == "capped-1")["max_iterations"] == 90

    resumed = client.post("/api/sessions", headers=AUTH,
                          json={"resume": "capped-1"}).json()
    assert resumed["max_iterations"] == 90

    overridden = client.post("/api/sessions", headers=AUTH,
                             json={"resume": "capped-1", "max_iterations": 12}).json()
    assert overridden["max_iterations"] == 12


def test_turn_stopped_by_the_cap_says_so_over_the_socket(client):
    """The silent stop is the bug: reaching the cap must be visible, not look
    like a model that gave up mid-plan."""
    sid, info = make_ws_session(client, _rounds(4), max_iterations=2)
    assert info["max_iterations"] == 2
    with client.websocket_connect(f"/ws/{sid}", headers=AUTH) as ws:
        ws.receive_json()
        ws.receive_json()
        ws.send_json({"type": "send", "text": "trabaja"})
        notices, results = [], []
        while True:
            event = ws.receive_json()
            if event["type"] == "notice":
                notices.append(event)
            elif event["type"] == "tool_call_result":
                results.append(event)
            elif event["type"] == "turn_done":
                break
        assert event["reason"] == "max_iterations"
        assert len(results) == 2                       # exactly the 2 allowed rounds
        # the warning must precede turn_done: a client that stops reading there
        # would otherwise never learn why the turn ended
        assert notices and notices[-1]["kind"] == "warning"
        assert "2 tool-call rounds" in notices[-1]["text"]


def test_set_max_iterations_command_retunes_a_live_session(client):
    sid, info = make_ws_session(client, [], max_iterations=3)
    assert info["max_iterations"] == 3
    live = srv.STATE.sessions[sid].session
    with client.websocket_connect(f"/ws/{sid}", headers=AUTH) as ws:
        ws.receive_json()
        ws.receive_json()
        ws.send_json({"type": "set_max_iterations", "max_iterations": 150})
        assert "150" in _drain_notice(ws)["text"]
        assert live.iteration_cap() == 150

        ws.send_json({"type": "set_max_iterations", "max_iterations": 9000})
        while True:
            event = ws.receive_json()
            if event["type"] != "telemetry":
                break
        assert event["type"] == "error" and "max_iterations" in event["message"]
        assert live.iteration_cap() == 150          # rejected, cap unchanged
