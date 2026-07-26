"""Headless CLI — `raiko run`, a thin adapter over the engine (the same turn
loop the TUI and `raiko web` use; the duplicate loop that used to live in
agent.py is gone, this file replaces it).

One-shot: `raiko run --provider local --base-url http://localhost:25565/v1 \
--model qwen35-9b "your prompt"` runs a single turn, prints it, and exits.
Omit the prompt for a REPL (Ctrl+C/EOF exits cleanly).

Permissions: flagged operations (dangerous exec, Jira/Confluence writes, writes
outside the workspace) follow the persisted allowlist in tui_config.json; anything
that would need an interactive prompt is DENIED (no one to ask headless) unless
you pass --dangerously-skip-permissions.
"""

import argparse
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


def make_session(provider=None, model=None, base_url=None, api_key=None,
                  skip_permissions=False, max_iterations=None) -> Session:
    """Build a Session from tui_config.json.

    Provider/model resolution mirrors the TUI's startup wizard: an explicit
    CLI flag wins, otherwise fall back to the last-used pair persisted at
    cfg["last"] (see tui.py's ProviderScreen.on_mount / AgentTUI._save_last).
    With neither a flag nor a last-used pair on record, there is nothing
    sensible to connect to, so this exits with a clear error instead of
    guessing a default provider.
    """
    cfg = load_config()
    last = cfg.get("last") or {}
    provider = provider or last.get("provider")
    model = model or last.get("model")
    if not provider or not model:
        print(
            "no provider/model given and no last-used session on record — "
            "pass --provider and --model (e.g. --provider local --model qwen35-9b)",
            file=sys.stderr,
        )
        raise SystemExit(2)
    cfg["max_iterations"] = max_iterations or MAX_ITERATIONS
    session = Session(cfg, emit=ConsoleRenderer(), ask_permission=permission_policy,
                      skip_permissions=skip_permissions, persist=False)
    # base_url/api_key: pass through only what was explicitly given; Session.configure
    # already falls back to the provider's configured base_url and resolve_key()
    # (config value, then the provider's conventional env var) when left as None.
    session.configure(provider, model, base_url=base_url, api_key=api_key)
    # apply_persona() (not a raw assignment) so it goes through build_system_prompt:
    # TOOL_RULES and the skills index (if any) get appended instead of clobbered.
    session.apply_persona(SYSTEM_PROMPT)
    return session


def run_once(session: Session, prompt: str) -> str:
    """Run one turn, printing it with rich as it streams. Returns the assistant's
    final text, or 'ERROR: max iterations reached' if the turn was cut short."""
    console.print(Rule(f"[bold magenta]🤖 agent[/]  [dim]{session.tracker.format_label(session.messages)}[/]",
                       style="magenta"))
    console.print(Panel(prompt, title="[bold blue]you[/]", border_style="blue", expand=False))
    reason = session.run_turn(prompt)
    if reason == "max_iterations":
        console.print(Rule("[red]max iterations[/]", style="red"))
        return "ERROR: max iterations reached"
    console.print(Rule(f"[dim]{session.tracker.format_label(session.messages)}[/]", style="magenta"))
    last = session.messages[-1]
    return (last.get("content") or "") if last.get("role") == "assistant" else ""


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="raiko run",
        description="headless agent loop: one-shot with a prompt, or a REPL without one",
    )
    ap.add_argument("prompt", nargs="?", help="run once with this prompt (omit it for a REPL)")
    ap.add_argument("--provider",
                    help="nano | local | xai | openrouter | openai | anthropic | gemini | remote | vllm "
                         "(defaults to the last provider used by the TUI/CLI)")
    ap.add_argument("--model", help="defaults to the last model used")
    ap.add_argument("--base-url", help="override the provider's configured base_url")
    ap.add_argument("--api-key", help="override the provider's configured/env API key")
    ap.add_argument("--max-iterations", type=int,
                    help=f"tool-call rounds per turn (default {MAX_ITERATIONS})")
    ap.add_argument("--dangerously-skip-permissions", action="store_true", dest="skip_perms",
                    help="auto-allow flagged operations (no prompts)")
    args = ap.parse_args(argv)

    session = make_session(provider=args.provider, model=args.model, base_url=args.base_url,
                           api_key=args.api_key, skip_permissions=args.skip_perms,
                           max_iterations=args.max_iterations)

    if args.prompt:
        result = run_once(session, args.prompt)
        if result.startswith("ERROR"):
            raise SystemExit(1)
        return

    while True:
        try:
            user_prompt = input("Ask something to JJ:\n")
        except (KeyboardInterrupt, EOFError):
            console.print()
            return
        run_once(session, user_prompt)


if __name__ == "__main__":
    main()
