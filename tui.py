"""Cool TUI for the agent — startup wizard + nano-gpt / local llama.cpp.

Startup:
  1) Choose provider (nano-gpt cloud / local llama.cpp).
  2) Choose a model from a list (nano: 600+ via API with filter; local: registry).
  3) If local and the server is not up, the TUI starts it itself.

Interface:
  - Main panel: streaming thinking + detailed tool-calling with colors.
  - Right sidebar (local only): live GPU/VRAM/CPU/RAM graphs + temp/power/tok-s.

Optional flags (skip the wizard):
  python tui.py                      -> full wizard
  python tui.py --provider local     -> wizard from the model list
  python tui.py --provider local --model qwen35-9b
  python tui.py --provider nano --model deepseek-ai/DeepSeek-R1
"""

import argparse
import json
import os
import sys
import threading
import time
from collections import deque
from datetime import datetime

from openai import OpenAI
from rich.console import Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen, Screen
from textual.widgets import (Button, Footer, Input, LoadingIndicator, OptionList,
                             RichLog, Sparkline, Static, TextArea)
from textual.widgets.option_list import Option

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "bench"))

import serve  # noqa: E402  (bench/serve.py)
import models as registry  # noqa: E402  (bench/models.py)
from context import ContextTracker  # noqa: E402  (demo mode only)
import pricing  # noqa: E402  (USD formatting for the status bar)

from engine import protocol, telemetry  # noqa: E402
from engine.config import (CONFIG_PATH, DEFAULT_CONFIG, CLOUD, URL_PROVIDERS,  # noqa: E402
                           PROVIDER_ENV, load_config, save_config, resolve_key)
from engine.session import Session, tool_arg_summary  # noqa: E402
from engine.store import (list_sessions, load_session, delete_session,  # noqa: E402
                          sessions_dir as _sessions_dir)


# ----------------------------- render utilities -----------------------------

def bar(pct, width=18, color="green"):
    pct = max(0.0, min(100.0, pct))
    filled = int(round(width * pct / 100))
    t = Text()
    t.append("█" * filled, style=color)
    t.append("░" * (width - filled), style="grey37")
    t.append(f" {pct:4.0f}%", style="bold " + color)
    return t


def util_color(p):
    return "green" if p < 60 else ("yellow" if p < 85 else "red")


# --------------------------------- screens ---------------------------------

class ProviderScreen(Screen):
    CSS = """
    ProviderScreen { align: center middle; }
    #box { width: 64; height: auto; border: round #5f5fd7; padding: 1 2; }
    #title { text-style: bold; color: #d787ff; margin-bottom: 1; }
    OptionList { height: auto; }
    """

    PROVIDERS = [
        ("nano", "☁  nano-gpt    (cloud · 600+ models)"),
        ("local", "🖥  local       (llama.cpp on your GPU)"),
        ("remote", "🌐  llama.cpp   (remote · enter a URL)"),
        ("vllm", "🚀  vLLM        (OpenAI server · enter a URL)"),
        ("openai", "⚙  OpenAI      (GPT · cloud)"),
        ("anthropic", "✺  Anthropic   (Claude · cloud)"),
        ("gemini", "♊  Gemini      (Google · cloud)"),
        ("xai", "✦  xAI         (Grok · cloud)"),
        ("openrouter", "🔀  OpenRouter  (cloud · many models)"),
    ]
    BINDINGS = [("escape", "quit", "Quit"), ("ctrl+p", "prompt", "📝 Prompt")]

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Static("🤖  What do you want to run with?", id="title")
            yield OptionList(*[Option(lbl, id=pid) for pid, lbl in self.PROVIDERS], id="prov")
            yield Static("", id="phint")
        yield Footer()

    def on_mount(self):
        ol = self.query_one("#prov", OptionList)
        ol.focus()
        last = self.app.cfg.get("last", {})
        ids = [p for p, _ in self.PROVIDERS]
        if last.get("provider") in ids:
            ol.highlighted = ids.index(last["provider"])
            self.query_one("#phint", Static).update(Text.from_markup(
                f"[dim]last used → [cyan]{last['provider']}[/] · {last.get('model') or '?'}[/]"))

    def action_prompt(self):
        self.app.push_screen(SystemPromptScreen(wizard=True))

    def on_option_list_option_selected(self, e: OptionList.OptionSelected):
        pid = e.option.id
        self.app.provider = pid
        if pid in URL_PROVIDERS:
            self.app.push_screen(RemoteUrlScreen(pid))
        elif pid in CLOUD and not resolve_key(pid, self.app.cfg.get(pid, {})):
            self.app.push_screen(ApiKeyScreen(pid))
        else:
            self.app.push_screen(ModelScreen(pid))


class ApiKeyScreen(Screen):
    """Asks for a cloud provider's API key when missing and saves it to the config."""

    CSS = """
    ApiKeyScreen { align: center middle; }
    #kbox { width: 78; height: auto; border: round #d7af00; padding: 1 2; }
    #ktitle { text-style: bold; color: #ffd700; }
    #kinfo { margin: 1 0; color: #999999; }
    #key_in { margin-bottom: 1; }
    """
    BINDINGS = [("escape", "back", "Back")]

    LABELS = {"xai": "xAI", "openai": "OpenAI", "anthropic": "Anthropic",
              "openrouter": "OpenRouter", "nano": "nano-gpt", "gemini": "Google Gemini"}

    def __init__(self, provider):
        super().__init__()
        self.provider = provider

    def action_back(self):
        self.app.pop_screen()

    def compose(self) -> ComposeResult:
        name = self.LABELS.get(self.provider, self.provider)
        with Vertical(id="kbox"):
            yield Static(f"🔑  {name} API key", id="ktitle")
            yield Static(Text.from_markup(
                f"No API key found for [b]{self.provider}[/]. Paste it below — it will be "
                f"saved to [dim]tui_config.json[/] for next time."), id="kinfo")
            yield Input(placeholder="paste API key…", password=True, id="key_in")
            yield Static(Text.from_markup("[dim]Enter to continue · Esc back[/]"), id="khint")

    def on_mount(self):
        self.query_one("#key_in", Input).focus()

    def on_input_submitted(self, e: Input.Submitted):
        key = (e.value or "").strip()
        if not key:
            self.query_one("#kinfo", Static).update(Text("Please paste a key.", style="red"))
            return
        self.app.cfg[self.provider]["api_key"] = key
        save_config(self.app.cfg)
        self.app.push_screen(ModelScreen(self.provider))


class RemoteUrlScreen(Screen):
    """Asks for the URL of an OpenAI-compatible server (remote llama.cpp or vLLM)
    and then lists its models."""

    CSS = """
    RemoteUrlScreen { align: center middle; }
    #rbox { width: 78; height: auto; border: round #00afaf; padding: 1 2; }
    #rtitle { text-style: bold; color: #00d7ff; }
    #rinfo { margin: 1 0; color: #999999; }
    #url_in { margin-bottom: 1; }
    """
    BINDINGS = [("escape", "back", "Back")]

    META = {
        "remote": ("🌐  Remote llama.cpp server",
                   "e.g. http://192.168.1.50:8080  →  /v1 is added automatically"),
        "vllm": ("🚀  vLLM server (OpenAI-compatible)",
                 "e.g. http://gpu-host:8000  (the default vLLM port)  →  /v1 is added automatically"),
    }

    def __init__(self, provider="remote"):
        super().__init__()
        self.provider = provider

    def action_back(self):
        self.app.pop_screen()

    def compose(self) -> ComposeResult:
        title, example = self.META.get(self.provider, self.META["remote"])
        with Vertical(id="rbox"):
            yield Static(title, id="rtitle")
            yield Static(Text.from_markup(
                f"Enter the server URL (host:port or full /v1 URL).\n[dim]{example}[/]"), id="rinfo")
            yield Input(value=self.app.cfg.get(self.provider, {}).get("base_url", "")
                        or "http://", id="url_in")
            yield Static(Text.from_markup("[dim]Enter to list models · Esc back[/]"), id="rhint")

    def on_mount(self):
        self.query_one("#url_in", Input).focus()

    @staticmethod
    def _normalize(raw):
        u = (raw or "").strip().rstrip("/")
        if not u:
            return ""
        if not u.startswith(("http://", "https://")):
            u = "http://" + u
        if not u.endswith("/v1"):
            u = u + "/v1"
        return u

    def on_input_submitted(self, e: Input.Submitted):
        url = self._normalize(e.value)
        if not url:
            self.query_one("#rinfo", Static).update(Text("Please enter a URL.", style="red"))
            return
        self.app.cfg.setdefault(self.provider, {})["base_url"] = url
        save_config(self.app.cfg)
        self.app.push_screen(ModelScreen(self.provider))


