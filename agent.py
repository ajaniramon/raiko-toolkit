import json
import os
from openai import OpenAI
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.rule import Rule
from rich.text import Text

from tools import TOOLS, call_tool
from context import ContextTracker

MAX_ITERATIONS = 5

# Environment config (defaults = nano-gpt, original behavior unchanged).
# To point at a local llama.cpp:
#   AGENT_PROVIDER=llamacpp AGENT_BASE_URL=http://localhost:25565/v1 \
#   AGENT_API_KEY=sk-noop AGENT_MODEL=qwythos python agent.py
PROVIDER = os.environ.get("AGENT_PROVIDER", "nanogpt")  # "nanogpt" | "llamacpp"
BASE_URL = os.environ.get("AGENT_BASE_URL", "https://nano-gpt.com/api/v1")
API_KEY = os.environ.get("AGENT_API_KEY", "")  # set via env or tui_config.json (not versioned)
MODEL = os.environ.get("AGENT_MODEL", "xiaomi/mimo-v2.5-pro-ultraspeed")

SYSTEM_PROMPT = (
    "You are an agent with access to file tools. Use them when needed.\n"
    "MANDATORY RULE: before calling ANY tool, first write 1-2 sentences "
    "in plain text explaining what you are going to do and why (which file/pattern, what you expect "
    "to find, which step of the plan you are on). Then make the tool call in the same turn. "
    "Never call a tool silently."
)

DEBUG = os.environ.get("AGENT_DEBUG") == "1"

messages = [{"role": "system", "content": SYSTEM_PROMPT}]
tracker = ContextTracker(MODEL)

console = Console()
client = OpenAI(base_url=BASE_URL, api_key=API_KEY)


class ThinkSplitter:
    """Slices a text stream and separates what goes inside <think>...</think>.
    Handles tags split across chunks. feed() returns [(mode, text), ...] where
    mode is 'thinking' or 'content'."""

    OPEN = "<think>"
    CLOSE = "</think>"

    def __init__(self):
        self.mode = "content"
        self.buffer = ""

    def feed(self, chunk: str):
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
            # no complete tag, but the end of the buffer could be a prefix of the tag
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


def stream_completion(messages):
    """Stream the response, accumulating content + tool_calls. Returns the assistant message as a dict."""
    params = dict(
        model=MODEL,
        messages=messages,
        tools=TOOLS,
        tool_choice="auto",
        stream=True,
    )
    if PROVIDER == "nanogpt":
        # nano-gpt proprietary fields to expose the reasoning
        params["extra_body"] = {"reasoning": {"enabled": True}, "include_reasoning": True}
    else:
        # llama-server (OpenAI-compatible): request the usage in the final chunk.
        # We don't send the proprietary fields to avoid triggering a 400.
        params["stream_options"] = {"include_usage": True}
    stream = client.chat.completions.create(**params)

    content_parts = []
    reasoning_parts = []
    tool_calls = {}  # index -> {id, name, arguments}
    printed_content_header = False
    printed_reasoning_header = False
    splitter = ThinkSplitter()
    last_pricing_chunk = None

    def emit(mode: str, text: str):
        nonlocal printed_content_header, printed_reasoning_header
        if not text:
            return
        if mode == "thinking":
            if not printed_reasoning_header:
                console.print(Text("◇ thinking", style="bold magenta"))
                printed_reasoning_header = True
            console.print(text, end="", style="dim italic magenta", highlight=False, soft_wrap=True)
            reasoning_parts.append(text)
        else:
            if printed_reasoning_header and not printed_content_header:
                console.print()
            if not printed_content_header:
                console.print(Text("◆ assistant", style="bold cyan"))
                printed_content_header = True
            console.print(text, end="", style="white", highlight=False, soft_wrap=True)
            content_parts.append(text)

    for chunk in stream:
        chunk_dict = chunk.model_dump(exclude_none=True)
        if "x_nanogpt_pricing" in chunk_dict or chunk_dict.get("usage"):
            last_pricing_chunk = chunk_dict

        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta

        if DEBUG:
            console.print(Text(f"[debug] {chunk.model_dump_json(exclude_none=True)}", style="dim yellow"))

        # 1) separate reasoning field (DeepSeek/Qwen API style)
        reasoning_chunk = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
        if not reasoning_chunk and getattr(delta, "model_extra", None):
            reasoning_chunk = delta.model_extra.get("reasoning_content") or delta.model_extra.get("reasoning")
        if reasoning_chunk:
            emit("thinking", reasoning_chunk)

        # 2) <think>...</think> inline within the content (QwQ / self-hosted style)
        if delta.content:
            for mode, text in splitter.feed(delta.content):
                emit(mode, text)

        if delta.tool_calls:
            for tc_delta in delta.tool_calls:
                idx = tc_delta.index
                slot = tool_calls.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if tc_delta.id:
                    slot["id"] = tc_delta.id
                if tc_delta.function:
                    if tc_delta.function.name:
                        slot["name"] += tc_delta.function.name
                    if tc_delta.function.arguments:
                        slot["arguments"] += tc_delta.function.arguments

    for mode, text in splitter.flush():
        emit(mode, text)

    if printed_reasoning_header and not printed_content_header:
        console.print()
    if printed_content_header:
        console.print()

    if last_pricing_chunk:
        tracker.update_from_chunk_dict(last_pricing_chunk)

    msg = {"role": "assistant", "content": "".join(content_parts) or None}
    if tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc["id"],
                "type": "function",
                "function": {"name": tc["name"], "arguments": tc["arguments"]},
            }
            for _, tc in sorted(tool_calls.items())
        ]
    return msg


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


def run(user_prompt: str) -> str:
    messages.append({"role": "user", "content": user_prompt})
    console.print(Rule(f"[bold magenta]🤖 agent[/]  [dim]{tracker.format_label(messages)}[/]", style="magenta"))
    console.print(Panel(user_prompt, title="[bold blue]you[/]", border_style="blue", expand=False))

    for _ in range(MAX_ITERATIONS):
        msg = stream_completion(messages)
        messages.append(msg)

        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            console.print(Rule(f"[dim]{tracker.format_label(messages)}[/]", style="magenta"))
            return msg.get("content") or ""

        for tc in tool_calls:
            name = tc["function"]["name"]
            args = tc["function"]["arguments"]
            render_tool_call(name, args)
            result = call_tool(name, args)
            render_tool_result(name, result)
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result,
            })

    console.print(Rule("[red]max iterations[/]", style="red"))
    return "ERROR: max iterations reached"


if __name__ == "__main__":
    while True:
        run(input("Ask something to JJ:\n"))
