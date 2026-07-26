"""The agent engine: turn loop, streaming, tool dispatch, permissions, sessions,
cost/budget and compaction — extracted from tui.py (Fase 2). UI- and
transport-agnostic: it emits engine.protocol events through an `emit` callback
and receives decisions through an `ask_permission` hook. It never touches
widgets or sockets.

Concurrency model (kept from the TUI): a turn runs synchronously in whatever
worker thread calls `run_turn`; `emit(event)` is invoked inline from that
thread; `interrupt()` may be called from any other thread. The permission hook
BLOCKS the turn until it returns a decision — that pause/resume is what lets
the same mechanism back a modal (TUI), a WebSocket round-trip (web) or an
automatic policy (headless). Async frontends bridge with a queue.
"""

import difflib
import json
import os
import threading
import time
import uuid
from datetime import datetime

from openai import OpenAI

import context
import mcp_client
import pricing
from context import ContextTracker
from tools import TOOLS, call_tool, danger_match

from engine import protocol as ev
from engine import skills as skills_mod
from engine import store
from engine.config import (AUTO_COMPACT_PCT, MAX_ITERATIONS, THINK_RETRIES,
                           TOOL_TIMEOUT, build_system_prompt, resolve_key,
                           save_config)
from engine.textparse import ThinkSplitter, parse_text_tool_calls, strip_tool_call_text


def tool_arg_summary(name, args):
    """One-line, newline-free summary of the salient argument(s)."""
    try:
        d = json.loads(args) if isinstance(args, str) else dict(args or {})
    except Exception:
        d = None
    if isinstance(d, dict) and name == "skill" and d.get("name"):
        val = str(d["name"])
    elif isinstance(d, dict):
        for key in ("pattern", "name_glob", "command", "code", "query", "path", "local_path"):
            if d.get(key):
                val = str(d[key])
                break
        else:
            val = ", ".join(f"{k}={v}" for k, v in d.items()) if d else ""
    else:
        val = str(args or "")
    val = " ".join(val.split())
    return val[:70] + "…" if len(val) > 70 else val


def result_summary(result):
    """Short, newline-free note about a tool result. Returns (text, is_error)."""
    r = (result or "").strip()
    if r.startswith("ERROR"):
        first = r.splitlines()[0]
        return (first[:80] + "…" if len(first) > 80 else first), True
    first = next((ln for ln in r.splitlines() if ln.strip()), "")
    first = " ".join(first.split())
    if not first:
        return "(no output)", False
    note = first[:80] + "…" if len(first) > 80 else first
    if len(r) > len(first) + 10:
        note += f"  ·  {len(r)} chars"
    return note, False