class ModelScreen(Screen):
    CSS = """
    ModelScreen { align: center middle; }
    #box { width: 92; height: 80%; border: round #00afaf; padding: 1 2; }
    #mtitle { text-style: bold; color: #00d7ff; }
    #filter { margin: 1 0; }
    OptionList { height: 1fr; }
    #status { color: #999999; }
    """

    BINDINGS = [("escape", "back", "Back"), ("f", "fav", "Favorite")]

    def __init__(self, provider, swap=False):
        super().__init__()
        self.provider = provider
        self.swap = swap               # True → switch model mid-session (keep history)
        self.names = []                # model names/aliases
        self.label_of = {}             # name -> label to display

    def _pick(self, model_value):
        if self.swap:
            self.app.swap_model(model_value)
        else:
            self.app.choose_model(model_value)

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Static(f"Choose a model · [b]{self.provider}[/]", id="mtitle")
            yield Input(placeholder="filter… (type to search)", id="filter")
            yield OptionList(id="models")
            yield Static("", id="status")
            yield Static(Text.from_markup(
                "[dim]Esc back · ↓ enter list · f favorite · Enter select[/]"), id="mhint")

    def on_mount(self):
        if self.provider == "local":
            ms = registry.all_models()
            self.names = [m["alias"] for m in ms]
            self.label_of = {m["alias"]: f"{m['alias']}   [dim]{os.path.basename(m['path'])}[/]" for m in ms}
            self.rebuild()
            running = self.app.local_running()
            self.query_one("#status", Static).update(Text.from_markup(
                f"{len(self.names)} local models · server "
                + ("[green]running[/]" if running else "[yellow]stopped (started on select)[/]")))
            self.query_one("#filter", Input).focus()
        else:
            self.query_one("#status", Static).update(Text(f"loading {self.provider} models…", style="yellow"))
            self.run_worker(self.fetch_cloud, thread=True)

    def fetch_cloud(self):
        cfg = self.app.cfg[self.provider]
        fallback = cfg.get("models") or []
        try:
            if self.provider == "anthropic":
                ids = self._anthropic_models(cfg)      # native endpoint, own headers
            else:
                cli = OpenAI(base_url=cfg["base_url"], api_key=resolve_key(self.provider, cfg) or "x")
                ids = sorted(m.id for m in cli.models.list().data)
            if not ids:
                raise ValueError("empty model list")
            self.app.call_from_thread(self.set_cloud, ids)
        except Exception as e:
            if fallback:
                # Live listing failed — fall back to the curated config list, but say
                # so (a silent fallback hides a bad/missing key behind a stale list).
                reason = str(e).splitlines()[0][:70] if str(e) else type(e).__name__
                self.app.call_from_thread(self.set_cloud, list(fallback),
                                          f"⚠ live list failed ({reason}) · showing saved list")
                return
            hint = (f"set an api_key for '{self.provider}' (config or {PROVIDER_ENV.get(self.provider, 'env')})"
                    if not resolve_key(self.provider, cfg) else str(e))
            self.app.call_from_thread(lambda: self.query_one("#status", Static).update(
                Text(f"error: {hint}", style="red")))

    def set_cloud(self, ids, note=None):
        self.names = ids
        self.label_of = {i: i for i in ids}
        self.rebuild()
        self.query_one("#status", Static).update(
            Text(note, style="yellow") if note else Text(f"{len(ids)} models · filter, ↓, Enter"))
        self.query_one("#filter", Input).focus()

    def _anthropic_models(self, cfg):
        """Anthropic's OpenAI-compatible endpoint doesn't serve /models with bearer
        auth, so query the native endpoint with its own headers. Returns ids newest
        first (the API's order); the curated config list stays as the fallback."""
        import requests
        base = cfg["base_url"].rstrip("/")
        r = requests.get(
            f"{base}/models",
            headers={"x-api-key": resolve_key("anthropic", cfg), "anthropic-version": "2023-06-01"},
            params={"limit": 1000}, timeout=15,
        )
        r.raise_for_status()
        return [m["id"] for m in r.json().get("data", [])]

    def _favs(self):
        return self.app.cfg.setdefault("favorites", {}).setdefault(self.provider, [])

    def rebuild(self, filt=""):
        favs = set(self._favs())
        last = self.app.cfg.get("last", {})
        last_model = last.get("model") if last.get("provider") == self.provider else None
        names = [n for n in self.names if filt.lower() in n.lower()] if filt else list(self.names)
        ordered = [n for n in names if n in favs] + [n for n in names if n not in favs]
        ol = self.query_one("#models", OptionList)
        ol.clear_options()
        hi = None
        for i, n in enumerate(ordered[:400]):
            star = "★ " if n in favs else "  "
            ol.add_option(Option(star + self.label_of.get(n, n), id=n))
            if n == last_model and hi is None:
                hi = i
        if ol.option_count:
            ol.highlighted = hi if hi is not None else 0

    def on_input_changed(self, e: Input.Changed):
        self.rebuild(e.value)

    def on_key(self, event):
        if event.key == "down" and isinstance(self.app.focused, Input):
            ol = self.query_one("#models", OptionList)
            if ol.option_count:
                ol.focus()
            event.stop()

    def on_input_submitted(self, e: Input.Submitted):
        ol = self.query_one("#models", OptionList)
        if ol.option_count:
            idx = ol.highlighted if ol.highlighted is not None else 0
            self._pick(ol.get_option_at_index(idx).id)

    def action_back(self):
        self.app.pop_screen()

    def action_fav(self):
        ol = self.query_one("#models", OptionList)
        if ol.highlighted is None:
            return
        name = ol.get_option_at_index(ol.highlighted).id
        favs = self._favs()
        if name in favs:
            favs.remove(name)
        else:
            favs.append(name)
        save_config(self.app.cfg)
        self.rebuild(self.query_one("#filter", Input).value)

    def on_option_list_option_selected(self, e: OptionList.OptionSelected):
        self._pick(e.option.id)


class LoadingScreen(Screen):
    CSS = """
    LoadingScreen { align: center middle; }
    #lbox { width: 64; height: auto; border: round #ffd700; padding: 2 4; }
    #lmsg { text-style: bold; color: #ffd700; }
    """

    def __init__(self, msg):
        super().__init__()
        self.msg = msg

    def compose(self) -> ComposeResult:
        with Vertical(id="lbox"):
            yield Static(self.msg, id="lmsg")
            yield LoadingIndicator()
            yield Static("", id="lerr")


class PermissionScreen(ModalScreen):
    """Asks for permission (Claude Code style) when a tool wants to run something
    flagged. Dismisses with 'once' / 'always' / 'deny'. 'Always' persists THIS tool
    to the config allowlist (not a global skip)."""

    CSS = """
    PermissionScreen { align: center middle; background: $background 60%; }
    #pbox { width: 100; height: auto; max-height: 80%; border: thick #ff5f5f; background: $surface; padding: 1 2; }
    #ptitle { text-style: bold; color: #ff5f5f; }
    #pwhat { margin: 1 0; }
    #pcode { max-height: 16; border: round #44475a; padding: 0 1; color: #d7d7af; }
    #pbtns { height: auto; margin-top: 1; align: center middle; }
    Button { margin: 0 1; }
    """
    BINDINGS = [("y", "allow", "Allow"), ("a", "always", "Always"),
                ("n", "deny", "Deny"), ("escape", "deny", "Deny")]

    def __init__(self, tool, snippet, code):
        super().__init__()
        self.tool = tool
        self.snippet = snippet
        self.code = code

    def compose(self) -> ComposeResult:
        with Vertical(id="pbox"):
            yield Static("⚠  Permission required", id="ptitle")
            yield Static(Text.from_markup(
                f"Tool [bold yellow]{self.tool}[/] wants to run a flagged operation: "
                f"[bold red]{self.snippet}[/]\nReview the command below."), id="pwhat")
            yield Static(self.code[:1500], id="pcode")
            with Horizontal(id="pbtns"):
                yield Button("Allow once (y)", id="allow", variant="success")
                yield Button(f"Always allow {self.tool} (a)", id="always", variant="warning")
                yield Button("Deny (n)", id="deny", variant="error")

    def on_button_pressed(self, e: Button.Pressed):
        {"allow": self.action_allow, "always": self.action_always,
         "deny": self.action_deny}[e.button.id]()

    def action_allow(self):
        self.dismiss("once")

    def action_always(self):
        self.dismiss("always")

    def action_deny(self):
        self.dismiss("deny")


class CtxScreen(Screen):
    """Confirm/override of the optimal ctx-size (local) before starting the model."""

    CSS = """
    CtxScreen { align: center middle; }
    #cbox { width: 78; height: auto; border: round #00afaf; padding: 1 2; }
    #ctitle { text-style: bold; color: #00d7ff; }
    #cinfo { margin: 1 0; color: #999999; }
    #ctx_in { margin-bottom: 1; }
    """

    BINDINGS = [("escape", "back", "Back")]

    def __init__(self, model, optimal, info):
        super().__init__()
        self.model = model
        self.optimal = optimal
        self.info = info

    def action_back(self):
        self.app.pop_screen()

    def compose(self) -> ComposeResult:
        with Vertical(id="cbox"):
            yield Static(f"Context size for [b]{self.model['alias']}[/]", id="ctitle")
            yield Static(Text.from_markup(self.info), id="cinfo")
            yield Input(value=str(self.optimal), id="ctx_in")
            yield Static(Text.from_markup(
                "[dim]Enter to start with this ctx · edit the number to override[/]"))

    def on_mount(self):
        self.query_one("#ctx_in", Input).focus()

    def on_input_submitted(self, e: Input.Submitted):
        try:
            ctx = max(512, int(e.value.strip()))
        except ValueError:
            ctx = self.optimal
        self.app.start_local_with_ctx(self.model, ctx)


class UsageSidebar(Vertical):
    def compose(self) -> ComposeResult:
        yield Static("", id="gpu_name")
        yield Static("GPU util", classes="sec")
        yield Sparkline([0], summary_function=max, id="spark_gpu")
        yield Static("", id="lbl_gpu")
        yield Static("VRAM", classes="sec")
        yield Static("", id="lbl_vram")
        yield Static("CPU", classes="sec")
        yield Sparkline([0], summary_function=max, id="spark_cpu")
        yield Static("", id="lbl_cpu")
        yield Static("RAM", classes="sec")
        yield Static("", id="lbl_ram")
        yield Static("", id="lbl_extra")
        yield Static("", id="lbl_toks")


class SettingsScreen(ModalScreen):
    """Opens tui_config.json in an editor to view/edit and save it."""

    CSS = """
    SettingsScreen { align: center middle; background: $background 70%; }
    #sbox { width: 90%; height: 88%; border: thick #5f5fd7; background: $surface; padding: 1 2; }
    #stitle { text-style: bold; color: #d787ff; }
    #spath { color: #999999; height: 1; }
    #sjson { height: 1fr; border: round #44475a; }
    #serr { height: auto; color: #ffd700; }
    #sbtns { height: auto; align: center middle; margin-top: 1; }
    Button { margin: 0 1; }
    """
    BINDINGS = [("escape", "cancel", "Cancel"), ("ctrl+s", "save", "Save")]

    def compose(self) -> ComposeResult:
        with Vertical(id="sbox"):
            yield Static("⚙  Settings  —  tui_config.json", id="stitle")
            yield Static(Text.from_markup(f"[dim]{CONFIG_PATH}[/]"), id="spath")
            ta = TextArea(id="sjson", show_line_numbers=True)
            try:
                ta.text = open(CONFIG_PATH, encoding="utf-8").read()
            except Exception:
                ta.text = json.dumps(DEFAULT_CONFIG, indent=2)
            try:
                ta.language = "json"   # highlighting if tree-sitter is present; otherwise plain text
            except Exception:
                pass
            yield ta
            yield Static(Text.from_markup(
                "[dim]Edit and Ctrl+S to save · changes to the active connection apply "
                "on next launch/model pick[/]"), id="serr")
            with Horizontal(id="sbtns"):
                yield Button("Save (Ctrl+S)", id="save", variant="success")
                yield Button("Cancel (Esc)", id="cancel", variant="error")

    def on_mount(self):
        self.query_one("#sjson", TextArea).focus()

    def on_button_pressed(self, e: Button.Pressed):
        {"save": self.action_save, "cancel": self.action_cancel}[e.button.id]()

    def action_cancel(self):
        self.app.pop_screen()

    def action_save(self):
        text = self.query_one("#sjson", TextArea).text
        err = self.query_one("#serr", Static)
        try:
            json.loads(text)
        except Exception as ex:
            err.update(Text(f"✗ Invalid JSON — not saved: {ex}", style="bold red"))
            return
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
                fh.write(text)
        except Exception as ex:
            err.update(Text(f"✗ Write failed: {ex}", style="bold red"))
            return
        self.app.cfg = load_config()   # hot reload (merge with defaults)
        self.app.pop_screen()
        try:
            self.app.write_log(Panel(Text("Settings saved to tui_config.json",
                                          style="bold green"),
                                     title="[bold green]⚙ settings[/]", border_style="green"))
        except Exception:
            pass


def _cfg_get(cfg, path):
    cur = cfg
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _cfg_set(cfg, path, val):
    cur = cfg
    for k in path[:-1]:
        nxt = cur.get(k)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[k] = nxt
        cur = nxt
    cur[path[-1]] = val


