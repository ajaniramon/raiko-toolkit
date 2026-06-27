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
import re
import subprocess
import sys
import threading
import time
from collections import deque

import psutil
from openai import OpenAI
from rich.console import Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from textual.app import App, ComposeResult
from textual.containers import Center, Horizontal, Middle, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import (Button, Footer, Input, LoadingIndicator, OptionList,
                             RichLog, Sparkline, Static, TextArea)
from textual.widgets.option_list import Option

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "bench"))

import serve  # noqa: E402  (bench/serve.py)
import models as registry  # noqa: E402  (bench/models.py)
from tools import TOOLS, call_tool, danger_match  # noqa: E402
from context import ContextTracker  # noqa: E402
import mcp_client  # noqa: E402  (MCP client for remote tools)


# ---- parsing of tool-calls emitted as TEXT (fallback) ----
_TC_BLOCK = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)  # captures the WHOLE block
_TC_OPEN = re.compile(r"<tool_call>\s*(\{.*)", re.DOTALL)                 # block without closing tag
_TC_FUNC = re.compile(r"<function=([\w\-/.]+)>(.*?)</function>", re.DOTALL)
_TC_PARAM = re.compile(r"<parameter=([\w\-]+)>(.*?)</parameter>", re.DOTALL)


def _balanced_objects(s):
    """Returns all top-level JSON objects {...} (with well-balanced nested
    braces) found in s."""
    objs, i, n = [], 0, len(s)
    while i < n:
        if s[i] == "{":
            depth, start = 0, i
            j = i
            in_str = esc = False
            while j < n:
                ch = s[j]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                elif ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        objs.append(s[start:j + 1])
                        i = j
                        break
                j += 1
        i += 1
    return objs


def _as_call(d):
    if not isinstance(d, dict):
        return None
    name = d.get("name") or d.get("tool") or d.get("function")
    if not name:
        return None
    a = d.get("arguments", d.get("parameters", d.get("args", {})))
    return {"name": name, "arguments": a if isinstance(a, str) else json.dumps(a)}


def parse_text_tool_calls(content):
    """Recovers tool-calls that the model emitted as plain text. Supports:
    - <tool_call>{...}</tool_call> with NESTED arguments (parsing by balanced braces),
      several blocks, and blocks without a closing </tool_call>.
    - <function=NAME><parameter=p>val</parameter></function>.
    - Loose JSON {"name":...,"arguments":...} without tags."""
    content = content or ""
    out = []
    blocks = _TC_BLOCK.findall(content)
    if not blocks:
        m = _TC_OPEN.search(content)
        if m:
            blocks = [m.group(1)]
    for b in blocks:
        for frag in (_balanced_objects(b) or [b]):
            try:
                call = _as_call(json.loads(frag))
            except Exception:
                call = None
            if call:
                out.append(call)
    if out:
        return out
    # XML format <function=...>
    for m in _TC_FUNC.finditer(content):
        args = {p: v.strip() for p, v in _TC_PARAM.findall(m.group(2))}
        out.append({"name": m.group(1), "arguments": json.dumps(args)})
    if out:
        return out
    # loose JSON with "name" (without any tag)
    if '"name"' in content:
        for frag in _balanced_objects(content):
            if '"name"' in frag:
                try:
                    call = _as_call(json.loads(frag))
                except Exception:
                    call = None
                if call:
                    out.append(call)
    return out


def strip_tool_call_text(content):
    if not content:
        return content
    c = re.sub(r"<tool_call>.*?</tool_call>", "", content, flags=re.DOTALL)
    c = re.sub(r"<tool_call>\s*\{.*", "", c, flags=re.DOTALL)   # block without closing tag
    c = _TC_FUNC.sub("", c)
    return c.strip()

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tui_config.json")
MAX_ITERATIONS = 8

