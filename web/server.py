"""`raiko web` — headless ASGI server exposing the engine over WebSocket + a
small session API. It serves NO frontend: the HOMELAB panel (separate repo)
consumes this contract, documented in docs/ws-protocol.md.

Endpoints
    GET  /api/sessions            saved sessions (metadata only)
    POST /api/sessions            create/attach a live session {provider, model,
                                  ctx_window?, resume?} -> {session_id, ...}
    WS   /ws/{session_id}         events out / commands in, JSON both ways

Security (defaults are the safe ones)
    - binds 127.0.0.1 unless web.host says otherwise
    - token (config web.token) required via Authorization: Bearer / ?token=;
      refusing to start on a non-loopback bind without one
    - CORS: explicit whitelist (web.allowed_origins), never '*'
    - web.allow_exec=false (default) hard-disables run_python/run_powershell/
      run_bash for web sessions — they error even if allowlisted

Concurrency: each engine turn runs in a worker thread (asyncio.to_thread);
engine events are bridged to the socket through an asyncio.Queue via
call_soon_threadsafe. A permission_required event PAUSES the engine thread on a
threading.Event until the client answers permission_response (or the wait times
out and the operation is denied).
"""

import argparse
import asyncio
import dataclasses
import json
import secrets
import sys
import threading
import uuid

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bench"))

from engine import protocol, store, telemetry
from engine.config import load_config
from engine.session import Session

EXEC_TOOLS = {"run_python", "run_powershell", "run_bash"}
# writes always confirm over the web, even when allowlisted (Fase 5 hardening)
CONFIRM_TOOLS = {"write_file", "edit_file", "jira_assign", "jira_comment",
                 "confluence_create", "confluence_comment"}
PERMISSION_TIMEOUT = 300      # seconds a turn waits for permission_response before denying
TELEMETRY_INTERVAL = 2.0      # seconds between telemetry events on the socket

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def event_to_json(e) -> str:
    d = dataclasses.asdict(e)
    d["type"] = protocol.EVENT_NAMES[type(e)]
    return json.dumps(d, ensure_ascii=False)


class WebSession:
    """One live engine session owned by the web layer: outbound event queue,
    pending permission waits, and the busy gate (one turn at a time)."""

    def __init__(self, cfg, allow_exec: bool):
        self.id = uuid.uuid4().hex[:12]
        self.loop: asyncio.AbstractEventLoop | None = None
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.busy = False
        self._perm_lock = threading.Lock()
        self._pending_perms = {}   # perm_id -> {"event": threading.Event, "decision": str}
        self.session = Session(cfg, emit=self._emit, ask_permission=self._ask_permission)
        if not allow_exec:
            self.session.blocked_tools |= EXEC_TOOLS
        # every exec/write confirms over the socket even if allowlisted; the
        # config allowlist was granted to the TUI modal, not to remote clients
        self.session.always_ask_tools |= CONFIRM_TOOLS | EXEC_TOOLS

    # engine thread -> asyncio queue
    def _emit(self, e):
        self._push(event_to_json(e))

    def _push(self, payload: str):
        if self.loop is None:
            return   # no socket attached yet (POST-only phase): drop
        self.loop.call_soon_threadsafe(self.queue.put_nowait, payload)

    # engine thread blocks here until the client answers (or timeout -> deny)
    def _ask_permission(self, req: protocol.PermissionRequired) -> str:
        waiter = {"event": threading.Event(), "decision": "deny"}
        with self._perm_lock:
            self._pending_perms[req.perm_id] = waiter
        self._emit(req)
        waiter["event"].wait(PERMISSION_TIMEOUT)
        with self._perm_lock:
            self._pending_perms.pop(req.perm_id, None)
        return waiter["decision"]

    def resolve_permission(self, perm_id: str, decision: str):
        with self._perm_lock:
            waiter = self._pending_perms.get(perm_id)
        if waiter:
            waiter["decision"] = decision if decision in (
                "allow_once", "allow_always", "deny") else "deny"
            waiter["event"].set()


class AppState:
    def __init__(self, cfg):
        self.cfg = cfg
        self.web_cfg = cfg.get("web", {}) or {}
        self.token = (self.web_cfg.get("token") or "").strip()
        self.allow_exec = bool(self.web_cfg.get("allow_exec", False))
        self.sessions: dict[str, WebSession] = {}