class ConfigureScreen(ModalScreen):
    """Guided setup wizard: fill provider keys + Jira/Confluence/MCP, validate the
    credentials live, and save everything to tui_config.json (merging, not clobbering).
    Reachable via `python tui.py --configure`, the `/configure` command, or on first run."""

    CSS = """
    ConfigureScreen { align: center middle; }
    #cbox { width: 96; height: 90%; border: round #5f5fd7; padding: 1 2; background: $surface; }
    #ctitle { text-style: bold; color: #b394ff; }
    #cform { height: 1fr; }
    .csec { text-style: bold; color: #00d7ff; margin-top: 1; }
    .crow { height: 3; }
    .clabel { width: 34; content-align: left middle; color: #b7b7c8; }
    .crow Input { width: 1fr; }
    #cstatus { height: auto; max-height: 8; margin-top: 1; color: #9b93b8; border-top: solid #44475a; }
    #cbtns { height: 3; align: right middle; }
    #cbtns Button { margin-left: 2; }
    """
    BINDINGS = [("escape", "close", "Close"), ("ctrl+s", "save", "Save"), ("ctrl+t", "validate", "Validate")]

    # (input id, label, password, cfg path)
    SECTIONS = [
        ("Providers — API keys (leave blank to skip)", [
            ("k_nano", "nano-gpt key", True, ("nano", "api_key")),
            ("k_openai", "OpenAI key", True, ("openai", "api_key")),
            ("k_anthropic", "Anthropic key", True, ("anthropic", "api_key")),
            ("k_gemini", "Google Gemini key", True, ("gemini", "api_key")),
            ("k_xai", "xAI key", True, ("xai", "api_key")),
            ("k_openrouter", "OpenRouter key", True, ("openrouter", "api_key")),
            ("remote_url", "Remote llama.cpp base_url", False, ("remote", "base_url")),
            ("vllm_url", "vLLM base_url (e.g. http://host:8000/v1)", False, ("vllm", "base_url")),
        ]),
        ("Web search", [
            ("tavily", "Tavily API key", True, ("tavily_api_key",)),
        ]),
        ("Jira (Atlassian)", [
            ("jira_token", "API token (shared with Confluence)", True, ("jira", "api_token")),
            ("jira_server", "Server URL (e.g. https://x.atlassian.net)", False, ("jira", "server")),
            ("jira_login", "Login email", False, ("jira", "login")),
            ("jira_project", "Default project key", False, ("jira", "project")),
        ]),
        ("Confluence", [
            ("conf_base", "Base URL (…/wiki)", False, ("confluence", "base_url")),
            ("conf_email", "Login email", False, ("confluence", "email")),
            ("conf_space", "Default space key", False, ("confluence", "space")),
        ]),
        ("MCP tool server", [
            ("mcp_enabled", "Enabled (yes/no)", False, ("mcp", "enabled")),
            ("mcp_url", "URL", False, ("mcp", "url")),
            ("mcp_prefix", "Tool prefix", False, ("mcp", "prefix")),
        ]),
        ("Behavior", [
            ("gem_effort", "Gemini reasoning (off/low/medium/high)", False, ("gemini", "reasoning_effort")),
            ("max_iters", "Max tool iterations per turn", False, ("max_iterations",)),
            ("budget_usd", "Session budget USD (0 = off)", False, ("budget_usd",)),
            ("workspace", "Workspace dir (write/edit confined; blank = launch dir)", False, ("permissions", "workspace")),
        ]),
    ]

    def __init__(self, startup=False):
        super().__init__()
        self.startup = startup
        self._fields = [f for _, fs in self.SECTIONS for f in fs]

    def compose(self) -> ComposeResult:
        import installers
        with Vertical(id="cbox"):
            yield Static("🛠  Configure raiko", id="ctitle")
            with VerticalScroll(id="cform"):
                # ---- optional dependency downloads ----
                yield Static("Optional downloads — click to fetch (skip = do nothing)", classes="csec")
                yield Static(Text.from_markup(f"[dim]detected: {installers.describe()}[/]"), id="cdetect")
                with Horizontal(classes="crow"):
                    yield Static("Jira CLI", classes="clabel")
                    yield Button("Download Jira CLI", id="dl_clis", variant="primary")
                with Horizontal(classes="crow"):
                    yield Static("llama-server (local GPU)", classes="clabel")
                    yield Button("Download for my machine", id="dl_llama", variant="primary")
                # llama.cpp models folder -> models.json `models_base` (walked for .gguf)
                try:
                    import models as _m
                    _models_base = (_m._load_config() or {}).get("models_base", "")
                except Exception:
                    _models_base = ""
                with Horizontal(classes="crow"):
                    yield Static("llama.cpp models directory", classes="clabel")
                    yield Input(value=_models_base, id="llama_models_dir",
                                placeholder="folder with your .gguf models")
                # ---- config fields ----
                for header, fields in self.SECTIONS:
                    yield Static(header, classes="csec")
                    for fid, label, pw, path in fields:
                        cur = _cfg_get(self.app.cfg, path)
                        if isinstance(cur, bool):
                            cur = "yes" if cur else "no"
                        with Horizontal(classes="crow"):
                            yield Static(label, classes="clabel")
                            yield Input(value="" if cur is None else str(cur),
                                        password=pw, id=fid)
            yield Static(Text.from_markup("[dim]Ctrl+S save · Ctrl+T validate · Esc close[/]"), id="cstatus")
            with Horizontal(id="cbtns"):
                yield Button("Validate", id="validate", variant="primary")
                yield Button("Save", id="save", variant="success")
                yield Button("Close", id="close", variant="error")

    def _collect(self):
        """Return a dict {path: value} from the current input boxes (typed)."""
        out = {}
        for fid, label, pw, path in self._fields:
            val = self.query_one(f"#{fid}", Input).value.strip()
            if path == ("mcp", "enabled"):
                out[path] = val.lower() in ("yes", "y", "true", "1", "on")
            else:
                out[path] = val
        return out

    def action_close(self):
        self.app.pop_screen()
        if self.startup:
            self.app._route_startup()

    def on_button_pressed(self, e: Button.Pressed):
        {"validate": self.action_validate, "save": self.action_save,
         "close": self.action_close, "dl_clis": self.action_dl_clis,
         "dl_llama": self.action_dl_llama}[e.button.id]()

    def action_dl_clis(self):
        self._status("[cyan]Downloading Jira CLI… (this can take a minute)[/]")
        self.run_worker(self._dl_clis_worker, thread=True)

    def _dl_clis_worker(self):
        import installers
        log = lambda m: self.app.call_from_thread(self._status, f"[dim]{m}[/]")
        out = []
        for name, fn in [("jira", installers.install_jira_cli)]:
            try:
                path, msg = fn(log=log)
            except Exception as ex:
                path, msg = None, f"{name}: {type(ex).__name__}: {ex}"
            out.append(("[green]✓[/] " if path else "[red]✗[/] ") + msg)
            if path and name == "jira":
                _cfg_set(self.app.cfg, ("jira", "cli_path"), path)
                save_config(self.app.cfg)
                os.environ["JIRA_CLI"] = path
        self.app.call_from_thread(self._status, "  ".join(out))

    def action_dl_llama(self):
        self._status("[cyan]Downloading llama-server for your machine… (large, please wait)[/]")
        self.run_worker(self._dl_llama_worker, thread=True)

    def _dl_llama_worker(self):
        import installers
        log = lambda m: self.app.call_from_thread(self._status, f"[dim]{m}[/]")
        try:
            path, msg = installers.install_llama_server(log=log)
        except Exception as ex:
            path, msg = None, f"{type(ex).__name__}: {ex}"
        if path:
            try:
                installers.set_llama_path_in_models_json(path)
            except Exception:
                pass
        self.app.call_from_thread(self._status, ("[green]✓[/] " if path else "[red]✗[/] ") + msg)

    def _status(self, markup):
        try:
            self.query_one("#cstatus", Static).update(Text.from_markup(markup))
        except Exception:
            pass

    def action_save(self):
        values = self._collect()
        for path, val in values.items():
            if path == ("mcp", "enabled"):
                _cfg_set(self.app.cfg, path, val)   # real yes/no toggle
                continue
            # a blank box NEVER clears an existing value — only non-empty input updates
            if val == "":
                continue
            if path == ("max_iterations",):
                try:
                    val = max(1, int(val))
                except ValueError:
                    continue
            elif path == ("budget_usd",):
                try:
                    val = max(0.0, float(val))
                except ValueError:
                    continue
            _cfg_set(self.app.cfg, path, val)
        ok = save_config(self.app.cfg)
        self.app.cfg = load_config()   # re-export env (tokens, confluence, tavily)
        if not ok:
            self._status(f"[red]✗ Could not write {CONFIG_PATH}[/]")
            return
        # llama.cpp models folder lives in models.json (read by bench/models.py), not
        # tui_config.json — persist it there when set.
        mdir = self.query_one("#llama_models_dir", Input).value.strip()
        if mdir:
            try:
                import installers
                installers.set_models_base_in_models_json(mdir)
            except Exception:
                pass
        self._status(f"[green]✓ Saved to {CONFIG_PATH}.[/] Running jira init / checks…")
        self.run_worker(self._post_save, thread=True)

    def _post_save(self):
        msgs = [f"[green]✓ Saved to {CONFIG_PATH}[/]"]
        j = self.app.cfg.get("jira", {})
        if j.get("api_token") and j.get("server") and j.get("login") and j.get("project"):
            import shutil as _sh
            jbin = os.environ.get("JIRA_CLI") or _sh.which("jira") or r"C:\utils\jira\bin\jira.exe"
            if os.path.isfile(jbin) or _sh.which(jbin):
                try:
                    import subprocess
                    p = subprocess.run([jbin, "init", "--installation", "cloud",
                                        "--server", j["server"], "--login", j["login"],
                                        "--project", j["project"], "--auth-type", "basic", "--force"],
                                       capture_output=True, text=True, encoding="utf-8",
                                       errors="replace", timeout=60, stdin=subprocess.DEVNULL)
                    ok = "configuration generated" in (p.stdout + p.stderr).lower()
                    msgs.append("[green]✓ jira init done[/]" if ok
                                else "[yellow]jira init needs a board — run it once manually[/]")
                except Exception as e:
                    msgs.append(f"[yellow]jira init skipped: {type(e).__name__}[/]")
            else:
                msgs.append("[yellow]jira CLI not found (install it / set JIRA_CLI)[/]")
        self.app.call_from_thread(self._status, "  ·  ".join(msgs))

    def action_validate(self):
        self._status("[cyan]Validating credentials…[/]")
        values = self._collect()
        self.run_worker(lambda: self._validate_worker(values), thread=True)

    def _validate_worker(self, values):
        lines = []
        # cloud provider keys → models.list()
        for prov, fid in [("nano", "k_nano"), ("openai", "k_openai"), ("anthropic", "k_anthropic"),
                          ("gemini", "k_gemini"), ("xai", "k_xai"), ("openrouter", "k_openrouter")]:
            key = values.get((prov, "api_key"), "")
            if not key:
                continue
            base = self.app.cfg.get(prov, {}).get("base_url", "")
            try:
                OpenAI(base_url=base, api_key=key).models.list()
                lines.append(f"[green]✓[/] {prov}")
            except Exception as e:
                lines.append(f"[red]✗[/] {prov} ({type(e).__name__})")
        # jira token via `jira me`
        token = values.get(("jira", "api_token"), "")
        if token:
            os.environ["JIRA_API_TOKEN"] = token
            try:
                import tools
                ok, out = tools._run_jira(["me"])
                lines.append(f"[green]✓[/] jira ({out.strip().splitlines()[-1][:40]})" if ok and "@" in out
                             else "[yellow]?[/] jira token set (run jira init to use it)")
            except Exception:
                lines.append("[yellow]?[/] jira token set")
        # confluence via user/current
        cbase = values.get(("confluence", "base_url"), "").rstrip("/")
        cmail = values.get(("confluence", "email"), "")
        if cbase and cmail and token:
            try:
                import requests
                r = requests.get(f"{cbase}/rest/api/user/current", auth=(cmail, token), timeout=15)
                lines.append("[green]✓[/] confluence" if r.status_code == 200
                             else f"[red]✗[/] confluence ({r.status_code})")
            except Exception as e:
                lines.append(f"[red]✗[/] confluence ({type(e).__name__})")
        if values.get(("tavily_api_key",)):
            lines.append("[green]✓[/] tavily (set)")
        self.app.call_from_thread(self._status,
                              "  ".join(lines) if lines else "[yellow]Nothing to validate — fill some fields.[/]")