DEFAULT_CONFIG = {
    "nano": {"base_url": "https://nano-gpt.com/api/v1",
             "api_key": "",   # set in tui_config.json (not versioned) or via the key prompt
             "model": "xiaomi/mimo-v2.5-pro-ultraspeed",
             "ctx_window": 131072},   # nano = frontier: we assume the model's max
    "local": {"base_url": "http://localhost:25565/v1", "api_key": "sk-noop",
              "model": "qwen35-9b", "enable_thinking": True},
    "xai": {"base_url": "https://api.x.ai/v1", "api_key": "",
            "model": "grok-4", "ctx_window": 256000},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "api_key": "",
                   "model": "", "ctx_window": 131072},
    "openai": {"base_url": "https://api.openai.com/v1", "api_key": "",
               "model": "gpt-5", "ctx_window": 128000},
    # Anthropic via its OpenAI-compatible endpoint (same openai SDK)
    "anthropic": {"base_url": "https://api.anthropic.com/v1/", "api_key": "",
                  "model": "claude-opus-4-8", "ctx_window": 200000,
                  "models": ["claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
                             "claude-sonnet-4-6", "claude-haiku-4-5", "claude-fable-5"]},
    # remote llama.cpp: the URL is requested on selection and remembered here
    "remote": {"base_url": "", "api_key": "sk-noop", "model": "", "ctx_window": 16000},
    # MCP url: overridden in tui_config.json (not versioned) with your real host
    "mcp": {"enabled": True, "url": "http://localhost:8765/mcp", "prefix": "mac_"},
    "last": {"provider": None, "model": None},   # last used (default on startup)
    "favorites": {"nano": [], "xai": [], "openrouter": [], "openai": [], "anthropic": [], "remote": []},
}

# OpenAI-compatible providers served via API (GPU sidebar OFF; model's ctx)
CLOUD = {"nano", "xai", "openrouter", "openai", "anthropic", "remote"}

SYSTEM_PROMPT = (
    "You are an agent with access to file, execution and system tools. Use them when needed. "
    "Before each tool call, briefly explain in 1-2 sentences what you are about to do and why.\n"
    "TOOL ARGUMENTS RULE: every tool call's arguments must be ONE valid JSON object — escape "
    "newlines as \\n and double quotes as \\\". Keep run_python / run_shell code SHORT (a few "
    "lines). If you need a longer or multi-line script, do NOT paste it as a tool argument: "
    "first save it to a file with write_file, then run it with run_python or run_shell. This "
    "avoids invalid-JSON errors from large code blocks.\n"
    "When you have the answer, give it directly. Format your final answers in clean Markdown."
)


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if os.path.exists(CONFIG_PATH):
        try:
            user = json.load(open(CONFIG_PATH, encoding="utf-8"))
            for k, v in user.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
        except Exception:
            pass
    else:
        json.dump(DEFAULT_CONFIG, open(CONFIG_PATH, "w", encoding="utf-8"), indent=2)
    cfg.setdefault("last", {"provider": None, "model": None})
    cfg.setdefault("favorites", {})
    return cfg


def save_config(cfg):
    try:
        json.dump(cfg, open(CONFIG_PATH, "w", encoding="utf-8"), indent=2)
    except Exception:
        pass


# ----------------------------- render utilities -----------------------------

