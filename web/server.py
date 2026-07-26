"""Headless ASGI adapter exposing the Raiko engine over REST and WebSocket.

The web layer owns transport concerns only: authentication, live-session
ownership, bounded event queues, disconnect cleanup and JSON validation. The
engine remains synchronous and transport agnostic.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import importlib.metadata
import json
import os
import re
import secrets
import sys
import threading
import time
import uuid
from copy import deepcopy

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bench"),
)

from engine import protocol, store, telemetry
from engine.config import CLOUD, URL_PROVIDERS, load_config, resolve_key
from engine.session import Session

EXEC_TOOLS = {"run_python", "run_powershell", "run_bash"}
CONFIRM_TOOLS = {
    "write_file",
    "edit_file",
    "jira_assign",
    "jira_comment",
    "confluence_create",
    "confluence_comment",
}
PERMISSION_TIMEOUT = 300
TELEMETRY_INTERVAL = 2.0
MAX_COMMAND_CHARS = 262_144
MAX_PROMPT_CHARS = 100_000
MAX_MODEL_CHARS = 191
DEFAULT_QUEUE_SIZE = 4096
DEFAULT_MAX_LIVE_SESSIONS = 16
DEFAULT_SESSION_TTL_SECONDS = 3600

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_PROVIDER_RE = re.compile(r"^[A-Za-z0-9_-]{1,48}$")


def event_to_json(event) -> str:
    payload = dataclasses.asdict(event)
    payload["type"] = protocol.EVENT_NAMES[type(event)]
    return json.dumps(payload, ensure_ascii=False)


def _engine_version() -> str:
    try:
        return importlib.metadata.version("raiko-toolkit")
    except importlib.metadata.PackageNotFoundError:
        return "0.2.6-dev"


class WebSession:
    """One live engine session and its single attached WebSocket client."""

    def __init__(self, cfg, allow_exec: bool, queue_size: int = DEFAULT_QUEUE_SIZE):
        self.id = uuid.uuid4().hex[:16]
        self.loop: asyncio.AbstractEventLoop | None = None
        self.queue_size = max(64, queue_size)
        self.queue: asyncio.Queue[str] | None = None
        self.connection_id: str | None = None
        self.busy = False
        self.turn_task: asyncio.Task | None = None
        self.last_seen = time.monotonic()
        self.created_at = self.last_seen
        self._perm_lock = threading.Lock()
        self._pending_perms: dict[str, dict] = {}
        self.session = Session(
            cfg,
            emit=self._emit,
            ask_permission=self._ask_permission,
        )
        if not allow_exec:
            self.session.blocked_tools |= EXEC_TOOLS
        self.session.always_ask_tools |= CONFIRM_TOOLS | EXEC_TOOLS

    def attach(self, loop: asyncio.AbstractEventLoop) -> str | None:
        if self.loop is not None:
            return None
        self.loop = loop
        self.queue = asyncio.Queue(maxsize=self.queue_size)
        self.connection_id = uuid.uuid4().hex[:12]
        self.last_seen = time.monotonic()
        return self.connection_id

    def detach(self, connection_id: str) -> None:
        if connection_id != self.connection_id:
            return
        self.deny_pending_permissions()
        self.session.interrupt()
        self.loop = None
        self.queue = None
        self.connection_id = None
        self.last_seen = time.monotonic()

    def _emit(self, event) -> None:
        self._push(event_to_json(event))

    def _push(self, payload: str) -> None:
        loop = self.loop
        if loop is None:
            return

        def enqueue() -> None:
            queue = self.queue
            if queue is None:
                return
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(payload)

        loop.call_soon_threadsafe(enqueue)

    def _ask_permission(self, request: protocol.PermissionRequired) -> str:
        # Remote writes/execs intentionally confirm every time, so persisting
        # allow_always would be misleading on this interface.
        request = dataclasses.replace(
            request,
            allowed_decisions=("allow_once", "deny"),
        )
        waiter = {
            "event": threading.Event(),
            "decision": "deny",
            "allowed": set(request.allowed_decisions),
        }
        with self._perm_lock:
            self._pending_perms[request.perm_id] = waiter
        self._emit(request)
        waiter["event"].wait(PERMISSION_TIMEOUT)
        with self._perm_lock:
            self._pending_perms.pop(request.perm_id, None)
        return waiter["decision"]

    def resolve_permission(self, perm_id: str, decision: str) -> bool:
        with self._perm_lock:
            waiter = self._pending_perms.get(perm_id)
            if waiter is None:
                return False
            waiter["decision"] = decision if decision in waiter["allowed"] else "deny"
            waiter["event"].set()
            return True

    def deny_pending_permissions(self) -> None:
        with self._perm_lock:
            waiters = list(self._pending_perms.values())
        for waiter in waiters:
            waiter["decision"] = "deny"
            waiter["event"].set()

    def snapshot(self) -> protocol.SessionSnapshot:
        session = self.session
        tracker = session.tracker
        return protocol.SessionSnapshot(
            session_id=self.id,
            engine_session_id=session.session_id,
            busy=self.busy,
            provider=session.provider or "",
            model=session.model or "",
            ctx_window=tracker.limit if tracker else None,
            messages=deepcopy(session.messages),
            input_tokens=session.session_input,
            output_tokens=session.session_output,
            session_usd=session.session_cost,
        )


class AppState:
    def __init__(self, cfg):
        self.cfg = cfg
        self.web_cfg = cfg.get("web", {}) or {}
        self.token = (
            os.environ.get("RAIKO_WEB_TOKEN")
            or self.web_cfg.get("token")
            or ""
        ).strip()
        self.allow_exec = bool(self.web_cfg.get("allow_exec", False))
        self.max_live_sessions = max(
            1,
            int(self.web_cfg.get("max_live_sessions") or DEFAULT_MAX_LIVE_SESSIONS),
        )
        self.session_ttl = max(
            60,
            int(self.web_cfg.get("session_ttl_seconds") or DEFAULT_SESSION_TTL_SECONDS),
        )
        self.queue_size = max(
            64,
            int(self.web_cfg.get("queue_size") or DEFAULT_QUEUE_SIZE),
        )
        self.sessions: dict[str, WebSession] = {}
        self.started_at = time.monotonic()

    def cleanup_idle(self) -> None:
        now = time.monotonic()
        stale = [
            sid
            for sid, live in self.sessions.items()
            if live.loop is None
            and not live.busy
            and now - live.last_seen >= self.session_ttl
        ]
        for sid in stale:
            self.sessions.pop(sid, None)


STATE: AppState | None = None


def _state() -> AppState:
    if STATE is None:
        raise RuntimeError("web application state is not initialized")
    return STATE


def _authorized(request_or_ws) -> bool:
    state = _state()
    if not state.token:
        return True
    auth = request_or_ws.headers.get("authorization", "")
    return (
        auth.startswith("Bearer ")
        and secrets.compare_digest(auth[7:], state.token)
    )


def _unauthorized() -> JSONResponse:
    return JSONResponse({"error": "unauthorized"}, status_code=401)


def _valid_provider(cfg, provider) -> bool:
    return (
        isinstance(provider, str)
        and bool(_PROVIDER_RE.fullmatch(provider))
        and isinstance(cfg.get(provider), dict)
        and provider in CLOUD | {"local"}
    )


def _valid_model(model) -> bool:
    return (
        isinstance(model, str)
        and 0 < len(model.strip()) <= MAX_MODEL_CHARS
        and "\x00" not in model
        and "\r" not in model
        and "\n" not in model
    )


def _provider_capabilities(cfg) -> list[dict]:
    favorites = cfg.get("favorites") or {}
    result = []
    for provider in sorted(CLOUD | {"local"}):
        pcfg = cfg.get(provider)
        if not isinstance(pcfg, dict):
            continue
        models = []
        for model in [
            pcfg.get("model"),
            *(pcfg.get("models") or []),
            *(favorites.get(provider) or []),
        ]:
            if isinstance(model, str) and model and model not in models:
                models.append(model)
        if provider in {"local"}:
            configured = bool(pcfg.get("base_url"))
        elif provider in URL_PROVIDERS:
            configured = bool(pcfg.get("base_url"))
        else:
            configured = bool(resolve_key(provider, pcfg))
        result.append(
            {
                "id": provider,
                "label": provider.replace("_", " ").title(),
                "models": models,
                "default_model": pcfg.get("model") or (models[0] if models else None),
                "configured": configured,
                "supports_model_swap": provider != "local",
                "ctx_window": pcfg.get("ctx_window"),
            }
        )
    return result


async def health(request):
    if not _authorized(request):
        return _unauthorized()
    state = _state()
    state.cleanup_idle()
    return JSONResponse(
        {
            "status": "ok",
            "protocol_version": protocol.PROTOCOL_VERSION,
            "engine_version": _engine_version(),
            "uptime_s": round(time.monotonic() - state.started_at, 1),
            "live_sessions": len(state.sessions),
        }
    )


async def capabilities(request):
    if not _authorized(request):
        return _unauthorized()
    state = _state()
    mcp = state.cfg.get("mcp", {}) or {}
    servers = mcp.get("servers") or ([mcp] if mcp.get("url") else [])
    return JSONResponse(
        {
            "protocol_version": protocol.PROTOCOL_VERSION,
            "providers": _provider_capabilities(state.cfg),
            "system_prompts": [
                {"name": name} for name in (state.cfg.get("system_prompts") or {})
            ],
            "exec_enabled": state.allow_exec,
            "mcp": {
                "enabled": bool(mcp.get("enabled")),
                "servers": len(servers),
            },
        }
    )


async def list_sessions(request):
    if not _authorized(request):
        return _unauthorized()
    state = _state()
    state.cleanup_idle()
    saved = [
        {
            "id": session.get("id"),
            "title": session.get("title"),
            "provider": session.get("provider"),
            "model": session.get("model"),
            "updated": session.get("updated"),
            "messages": len(session.get("messages", [])),
        }
        for session in store.list_sessions()
    ]
    live = [
        {
            "session_id": web_session.id,
            "provider": web_session.session.provider,
            "model": web_session.session.model,
            "busy": web_session.busy,
            "connected": web_session.loop is not None,
            "engine_session_id": web_session.session.session_id,
        }
        for web_session in state.sessions.values()
    ]
    return JSONResponse({"saved": saved, "live": live})


async def session_detail(request):
    if not _authorized(request):
        return _unauthorized()
    session_id = request.path_params["session_id"]
    if not store.valid_session_id(session_id):
        return JSONResponse({"error": "invalid session id"}, status_code=400)
    saved = store.load_session(session_id)
    if saved is None:
        return JSONResponse({"error": "unknown saved session"}, status_code=404)
    return JSONResponse(saved)


async def create_session(request):
    if not _authorized(request):
        return _unauthorized()
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "JSON body must be an object"}, status_code=400)

    state = _state()
    state.cleanup_idle()
    if len(state.sessions) >= state.max_live_sessions:
        return JSONResponse({"error": "live session limit reached"}, status_code=429)

    provider = body.get("provider")
    model = body.get("model")
    resume_id = body.get("resume")
    if resume_id is not None and not store.valid_session_id(resume_id):
        return JSONResponse({"error": "invalid saved session id"}, status_code=400)
    saved = store.load_session(resume_id) if resume_id else None
    if resume_id and not saved:
        return JSONResponse(
            {"error": f"unknown saved session '{resume_id}'"},
            status_code=404,
        )
    if saved:
        provider = provider or saved.get("provider")
        model = model or saved.get("model")
    if not _valid_provider(state.cfg, provider) or not _valid_model(model):
        return JSONResponse(
            {"error": "a configured provider and valid model are required"},
            status_code=400,
        )
    ctx_window = body.get("ctx_window")
    if ctx_window is not None:
        if (
            isinstance(ctx_window, bool)
            or not isinstance(ctx_window, int)
            or not 1024 <= ctx_window <= 4_194_304
        ):
            return JSONResponse({"error": "invalid ctx_window"}, status_code=400)

    web_session = WebSession(
        state.cfg,
        allow_exec=state.allow_exec,
        queue_size=state.queue_size,
    )
    base_url = None
    if provider == "local":
        import serve

        base_url = serve.base_url()
    try:
        await asyncio.to_thread(
            web_session.session.configure,
            provider,
            model.strip(),
            ctx_window,
            base_url,
        )
        if saved:
            web_session.session.resume(saved)
        mcp_result = []
        mcp_error = None
        if web_session.session.mcp_servers:
            try:
                mcp_result = await asyncio.to_thread(web_session.session.load_mcp_tools)
            except Exception as error:
                mcp_error = f"{type(error).__name__}: {error}"
    except Exception as error:
        return JSONResponse(
            {"error": f"configure failed: {type(error).__name__}: {error}"},
            status_code=400,
        )

    state.sessions[web_session.id] = web_session
    return JSONResponse(
        {
            "session_id": web_session.id,
            "provider": provider,
            "model": model.strip(),
            "ctx_window": web_session.session.tracker.limit,
            "protocol_version": protocol.PROTOCOL_VERSION,
            "exec_enabled": state.allow_exec,
            "mcp_tools": len(web_session.session.mcp_tools),
            "mcp_servers": len(mcp_result),
            "mcp_error": mcp_error,
        },
        status_code=201,
    )


async def delete_session(request):
    if not _authorized(request):
        return _unauthorized()
    session_id = request.path_params["session_id"]
    state = _state()
    live = state.sessions.pop(session_id, None)
    if live is not None:
        live.deny_pending_permissions()
        live.session.interrupt()
        return JSONResponse({"deleted": True, "kind": "live"})
    if not store.valid_session_id(session_id):
        return JSONResponse({"error": "invalid session id"}, status_code=400)
    if store.delete_session(session_id):
        return JSONResponse({"deleted": True, "kind": "saved"})
    return JSONResponse({"error": "unknown session"}, status_code=404)


async def _pump_events(socket: WebSocket, web_session: WebSession):
    while True:
        queue = web_session.queue
        if queue is None:
            return
        await socket.send_text(await queue.get())


async def _pump_telemetry(web_session: WebSession):
    while True:
        sample = await asyncio.to_thread(telemetry.sample)
        web_session._push(event_to_json(sample))
        await asyncio.sleep(TELEMETRY_INTERVAL)


async def _run_turn(web_session: WebSession, text: str):
    try:
        await asyncio.to_thread(web_session.session.run_turn, text)
    finally:
        web_session.busy = False


async def _run_compaction(web_session: WebSession):
    try:
        await asyncio.to_thread(web_session.session.compact, False)
        web_session._emit(web_session.snapshot())
    finally:
        web_session.busy = False


async def ws_endpoint(socket: WebSocket):
    if not _authorized(socket):
        await socket.close(code=4401, reason="unauthorized")
        return
    web_session = _state().sessions.get(socket.path_params["session_id"])
    if web_session is None:
        await socket.close(code=4404, reason="unknown session")
        return

    connection_id = web_session.attach(asyncio.get_running_loop())
    if connection_id is None:
        await socket.close(code=4409, reason="session already has a client")
        return

    await socket.accept()
    session = web_session.session
    await socket.send_text(
        event_to_json(
            protocol.SessionStarted(
                session_id=web_session.id,
                provider=session.provider or "",
                model=session.model or "",
                ctx_window=session.tracker.limit if session.tracker else None,
                connection_id=connection_id,
            )
        )
    )
    await socket.send_text(event_to_json(web_session.snapshot()))
    pumps = [
        asyncio.create_task(_pump_events(socket, web_session)),
        asyncio.create_task(_pump_telemetry(web_session)),
    ]
    try:
        while True:
            raw = await socket.receive_text()
            web_session.last_seen = time.monotonic()
            if len(raw) > MAX_COMMAND_CHARS:
                web_session._emit(protocol.Error(message="command is too large"))
                continue
            try:
                command = json.loads(raw)
            except json.JSONDecodeError:
                web_session._emit(protocol.Error(message="invalid JSON command"))
                continue
            if not isinstance(command, dict):
                web_session._emit(protocol.Error(message="command must be an object"))
                continue

            command_type = command.get("type")
            if command_type == "send":
                text = command.get("text")
                if not isinstance(text, str) or not text.strip():
                    web_session._emit(protocol.Error(message="prompt cannot be empty"))
                    continue
                if len(text) > MAX_PROMPT_CHARS:
                    web_session._emit(protocol.Error(message="prompt is too large"))
                    continue
                if web_session.busy:
                    web_session._emit(
                        protocol.Error(
                            message="a turn is already running (interrupt it first)"
                        )
                    )
                    continue
                if session.over_budget():
                    web_session._emit(protocol.TurnDone(reason="budget_exceeded"))
                    continue
                web_session.busy = True
                web_session.turn_task = asyncio.create_task(
                    _run_turn(web_session, text)
                )
            elif command_type == "interrupt":
                session.interrupt()
                web_session.deny_pending_permissions()
            elif command_type == "permission_response":
                if not web_session.resolve_permission(
                    str(command.get("perm_id", "")),
                    str(command.get("decision", "deny")),
                ):
                    web_session._emit(
                        protocol.Error(message="permission is no longer pending")
                    )
            elif command_type == "swap_model":
                if web_session.busy:
                    web_session._emit(
                        protocol.Error(message="cannot swap models mid-turn")
                    )
                    continue
                model = command.get("model")
                provider = command.get("provider") or session.provider
                if not _valid_provider(_state().cfg, provider) or not _valid_model(model):
                    web_session._emit(
                        protocol.Error(message="invalid provider or model")
                    )
                    continue
                await asyncio.to_thread(
                    session.swap_model,
                    model.strip(),
                    provider,
                )
                web_session._emit(web_session.snapshot())
            elif command_type == "compact":
                if web_session.busy:
                    web_session._emit(
                        protocol.Error(message="cannot compact mid-turn")
                    )
                    continue
                web_session.busy = True
                web_session.turn_task = asyncio.create_task(
                    _run_compaction(web_session)
                )
            elif command_type == "clear":
                if web_session.busy:
                    web_session._emit(protocol.Error(message="cannot clear mid-turn"))
                    continue
                session.clear()
                web_session._emit(protocol.Notice(text="context cleared"))
                web_session._emit(web_session.snapshot())
            elif command_type == "rewind_last_user":
                if web_session.busy:
                    web_session._emit(protocol.Error(message="cannot rewind mid-turn"))
                    continue
                text = session.rewind_last_user()
                session.save()
                web_session._emit(
                    protocol.Notice(
                        text=text if text is not None else "",
                        kind="rewind",
                    )
                )
                web_session._emit(web_session.snapshot())
            elif command_type == "set_system_prompt":
                if web_session.busy:
                    web_session._emit(
                        protocol.Error(message="cannot change prompt mid-turn")
                    )
                    continue
                presets = session.cfg.get("system_prompts") or {}
                persona = command.get("text")
                if persona is None:
                    persona = presets.get(command.get("name") or "", "")
                if not isinstance(persona, str) or len(persona) > MAX_PROMPT_CHARS:
                    web_session._emit(
                        protocol.Error(message="invalid system prompt")
                    )
                    continue
                session.apply_persona(persona)
                web_session._emit(protocol.Notice(text="system prompt updated"))
                web_session._emit(web_session.snapshot())
            else:
                web_session._emit(
                    protocol.Error(
                        message=f"unknown command type '{command_type}'"
                    )
                )
    except WebSocketDisconnect:
        pass
    finally:
        web_session.detach(connection_id)
        for task in pumps:
            task.cancel()
        await asyncio.gather(*pumps, return_exceptions=True)
        turn_task = web_session.turn_task
        if turn_task is not None and not turn_task.done():
            try:
                await asyncio.wait_for(asyncio.shield(turn_task), timeout=2)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass


def build_app(cfg) -> Starlette:
    global STATE
    STATE = AppState(cfg)
    origins = [
        origin
        for origin in (STATE.web_cfg.get("allowed_origins") or [])
        if origin and origin != "*"
    ]
    middleware = []
    if origins:
        middleware.append(
            Middleware(
                CORSMiddleware,
                allow_origins=origins,
                allow_methods=["GET", "POST", "DELETE"],
                allow_headers=["authorization", "content-type"],
            )
        )
    return Starlette(
        routes=[
            Route("/api/health", health, methods=["GET"]),
            Route("/api/capabilities", capabilities, methods=["GET"]),
            Route("/api/sessions", list_sessions, methods=["GET"]),
            Route("/api/sessions", create_session, methods=["POST"]),
            Route("/api/sessions/{session_id}", session_detail, methods=["GET"]),
            Route("/api/sessions/{session_id}", delete_session, methods=["DELETE"]),
            WebSocketRoute("/ws/{session_id}", ws_endpoint),
        ],
        middleware=middleware,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="raiko web",
        description="headless engine server (WS + API + telemetry)",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="override web.host (default 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="override web.port (default 8484)",
    )
    args = parser.parse_args(argv)

    cfg = load_config()
    web_cfg = cfg.get("web", {}) or {}
    host = args.host or web_cfg.get("host") or "127.0.0.1"
    port = args.port or int(web_cfg.get("port") or 8484)
    token = (
        os.environ.get("RAIKO_WEB_TOKEN")
        or web_cfg.get("token")
        or ""
    ).strip()

    if host not in _LOOPBACK and not token:
        print(
            f"REFUSING to start: binding to {host} without web.token set.\n"
            "The exec tools would be one HTTP call away from anyone on the network.\n"
            "Set web.token in tui_config.json (or bind 127.0.0.1).",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if not token:
        print(
            "[!] no web.token configured - acceptable on 127.0.0.1, "
            "required for anything else."
        )
    if not web_cfg.get("allow_exec", False):
        print(
            "exec tools (run_python/run_powershell/run_bash) are DISABLED over "
            "the web (web.allow_exec=false)."
        )
    else:
        print("[!] web.allow_exec=true - exec tools are callable over this socket.")
    print(
        f"raiko web | ws://{host}:{port}/ws/{{session_id}} | "
        f"protocol v{protocol.PROTOCOL_VERSION}"
    )

    uvicorn.run(build_app(cfg), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