class StartScreen(Screen):
    """First screen when saved sessions exist: continue one or start fresh."""

    CSS = """
    StartScreen { align: center middle; }
    #box { width: 64; height: auto; border: round #5f5fd7; padding: 1 2; }
    #title { text-style: bold; color: #d787ff; margin-bottom: 1; }
    OptionList { height: auto; }
    #shint { color: #999999; margin-top: 1; }
    """
    BINDINGS = [("escape", "quit", "Quit"), ("ctrl+p", "prompt", "📝 Prompt")]

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Static("🦊  raiko — welcome back", id="title")
            yield OptionList(
                Option("▸  Continue a session", id="continue"),
                Option("✦  Start a new session", id="new"),
                id="start")
            yield Static("", id="shint")
        yield Footer()

    def on_mount(self):
        self.query_one("#start", OptionList).focus()
        n = len(list_sessions())
        self.query_one("#shint", Static).update(Text.from_markup(
            f"[dim]{n} saved session{'s' if n != 1 else ''} · Esc to quit[/]"))

    def action_prompt(self):
        self.app.push_screen(SystemPromptScreen(wizard=True))

    def on_option_list_option_selected(self, e: OptionList.OptionSelected):
        if e.option.id == "continue":
            self.app.push_screen(SessionListScreen())
        else:
            self.app.push_screen(ProviderScreen())


class SessionListScreen(Screen):
    """Pick a saved session to resume (or delete one)."""

    CSS = """
    SessionListScreen { align: center middle; }
    #box { width: 92; height: 80%; border: round #00afaf; padding: 1 2; }
    #title { text-style: bold; color: #00d7ff; }
    OptionList { height: 1fr; }
    #shint { color: #999999; }
    """
    BINDINGS = [("escape", "back", "Back"), ("d", "delete", "Delete")]

    def __init__(self):
        super().__init__()
        self.sessions = []

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Static("Continue a session", id="title")
            yield OptionList(id="sessions")
            yield Static("", id="shint")

    def on_mount(self):
        self.rebuild()

    def rebuild(self):
        ol = self.query_one("#sessions", OptionList)
        ol.clear_options()
        self.sessions = list_sessions()
        for s in self.sessions:
            when = (s.get("updated", "") or "")[:16].replace("T", " ")
            title = s.get("title") or "(untitled)"
            ol.add_option(Option(Text.from_markup(
                f"[b]{title}[/]\n   [dim]{s.get('provider')} · {s.get('model')} · "
                f"{len(s.get('messages', []))} msgs · {when}[/]"), id=s["id"]))
        if ol.option_count:
            ol.highlighted = 0
        self.query_one("#shint", Static).update(Text.from_markup(
            "[dim]Enter resume · d delete · Esc back[/]"))

    def on_option_list_option_selected(self, e: OptionList.OptionSelected):
        sess = load_session(e.option.id)
        if sess:
            self.app.resume_session(sess)

    def action_delete(self):
        ol = self.query_one("#sessions", OptionList)
        if ol.highlighted is None:
            return
        delete_session(ol.get_option_at_index(ol.highlighted).id)
        self.rebuild()
        if not self.sessions:
            self.app.pop_screen()   # nothing left → back to start

    def action_back(self):
        self.app.pop_screen()


class ToolLogScreen(Screen):
    """Full, scrollable log of every tool call in the current session (full commands
    + results), derived from the conversation messages."""

    CSS = """
    ToolLogScreen { align: center middle; }
    #box { width: 92%; height: 88%; border: round #e7b94e; padding: 1 2; }
    #title { text-style: bold; color: #e7b94e; }
    #toollog { height: 1fr; background: $surface; scrollbar-size-vertical: 1; }
    #thint { color: #999999; height: 1; }
    """
    BINDINGS = [("escape", "back", "Back")]

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Static("🛠  Tool-call log — this session", id="title")
            yield RichLog(id="toollog", wrap=True, markup=False, highlight=False)
            yield Static(Text.from_markup("[dim]Esc to go back[/]"), id="thint")

    def on_mount(self):
        log = self.query_one("#toollog", RichLog)
        # pair each tool result with its call id
        results = {m.get("tool_call_id"): m.get("content", "")
                   for m in self.app.messages if m.get("role") == "tool"}
        n = 0
        for m in self.app.messages:
            if m.get("role") != "assistant":
                continue
            for tc in (m.get("tool_calls") or []):
                n += 1
                fn = tc.get("function", {})
                name, raw = fn.get("name", "?"), fn.get("arguments", "")
                try:
                    pretty = json.dumps(json.loads(raw), indent=2, ensure_ascii=False)
                except Exception:
                    pretty = raw or "{}"
                log.write(Panel(Syntax(pretty, "json", theme="monokai", background_color="default"),
                                title=f"[bold yellow]#{n}  🔧 {name}[/]", border_style="yellow",
                                expand=False), expand=True)
                res = results.get(tc.get("id"), "")
                if res:
                    err = res.startswith("ERROR")
                    style = "red" if err else "green"
                    prev = res if len(res) <= 4000 else res[:4000] + f"\n… (+{len(res)-4000} chars)"
                    log.write(Panel(Text(prev), title=f"[bold {style}]{'✗' if err else '✓'} result[/]",
                                    border_style=style, expand=False), expand=True)
        if n == 0:
            log.write(Text("No tool calls in this session yet.", style="dim"))
        self.query_one("#thint", Static).update(Text.from_markup(
            f"[dim]{n} tool call{'s' if n != 1 else ''} · Esc to go back[/]"))

    def action_back(self):
        self.app.pop_screen()


class SystemPromptScreen(Screen):
    """Pick / edit / save named system-prompt presets, and apply one live.
    In `start` mode it's shown when a new session begins; otherwise it's the F6
    in-session editor. The fixed tool-calling rules are always appended on top."""

    CSS = """
    SystemPromptScreen { align: center middle; background: $background 70%; }
    #box { width: 88%; height: 88%; border: thick #5f5fd7; background: $surface; padding: 1 2; }
    #title { text-style: bold; color: #d787ff; }
    #presets { height: 6; border: round #44475a; margin-bottom: 1; }
    #pname { margin-bottom: 1; }
    #pbody { height: 1fr; border: round #44475a; }
    #perr { height: auto; color: #ffd700; }
    #pbtns { height: auto; align: center middle; margin-top: 1; }
    Button { margin: 0 1; }
    """
    BINDINGS = [("escape", "cancel", "Cancel"), ("ctrl+s", "apply", "Apply")]

    def __init__(self, start=False, wizard=False):
        super().__init__()
        self.start = start      # shown right before a new session begins
        self.wizard = wizard    # opened from the startup wizard (apply → back to wizard)

    def compose(self) -> ComposeResult:
        sub = ("  ·  starting a new session" if self.start
               else "  ·  for the next session" if self.wizard else "")
        with Vertical(id="box"):
            yield Static("📝  System prompt" + sub, id="title")
            yield OptionList(id="presets")
            yield Input(placeholder="preset name", id="pname")
            yield TextArea(id="pbody")
            yield Static("", id="perr")
            with Horizontal(id="pbtns"):
                yield Button("Apply & save (Ctrl+S)", id="apply", variant="success")
                yield Button("Delete preset", id="delete", variant="warning")
                yield Button("Skip" if self.start else "Cancel (Esc)", id="cancel", variant="error")

    def on_mount(self):
        self._reload(self.app.cfg.get("active_system_prompt", "default"))
        self.query_one("#pbody", TextArea).focus()

    def _reload(self, select=None):
        sp = self.app.cfg.get("system_prompts") or {"default": ""}
        ol = self.query_one("#presets", OptionList)
        ol.clear_options()
        names = list(sp)
        for i, name in enumerate(names):
            ol.add_option(Option(("★ " if name == self.app.cfg.get("active_system_prompt") else "  ") + name,
                                 id=name))
            if name == select:
                ol.highlighted = i
        if select is None and names:
            ol.highlighted = 0
            select = names[0]
        self.query_one("#pname", Input).value = select or ""
        self.query_one("#pbody", TextArea).text = sp.get(select, "")

    def on_option_list_option_selected(self, e: OptionList.OptionSelected):
        sp = self.app.cfg.get("system_prompts") or {}
        self.query_one("#pname", Input).value = e.option.id
        self.query_one("#pbody", TextArea).text = sp.get(e.option.id, "")

    def on_button_pressed(self, e: Button.Pressed):
        {"apply": self.action_apply, "delete": self.action_delete,
         "cancel": self.action_cancel}[e.button.id]()

    def action_apply(self):
        name = self.query_one("#pname", Input).value.strip()
        text = self.query_one("#pbody", TextArea).text
        if not name:
            self.query_one("#perr", Static).update(Text("Give the preset a name.", style="red"))
            return
        self.app.save_preset(name, text)
        self.app.set_active_preset(name)
        self.app.apply_persona(text)
        if self.start:
            self.app._go_main(replace=True)
        elif self.wizard:
            self.app._prompt_chosen = True   # don't re-prompt at session start
            self.app.pop_screen()
        else:
            self.app.pop_screen()
            self.app.write_log(Panel(Text.from_markup(
                f"[bold]System prompt set[/] → preset [cyan]{name}[/] [dim](applies next turn)[/]"),
                border_style="magenta", expand=False))
            self.app.update_ctx()

    def action_delete(self):
        ol = self.query_one("#presets", OptionList)
        if ol.highlighted is None:
            return
        name = ol.get_option_at_index(ol.highlighted).id
        self.app.delete_preset(name)
        self._reload(self.app.cfg.get("active_system_prompt", "default"))

    def action_cancel(self):
        if self.start:
            self.app._go_main(replace=True)   # keep the active default and continue
        else:
            self.app.pop_screen()


class Composer(TextArea):
    """Multiline chat input. Enter sends; Shift+Enter / Ctrl+J inserts a newline.
    ↑/↓ recall the prompt history when the cursor is on the first/last line.
    Esc interrupts a running turn."""

    # TextArea binds f6 to select_line, which would swallow the screen's
    # "f6 = open prompt editor" while the input is focused. Forward it instead.
    BINDINGS = [Binding("f6", "screen.prompt", "Prompt", show=False)]

    class Submitted(Message):
        def __init__(self, value):
            self.value = value
            super().__init__()

    def __init__(self, **kw):
        super().__init__(**kw)
        self._hist_pos = None

    def _load(self, text):
        self.load_text(text)
        self.move_cursor(self.document.end)

    def _recall(self, direction):
        h = self.app.prompt_history
        if not h:
            return
        if direction < 0:
            self._hist_pos = len(h) - 1 if self._hist_pos is None else max(0, self._hist_pos - 1)
            self._load(h[self._hist_pos])
        elif self._hist_pos is not None:
            self._hist_pos += 1
            if self._hist_pos >= len(h):
                self._hist_pos = None
                self._load("")
            else:
                self._load(h[self._hist_pos])

    def _hint(self):
        try:
            return self.screen.query_one("#cmdhint", CommandHint)
        except Exception:
            return None

    async def _on_key(self, event):
        key = event.key
        # Slash-command autocomplete: when the hint dropdown is open it captures the
        # navigation/accept keys before they fall through to send / history / indent.
        hint = self._hint()
        if hint and hint.is_open:
            if key in ("up", "down"):
                event.prevent_default(); event.stop()
                hint.move(-1 if key == "up" else 1)
                return
            if key == "tab":
                event.prevent_default(); event.stop()
                sel = hint.selected()
                if sel:
                    self._load(sel + " ")
                    hint.reset()
                return
            if key == "escape":
                event.prevent_default(); event.stop()
                hint.reset()
                return
            if key == "enter":
                event.prevent_default(); event.stop()
                sel = hint.selected()
                hint.reset()
                self.post_message(self.Submitted(sel or self.text))
                return
        if key == "enter":
            event.prevent_default(); event.stop()
            self.post_message(self.Submitted(self.text))
            return
        if key in ("shift+enter", "alt+enter", "ctrl+j"):
            event.prevent_default(); event.stop()
            self.insert("\n")
            return
        if key == "escape" and getattr(self.app, "busy", False):
            event.prevent_default(); event.stop()
            self.app.session.interrupt()
            return
        if key in ("up", "down") and self.app.prompt_history:
            row = self.cursor_location[0]
            if key == "up" and row == 0:
                event.prevent_default(); event.stop(); self._recall(-1); return
            if key == "down" and row == self.document.line_count - 1:
                event.prevent_default(); event.stop(); self._recall(+1); return
        await super()._on_key(event)