class ThinkSplitter:
    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self):
        self.mode = "content"
        self.buffer = ""

    def feed(self, chunk):
        self.buffer += chunk
        out = []
        while True:
            tag = self.OPEN if self.mode == "content" else self.CLOSE
            idx = self.buffer.find(tag)
            if idx != -1:
                if idx > 0:
                    out.append((self.mode, self.buffer[:idx]))
                self.buffer = self.buffer[idx + len(tag):]
                self.mode = "thinking" if self.mode == "content" else "content"
                continue
            safe = len(self.buffer)
            for i in range(min(len(tag) - 1, len(self.buffer)), 0, -1):
                if self.buffer.endswith(tag[:i]):
                    safe = len(self.buffer) - i
                    break
            if safe > 0:
                out.append((self.mode, self.buffer[:safe]))
                self.buffer = self.buffer[safe:]
            break
        return out

    def flush(self):
        if self.buffer:
            out = [(self.mode, self.buffer)]
            self.buffer = ""
            return out
        return []


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
        ("openai", "⚙  OpenAI      (GPT · cloud)"),
        ("anthropic", "✺  Anthropic   (Claude · cloud)"),
        ("xai", "✦  xAI         (Grok · cloud)"),
        ("openrouter", "🔀  OpenRouter  (cloud · many models)"),
    ]
    BINDINGS = [("escape", "quit", "Quit")]

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Static("🤖  What do you want to run with?", id="title")
            yield OptionList(*[Option(lbl, id=pid) for pid, lbl in self.PROVIDERS], id="prov")
            yield Static("", id="phint")

    def on_mount(self):
        ol = self.query_one("#prov", OptionList)
        ol.focus()
        last = self.app.cfg.get("last", {})
        ids = [p for p, _ in self.PROVIDERS]
        if last.get("provider") in ids:
            ol.highlighted = ids.index(last["provider"])
            self.query_one("#phint", Static).update(Text.from_markup(
                f"[dim]last used → [cyan]{last['provider']}[/] · {last.get('model') or '?'}[/]"))

    def on_option_list_option_selected(self, e: OptionList.OptionSelected):
        pid = e.option.id
        self.app.provider = pid
        if pid == "remote":
            self.app.push_screen(RemoteUrlScreen())
        elif pid in CLOUD and not (self.app.cfg.get(pid, {}).get("api_key")):
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
              "openrouter": "OpenRouter", "nano": "nano-gpt"}

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
    """Asks for a remote llama-server's URL and then lists its models."""

    CSS = """
    RemoteUrlScreen { align: center middle; }
    #rbox { width: 78; height: auto; border: round #00afaf; padding: 1 2; }
    #rtitle { text-style: bold; color: #00d7ff; }
    #rinfo { margin: 1 0; color: #999999; }
    #url_in { margin-bottom: 1; }
    """
    BINDINGS = [("escape", "back", "Back")]

    def action_back(self):
        self.app.pop_screen()

    def compose(self) -> ComposeResult:
        with Vertical(id="rbox"):
            yield Static("🌐  Remote llama.cpp server", id="rtitle")
            yield Static(Text.from_markup(
                "Enter the server URL (host:port or full /v1 URL).\n"
                "[dim]e.g. http://192.168.1.50:8080  →  /v1 is added automatically[/]"), id="rinfo")
            yield Input(value=self.app.cfg["remote"].get("base_url", "")
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
        self.app.cfg["remote"]["base_url"] = url
        save_config(self.app.cfg)
        self.app.push_screen(ModelScreen("remote"))


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

    def __init__(self, provider):
        super().__init__()
        self.provider = provider
        self.names = []                # model names/aliases
        self.label_of = {}             # name -> label to display

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
            cli = OpenAI(base_url=cfg["base_url"], api_key=cfg.get("api_key") or "x")
            ids = sorted(m.id for m in cli.models.list().data)
            if not ids:
                raise ValueError("empty model list")
            self.app.call_from_thread(self.set_cloud, ids)
        except Exception as e:
            if fallback:
                # /models not available (e.g. Anthropic): use the curated list from config
                self.app.call_from_thread(self.set_cloud, list(fallback))
                return
            hint = (f"set an api_key for '{self.provider}' in tui_config.json"
                    if not cfg.get("api_key") else str(e))
            self.app.call_from_thread(lambda: self.query_one("#status", Static).update(
                Text(f"error: {hint}", style="red")))

    def set_cloud(self, ids):
        self.names = ids
        self.label_of = {i: i for i in ids}
        self.rebuild()
        self.query_one("#status", Static).update(Text(f"{len(ids)} models · filter, ↓, Enter"))
        self.query_one("#filter", Input).focus()

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
            self.app.choose_model(ol.get_option_at_index(idx).id)

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
        self.app.choose_model(e.option.id)


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
    flagged as dangerous. Returns True (allow) / False (deny) on dismiss.
    'Always allow' enables skip_permissions in the app."""

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
                yield Button("Always allow (a)", id="always", variant="warning")
                yield Button("Deny (n)", id="deny", variant="error")

    def on_button_pressed(self, e: Button.Pressed):
        {"allow": self.action_allow, "always": self.action_always,
         "deny": self.action_deny}[e.button.id]()

    def action_allow(self):
        self.dismiss(True)

    def action_always(self):
        self.app.skip_permissions = True
        self.dismiss(True)

    def action_deny(self):
        self.dismiss(False)


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


class MainScreen(Screen):
    BINDINGS = [("f2", "settings", "⚙ Settings")]
    CSS = """
    #main { width: 3fr; border: round #5f5fd7; padding: 0 1; }
    #log { height: 1fr; background: $surface; scrollbar-size-vertical: 1; }
    #live { height: auto; max-height: 14; border-top: dashed #44475a; padding: 0 1; }
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
    #prompt { dock: bottom; border: round #5f5fd7; }
    #titlebar { height: 1; background: #5f5fd7; color: white; text-style: bold; padding: 0 1; }
    #statusbar { height: 1; color: #00d7ff; padding: 0 1; }
    """

    def compose(self) -> ComposeResult:
        yield Static("🤖  JJ agent", id="titlebar")
        yield Static(id="statusbar")
        with Horizontal(id="body"):
            with Vertical(id="main"):
                yield RichLog(id="log", wrap=True, markup=False, highlight=False)
                yield Static(id="live")
            if self.app.is_local:
                yield UsageSidebar()
        yield Input(placeholder="Ask JJ…  (Enter to send)", id="prompt")
        yield Footer()

    def on_mount(self):
        app = self.app
        app.update_ctx()
        app._log_lines = []
        app.write_log(Panel(Text.from_markup(
            f"[bold]Connected[/]  provider=[cyan]{app.provider}[/]  model=[cyan]{app.model}[/]\n"
            + ("[green]LOCAL mode: live GPU/CPU graphs on the right →[/]" if app.is_local
               else f"[yellow]{app.provider} · cloud[/]")),
            title="[bold magenta]JJ agent TUI[/]", border_style="magenta"))
        if app.is_local:
            self.set_interval(1.0, app.poll_usage)
            app.poll_usage()
        if app.mcp_url:
            app.run_worker(app.load_mcp_tools, thread=True)
        self.query_one("#prompt", Input).focus()

    def action_settings(self):
        self.app.push_screen(SettingsScreen())

    def on_input_submitted(self, event: Input.Submitted):
        text = event.value.strip()
        if not text or self.app.busy:
            return
        self.query_one("#prompt", Input).value = ""
        self.app.write_log(Panel(Text(text, style="bold white"),
                                 title="[bold blue]you[/]", border_style="blue"))
        self.app.busy = True
        self.app.run_worker(lambda: self.app.agent_turn(text), thread=True, exclusive=True)


# ----------------------------------- App -----------------------------------

class AgentTUI(App):
    CSS = "Screen { layout: vertical; } #body { height: 1fr; }"
    BINDINGS = [("ctrl+c", "quit", "Quit"), ("ctrl+l", "clear_log", "Clear")]

    def __init__(self, cfg, cli_provider=None, cli_model=None, cli_demo=False,
                 skip_permissions=False):
        super().__init__()
        self.cfg = cfg
        self.cli_provider = cli_provider
        self.cli_model = cli_model
        self.cli_demo = cli_demo
        self.skip_permissions = skip_permissions
        self.provider = None
        self.model = None
        self.is_local = False
        self.client = None
        self.tracker = None
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.gpu_hist = deque([0] * 40, maxlen=40)
        self.cpu_hist = deque([0] * 40, maxlen=40)
        self.cur_think = self.cur_content = ""
        self.busy = False
        self.mcp_url = None
        self.mcp_prefix = "mac_"
        self.mcp_tools = []
        self.mcp_map = {}
        self.mcp_names = set()
        self._stream_chars = 0
        self._pending_ctx = None
        self.server_proc = None
        self.started_server = False
        self._log_lines = []
        self._turn_tokens = 0
        self._turn_start = 0.0

    def on_mount(self):
        self.title = "🤖 JJ agent"
        if getattr(self, "cli_demo", False):
            self.provider = "local"
            self.is_local = True
            self.model = "qwen35-9b"
            self.tracker = ContextTracker("qwen35-9b")
            self.push_screen(MainScreen())
            self.set_timer(0.6, self._demo_fill)
            return
        if self.cli_provider and self.cli_model:
            self.provider = self.cli_provider
            self.choose_model(self.cli_model)
        elif self.cli_provider:
            self.provider = self.cli_provider
            self.push_screen(ModelScreen(self.cli_provider))
        else:
            self.push_screen(ProviderScreen())

    def _demo_fill(self):
        self.update_ctx()
        self.write_log(Panel(Text("How many .py files are in src and how many lines do they total?",
                                  style="bold white"), title="[bold blue]you[/]", border_style="blue"))
        self.cur_think = "I'll find the .py files in src with find_files, then count their lines."
        self.cur_content = "Listing and counting."
        self.commit_live()
        self.render_tool_call("find_files", '{"name_glob": "**/*.py", "path": "src"}')
        self.render_tool_result("find_files", "src/app.py\nsrc/db.py\nsrc/orders.py\nsrc/parser.py")
        self.cur_content = ("## Result\n\nThere are **4** `.py` files in `src`:\n\n"
                            "1. `app.py`\n2. `db.py`\n3. `orders.py`\n4. `parser.py`\n\n"
                            "They total **46 lines**.")
        self.commit_live()
        self.cur_think = "Double-checking…"
        self.cur_content = "Live streaming example."
        self.update_live()
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
        pc = self.cfg[provider]
        self.base_url = serve.base_url() if self.is_local else pc["base_url"]
        self.api_key = pc.get("api_key", "sk-noop")
        self.model = model
        self.enable_thinking = self.cfg["local"].get("enable_thinking", True)
        self.client = OpenAI(base_url=self.base_url, api_key=self.api_key)
        self.tracker = ContextTracker(self.model)
        mc = self.cfg.get("mcp", {})
        if mc.get("enabled") and mc.get("url"):
            self.mcp_url = mc["url"]
            self.mcp_prefix = mc.get("prefix", "mac_")
        else:
            self.mcp_url = None
        if ctx_limit:
            self.tracker.limit = ctx_limit
        elif not self.is_local:
            # nano = frontier: we use the model's maximum (assumed)
            self.tracker.limit = pc.get("ctx_window", 131072)

    def _save_last(self, provider, model):
        self.cfg["last"] = {"provider": provider, "model": model}
        save_config(self.cfg)

    def choose_model(self, model_value):
        if self.provider in CLOUD:
            self.configure(self.provider, model_value)
            self._save_last(self.provider, model_value)
            self._go_main()
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
            self._go_main()
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
        self._go_main(replace=True)

    def _local_failed(self, err):
        try:
            self.screen.query_one("#lmsg", Static).update(Text("Could not start the model", style="bold red"))
            self.screen.query_one("#lerr", Static).update(Text(f"{err}\n(Ctrl+C to quit)", style="red"))
        except Exception:
            pass

    def _go_main(self, replace=False):
        if replace:
            self.switch_screen(MainScreen())
        else:
            self.push_screen(MainScreen())

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

    def write_log(self, renderable):
        self._log_lines.append(renderable)
        try:
            self._q("#log", RichLog).write(renderable, expand=True)
        except Exception:
            pass

    def update_live(self):
        parts = []
        if self.cur_think:
            parts += [Text("◇ thinking", style="bold magenta"),
                      Text(self.cur_think, style="dim italic magenta")]
        if self.cur_content:
            parts += [Text("◆ assistant", style="bold cyan"),
                      Text(self.cur_content, style="white")]
        try:
            self._q("#live", Static).update(Group(*parts) if parts else Text(""))
        except Exception:
            pass

    def commit_live(self):
        parts = []
        if self.cur_think:
            parts += [Text("◇ thinking", style="bold magenta"),
                      Text(self.cur_think.strip(), style="dim italic magenta")]
        if self.cur_content:
            # render the final message as nice Markdown (##, **bold**, lists…)
            parts += [Text("◆ assistant", style="bold cyan"),
                      Markdown(self.cur_content.strip())]
        if parts:
            self.write_log(Group(*parts))
        self.cur_think = self.cur_content = ""
        try:
            self._q("#live", Static).update(Text(""))
        except Exception:
            pass

    def action_clear_log(self):
        self._log_lines = []
        try:
            self._q("#log", RichLog).clear()
        except Exception:
            pass

    # ---------- MCP (remote tools) ----------
    def load_mcp_tools(self):
        raw, _names = mcp_client.list_tools_openai(self.mcp_url)
        self.mcp_tools, self.mcp_map = [], {}
        for t in raw:
            orig = t["function"]["name"]
            pn = self.mcp_prefix + orig
            t["function"]["name"] = pn
            self.mcp_tools.append(t)
            self.mcp_map[pn] = orig
        self.mcp_names = set(self.mcp_map)
        n = len(self.mcp_names)
        msg = (f"[green]MCP connected[/] · {n} remote tools from {self.mcp_url} "
               f"(prefix '{self.mcp_prefix}')" if n
               else f"[yellow]MCP: no tools reachable at {self.mcp_url} (server down?)[/]")
        self.call_from_thread(self.write_log, Panel(Text.from_markup(msg),
                              border_style="green" if n else "yellow", expand=False))

    # ---------- permissions + tool execution ----------
    def ask_permission(self, tool, snippet, code):
        """Blocks the worker thread until the user decides in the modal."""
        box = {}
        ev = threading.Event()

        def show():
            self.push_screen(PermissionScreen(tool, snippet, code),
                             lambda v: (box.__setitem__("v", bool(v)), ev.set()))
        self.call_from_thread(show)
        ev.wait()
        return box.get("v", False)

    def execute_tool(self, name, raw_args):
        """Runs a tool; for run_python/run_powershell with a dangerous op it asks
        for permission (unless skip_permissions) and, if approved, runs with allow_unsafe."""
        if name in self.mcp_names:   # remote tool (MCP) → run on the server
            return mcp_client.call_tool(self.mcp_url, self.mcp_map[name], raw_args)
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
        except Exception:
            return call_tool(name, raw_args)
        if name in ("run_python", "run_powershell"):
            code = args.get("code") or args.get("command") or ""
            if self.skip_permissions:
                args["allow_unsafe"] = True
            else:
                snip = danger_match(code)
                if snip:
                    if self.ask_permission(name, snip, code):
                        args["allow_unsafe"] = True
                    else:
                        return f"DENIED by user: refused to run flagged operation '{snip}'"
        return call_tool(name, args)

    # ---------- agent (in thread) ----------
    def agent_turn(self, text):
        self.messages.append({"role": "user", "content": text})
        self._turn_tokens = 0
        self._stream_chars = 0
        self._turn_start = time.time()
        try:
            for _ in range(MAX_ITERATIONS):
                msg = self.stream_one()
                self.messages.append(msg)
                tcs = msg.get("tool_calls")
                if not tcs:
                    break
                for tc in tcs:
                    name, args = tc["function"]["name"], tc["function"]["arguments"]
                    self.call_from_thread(self.render_tool_call, name, args)
                    result = self.execute_tool(name, args)
                    self.call_from_thread(self.render_tool_result, name, result)
                    self.messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
            else:
                self.call_from_thread(self.write_log, Panel("max iterations reached", border_style="red"))
        except Exception as e:
            msg = str(e)
            if "parse tool call" in msg.lower() or "failed to parse" in msg.lower():
                short = ("the model emitted invalid JSON for the tool arguments (common with small "
                         "models on big code blocks). Try a smaller step or rephrase the request.")
            else:
                short = msg if len(msg) <= 280 else msg[:280] + " …"
            self.call_from_thread(self.write_log, Panel(
                Text(f"{type(e).__name__}: {short}"), title="[red]error[/]", border_style="red", expand=False))
        finally:
            self.busy = False
            elapsed = time.time() - self._turn_start
            tps = self._turn_tokens / elapsed if elapsed > 0 else 0
            self.call_from_thread(self.write_log, Text.from_markup(
                f"[dim]⚡ {tps:.1f} tok/s · {self._turn_tokens} output tokens · {elapsed:.1f}s[/]"))
            self.call_from_thread(self.update_ctx)

    def stream_one(self):
        params = dict(model=self.model, messages=self.messages, tools=TOOLS + self.mcp_tools,
                      tool_choice="auto", stream=True)
        params["stream_options"] = {"include_usage": True}
        if self.is_local:
            params["extra_body"] = {"chat_template_kwargs": {"enable_thinking": self.enable_thinking}}
        elif self.provider == "nano":
            params["extra_body"] = {"reasoning": {"enabled": True}, "include_reasoning": True}
        # xai / openrouter / openai / anthropic / remote: standard OpenAI-compatible,
        # without proprietary extra_body (Anthropic via its OpenAI-compat layer)
        stream = self.client.chat.completions.create(**params)
        content_parts, tool_calls = [], {}
        splitter = ThinkSplitter()
        self.cur_think = self.cur_content = ""
        last_pricing = None

        def emit(mode, t):
            if not t:
                return
            self._stream_chars += len(t)   # for the live tok/s (estimated)
            if mode == "thinking":
                self.cur_think += t
            else:
                self.cur_content += t
                content_parts.append(t)
            self.call_from_thread(self.update_live)

        for chunk in stream:
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
                emit("thinking", rc)
            if delta.content:
                for mode, t in splitter.feed(delta.content):
                    emit(mode, t)
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
            emit(mode, t)
        if last_pricing:
            self.tracker.update_from_chunk_dict(last_pricing)
            self._turn_tokens += self.tracker.last_output

        # fallback: the model emitted the tool-call as plain text -> recover it
        if not tool_calls:
            fb = parse_text_tool_calls("".join(content_parts))
            if fb:
                for i, tc in enumerate(fb):
                    tool_calls[i] = {"id": f"fb_{i}", "name": tc["name"], "arguments": tc["arguments"]}
                cleaned = strip_tool_call_text(self.cur_content)
                self.cur_content = cleaned
                content_parts[:] = [cleaned] if cleaned else []
                self.call_from_thread(self.write_log, Text.from_markup(
                    "[dim yellow]↻ recovered a tool call the model emitted as plain text[/]"))

        self.call_from_thread(self.commit_live)

        msg = {"role": "assistant", "content": "".join(content_parts) or None}
        if tool_calls:
            msg["tool_calls"] = [{"id": tc["id"], "type": "function",
                                  "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                                 for _, tc in sorted(tool_calls.items())]
        return msg

    def render_tool_call(self, name, args):
        try:
            pretty = json.dumps(json.loads(args), indent=2, ensure_ascii=False)
        except Exception:
            pretty = args
        self.write_log(Panel(Syntax(pretty, "json", theme="monokai", background_color="default"),
                             title=f"[bold yellow]🔧 {name}[/]", border_style="yellow", expand=False))

    def render_tool_result(self, name, result):
        prev = result if len(result) <= 1200 else result[:1200] + f"\n… (+{len(result)-1200} chars)"
        err = prev.startswith("ERROR")
        style = "red" if err else "green"
        self.write_log(Panel(Text(prev), title=f"[bold {style}]{'✗' if err else '✓'} {name}[/]",
                             border_style=style, expand=False))

    def update_ctx(self):
        if not self.tracker:
            return
        used, _ = self.tracker.current(self.messages)
        limit = self.tracker.limit or 1
        pct = 100 * used / limit
        remaining = max(0, limit - used)
        txt = (f"[#d787ff]{self.provider}[/] · [cyan]{self.model}[/] · "
               f"ctx [b]{used/1000:.1f}k[/]/{limit/1000:.0f}k used · "
               f"[green]{remaining/1000:.1f}k left[/] ({pct:.0f}%)"
               + ("  · [green]LOCAL[/]" if self.is_local else ""))
        try:
            self._q("#statusbar", Static).update(Text.from_markup(txt))
        except Exception:
            pass

    # ---------- usage polling (local) ----------
    def poll_usage(self):
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
                 "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=3).stdout.strip()
            name, util, mused, mtot, temp, power = [x.strip() for x in out.split(",")]
            util, mused, mtot, temp = float(util), float(mused), float(mtot), float(temp)
            self.gpu_hist.append(util)
            vram_pct = 100 * mused / mtot if mtot else 0
            self._q("#gpu_name", Static).update(Text(name, style="bold #00d7ff"))
            self._q("#spark_gpu", Sparkline).data = list(self.gpu_hist)
            self._q("#lbl_gpu", Static).update(bar(util, color=util_color(util)))
            self._q("#lbl_vram", Static).update(Group(
                bar(vram_pct, color=util_color(vram_pct)),
                Text(f"{mused/1024:.1f} / {mtot/1024:.1f} GiB", style="#999999")))
            self._q("#lbl_extra", Static).update(Text.from_markup(
                f"[#00d7ff]temp[/] {temp:.0f}°C   [#00d7ff]power[/] {power} W"))
        except Exception:
            pass
        try:
            cpu = psutil.cpu_percent()
            self.cpu_hist.append(cpu)
            vm = psutil.virtual_memory()
            self._q("#spark_cpu", Sparkline).data = list(self.cpu_hist)
            self._q("#lbl_cpu", Static).update(bar(cpu, color=util_color(cpu)))
            self._q("#lbl_ram", Static).update(Group(
                bar(vm.percent, color=util_color(vm.percent)),
                Text(f"{vm.used/1e9:.1f} / {vm.total/1e9:.1f} GB", style="#999999")))
            # live tok/s (estimated by characters ~4/token as the stream arrives)
            tps = (self._stream_chars / 4) / max(0.001, time.time() - self._turn_start) if self.busy else 0
            self._q("#lbl_toks", Static).update(Text.from_markup(
                f"[#00d7ff]tok/s~[/] {tps:5.1f}   [#00d7ff]out[/] {self.tracker.total_output if self.tracker else 0}"))
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["nano", "local"])
    ap.add_argument("--model")
    ap.add_argument("--demo", action="store_true", help="example screen (no model needed)")
    ap.add_argument("--dangerously-skip-permissions", action="store_true",
                    dest="skip_perms", help="auto-allow flagged operations (no prompts)")
    args = ap.parse_args()
    AgentTUI(load_config(), cli_provider=args.provider, cli_model=args.model,
             cli_demo=args.demo, skip_permissions=args.skip_perms).run()


if __name__ == "__main__":
    main()