STATE: AppState | None = None


def _authorized(request_or_ws) -> bool:
    if not STATE.token:
        return True   # loopback-only mode (enforced at startup)
    auth = request_or_ws.headers.get("authorization", "")
    if auth.startswith("Bearer ") and secrets.compare_digest(auth[7:], STATE.token):
        return True
    return secrets.compare_digest(request_or_ws.query_params.get("token", ""), STATE.token)


async def list_sessions(request):
    if not _authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    saved = [{"id": s.get("id"), "title": s.get("title"), "provider": s.get("provider"),
              "model": s.get("model"), "updated": s.get("updated"),
              "messages": len(s.get("messages", []))} for s in store.list_sessions()]
    live = [{"session_id": ws.id, "provider": ws.session.provider, "model": ws.session.model,
             "busy": ws.busy, "engine_session_id": ws.session.session_id}
            for ws in STATE.sessions.values()]
    return JSONResponse({"saved": saved, "live": live})


async def create_session(request):
    """Create a live session: {provider, model, ctx_window?, resume?} where
    `resume` is a saved session id from GET /api/sessions."""
    if not _authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    provider = body.get("provider")
    model = body.get("model")
    resume_id = body.get("resume")
    saved = store.load_session(resume_id) if resume_id else None
    if resume_id and not saved:
        return JSONResponse({"error": f"unknown saved session '{resume_id}'"}, status_code=404)
    if saved:
        provider = provider or saved.get("provider")
        model = model or saved.get("model")
    if not provider or not model:
        return JSONResponse({"error": "provider and model are required"}, status_code=400)
    ws = WebSession(STATE.cfg, allow_exec=STATE.allow_exec)
    base_url = None
    if provider == "local":
        import serve
        base_url = serve.base_url()   # attach to a running llama-server; never boots one
    try:
        ws.session.configure(provider, model, ctx_limit=body.get("ctx_window"), base_url=base_url)
    except Exception as e:
        return JSONResponse({"error": f"configure failed: {e}"}, status_code=400)
    if saved:
        ws.session.resume(saved)
    STATE.sessions[ws.id] = ws
    return JSONResponse({"session_id": ws.id, "provider": provider, "model": model,
                         "ctx_window": ws.session.tracker.limit,
                         "protocol_version": protocol.PROTOCOL_VERSION,
                         "exec_enabled": STATE.allow_exec}, status_code=201)


async def _pump_events(socket: WebSocket, ws: WebSession):
    while True:
        payload = await ws.queue.get()
        await socket.send_text(payload)


async def _pump_telemetry(socket: WebSocket, ws: WebSession):
    while True:
        t = await asyncio.to_thread(telemetry.sample)
        await socket.send_text(event_to_json(t))
        await asyncio.sleep(TELEMETRY_INTERVAL)


async def _run_turn(ws: WebSession, text: str):
    try:
        await asyncio.to_thread(ws.session.run_turn, text)
    finally:
        ws.busy = False