# ---- chat message widgets (lightweight, box-less) ----
_WAVE = "▁▂▃▄▅▆▇▆▅▄▃▂"
_SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧"


class SelectableStatic(Static):
    """A Static that stays selectable even when it renders a non-Text/Content
    renderable (Markdown, a diff Syntax, a Panel…). Textual's default get_selection
    returns None for those, so the text can't be copied. We fall back to rebuilding
    the on-screen text from the rendered strips, whose lines/columns line up exactly
    with the selection offsets the user dragged over."""

    def get_selection(self, selection):
        result = super().get_selection(selection)
        if result is not None:
            return result
        cache = getattr(self, "_render_cache", None)
        lines = getattr(cache, "lines", None) if cache else None
        if not lines:
            return None
        text = "\n".join(strip.text for strip in lines)
        return selection.extract(text), "\n"


class Notice(SelectableStatic):
    """A one-off, box-less line in the chat (connect line, errors, status notes)."""


# Assistant Markdown with small colored bullets (Claude-Code-ish) instead of Rich's
# default uncoloured "•" / mixed glyphs.
from rich.markdown import ListItem as _RichListItem
from rich._loop import loop_first as _loop_first
from rich.segment import Segment as _Seg
from rich.style import Style as _Sty

_BULLET_STYLE = _Sty(color="#b48eff", bold=True)


class _ColorBulletItem(_RichListItem):
    def render_bullet(self, console, options):
        render_options = options.update(width=options.max_width - 3)
        lines = console.render_lines(self.elements, render_options, style=self.style)
        bullet, padding, nl = _Seg(" • ", _BULLET_STYLE), _Seg("   ", _BULLET_STYLE), _Seg("\n")
        for first, line in _loop_first(lines):
            yield bullet if first else padding
            yield from line
            yield nl


class ChatMarkdown(Markdown):
    """Rich Markdown whose unordered-list bullets are small colored circles."""
    elements = {**Markdown.elements, "list_item_open": _ColorBulletItem}


class UserMsg(Static):
    """The user's message — a gray rounded block with white text (Claude-Code style)."""
    def __init__(self, text):
        super().__init__(Text(text))


class ThinkingBlock(Vertical):
    """Inline, dim/italic reasoning under a left rule; click to collapse/expand."""

    def __init__(self):
        super().__init__()
        self._head = Static(Text("💭 thinking", style="#9b93b8"), classes="thead")
        self._body = SelectableStatic("", classes="tbody")
        self._open = True
        self._has_text = False

    def compose(self) -> ComposeResult:
        yield self._head
        yield self._body

    def on_mount(self):
        # respect whether set() already ran before mount (avoids a hide race)
        self.display = self._has_text

    def set(self, text):
        self._has_text = True
        self.display = True
        self._body.update(Text(text, style="italic #9b93b8"))

    def on_click(self):
        self._open = not self._open
        self._body.display = self._open
        self._head.update(Text("💭 thinking" + ("" if self._open else "  (collapsed)"),
                               style="#9b93b8"))


class AssistantBlock(Vertical):
    """One assistant segment: an (optional) thinking block + the answer content."""

    def __init__(self):
        super().__init__()
        self.think = ThinkingBlock()
        self.body = SelectableStatic("")

    def compose(self) -> ComposeResult:
        yield self.think
        yield self.body

    def set_think(self, text):
        self.think.set(text)

    def set_content(self, text):
        self.body.update(Text(text))

    def finalize(self, md):
        if md:
            self.body.update(ChatMarkdown(md))
        else:
            self.body.display = False


class ToolBlock(Vertical):
    """A compact tool call (⏺ name(arg)) plus its result line / inline diff."""

    def __init__(self, call_renderable):
        super().__init__()
        self.call_static = SelectableStatic(call_renderable)
        self.result_static = SelectableStatic("")

    def compose(self) -> ComposeResult:
        yield self.call_static
        yield self.result_static

    def set_result(self, renderable):
        self.result_static.update(renderable)


class WorkingBar(Static):
    """Animated 'working' indicator (opencode-style): spinner + phase + wiggling wave."""

    def on_mount(self):
        self._i = 0
        self._was_busy = False
        self.display = False
        self.set_interval(0.1, self._tick)

    def _tick(self):
        app = self.app
        busy = bool(getattr(app, "busy", False))
        if busy != self._was_busy:
            # busy changed — refresh the footer so the Stop binding shows/hides
            self._was_busy = busy
            try:
                self.screen.refresh_bindings()
            except Exception:
                pass
        if not busy:
            if self.display:
                self.display = False
            return
        self.display = True
        self._i += 1
        spin = _SPIN[self._i % len(_SPIN)]
        off = self._i % len(_WAVE)
        wave = (_WAVE * 2)[off:off + 14]
        elapsed = time.time() - app._turn_start if app._turn_start else 0
        tps = (app._stream_chars / 4) / max(0.001, elapsed)
        phase = app._phase or "working"
        self.update(Text.from_markup(
            f"[#00d7ff]{spin}[/] [bold #00d7ff]{phase}[/][dim]…[/]  "
            f"[#b394ff]{wave}[/]  [dim]{elapsed:.0f}s · {tps:.0f} tok/s · Esc to stop[/]"))


SLASH_COMMANDS = [
    ("/clear", "clear the conversation (keeps the system prompt)"),
    ("/compact", "summarize older turns to free up context"),
    ("/system", "edit the system prompt (also F6)"),
    ("/tools", "open the tool-call log (also F4)"),
    ("/retry", "regenerate the last response"),
    ("/edit", "edit & resend your last message"),
    ("/export", "save the conversation to Markdown"),
    ("/configure", "setup wizard: keys · Jira · Confluence · MCP"),
    ("/permissions", "show tool allow/deny + workspace boundary"),
    ("/help", "list the available commands"),
]


class CommandHint(Static):
    """A slash-command autocomplete dropdown shown above the composer. Filters as
    you type `/…`; ↑/↓ moves the highlight, Tab completes, Enter runs it."""

    def __init__(self, **kw):
        super().__init__("", **kw)
        self.matches = []   # list of (cmd, desc)
        self.sel = 0

    def update_for(self, text):
        # Only while a single slash-token is being typed — any whitespace (incl. a
        # trailing space after Tab-completing) means the command is done; hide.
        t = text or ""
        if t.startswith("/") and not any(ch.isspace() for ch in t):
            q = t[1:].lower()
            self.matches = [(c, d) for (c, d) in SLASH_COMMANDS if c[1:].startswith(q)]
        else:
            self.matches = []
        self.sel = 0
        if not self.matches:
            self.display = False
            return
        self._render_rows()
        self.display = True

    def _render_rows(self):
        t = Text()
        for i, (c, d) in enumerate(self.matches):
            on = i == self.sel
            t.append(" › " if on else "   ", style="bold #5f8fff" if on else "#44475a")
            t.append(f"{c:<9}", style="bold #8be9fd" if on else "#6f6f8f")
            t.append(f"  {d}", style="#9b93b8" if on else "#6f6f8f")
            if i != len(self.matches) - 1:
                t.append("\n")
        self.update(t)

    def move(self, delta):
        if not self.matches:
            return
        self.sel = (self.sel + delta) % len(self.matches)
        self._render_rows()

    def selected(self):
        if self.matches and 0 <= self.sel < len(self.matches):
            return self.matches[self.sel][0]
        return None

    def reset(self):
        self.matches = []
        self.sel = 0
        self.display = False

    @property
    def is_open(self):
        return bool(self.display and self.matches)