class Session:
    """One conversation: provider+model connection, message history, cost tally
    and the turn loop. Frontends construct it with their `emit`/`ask_permission`
    hooks, call `configure()` (or `resume()`), then `run_turn()` per user message.
    """

    def __init__(self, cfg, emit=None, ask_permission=None,
                 skip_permissions=False, persist=True, cwd=None):
        self.cfg = cfg
        # Working directory of THIS conversation: relative tool paths resolve
        # against it and the exec tools run in it. Defaults to the process cwd
        # (so `cd project && raiko` just works); `raiko web` sets it per session
        # instead of chdir-ing the shared process.
        self.cwd = os.path.abspath(os.path.expanduser(str(cwd))) if cwd else os.getcwd()
        self.emit = emit or (lambda e: None)
        # callable(protocol.PermissionRequired) -> "allow_once"|"allow_always"|"deny".
        # None = deny everything that would need a prompt (safe default).
        self.ask_permission = ask_permission
        self.skip_permissions = skip_permissions
        self.persist = persist          # False → never write session files (demo)
        # tools refused outright on this frontend regardless of allowlists
        # (e.g. run_* over the web when web.allow_exec is false)
        self.blocked_tools = set()
        # tools that must confirm EVERY call on this frontend: the allowlist and
        # skip_permissions shortcuts do not apply (web exec/write hardening)
        self.always_ask_tools = set()

        self.provider = None
        self.model = None
        self.is_local = False
        self.base_url = None
        self.api_key = None
        self.enable_thinking = True
        self.client = None
        self.tracker = None

        # Agent Skills (SKILL.md discovery): a failure here must never block
        # session creation, just leave the session skill-less.
        try:
            self.skills = skills_mod.discover_skills(cfg)
        except Exception as e:
            self.skills = []
            self.emit(ev.Notice(kind="warning",
                                text=f"skills: discovery failed: {type(e).__name__}: {e}"))
        self._skill_tools = [skills_mod.skill_tool_schema()] if self.skills else []

        self.messages = [{"role": "system", "content": self._system_prompt()}]
        self.session_id = None          # set on first save; reused on resume

        # MCP (remote tools)
        self.mcp_servers = []           # [{name,url,prefix}] enabled servers
        self.mcp_tools = []             # OpenAI tool schemas (prefixed names)
        self.mcp_route = {}             # prefixed_name -> (url, original_name)
        self.mcp_names = set()          # set(self.mcp_route)

        # cost accounting (session totals + current turn)
        self.session_cost = 0.0
        self.session_input = 0
        self.session_output = 0
        self._turn_cost = 0.0
        self._turn_tokens = 0
        self._turn_start = 0.0

        self._cancel = threading.Event()   # set to interrupt the running turn
        self._think_leak_calls = []
        self._pending_diff = None       # (path, unified_diff) from the last write tool
        self.cur_think = self.cur_content = ""   # accumulators for the live segment

    # ---------- configuration / lifecycle ----------
    def _active_persona(self):
        sp = self.cfg.get("system_prompts") or {}
        return sp.get(self.cfg.get("active_system_prompt", "default"), "")

    def _system_prompt(self, persona=None):
        """Effective system prompt: the given (or active) persona + TOOL_RULES +
        the skills index, if any skill was discovered."""
        if persona is None:
            persona = self._active_persona()
        return build_system_prompt(persona, skills_mod.skills_index(self.skills))

    def configure(self, provider, model, ctx_limit=None, base_url=None, api_key=None,
                  emit_started=True):
        """Open (or re-open, for model swap) the provider connection. `base_url`
        overrides the config value (the TUI passes the local llama-server URL)."""
        self.provider = provider
        self.is_local = provider == "local"
        pc = self.cfg.get(provider, {})
        self.base_url = base_url or pc.get("base_url")
        self.api_key = api_key or ("sk-noop" if self.is_local
                                   else (resolve_key(provider, pc) or "sk-noop"))
        self.model = model
        self.enable_thinking = self.cfg.get("local", {}).get("enable_thinking", True)
        self.client = OpenAI(base_url=self.base_url, api_key=self.api_key)
        self.tracker = ContextTracker(self.model)
        self.mcp_servers = self._mcp_servers_from_cfg()
        if ctx_limit:
            self.tracker.limit = ctx_limit
        elif not self.is_local:
            # nano = frontier: we use the model's maximum (assumed)
            self.tracker.limit = pc.get("ctx_window", 131072)
        if emit_started:
            self.emit(ev.SessionStarted(session_id=self.session_id or "",
                                        provider=provider, model=model,
                                        ctx_window=self.tracker.limit))

    def swap_model(self, model, provider=None):
        """Switch the model mid-session (cloud/remote only). History is kept."""
        old = self.model
        self.configure(provider or self.provider, model, emit_started=False)
        # new client+tracker, messages untouched
        self.save()
        self.emit(ev.ModelSwapped(old_model=old, new_model=model, provider=self.provider))

    def interrupt(self):
        self._cancel.set()

    # ---------- system prompt ----------
    def apply_persona(self, persona):
        """Set the live system message (messages[0]); takes effect on the next turn."""
        prompt = self._system_prompt(persona)
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0]["content"] = prompt
        else:
            self.messages.insert(0, {"role": "system", "content": prompt})
        self.save()

    # ---------- sessions ----------
    def save(self):
        """Persist the current conversation (skipped for demo / empty sessions)."""
        if not self.persist or not self.model or len(self.messages) <= 1:
            return
        if not self.session_id:
            self.session_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        title = next((m.get("content") for m in self.messages
                      if m.get("role") == "user" and m.get("content")), "(no prompt)")
        sess = {
            "id": self.session_id,
            "updated": datetime.now().isoformat(timespec="seconds"),
            "provider": self.provider,
            "model": self.model,
            "title": (title or "")[:70],
            "cwd": self.cwd,
            "ctx_window": getattr(self.tracker, "limit", None),
            "messages": self.messages,
        }
        from engine.config import URL_PROVIDERS
        if self.provider in URL_PROVIDERS:
            sess["base_url"] = self.cfg.get(self.provider, {}).get("base_url", "")
        store.write_session(sess)

    def resume(self, sess, keep_cwd=False):
        """Install a saved session's state. The caller re-`configure()`s first
        (it owns provider selection / local server boot).

        The saved working directory comes back with the conversation, so a
        resumed session keeps reading and writing where it left off — unless
        the caller already picked one (`keep_cwd`, e.g. the web API validating
        a client-supplied cwd) or the folder is gone."""
        self.messages = sess.get("messages") or [
            {"role": "system", "content": self._system_prompt()}]
        self.session_id = sess.get("id")
        if sess.get("ctx_window") and self.tracker:
            self.tracker.limit = sess["ctx_window"]
        saved_cwd = sess.get("cwd")
        if not keep_cwd and saved_cwd and os.path.isdir(saved_cwd):
            self.cwd = os.path.abspath(saved_cwd)

    def clear(self):
        """Drop the conversation (keep the system prompt) and start a fresh session.
        The previous session stays saved on disk."""
        sys_msg = (self.messages[0] if self.messages and self.messages[0].get("role") == "system"
                   else {"role": "system", "content": self._system_prompt()})
        self.messages = [sys_msg]
        self.session_id = None          # the cleared chat becomes a new session
        self.session_cost = 0.0         # reset the spend tally with the conversation
        self.session_input = self.session_output = 0

    def rewind_last_user(self):
        """Drop the last user message and everything after it. Returns the dropped
        user text, or None if there is no user message yet (/retry, /edit)."""
        idx = None
        for i in range(len(self.messages) - 1, -1, -1):
            if self.messages[i].get("role") == "user":
                idx = i
                break
        if idx is None:
            return None
        text = self.messages[idx].get("content") or ""
        self.messages = self.messages[:idx]
        return text

    # ---------- MCP (remote tools) ----------
    def _mcp_servers_from_cfg(self):
        """Build the list of enabled MCP servers from config. Supports a `servers`
        list ([{name,url,prefix}]) and a legacy single {url, prefix} shape."""
        mc = self.cfg.get("mcp", {}) or {}
        if not mc.get("enabled"):
            return []
        servers = mc.get("servers")
        if not servers and mc.get("url"):   # legacy single-server config (wizard)
            servers = [{"name": "mcp", "url": mc["url"], "prefix": mc.get("prefix", "")}]
        out = []
        for i, s in enumerate(servers or []):
            url = (s.get("url") or "").strip()
            if url:
                out.append({"name": s.get("name") or f"mcp{i + 1}",
                            "url": url, "prefix": s.get("prefix", "")})
        return out

    def load_mcp_tools(self):
        """Connect to each configured MCP server and merge its tools (name-prefixed).
        Returns [(server_name, tool_count, prefix, url)] so each frontend renders
        its own summary. Blocking — call from a worker thread."""
        self.mcp_tools, self.mcp_route = [], {}
        results = []
        for srv in self.mcp_servers:
            raw, _ = mcp_client.list_tools_openai(srv["url"])
            cnt = 0
            for t in raw:
                orig = t["function"]["name"]
                pn = srv["prefix"] + orig
                t = {**t, "function": {**t["function"], "name": pn}}
                self.mcp_tools.append(t)
                self.mcp_route[pn] = (srv["url"], orig)
                cnt += 1
            results.append((srv["name"], cnt, srv["prefix"], srv["url"]))
        self.mcp_names = set(self.mcp_route)
        return results

    # ---------- permissions ----------
    def _permit(self, tool, snippet, code, scope, force=False):
        """Gate a flagged operation. Honors the global skip, the persistent
        allow/deny lists in config, then asks the frontend hook. 'allow_always'
        persists `tool` to the allowlist. Returns True to proceed.
        `force=True` (always_ask_tools) disables the skip/allowlist shortcuts so
        the hook is consulted on every call; the deny list still wins."""
        perms = self.cfg.setdefault("permissions", {})
        if tool in (perms.get("deny") or []):
            return False
        if self.skip_permissions and not force:
            return True
        if tool in (perms.get("allow") or []) and not force:
            return True
        if self.ask_permission is None:
            return False   # no one to ask → deny (headless safe default)
        req = ev.PermissionRequired(perm_id=uuid.uuid4().hex[:8], tool=tool,
                                    action=snippet, detail=code, scope=scope)
        decision = self.ask_permission(req) or "deny"
        if decision == "allow_always":
            allow = perms.setdefault("allow", [])
            if tool not in allow:
                allow.append(tool)
                save_config(self.cfg)
            return True
        return decision == "allow_once"

    def workspace(self):
        """Root writes are confined to. An explicit permissions.workspace still
        wins (it is a user-level confinement, not a default), otherwise it is
        this session's working directory."""
        ws = (self.cfg.get("permissions", {}) or {}).get("workspace") or ""
        return os.path.abspath(ws) if ws else self.cwd

    def abspath(self, path):
        """A tool path argument as the tools will resolve it: relative to this
        session's cwd, not the process's."""
        p = os.path.expanduser(str(path))
        return os.path.abspath(p if os.path.isabs(p) else os.path.join(self.cwd, p))

    def _in_workspace(self, path):
        """True if `path` resolves inside the workspace root (writes are confined there)."""
        try:
            root = self.workspace()
            return os.path.commonpath([root, self.abspath(path)]) == root
        except (ValueError, OSError):
            return False   # different drive / bad path → treat as outside

    # ---------- tool execution ----------
    def _with_timeout(self, name, fn):
        """Run a (blocking) tool call with a hard ceiling so a hung tool or an
        unreachable MCP server can't freeze the turn. The work runs in a daemon
        thread; on timeout we return an ERROR and move on (it keeps running in the
        background but won't block the caller or process exit)."""
        box = {}

        def run():
            try:
                box["v"] = fn()
            except Exception as e:
                box["v"] = f"ERROR: {type(e).__name__}: {e}"

        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(TOOL_TIMEOUT)
        if t.is_alive():
            return (f"ERROR: tool '{name}' timed out after {TOOL_TIMEOUT}s "
                    f"(it may still be running in the background; the turn continued)")
        return box.get("v", "ERROR: tool produced no result")

    def execute_tool(self, name, raw_args):
        """Runs a tool; for run_python/run_powershell with a dangerous op it asks
        for permission (unless skip_permissions) and, if approved, runs with
        allow_unsafe. Every actual call is bounded by TOOL_TIMEOUT (the permission
        prompt is not)."""
        if name in self.blocked_tools:
            return (f"ERROR: tool '{name}' is disabled on this interface by policy "
                    f"(it cannot be allowed from here)")
        denied_tools = (self.cfg.get("permissions", {}) or {}).get("deny") or []
        if name in denied_tools:
            return f"DENIED by policy: tool '{name}' is explicitly denied"
        if name == "skill":   # instructions only — no permission gating needed
            try:
                skill_args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
            except Exception:
                skill_args = {}
            return skills_mod.load_skill(self.skills, str((skill_args or {}).get("name", "")))
        if name in self.mcp_route:   # remote tool (MCP) → run on its server
            url, orig = self.mcp_route[name]
            return self._with_timeout(name, lambda: mcp_client.call_tool(url, orig, raw_args))
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
        except Exception:
            return self._with_timeout(name, lambda: call_tool(name, raw_args, base_dir=self.cwd))
        if name in ("run_python", "run_powershell", "run_bash"):
            code = args.get("code") or args.get("command") or ""
            force = name in self.always_ask_tools
            if self.skip_permissions and not force:
                args["allow_unsafe"] = True
            else:
                snip = danger_match(code)
                if snip or force:
                    if not self._permit(name, snip or f"exec on this interface: {name}",
                                        code, scope="danger", force=force):
                        return ("DENIED by user: refused to run flagged operation "
                                f"'{snip or name}'")
                    args["allow_unsafe"] = True
        if name in ("jira_assign", "jira_comment"):
            key = args.get("key", "?")
            if name == "jira_assign":
                snip = f"assign {key} → {args.get('assignee', '?')}"
                code = f"jira issue assign {key} {args.get('assignee', '')}"
            else:
                body = (args.get("body", "") or "")
                snip = f"comment on {key}: {body[:80]}" + ("…" if len(body) > 80 else "")
                code = f"jira issue comment add {key} \"{body[:300]}\""
            if not self._permit(name, snip, code, scope="external_write",
                                force=name in self.always_ask_tools):
                return f"DENIED by user: refused Jira write '{snip}'"
        if name in ("confluence_create", "confluence_comment"):
            body = (args.get("body", "") or "")
            if name == "confluence_create":
                sp = args.get("space") or os.environ.get("CONFLUENCE_SPACE", "?")
                snip = f"create page in {sp}: {args.get('title', '')}"
                code = f"POST confluence page · space={sp} · title={args.get('title', '')}"
            else:
                snip = f"comment on page {args.get('page_id', '?')}: {body[:80]}" + ("…" if len(body) > 80 else "")
                code = f"POST confluence comment · page={args.get('page_id', '')} · {body[:200]}"
            if not self._permit(name, snip, code, scope="external_write",
                                force=name in self.always_ask_tools):
                return f"DENIED by user: refused Confluence write '{snip}'"
        if name in ("write_file", "edit_file") and isinstance(args, dict) and args.get("path"):
            path = args["path"]
            in_ws = self._in_workspace(path)   # writes are confined to the workspace
            force = name in self.always_ask_tools
            if not in_ws or force:
                snip = (f"{name} OUTSIDE the workspace" if not in_ws
                        else f"{name} on this interface")
                code = f"target: {self.abspath(path)}\nworkspace: {self.workspace()}"
                if not self._permit(name, snip, code, scope="workspace", force=force):
                    return (f"DENIED by user: '{name}' targets a path outside the workspace "
                            f"({path}). Set permissions.workspace or approve it."
                            if not in_ws else
                            f"DENIED by user: '{name}' was not confirmed on this interface.")
            return self._run_with_diff(name, args)
        return self._with_timeout(name, lambda: call_tool(name, args, base_dir=self.cwd))

    def _run_with_diff(self, name, args):
        """Run a file-writing tool, capturing a before/after unified diff for the
        tool_call_result event."""
        path = self.abspath(args.get("path"))   # as the tool will resolve it

        def _read():
            try:
                return open(path, encoding="utf-8", errors="replace").read() if os.path.isfile(path) else ""
            except Exception:
                return ""

        before = _read()
        result = self._with_timeout(name, lambda: call_tool(name, args, base_dir=self.cwd))
        if not str(result).startswith("ERROR"):
            after = _read()
            if after != before:
                self._pending_diff = (path, "".join(difflib.unified_diff(
                    before.splitlines(keepends=True), after.splitlines(keepends=True),
                    fromfile=f"{path} (before)", tofile=f"{path} (after)")))
        return result

    # ---------- cost / budget ----------
    def _account_cost(self, in_t, out_t, chunk_dict):
        """Add one API call's tokens + USD cost to the turn/session totals. Each call
        is billed its full prompt+completion; provider-reported cost (nano-gpt /
        OpenRouter) wins, else we estimate from the price table. Self-hosted = free."""
        self.session_input += in_t
        self.session_output += out_t
        if self.provider not in ("local", "remote", "vllm"):
            rep = None
            np = chunk_dict.get("x_nanogpt_pricing") or {}
            for k in ("cost", "totalCost", "totalCostUsd"):
                if isinstance(np.get(k), (int, float)):
                    rep = float(np[k]); break
            u = chunk_dict.get("usage") or {}
            if rep is None and isinstance(u.get("cost"), (int, float)):
                rep = float(u["cost"])   # OpenRouter
            if rep is None:
                rep = pricing.cost_usd(self.model, in_t, out_t, self.cfg.get("pricing"))
            c = rep or 0.0
            self._turn_cost += c
            self.session_cost += c
        self.emit(ev.CostUpdate(input_tokens=in_t, output_tokens=out_t,
                                turn_usd=self._turn_cost, session_usd=self.session_cost,
                                budget_usd=self.cfg.get("budget_usd") or None))

    def over_budget(self):
        cap = self.cfg.get("budget_usd") or 0
        return cap > 0 and self.session_cost >= cap

    # ---------- compaction ----------
    def _transcript(self, msgs):
        """Flatten messages into a plain transcript for the summarizer (bounded)."""
        lines = []
        for m in msgs:
            role = m.get("role")
            if role == "user" and m.get("content"):
                lines.append(f"USER: {m['content']}")
            elif role == "assistant":
                if m.get("content"):
                    lines.append(f"ASSISTANT: {m['content']}")
                for tc in (m.get("tool_calls") or []):
                    fn = tc.get("function", {})
                    lines.append(f"ASSISTANT called {fn.get('name')}"
                                 f"({tool_arg_summary(fn.get('name'), fn.get('arguments', ''))})")
            elif role == "tool":
                lines.append(f"TOOL RESULT: {(m.get('content') or '')[:500]}")
        txt = "\n".join(lines)
        return txt[-24000:] if len(txt) > 24000 else txt

    def enough_to_compact(self):
        real = [m for m in self.messages[1:] if m.get("role") in ("user", "assistant", "tool")]
        return len(real) >= 4

    def compact(self, auto=False):
        """Summarize older turns into a recap (keeps the system prompt). Blocking —
        call from a worker thread. Emits Compacted on success, Error on failure.
        Returns True on success."""
        try:
            instr = ("Summarize the conversation so far so it can be continued without the full "
                     "history. Preserve the user's goals and decisions, key facts, file paths and "
                     "edits, important tool results, and any open/unfinished tasks. Be concise but "
                     "complete; use bullet points. Do not invent anything not in the conversation.")
            resp = self.client.chat.completions.create(
                model=self.model, stream=False, max_tokens=1200,
                messages=[{"role": "system", "content": instr},
                          {"role": "user", "content": self._transcript(self.messages[1:])}])
            summary = (resp.choices[0].message.content or "").strip()
            if not summary:
                raise ValueError("empty summary")
            before_n = len(self.messages)
            self.messages = [self.messages[0],
                             {"role": "user", "content": "[Summary of the earlier conversation]\n" + summary}]
            self.emit(ev.Compacted(before_messages=before_n, after_messages=len(self.messages),
                                   summary=summary, auto=auto))
            self.save()
            return True
        except Exception as e:
            self.emit(ev.Error(message=f"compaction failed: {type(e).__name__}: {e}"))
            return False

    def maybe_autocompact(self):
        """Compact if the context is near full. Called inline at the start of a
        turn (inside the turn worker) so a turn is never interrupted mid-stream."""
        if not self.cfg.get("auto_compact", True) or not self.client or not self.tracker:
            return
        if not self.enough_to_compact():
            return
        try:
            used, _ = self.tracker.current(self.messages)
            limit = self.tracker.limit or 0
        except Exception:
            return
        if limit and used / limit >= AUTO_COMPACT_PCT:
            self.emit(ev.Notice(text="context is getting full — auto-compacting…"))
            self.compact(auto=True)

    # ---------- the turn loop ----------
    def _repair_history(self):
        """Keep the message list valid for strict providers (e.g. Anthropic) after an
        interrupt or when resuming a session saved mid-turn: drop assistant messages
        with neither content nor tool_calls, and give every tool_call a matching tool
        result (a placeholder if the turn was stopped before the tool ran)."""
        kept = [m for m in self.messages
                if not (m.get("role") == "assistant"
                        and not m.get("content") and not m.get("tool_calls"))]
        answered = {m.get("tool_call_id") for m in kept if m.get("role") == "tool"}
        out = []
        for m in kept:
            out.append(m)
            if m.get("role") == "assistant" and m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    if tc.get("id") and tc["id"] not in answered:
                        out.append({"role": "tool", "tool_call_id": tc["id"],
                                    "content": "[interrupted]"})
                        answered.add(tc["id"])
        self.messages = out

    def run_turn(self, text):
        """One full user turn: autocompact → stream/tool-dispatch rounds → save.
        Blocking; emits events as it goes. Returns the TurnDone reason."""
        self.maybe_autocompact()
        self.emit(ev.TurnStarted(text=text))
        self.messages.append({"role": "user", "content": text})
        self._turn_tokens = 0
        self._turn_cost = 0.0
        self._turn_start = time.time()
        self._cancel.clear()
        reason = "completed"
        try:
            max_iters = int(self.cfg.get("max_iterations", MAX_ITERATIONS) or MAX_ITERATIONS)
            for _ in range(max_iters):
                msg = self.stream_one()
                # If the model leaked the tool call into its thinking and produced
                # no real tool_call, retry the whole turn (the discarded attempt is
                # not added to history). Recover from the thinking as a last resort.
                attempt = 0
                while (self._think_leak_calls and not msg.get("tool_calls")
                       and attempt < THINK_RETRIES and not self._cancel.is_set()):
                    attempt += 1
                    self.emit(ev.Notice(kind="warning", text=(
                        f"↻ tool call ended up in the thinking; retrying turn "
                        f"({attempt}/{THINK_RETRIES})…")))
                    msg = self.stream_one()
                if self._think_leak_calls and not msg.get("tool_calls"):
                    msg["tool_calls"] = [
                        {"id": f"tk_{i}", "type": "function",
                         "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                        for i, tc in enumerate(self._think_leak_calls)]
                    self.emit(ev.Notice(kind="warning",
                                        text="↻ recovered the tool call from the thinking"))
                # An interrupt can end a segment with no content and no tool_calls;
                # don't persist that — strict providers (e.g. Anthropic) 400 on an
                # assistant message that has neither.
                if msg.get("content") or msg.get("tool_calls"):
                    self.messages.append(msg)
                if self._cancel.is_set():
                    reason = "interrupted"
                    break
                tcs = msg.get("tool_calls")
                if not tcs:
                    break
                for tc in tcs:
                    if self._cancel.is_set():
                        break
                    name, args = tc["function"]["name"], tc["function"]["arguments"]
                    self.emit(ev.ToolCallStarted(call_id=tc["id"], name=name, args=args))
                    result = self.execute_tool(name, args)
                    note, err = result_summary(result)
                    ok = not err and not str(result).startswith("DENIED")
                    diff = self._pending_diff if name in ("write_file", "edit_file") else None
                    self._pending_diff = None
                    self.emit(ev.ToolCallResult(
                        call_id=tc["id"], name=name, ok=ok, summary=note, result=result,
                        diff=diff[1] if diff else None, path=diff[0] if diff else None))
                    self.messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
                if self._cancel.is_set():
                    reason = "interrupted"
                    break
            else:
                reason = "max_iterations"
        except Exception as e:
            msg = str(e)
            if "parse tool call" in msg.lower() or "failed to parse" in msg.lower():
                short = ("the model emitted invalid JSON for the tool arguments (common with small "
                         "models on big code blocks). Try a smaller step or rephrase the request.")
            else:
                short = msg if len(msg) <= 280 else msg[:280] + " …"
            self.emit(ev.Error(message=f"{type(e).__name__}: {short}"))
            reason = "error"
        finally:
            elapsed = time.time() - self._turn_start
            tps = self._turn_tokens / elapsed if elapsed > 0 else 0
            self.emit(ev.TurnDone(reason=reason, elapsed_s=elapsed,
                                  output_tokens=self._turn_tokens, tok_s=tps,
                                  turn_usd=self._turn_cost))
            if self.over_budget():
                self.emit(ev.Notice(kind="warning", text=(
                    f"⚠ budget reached — session {pricing.fmt_usd(self.session_cost)} ≥ "
                    f"{pricing.fmt_usd(self.cfg.get('budget_usd'))}. New turns are blocked "
                    f"until you raise budget_usd or clear the conversation.")))
            self.save()   # persist the conversation after every turn
        return reason

    def stream_one(self):
        """One streamed model request. Emits thinking/text deltas and SegmentEnd;
        accumulates tool-call deltas; accounts usage/cost. Returns the assistant
        message as a dict."""
        self._repair_history()
        params = dict(model=self.model, messages=self.messages,
                      tools=TOOLS + self.mcp_tools + self._skill_tools,
                      tool_choice="auto", stream=True)
        params["stream_options"] = {"include_usage": True}
        if self.is_local:
            params["extra_body"] = {"chat_template_kwargs": {"enable_thinking": self.enable_thinking}}
        elif self.provider == "nano":
            params["extra_body"] = {"reasoning": {"enabled": True}, "include_reasoning": True}
        elif self.provider == "gemini":
            # Gemini 2.5 "thinks". Map a reasoning toggle to a thinking budget and ask for
            # the thought summaries (they stream as content flagged extra_content.google.thought).
            eff = str((self.cfg.get("gemini", {}) or {}).get("reasoning_effort", "low")).lower()
            budget = {"off": 0, "none": 0, "low": 512, "medium": 4096, "high": -1}.get(eff, 512)
            tc = {"thinking_budget": budget}
            if budget != 0:
                tc["include_thoughts"] = True
            params["extra_body"] = {"extra_body": {"google": {"thinking_config": tc}}}
        # xai / openrouter / openai / anthropic / remote: standard OpenAI-compatible,
        # without proprietary extra_body (Anthropic via its OpenAI-compat layer)
        stream = self.client.chat.completions.create(**params)
        content_parts, tool_calls = [], {}
        splitter = ThinkSplitter()
        self.cur_think = self.cur_content = ""
        last_pricing = None

        def emit_delta(mode, t):
            if not t:
                return
            if mode == "thinking":
                self.cur_think += t
                self.emit(ev.ThinkingDelta(text=t))
            else:
                self.cur_content += t
                content_parts.append(t)
                self.emit(ev.TextDelta(text=t))

        for chunk in stream:
            if self._cancel.is_set():
                try:
                    stream.close()
                except Exception:
                    pass
                break
            cd = chunk.model_dump(exclude_none=True)
            if "x_nanogpt_pricing" in cd or cd.get("usage"):
                last_pricing = cd
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            rc = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
            if not rc and getattr(delta, "model_extra", None):
                rc = delta.model_extra.get("reasoning_content") or delta.model_extra.get("reasoning")
            if rc:
                emit_delta("thinking", rc)
            if delta.content:
                # Gemini streams its thought summaries as content tagged
                # extra_content.google.thought — route those to the thinking block.
                ddict = (cd.get("choices") or [{}])[0].get("delta", {})
                is_thought = bool((ddict.get("extra_content") or {}).get("google", {}).get("thought"))
                if is_thought:
                    emit_delta("thinking", delta.content.replace("<thought>", "").replace("</thought>", ""))
                else:
                    for mode, t in splitter.feed(delta.content):
                        emit_delta(mode, t)
            if delta.tool_calls:
                for tcd in delta.tool_calls:
                    slot = tool_calls.setdefault(tcd.index, {"id": "", "name": "", "arguments": ""})
                    if tcd.id:
                        slot["id"] = tcd.id
                    if tcd.function:
                        if tcd.function.name:
                            slot["name"] += tcd.function.name
                        if tcd.function.arguments:
                            slot["arguments"] += tcd.function.arguments
        for mode, t in splitter.flush():
            emit_delta(mode, t)
        in_t = out_t = 0
        if last_pricing:
            self.tracker.update_from_chunk_dict(last_pricing)
            in_t, out_t = self.tracker.last_input or 0, self.tracker.last_output or 0
        if out_t == 0:   # provider returned no exact usage → estimate locally (tiktoken)
            in_t = in_t or context.estimate_messages(self.messages)
            out_t = context._ntok(self.cur_think + self.cur_content)
        self._turn_tokens += out_t
        self._account_cost(in_t, out_t, last_pricing or {})

        # fallback: the model emitted the tool-call as plain text -> recover it
        if not tool_calls:
            fb = parse_text_tool_calls("".join(content_parts))
            if fb:
                for i, tc in enumerate(fb):
                    tool_calls[i] = {"id": f"fb_{i}", "name": tc["name"], "arguments": tc["arguments"]}
                cleaned = strip_tool_call_text(self.cur_content)
                self.cur_content = cleaned
                content_parts[:] = [cleaned] if cleaned else []
                self.emit(ev.Notice(kind="warning",
                                    text="↻ recovered a tool call the model emitted as plain text"))

        # detect a tool call leaked into the THINKING (qwen sometimes stops there
        # and emits the call as part of its reasoning instead of a real tool_call)
        self._think_leak_calls = parse_text_tool_calls(self.cur_think) if not tool_calls else []

        self.emit(ev.SegmentEnd(content=self.cur_content, thinking=self.cur_think))

        msg = {"role": "assistant", "content": "".join(content_parts) or None}
        if tool_calls:
            msg["tool_calls"] = [{"id": tc["id"], "type": "function",
                                  "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                                 for _, tc in sorted(tool_calls.items())]
        return msg