async def ws_endpoint(socket: WebSocket):
    if not _authorized(socket):
        await socket.close(code=4401, reason="unauthorized")
        return
    ws = STATE.sessions.get(socket.path_params["session_id"])
    if ws is None:
        await socket.close(code=4404, reason="unknown session")
        return
    await socket.accept()
    ws.loop = asyncio.get_running_loop()
    s = ws.session
    await socket.send_text(event_to_json(protocol.SessionStarted(
        session_id=ws.id, provider=s.provider or "", model=s.model or "",
        ctx_window=s.tracker.limit if s.tracker else None)))
    pumps = [asyncio.create_task(_pump_events(socket, ws)),
             asyncio.create_task(_pump_telemetry(socket, ws))]
    turn_task = None
    try:
        while True:
            try:
                cmd = json.loads(await socket.receive_text())
            except json.JSONDecodeError:
                await socket.send_text(event_to_json(protocol.Error(message="invalid JSON command")))
                continue
            ctype = cmd.get("type")
            if ctype == "send":
                if ws.busy:
                    await socket.send_text(event_to_json(protocol.Error(
                        message="a turn is already running (interrupt it first)")))
                    continue
                if s.over_budget():
                    await socket.send_text(event_to_json(protocol.TurnDone(reason="budget_exceeded")))
                    continue
                ws.busy = True
                turn_task = asyncio.create_task(_run_turn(ws, str(cmd.get("text", ""))))
            elif ctype == "interrupt":
                s.interrupt()
            elif ctype == "permission_response":
                ws.resolve_permission(str(cmd.get("perm_id", "")), str(cmd.get("decision", "deny")))
            elif ctype == "swap_model":
                if ws.busy:
                    await socket.send_text(event_to_json(protocol.Error(
                        message="cannot swap models mid-turn")))
                    continue
                s.swap_model(str(cmd.get("model", "")),
                             provider=cmd.get("provider") or s.provider)
            elif ctype == "compact":
                if ws.busy:
                    continue
                ws.busy = True

                async def _compact():
                    try:
                        await asyncio.to_thread(s.compact, False)
                    finally:
                        ws.busy = False
                asyncio.create_task(_compact())
            elif ctype == "clear":
                if not ws.busy:
                    s.clear()
                    await socket.send_text(event_to_json(protocol.Notice(text="context cleared")))
            elif ctype == "rewind_last_user":
                if not ws.busy:
                    text = s.rewind_last_user()
                    await socket.send_text(event_to_json(protocol.Notice(
                        text=text if text is not None else "", kind="rewind")))
            elif ctype == "set_system_prompt":
                if not ws.busy:
                    presets = s.cfg.get("system_prompts") or {}
                    persona = cmd.get("text")
                    if persona is None:
                        persona = presets.get(cmd.get("name") or "", "")
                    s.apply_persona(persona or "")
                    await socket.send_text(event_to_json(protocol.Notice(text="system prompt updated")))
            else:
                await socket.send_text(event_to_json(protocol.Error(
                    message=f"unknown command type '{ctype}'")))
    except WebSocketDisconnect:
        pass
    finally:
        for p in pumps:
            p.cancel()
        if turn_task and not turn_task.done():
            s.interrupt()
        ws.loop = None   # events buffer no further; session stays attachable


def build_app(cfg) -> Starlette:
    global STATE
    STATE = AppState(cfg)
    origins = [o for o in (STATE.web_cfg.get("allowed_origins") or []) if o and o != "*"]
    middleware = []
    if origins:
        middleware.append(Middleware(CORSMiddleware, allow_origins=origins,
                                     allow_methods=["GET", "POST"],
                                     allow_headers=["authorization", "content-type"]))
    return Starlette(routes=[
        Route("/api/sessions", list_sessions, methods=["GET"]),
        Route("/api/sessions", create_session, methods=["POST"]),
        WebSocketRoute("/ws/{session_id}", ws_endpoint),
    ], middleware=middleware)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="raiko web",
                                 description="headless engine server (WS + API + telemetry)")
    ap.add_argument("--host", default=None, help="override web.host (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=None, help="override web.port (default 8484)")
    args = ap.parse_args(argv)

    cfg = load_config()
    web_cfg = cfg.get("web", {}) or {}
    host = args.host or web_cfg.get("host") or "127.0.0.1"
    port = args.port or int(web_cfg.get("port") or 8484)
    token = (web_cfg.get("token") or "").strip()

    if host not in _LOOPBACK and not token:
        print(f"REFUSING to start: binding to {host} without web.token set.\n"
              f"The exec tools would be one HTTP call away from anyone on the network.\n"
              f"Set web.token in tui_config.json (or bind 127.0.0.1).", file=sys.stderr)
        sys.exit(2)
    if not token:
        print("⚠ no web.token configured — fine on 127.0.0.1, required for anything else.")
    if not web_cfg.get("allow_exec", False):
        print("exec tools (run_python/run_powershell/run_bash) are DISABLED over the web "
              "(web.allow_exec=false).")
    else:
        print("⚠ web.allow_exec=true — exec tools are callable over this socket.")
    print(f"raiko web · ws://{host}:{port}/ws/{{session_id}} · protocol v{protocol.PROTOCOL_VERSION}")

    uvicorn.run(build_app(cfg), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