class MainScreen(Screen):
    BINDINGS = [("f2", "settings", "⚙ Settings"), ("f3", "swap_model", "⇄ Model"),
                ("f4", "tool_log", "🛠 Tools"), ("f6", "prompt", "📝 Prompt"),
                ("escape", "interrupt", "⏹ Stop")]
    CSS = """
    #main { width: 3fr; padding: 0 1; }
    #chat { height: 1fr; scrollbar-size-vertical: 1; }
    #working { height: 1; padding: 0 1; }
    UserMsg { height: auto; background: #2b2b3d; color: #ececf5; padding: 0 1; margin: 1 0 1 0; }
    AssistantBlock { height: auto; margin: 0 0 1 0; }
    ThinkingBlock { height: auto; }
    ThinkingBlock .thead { height: 1; }
    ThinkingBlock .tbody { height: auto; color: #9b93b8; text-style: italic; border-left: solid #44475a; padding-left: 1; }
    AssistantBlock > Static { height: auto; }
    ToolBlock { height: auto; }
    ToolBlock > Static { height: auto; }
    Notice { height: auto; color: #9b93b8; }
    UsageSidebar { width: 40; border: round #00afaf; padding: 0 1; }
    UsageSidebar .sec { color: #00d7ff; text-style: bold; margin-top: 1; }
    Sparkline { height: 3; margin-top: 0; }
    #spark_gpu > .sparkline--max-color { color: #ff5f5f; }
    #spark_gpu > .sparkline--min-color { color: #3a3a5a; }
    #spark_cpu > .sparkline--max-color { color: #ffd700; }
    #spark_cpu > .sparkline--min-color { color: #3a3a5a; }
    #gpu_name { height: 1; }
    #lbl_gpu { height: 1; }
    #lbl_vram { height: 2; }
    #lbl_cpu { height: 1; }
    #lbl_ram { height: 2; }
    #lbl_extra { height: 1; }
    #lbl_toks { height: 2; }
    #inputwrap { dock: bottom; height: auto; }
    #prompt { height: 5; border: round #5f5fd7; margin: 0 1 1 1; }
    #cmdhint { display: none; height: auto; max-height: 7; margin: 0 1 0 1;
               padding: 0 1; background: #1b1830; color: #9b93b8; }
    #statusbar { height: 1; color: #00d7ff; padding: 0 1; background: #1b1830; }
    """

    def compose(self) -> ComposeResult:
        yield Static(id="statusbar")
        with Horizontal(id="body"):
            with Vertical(id="main"):
                yield VerticalScroll(id="chat")
                yield WorkingBar(id="working")
            if self.app.is_local:
                yield UsageSidebar()
        with Vertical(id="inputwrap"):
            yield CommandHint(id="cmdhint")
            yield Composer(id="prompt")
        yield Footer()

    def on_text_area_changed(self, event):
        """Keep the slash-command hint in sync with what's typed in the composer."""
        try:
            hint = self.query_one("#cmdhint", CommandHint)
            hint.update_for(self.query_one("#prompt", Composer).text)
        except Exception:
            pass

    def on_mount(self):
        app = self.app
        app.update_ctx()
        resumed = getattr(app, "resumed", False)
        if resumed:
            app.render_history()
            app.resumed = False
        else:
            hint = "" if app.is_local else "  ·  F3 swap model"
            app.write_log(Text.from_markup(
                f"[dim]● connected · [cyan]{app.provider}[/] · [cyan]{app.model}[/]{hint}[/]"))
        if app.is_local:
            self.set_interval(1.0, app.poll_usage)
            app.poll_usage()
        if app.session.mcp_servers:
            app.run_worker(app.load_mcp_tools, thread=True)
        self.query_one("#prompt", Composer).focus()

    def check_action(self, action, parameters):
        # Only surface the Stop binding while a turn is running. Returning False
        # (not None) drops it from the footer entirely; None would just grey it.
        if action == "interrupt":
            return bool(getattr(self.app, "busy", False))
        return True

    def action_interrupt(self):
        if self.app.busy:
            self.app.session.interrupt()

    def action_settings(self):
        self.app.push_screen(SettingsScreen())

    def action_tool_log(self):
        self.app.push_screen(ToolLogScreen())

    def action_prompt(self):
        self.app.push_screen(SystemPromptScreen())

    def action_swap_model(self):
        if self.app.is_local:
            self.app.write_log(Panel(
                Text("Model swap isn't available in local mode (one model per llama-server).",
                     style="yellow"), border_style="yellow", expand=False))
            return
        self.app.push_screen(ModelScreen(self.app.provider, swap=True))

    def on_composer_submitted(self, event: "Composer.Submitted"):
        text = event.value.strip()
        if not text or self.app.busy:
            return
        comp = self.query_one("#prompt", Composer)
        comp._load("")
        comp._hist_pos = None
        self.app.prompt_history.append(text)
        if text.startswith("/"):
            self.handle_command(text)
            return
        if self.app.over_budget():
            self.app.write_log(Panel(Text.from_markup(
                f"[bold yellow]Budget reached[/] — session "
                f"{pricing.fmt_usd(self.app.session_cost)} ≥ {pricing.fmt_usd(self.app.cfg.get('budget_usd'))}. "
                f"Raise [cyan]budget_usd[/] in settings (F2) or /clear to reset the tally."),
                border_style="yellow", expand=False))
            return
        self.app.busy = True
        self.app.run_worker(lambda: self.app.agent_turn(text), thread=True, exclusive=True)

    def handle_command(self, text):
        cmd = text[1:].split()[0].lower() if len(text) > 1 else ""
        if cmd == "clear":
            self.app.clear_context()
        elif cmd == "compact":
            self.app.compact()
        elif cmd in ("system", "prompt"):
            self.app.push_screen(SystemPromptScreen())
        elif cmd == "tools":
            self.app.push_screen(ToolLogScreen())
        elif cmd in ("configure", "config", "setup"):
            self.app.push_screen(ConfigureScreen())
        elif cmd in ("permissions", "perms", "trust"):
            self.app.show_permissions()
        elif cmd in ("retry", "regen", "regenerate"):
            self.app.retry_last()
        elif cmd in ("edit", "rewind"):
            self.app.edit_last()
        elif cmd in ("export", "save"):
            self.app.export_session()
        elif cmd == "help":
            self.app.write_log(Panel(Text.from_markup(
                "[bold]Commands[/]\n"
                "  [cyan]/clear[/]      clear the conversation context (keeps the system prompt)\n"
                "  [cyan]/compact[/]    summarize older turns to free up context\n"
                "  [cyan]/retry[/]      regenerate the last response · [cyan]/edit[/] edit & resend your last message\n"
                "  [cyan]/export[/]     save the conversation to a Markdown file\n"
                "  [cyan]/system[/]     edit the system prompt (also F6)\n"
                "  [cyan]/tools[/]      open the tool-call log (also F4)\n"
                "  [cyan]/configure[/]  setup wizard (keys · Jira · Confluence · MCP)\n"
                "  [cyan]/permissions[/] tool allow/deny + workspace boundary\n"
                "[dim]Footer: F2 settings · F3 swap model · F4 tools · F6 prompt · Esc stop[/]"),
                title="[bold magenta]help[/]", border_style="magenta", expand=False))
        else:
            self.app.write_log(Panel(Text.from_markup(
                f"Unknown command [yellow]{text}[/] — try [cyan]/help[/]"),
                border_style="yellow", expand=False))


# ----------------------------------- App -----------------------------------

