"""Headless agent CLI — a thin adapter over the engine (the same turn loop the
TUI uses; the duplicate loop that used to live here is gone, Fase 3).

Environment config (defaults = nano-gpt, original behavior unchanged).
To point at a local llama.cpp:
  AGENT_PROVIDER=llamacpp AGENT_BASE_URL=http://localhost:25565/v1 \
  AGENT_API_KEY=sk-noop AGENT_MODEL=qwythos python agent.py

Permissions: flagged operations (dangerous exec, Jira/Confluence writes, writes
outside the workspace) follow the persisted allowlist in tui_config.json; anything
that would need an interactive prompt is DENIED unless you pass
--dangerously-skip-permissions (or set AGENT_SKIP_PERMISSIONS=1).
"""

import json
import os
import sys

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.text import Text

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine import protocol
from engine.config import load_config
from engine.session import Session

MAX_ITERATIONS = 5

PROVIDER = os.environ.get("AGENT_PROVIDER", "nanogpt")  # "nanogpt" | "llamacpp"
BASE_URL = os.environ.get("AGENT_BASE_URL", "https://nano-gpt.com/api/v1")
API_KEY = os.environ.get("AGENT_API_KEY", "")  # set via env or tui_config.json (not versioned)
MODEL = os.environ.get("AGENT_MODEL", "xiaomi/mimo-v2.5-pro-ultraspeed")
SKIP_PERMISSIONS = (os.environ.get("AGENT_SKIP_PERMISSIONS") == "1"
                    or "--dangerously-skip-permissions" in sys.argv)

# legacy env names -> engine provider ids (drives extra_body / pricing handling)
_PROVIDER_MAP = {"nanogpt": "nano", "llamacpp": "local"}

SYSTEM_PROMPT = (
    "You are an agent with access to file tools. Use them when needed.\n"
    "MANDATORY RULE: before calling ANY tool, first write 1-2 sentences "
    "in plain text explaining what you are going to do and why (which file/pattern, what you expect "
    "to find, which step of the plan you are on). Then make the tool call in the same turn. "
    "Never call a tool silently."
)

console = Console()


def render_tool_call(name: str, arguments: str):
    try:
        pretty = json.dumps(json.loads(arguments), indent=2, ensure_ascii=False)
    except json.JSONDecodeError:
        pretty = arguments
    body = Syntax(pretty, "json", theme="monokai", background_color="default")
    console.print(Panel(body, title=f"[bold yellow]🔧 {name}[/]", border_style="yellow", expand=False))


def render_tool_result(name: str, result: str):
    preview = result if len(result) <= 1500 else result[:1500] + f"\n... ({len(result) - 1500} more chars)"
    is_err = preview.startswith("ERROR")
    style = "red" if is_err else "green"
    icon = "✗" if is_err else "✓"
    console.print(Panel(preview, title=f"[bold {style}]{icon} {name}[/]", border_style=style, expand=False))


class ConsoleRenderer:
    """Engine events -> the same rich console output the old loop printed."""

    def __init__(self):
        self._reasoning_header = False
        self._content_header = False

    def __call__(self, e):
        if isinstance(e, protocol.ThinkingDelta):
            if not self._reasoning_header:
                console.print(Text("◇ thinking", style="bold magenta"))
                self._reasoning_header = True
            console.print(e.text, end="", style="dim italic magenta", highlight=False, soft_wrap=True)
        elif isinstance(e, protocol.TextDelta):
            if self._reasoning_header and not self._content_header:
                console.print()
            if not self._content_header:
                console.print(Text("◆ assistant", style="bold cyan"))
                self._content_header = True
            console.print(e.text, end="", style="white", highlight=False, soft_wrap=True)
        elif isinstance(e, protocol.SegmentEnd):
            if self._reasoning_header and not self._content_header:
                console.print()
            if self._content_header:
                console.print()
            self._reasoning_header = self._content_header = False
        elif isinstance(e, protocol.ToolCallStarted):
            render_tool_call(e.name, e.args)
        elif isinstance(e, protocol.ToolCallResult):
            render_tool_result(e.name, e.result)
        elif isinstance(e, protocol.Notice):
            console.print(Text(e.text, style="dim yellow" if e.kind == "warning" else "dim"))
        elif isinstance(e, protocol.Error):
            console.print(Panel(Text(e.message), title="[red]error[/]", border_style="red", expand=False))


def permission_policy(req: protocol.PermissionRequired) -> str:
    """Headless permission policy: nobody to ask, so deny (the config allowlist
    and --dangerously-skip-permissions are honored upstream by the engine)."""
    console.print(Text(f"⛔ permission denied (headless): {req.tool} — {req.action}",
                       style="bold red"))
    return "deny"


def make_session() -> Session:
    cfg = load_config()                 # allowlist / pricing / tool env exports
    cfg["max_iterations"] = MAX_ITERATIONS
    session = Session(cfg, emit=ConsoleRenderer(), ask_permission=permission_policy,
                      skip_permissions=SKIP_PERMISSIONS, persist=False)
    provider = _PROVIDER_MAP.get(PROVIDER, PROVIDER)
    session.configure(provider, MODEL, base_url=BASE_URL,
                      api_key=API_KEY or "sk-noop")
    session.messages[0]["content"] = SYSTEM_PROMPT
    return session


session = make_session()


def run(user_prompt: str) -> str:
    console.print(Rule(f"[bold magenta]🤖 agent[/]  [dim]{session.tracker.format_label(session.messages)}[/]",
                       style="magenta"))
    console.print(Panel(user_prompt, title="[bold blue]you[/]", border_style="blue", expand=False))
    reason = session.run_turn(user_prompt)
    if reason == "max_iterations":
        console.print(Rule("[red]max iterations[/]", style="red"))
        return "ERROR: max iterations reached"
    console.print(Rule(f"[dim]{session.tracker.format_label(session.messages)}[/]", style="magenta"))
    last = session.messages[-1]
    return (last.get("content") or "") if last.get("role") == "assistant" else ""


if __name__ == "__main__":
    while True:
        run(input("Ask something to JJ:\n"))