class AgentTUI(App):
    CSS = "Screen { layout: vertical; } #body { height: 1fr; }"
    # Quit on ctrl+q so we don't shadow Textual's smart ctrl+c, which copies the
    # current text selection to the clipboard (and still quits on a double press).
    BINDINGS = [("ctrl+q", "quit", "Quit"), ("ctrl+l", "clear_log", "Clear")]

    def __init__(self, cfg, cli_provider=None, cli_model=None, cli_demo=False,
                 skip_permissions=False, cli_configure=False):
        super().__init__()
        self.cfg = cfg
        self.cli_provider = cli_provider
        self.cli_model = cli_model
        self.cli_demo = cli_demo
        self.cli_configure = cli_configure
        self.skip_permissions = skip_permissions
        # the engine owns the turn loop / tools / permissions / sessions / cost;
        # this app is a thin adapter: events -> widgets, keys -> commands.
        self.session = Session(cfg, emit=self._emit_from_worker,
                               ask_permission=self._ask_permission_hook,
                               skip_permissions=skip_permissions,
                               persist=not cli_demo)
        self.provider = None
        self.model = None
        self.is_local = False
        self.gpu_hist = deque([0] * 40, maxlen=40)
        self.cpu_hist = deque([0] * 40, maxlen=40)
        self.cur_think = self.cur_content = ""
        self.busy = False
        self._stream_chars = 0
        self._pending_ctx = None
        self.server_proc = None
        self.started_server = False
        self._turn_start = 0.0
        self._resume_messages = None    # carried into a (local) resume
        self.resumed = False            # MainScreen renders prior history when True
        self._prompt_chosen = False     # True once the prompt was set from the wizard
        self.prompt_history = []        # submitted prompts (↑/↓ recall)
        self._live = None               # current streaming AssistantBlock widget
        self._live_tool = None          # current ToolBlock awaiting its result
        self._phase = ""                # working-bar status: thinking/generating/running …

    # engine state read by screens/widgets (single source of truth: the Session)
    @property
    def messages(self):
        return self.session.messages

    @property
    def tracker(self):
        return self.session.tracker

    @property
    def session_cost(self):
        return self.session.session_cost

    @property
    def mcp_names(self):
        return self.session.mcp_names

    def on_mount(self):
        self.title = "🤖 JJ agent"
        if getattr(self, "cli_demo", False):
            self.provider = self.session.provider = "local"
            self.is_local = self.session.is_local = True
            self.model = self.session.model = "qwen35-9b"
            self.session.tracker = ContextTracker("qwen35-9b")
            self.push_screen(MainScreen())
            self.set_timer(0.6, self._demo_fill)
            return
        if getattr(self, "cli_configure", False):
            self.push_screen(ConfigureScreen(startup=True))
            return
        self._route_startup()

    def _route_startup(self):
        """Normal startup routing (also re-run after the configure wizard closes)."""
        if self.cli_provider and self.cli_model:
            self.provider = self.cli_provider
            self.choose_model(self.cli_model)
        elif self.cli_provider:
            self.provider = self.cli_provider
            self.push_screen(ModelScreen(self.cli_provider))
        elif list_sessions():
            self.push_screen(StartScreen())   # continue / new
        else:
            self.push_screen(ProviderScreen())

    def _demo_fill(self):
        self.update_ctx()
        self._chat_mount(UserMsg("How many .py files are in src and how many lines do they total?"))
        self._start_assistant()
        self.cur_think = "I'll find the .py files in src with find_files, then count their lines."
        self.cur_content = ""
        self.update_live()
        self.commit_live()
        self.render_tool_call("find_files", '{"name_glob": "**/*.py", "path": "src"}')
        self.render_tool_result(protocol.ToolCallResult(
            call_id="demo", name="find_files", ok=True,
            summary="src/app.py  ·  4 files",
            result="src/app.py\nsrc/db.py\nsrc/orders.py\nsrc/parser.py"))
        self._start_assistant()
        self.cur_think = ""
        self.cur_content = ("## Result\n\nThere are **4** `.py` files in `src`:\n\n"
                            "1. `app.py`\n2. `db.py`\n3. `orders.py`\n4. `parser.py`\n\n"
                            "They total **46 lines**.")
        self.update_live()
        self.commit_live()
        self.poll_usage()

    # ---------- selection / startup ----------
    def local_running(self):
        try:
            import requests
            return requests.get(serve.health_url(), timeout=1.5).status_code == 200
        except Exception:
            return False

    def _loaded_local_model(self):
        try:
            cli = OpenAI(base_url=serve.base_url(), api_key="sk-noop")
            return cli.models.list().data[0].id
        except Exception:
            return None

    def configure(self, provider, model, ctx_limit=None):
        self.provider = provider
        self.is_local = provider == "local"
        self.model = model
        self.session.configure(provider, model, ctx_limit=ctx_limit,
                               base_url=serve.base_url() if self.is_local else None)

    # ---------- engine events -> widgets ----------
    def _emit_from_worker(self, event):
        """Engine emit hook. Turns run in a worker thread, so hop to the UI
        thread (and block until handled, preserving event order like the old
        call_from_thread-per-update flow did). Some engine calls happen on the
        app thread itself (configure/swap) — dispatch inline there, since
        call_from_thread refuses to run from the app thread."""
        if threading.get_ident() == getattr(self, "_thread_id", None):
            self._handle_event(event)
        else:
            self.call_from_thread(self._handle_event, event)

    def _handle_event(self, e):
        if isinstance(e, protocol.TurnStarted):
            self._chat_mount(UserMsg(e.text))
        elif isinstance(e, protocol.ThinkingDelta):
            if not self._live:
                self._start_assistant()
            self.cur_think += e.text
            self._stream_chars += len(e.text)
            self._phase = "thinking"
            self.update_live()
        elif isinstance(e, protocol.TextDelta):
            if not self._live:
                self._start_assistant()
            self.cur_content += e.text
            self._stream_chars += len(e.text)
            self._phase = "generating"
            self.update_live()
        elif isinstance(e, protocol.SegmentEnd):
            # final texts win (a text tool-call may have been stripped out)
            self.cur_think, self.cur_content = e.thinking, e.content
            if (e.thinking or e.content) and not self._live:
                self._start_assistant()
            self.commit_live()
        elif isinstance(e, protocol.ToolCallStarted):
            self._phase = f"running {e.name}"
            self.render_tool_call(e.name, e.args)
        elif isinstance(e, protocol.ToolCallResult):
            self.render_tool_result(e)
        elif isinstance(e, protocol.CostUpdate):
            self.update_ctx()
        elif isinstance(e, protocol.Compacted):
            self._after_compact(e)
        elif isinstance(e, protocol.Notice):
            style = "dim yellow" if e.kind == "warning" else "dim"
            self.write_log(Text(e.text, style=style))
        elif isinstance(e, protocol.Error):
            self.write_log(Panel(Text(e.message), title="[red]error[/]",
                                 border_style="red", expand=False))
        elif isinstance(e, protocol.TurnDone):
            if e.reason == "interrupted":
                self.write_log(Text.from_markup("[bold yellow]⏹ stopped[/]"))
            elif e.reason == "max_iterations":
                self.write_log(Panel("max iterations reached", border_style="red"))
            cost = "" if self.provider in ("local", "remote", "vllm") else \
                f" · {pricing.fmt_usd(e.turn_usd)} (session {pricing.fmt_usd(self.session_cost)})"
            self.write_log(Text.from_markup(
                f"[dim]⚡ {e.tok_s:.1f} tok/s · {e.output_tokens} output tokens · "
                f"{e.elapsed_s:.1f}s{cost}[/]"))
            self.update_ctx()

    def _ask_permission_hook(self, req):
        """Engine permission hook (runs in the turn worker): show the modal on the
        UI thread and block until the user decides. Returns the protocol decision."""
        box = {}
        done = threading.Event()

        def show():
            self.push_screen(PermissionScreen(req.tool, req.action, req.detail),
                             lambda v: (box.__setitem__("v", v or "deny"), done.set()))
        self.call_from_thread(show)
        done.wait()
        return {"once": "allow_once", "always": "allow_always"}.get(box.get("v"), "deny")

    def _save_last(self, provider, model):
        self.cfg["last"] = {"provider": provider, "model": model}
        save_config(self.cfg)

    def choose_model(self, model_value):
        if self.provider in CLOUD:
            self.configure(self.provider, model_value)
            self._save_last(self.provider, model_value)
            self._go_session()
            return
        # local
        model = registry.find(model_value)
        if model is None:
            return
        if self.local_running():
            # server already up: we reuse its ctx
            alias = self._loaded_local_model() or model_value
            self.configure("local", alias)
            self._save_last("local", alias)
            self._apply_resume()
            self._go_session()
        else:
            # compute the optimal ctx based on the GPU and allow overriding it
            optimal = registry.optimal_ctx(model["path"])
            free, total = registry.gpu_mem()
            weights = 0
            try:
                weights = os.path.getsize(model["path"]) / 1e9
            except OSError:
                pass
            info = (f"GPU free [b]{free:.1f}[/]/{total:.1f} GB · weights ~{weights:.1f} GB\n"
                    f"Optimal ctx for your hardware: [b green]{optimal}[/] tokens"
                    if free else f"GPU not detected · default ctx {optimal}")
            self.push_screen(CtxScreen(model, optimal, info))

    def start_local_with_ctx(self, model, ctx):
        self._pending_ctx = ctx
        model = dict(model, ctx=ctx)
        self.push_screen(LoadingScreen(f"Starting «{model['alias']}» on the GPU (ctx {ctx})…"))
        self.run_worker(lambda: self._start_local(model), thread=True, exclusive=True)

    def _start_local(self, model):
        try:
            proc = serve.start_server(model, log=lambda *_: None)
            self.server_proc = proc
            self.started_server = True
            self.call_from_thread(self._local_ready, model["alias"])
        except Exception as e:
            self.call_from_thread(self._local_failed, str(e))

    def _local_ready(self, alias):
        loaded = self._loaded_local_model() or alias
        self.configure("local", loaded, ctx_limit=getattr(self, "_pending_ctx", None))
        self._save_last("local", loaded)
        self._apply_resume()
        self._go_session(replace=True)

    # ---------- sessions ----------
    def _apply_resume(self):
        """If a resume is pending, install its messages as the live conversation."""
        if self._resume_messages is not None:
            self.session.resume(self._resume_messages)
            self._resume_messages = None
            self.resumed = True

    def resume_session(self, sess):
        """Restore a saved session: re-select its provider+model and load its history.
        Local restores boot the server for that model; cloud/remote just reconnect
        (a remote whose API is down will simply error on the next turn)."""
        provider = sess.get("provider")
        model = sess.get("model")
        self.provider = provider
        if provider in URL_PROVIDERS:
            self.cfg.setdefault(provider, {})["base_url"] = (
                sess.get("base_url") or self.cfg.get(provider, {}).get("base_url", ""))
        if provider == "local":
            if registry.find(model) is None:
                self.push_screen(ProviderScreen())
                self.write_log(Panel(Text(f"Model '{model}' is no longer in the registry.",
                                          style="red"), border_style="red", expand=False))
                return
            self._resume_messages = sess
            self.choose_model(model)           # boots the server, then _apply_resume
            return
        # cloud + remote
        self.configure(provider, model)
        self.session.resume(sess)
        self.resumed = True
        self._save_last(provider, model)
        self._go_main()

    def save_session(self):
        self.session.save()

    # ---------- system prompt (presets + live edit) ----------
    def apply_persona(self, persona):
        """Set the live system message (messages[0]); takes effect on the next turn."""
        self.session.apply_persona(persona)

    def save_preset(self, name, text):
        self.cfg.setdefault("system_prompts", {"default": ""})[name] = text
        save_config(self.cfg)

    def set_active_preset(self, name):
        if name in (self.cfg.get("system_prompts") or {}):
            self.cfg["active_system_prompt"] = name
            save_config(self.cfg)

    def delete_preset(self, name):
        sp = self.cfg.setdefault("system_prompts", {"default": ""})
        if name in sp and len(sp) > 1:
            del sp[name]
            if self.cfg.get("active_system_prompt") == name:
                self.cfg["active_system_prompt"] = next(iter(sp))
            save_config(self.cfg)

    def swap_model(self, model_value):
        """Switch the model mid-session (cloud/remote only). History is kept."""
        old = self.model
        self.configure(self.provider, model_value)   # new client+tracker, messages untouched
        self.session.save()
        self._save_last(self.provider, model_value)
        if len(self.screen_stack) > 1:
            self.pop_screen()                        # back to MainScreen
        self.write_log(Panel(Text.from_markup(
            f"[bold]Model switched[/]  [cyan]{old}[/] → [cyan]{model_value}[/]  "
            f"[dim](history kept)[/]"), border_style="magenta", expand=False))
        self.update_ctx()

    def render_history(self, footer=True):
        """Replay a resumed conversation as widgets (so it matches the live look)."""
        for m in self.messages:
            role, content = m.get("role"), m.get("content")
            if role == "user" and content:
                self._chat_mount(UserMsg(content))
            elif role == "assistant":
                if content:
                    blk = AssistantBlock()
                    self._chat_mount(blk)
                    blk.finalize(content)
                for tc in (m.get("tool_calls") or []):
                    fn = tc.get("function", {})
                    color = self._tool_color(fn.get("name", ""))
                    self._chat_mount(ToolBlock(Text.from_markup(
                        f"[{color}]⏺[/] [bold {color}]{fn.get('name', '?')}[/]"
                        f"[dim]({tool_arg_summary(fn.get('name', ''), fn.get('arguments', ''))})[/]")))
            elif role == "tool":
                note = " ".join((content or "").split())[:80]
                self.write_log(Text.from_markup(f"  [dim]⎿ {note}[/]"))
        if footer:
            self.write_log(Text.from_markup("[dim]— end of restored history —[/]"))

    # ---------- retry / edit / export ----------
    def _rewind_to_last_user(self):
        """Drop the last user message and everything after it; re-render the trimmed
        conversation. Returns the dropped user text, or None if there's nothing to do."""
        if self.busy:
            return None
        text = self.session.rewind_last_user()
        if text is None:
            self.write_log(Panel(Text("Nothing to retry/edit yet.", style="yellow"),
                                 border_style="yellow", expand=False))
            return None
        self.action_clear_log()
        self.render_history(footer=False)
        return text

    def retry_last(self):
        """Re-run the last user message (regenerate the response)."""
        text = self._rewind_to_last_user()
        if text is None:
            return
        self.busy = True
        self.run_worker(lambda: self.agent_turn(text), thread=True, exclusive=True)

    def edit_last(self):
        """Rewind to the last user message and load it into the composer to edit + resend."""
        text = self._rewind_to_last_user()
        if text is None:
            return
        self.save_session()
        try:
            comp = self._q("#prompt", Composer)
            comp._load(text)
            comp.focus()
        except Exception:
            pass
        self.write_log(Text.from_markup("[dim]✎ edit your message above and press Enter to resend[/]"))

    def export_session(self):
        """Write the current conversation to a Markdown file under the sessions dir."""
        ts = datetime.now().isoformat(timespec="seconds")
        lines = [f"# raiko conversation — {self.provider} · {self.model}", f"_exported {ts}_", ""]
        for m in self.messages:
            role, content = m.get("role"), (m.get("content") or "")
            if role == "system":
                continue
            if role == "user":
                lines += ["## 🧑 You", "", content, ""]
            elif role == "assistant":
                if content:
                    lines += ["## 🤖 Assistant", "", content, ""]
                for tc in (m.get("tool_calls") or []):
                    fn = tc.get("function", {})
                    args = (fn.get("arguments", "") or "")[:200]
                    lines += [f"> 🛠 `{fn.get('name', '?')}({args})`", ""]
            elif role == "tool":
                lines += ["> ⎿ " + " ".join(content.split())[:500], ""]
        fn = f"export-{self.session.session_id or datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
        path = os.path.join(_sessions_dir(), fn)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except Exception as e:
            self.write_log(Panel(Text(f"export failed: {e}", style="red"), border_style="red", expand=False))
            return
        n = sum(1 for m in self.messages if m.get("role") != "system")
        self.write_log(Panel(Text.from_markup(f"Exported {n} messages to [cyan]{path}[/]"),
                             title="[bold green]📄 export[/]", border_style="green", expand=False))

    def _local_failed(self, err):
        try:
            self.screen.query_one("#lmsg", Static).update(Text("Could not start the model", style="bold red"))
            self.screen.query_one("#lerr", Static).update(Text(f"{err}\n(Ctrl+Q to quit)", style="red"))
        except Exception:
            pass

    def _go_main(self, replace=False):
        if replace:
            self.switch_screen(MainScreen())
        else:
            self.push_screen(MainScreen())

    def _go_session(self, replace=False):
        """Enter the chat. For a brand-new session with more than one prompt preset,
        let the user pick the system prompt first; resumes and single-preset setups go
        straight in. (The picker replaces itself with MainScreen on confirm.)"""
        presets = self.cfg.get("system_prompts") or {}
        if (not self.resumed and not self.session.session_id and not self._prompt_chosen
                and len(presets) > 1):
            self.push_screen(SystemPromptScreen(start=True))
        else:
            self._go_main(replace=replace)

    def _shutdown_server(self):
        if self.started_server and self.server_proc:
            try:
                serve.stop_server(self.server_proc, log=lambda *_: None)
            except Exception:
                pass
            self.started_server = False

    def action_quit(self):
        # stop the model BEFORE exiting so we don't leave the GPU busy
        self._shutdown_server()
        self.exit()

    def on_unmount(self):
        self._shutdown_server()

    # ---------- logging ----------
    def _q(self, selector, typ):
        """ALWAYS query on the active screen (not the _default one)."""
        return self.screen.query_one(selector, typ)

    def _chat_mount(self, widget):
        """Mount a widget into the #chat scroll and follow the bottom — but only
        if the user was already at the bottom (so wheel-scrolling up isn't yanked
        back). scroll_end runs after the refresh so the new widget is measured."""
        try:
            chat = self._q("#chat", VerticalScroll)
            follow = chat.scroll_y >= chat.max_scroll_y - 2
            chat.mount(widget)
            if follow:
                chat.call_after_refresh(chat.scroll_end, animate=False)
        except Exception:
            pass

    def write_log(self, renderable):
        """Append a box-less notice line to the chat (banners, errors, status).
        Legacy callers pass a Panel; we unwrap it so nothing renders as a box."""
        if isinstance(renderable, Panel):
            renderable = renderable.renderable
        self._chat_mount(Notice(renderable))

    def _start_assistant(self):
        self._live = AssistantBlock()
        self._chat_mount(self._live)

    def update_live(self):
        if not self._live:
            return
        chat = None
        follow = True
        try:
            chat = self._q("#chat", VerticalScroll)
            follow = chat.scroll_y >= chat.max_scroll_y - 2
        except Exception:
            pass
        if self.cur_think:
            self._live.set_think(self.cur_think)
        if self.cur_content:
            self._live.set_content(self.cur_content)
        if chat is not None and follow:
            try:
                chat.call_after_refresh(chat.scroll_end, animate=False)
            except Exception:
                pass

    def commit_live(self):
        if self._live:
            if self.cur_content.strip():
                self._live.finalize(self.cur_content.strip())
            elif not self.cur_think.strip():
                try:
                    self._live.remove()   # produced nothing (e.g. tool-call only)
                except Exception:
                    pass
            else:
                self._live.finalize(None)  # thinking only
        self._live = None
        self.cur_think = self.cur_content = ""

    def action_clear_log(self):
        try:
            self._q("#chat", VerticalScroll).remove_children()
        except Exception:
            pass

    def clear_context(self):
        """/clear — drop the conversation (keep the system prompt) and start a fresh
        session. The previous session stays saved on disk."""
        if self.busy:
            return
        self.session.clear()
        self.action_clear_log()
        self.write_log(Panel(Text("Context cleared — fresh conversation (system prompt kept).",
                                  style="bold green"), border_style="green", expand=False))
        self.update_ctx()

    # ---------- compaction ----------
    def compact(self, auto=False):
        """/compact — manual entry point (main thread, while idle). Runs the engine
        summarization in its own worker and owns the busy flag for it."""
        if self.busy or not self.session.client:
            return
        if not self.session.enough_to_compact():
            if not auto:
                self.write_log(Panel(Text("Not enough conversation to compact yet.", style="yellow"),
                                     border_style="yellow", expand=False))
            return
        self.busy = True
        self.write_log(Text.from_markup("[dim]✦ compacting context…[/]"))

        def _job():
            try:
                self.session.compact(auto)   # emits Compacted / Error events
            finally:
                self.busy = False
        self.run_worker(_job, thread=True, exclusive=True)

    def _after_compact(self, e):
        """React to the engine's Compacted event (manual /compact or auto)."""
        self.action_clear_log()
        tag = "auto-compacted" if e.auto else "compacted"
        self.write_log(Panel(Text.from_markup(
            f"[bold green]✦ context {tag}[/] — {e.before_messages} → {e.after_messages} messages "
            f"(older turns summarized)"), border_style="green", expand=False))
        self.write_log(Panel(ChatMarkdown(e.summary),
                             title="[bold magenta]summary[/]", border_style="magenta"))
        self.update_ctx()

    # ---------- MCP (remote tools) ----------
    def load_mcp_tools(self):
        """Worker: connect the engine to its MCP servers and render the summary."""
        results = self.session.load_mcp_tools()
        if not results:
            return
        lines = [f"[green]✓[/] {name}: {cnt} tools (prefix '{prefix}')" if cnt
                 else f"[yellow]✗ {name}: none reachable at {url}[/]"
                 for name, cnt, prefix, url in results]
        total = len(self.session.mcp_names)
        self.call_from_thread(self.write_log, Panel(
            Text.from_markup("[bold]MCP[/] · " + str(total) + " tools\n" + "\n".join(lines)),
            border_style="green" if total else "yellow", expand=False))

    # ---------- permissions ----------
    def show_permissions(self):
        """/permissions — show the allow/deny lists + workspace boundary."""
        p = self.cfg.get("permissions", {}) or {}
        allow = ", ".join(p.get("allow") or []) or "(none)"
        deny = ", ".join(p.get("deny") or []) or "(none)"
        skip = " · [red]--dangerously-skip-permissions ON[/]" if self.skip_permissions else ""
        self.write_log(Panel(Text.from_markup(
            f"[bold]Permissions[/]{skip}\n"
            f"  [green]always-allow[/]: {allow}\n"
            f"  [red]deny[/]: {deny}\n"
            f"  [cyan]workspace[/] (write/edit confined here): {self.session.workspace()}\n"
            f"[dim]Edit in tui_config.json → permissions (F2). 'Always allow' in a prompt adds a tool here.[/]"),
            title="[bold cyan]🔐 permissions[/]", border_style="cyan", expand=False))

    # ---------- agent (engine turn in a worker thread) ----------
    def agent_turn(self, text):
        """Worker: run one engine turn; the UI is updated by the event stream."""
        self.cur_think = self.cur_content = ""
        self._stream_chars = 0
        self._turn_start = time.time()
        self._phase = "thinking"
        try:
            self.session.run_turn(text)
        finally:
            self.busy = False

    # ---- tool-call rendering (compact colored bullets, Claude-Code style) ----
    _READONLY_TOOLS = {
        "read_file", "list_dir", "get_current_directory", "grep", "find_files",
        "read_lines", "head", "tail", "count_lines", "stat_path", "tree",
        "find_in_files", "list_models",
    }
    _EXEC_TOOLS = {"write_file", "edit_file", "run_python", "run_powershell", "run_bash"}

    def _tool_color(self, name):
        if name in self.mcp_names:
            return "magenta"
        if name.startswith("vaultwarden_"):
            return "green"
        if name in self._EXEC_TOOLS:
            return "yellow"
        if name in self._READONLY_TOOLS:
            return "cyan"
        return "white"

    def render_tool_call(self, name, args):
        color = self._tool_color(name)
        summ = tool_arg_summary(name, args)
        self._live_tool = ToolBlock(Text.from_markup(
            f"[{color}]⏺[/] [bold {color}]{name}[/][dim]({summ})[/]"))
        self._chat_mount(self._live_tool)

    def render_tool_result(self, e):
        """Render a protocol.ToolCallResult under its ToolBlock (diff inline for
        file writes; one-line note otherwise)."""
        tb = self._live_tool
        self._live_tool = None
        if e.diff and e.name in ("write_file", "edit_file"):
            lines = e.diff.splitlines()
            shown = lines[:60]
            body = "\n".join(shown) + (f"\n… (+{len(lines) - 60} more diff lines)" if len(lines) > 60 else "")
            res = Group(Text.from_markup(f"  [dim]⎿[/] [green]edited[/] [cyan]{e.path}[/]"),
                        Syntax(body or "(no changes)", "diff", theme="monokai", background_color="default"))
        else:
            style = "red" if not e.ok else "green"
            res = Text.from_markup(f"  [dim]⎿[/] [{style}]{e.summary}[/]")
        if tb:
            tb.set_result(res)
        else:
            self.write_log(res)

    def over_budget(self):
        return self.session.over_budget()

    def _cost_label(self):
        """Statusbar suffix: session cost (and budget, if set). Empty for self-hosted."""
        if self.provider in ("local", "remote", "vllm"):
            return ""
        cap = self.cfg.get("budget_usd") or 0
        cost = pricing.fmt_usd(self.session_cost)
        if cap > 0:
            pct = 100 * self.session_cost / cap
            col = "red" if pct >= 100 else ("yellow" if pct >= 80 else "green")
            return f"  · [{col}]{cost}/{pricing.fmt_usd(cap)} ({pct:.0f}%)[/]"
        return f"  · [#9b93b8]{cost} session[/]"

    def update_ctx(self):
        if not self.tracker:
            return
        # never let the context estimate crash the turn / the app
        try:
            used, _ = self.tracker.current(self.messages)
            limit = self.tracker.limit or 1
            pct = 100 * used / limit
            remaining = max(0, limit - used)
            txt = (f"[#d787ff]{self.provider}[/] · [cyan]{self.model}[/] · "
                   f"ctx [b]{used/1000:.1f}k[/]/{limit/1000:.0f}k used · "
                   f"[green]{remaining/1000:.1f}k left[/] ({pct:.0f}%)"
                   + ("  · [green]LOCAL[/]" if self.is_local else ""))
            txt += self._cost_label()
            self._q("#statusbar", Static).update(Text.from_markup(txt))
        except Exception:
            pass

    # ---------- usage polling (local) ----------
    def poll_usage(self):
        """Render one engine telemetry sample into the sidebar."""
        t = telemetry.sample()
        try:
            if t.gpu_util is not None:
                self.gpu_hist.append(t.gpu_util)
                vram_pct = 100 * t.vram_used_mb / t.vram_total_mb if t.vram_total_mb else 0
                self._q("#gpu_name", Static).update(Text(t.gpu_name or "", style="bold #00d7ff"))
                self._q("#spark_gpu", Sparkline).data = list(self.gpu_hist)
                self._q("#lbl_gpu", Static).update(bar(t.gpu_util, color=util_color(t.gpu_util)))
                self._q("#lbl_vram", Static).update(Group(
                    bar(vram_pct, color=util_color(vram_pct)),
                    Text(f"{t.vram_used_mb/1024:.1f} / {t.vram_total_mb/1024:.1f} GiB", style="#999999")))
                power = f"{t.power_w:.0f}" if t.power_w is not None else "?"
                self._q("#lbl_extra", Static).update(Text.from_markup(
                    f"[#00d7ff]temp[/] {t.temp_c:.0f}°C   [#00d7ff]power[/] {power} W"))
        except Exception:
            pass
        try:
            if t.cpu is not None:
                self.cpu_hist.append(t.cpu)
                ram_pct = 100 * t.ram_used_gb / t.ram_total_gb if t.ram_total_gb else 0
                self._q("#spark_cpu", Sparkline).data = list(self.cpu_hist)
                self._q("#lbl_cpu", Static).update(bar(t.cpu, color=util_color(t.cpu)))
                self._q("#lbl_ram", Static).update(Group(
                    bar(ram_pct, color=util_color(ram_pct)),
                    Text(f"{t.ram_used_gb:.1f} / {t.ram_total_gb:.1f} GB", style="#999999")))
            # live tok/s (estimated by characters ~4/token as the stream arrives)
            tps = (self._stream_chars / 4) / max(0.001, time.time() - self._turn_start) if self.busy else 0
            self._q("#lbl_toks", Static).update(Text.from_markup(
                f"[#00d7ff]tok/s~[/] {tps:5.1f}   [#00d7ff]out[/] {self.tracker.total_output if self.tracker else 0}"))
        except Exception:
            pass


def main():
    # `raiko web` → headless engine server (WS + API + telemetry), no TUI.
    if len(sys.argv) > 1 and sys.argv[1] == "web":
        from web.server import main as web_main
        web_main(sys.argv[2:])
        return
    # `raiko run` → headless one-shot/REPL agent loop, no TUI.
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        from cli import main as cli_main
        cli_main(sys.argv[2:])
        return
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["nano", "local"])
    ap.add_argument("--model")
    ap.add_argument("--demo", action="store_true", help="example screen (no model needed)")
    ap.add_argument("--configure", action="store_true", help="open the setup wizard on startup")
    ap.add_argument("--dangerously-skip-permissions", action="store_true",
                    dest="skip_perms", help="auto-allow flagged operations (no prompts)")
    args = ap.parse_args()
    AgentTUI(load_config(), cli_provider=args.provider, cli_model=args.model,
             cli_demo=args.demo, skip_permissions=args.skip_perms,
             cli_configure=args.configure).run()


if __name__ == "__main__":
    main()
