"""Engine wire contract: the events the engine emits and the commands it accepts.

TYPES ONLY — no logic lives here. This is the single schema shared by every
frontend (TUI modal, WebSocket JSON, headless policy) and documented for external
consumers in docs/ws-protocol.md. Bump PROTOCOL_VERSION on any incompatible
change so clients can detect a mismatch from `session_started.protocol_version`.

Flow (one turn):
    send → [thinking_delta|text_delta]* → (tool_call_started →
    [permission_required → permission_response]? → tool_call_result)* →
    cost_update → turn_done

Permissions are ASYNC by design: on `permission_required` the turn pauses and
waits for a `permission_response` carrying the same `perm_id`. That single
mechanism backs the TUI modal, the web socket round-trip and the headless
auto-policy.

Wire shape (web): every event/command serializes as a flat JSON object with a
`"type"` field equal to its entry in EVENT_TYPES / COMMAND_TYPES plus the
dataclass fields.
"""

from dataclasses import dataclass
from typing import Any, Optional

# Bump on incompatible changes to any event/command below.
PROTOCOL_VERSION = "1.1"


# ------------------------------ events (engine → UI) ------------------------------

@dataclass
class SessionStarted:
    """A session is configured and ready to accept `send` commands."""
    session_id: str
    provider: str
    model: str
    ctx_window: Optional[int] = None
    protocol_version: str = PROTOCOL_VERSION
    connection_id: Optional[str] = None


@dataclass
class SessionSnapshot:
    """Canonical session state sent immediately after connecting/reconnecting."""
    session_id: str
    engine_session_id: Optional[str]
    busy: bool
    provider: str
    model: str
    ctx_window: Optional[int]
    messages: list[dict[str, Any]]
    input_tokens: int = 0
    output_tokens: int = 0
    session_usd: float = 0.0


@dataclass
class TurnStarted:
    """A turn began (after any auto-compaction). Echoes the user text so UIs
    mount the user message once compaction has already redrawn the log."""
    text: str


@dataclass
class TextDelta:
    """A chunk of assistant answer text (streamed)."""
    text: str


@dataclass
class ThinkingDelta:
    """A chunk of model reasoning (reasoning_content / <think> / Gemini thought)."""
    text: str


@dataclass
class SegmentEnd:
    """One model response (stream) within the turn finished. `content`/`thinking`
    are the FINAL texts for the segment — they can differ from the concatenated
    deltas when a tool call emitted as plain text was recovered and stripped.
    UIs finalize their live block here (e.g. render content as Markdown)."""
    content: str
    thinking: str


@dataclass
class ToolCallStarted:
    """The model requested a tool; execution is about to begin."""
    call_id: str
    name: str
    args: str          # raw JSON string as emitted by the model


@dataclass
class ToolCallResult:
    """A tool finished (or was denied / timed out)."""
    call_id: str
    name: str
    ok: bool           # False when the result is an ERROR/DENIED string
    summary: str       # one-line, newline-free note for compact UIs
    result: str        # full result text fed back to the model
    diff: Optional[str] = None   # unified diff for write_file/edit_file
    path: Optional[str] = None   # file the diff applies to


@dataclass
class PermissionRequired:
    """The turn is PAUSED until a PermissionResponse with this perm_id arrives."""
    perm_id: str
    tool: str          # tool name (what "always allow" would persist)
    action: str        # short human snippet, e.g. the flagged fragment or write target
    detail: str        # full command/code/target to review
    scope: str         # "danger" | "workspace" | "external_write" (jira/confluence)
    allowed_decisions: tuple[str, ...] = ("allow_once", "allow_always", "deny")


@dataclass
class Telemetry:
    """Host metrics; emitted periodically (local provider / `raiko web`)."""
    gpu_name: Optional[str] = None
    gpu_util: Optional[float] = None    # %
    vram_used_mb: Optional[float] = None
    vram_total_mb: Optional[float] = None
    ram_used_gb: Optional[float] = None
    ram_total_gb: Optional[float] = None
    cpu: Optional[float] = None         # %
    temp_c: Optional[float] = None
    power_w: Optional[float] = None
    tok_s: Optional[float] = None       # live estimated stream rate


@dataclass
class CostUpdate:
    """Token/cost accounting after each model request within a turn."""
    input_tokens: int
    output_tokens: int
    turn_usd: float
    session_usd: float
    budget_usd: Optional[float] = None  # None/0 = no cap configured


@dataclass
class TurnDone:
    """The turn ended. reason: completed | interrupted | error | budget_exceeded |
    max_iterations. Stats cover the whole turn (all model requests + tools)."""
    reason: str
    elapsed_s: float = 0.0
    output_tokens: int = 0
    tok_s: float = 0.0
    turn_usd: float = 0.0


@dataclass
class Compacted:
    """Older turns were summarized (manual /compact or auto at ~85% ctx)."""
    before_messages: int
    after_messages: int
    summary: str
    auto: bool = False


@dataclass
class ModelSwapped:
    """The session model changed mid-conversation (history kept)."""
    old_model: str
    new_model: str
    provider: str


@dataclass
class Notice:
    """Informational one-liner (think-leak retry, recovered text tool-call,
    MCP tools loaded…). kind: info | warning."""
    text: str
    kind: str = "info"


@dataclass
class Error:
    """A turn-level error already shortened for display."""
    message: str


# ------------------------------ commands (UI → engine) ------------------------------

@dataclass
class Send:
    """Submit a user message; starts a turn."""
    text: str


@dataclass
class Interrupt:
    """Stop the running turn (Esc). Safe when idle (no-op)."""


@dataclass
class PermissionResponse:
    """Answer to a PermissionRequired. decision: allow_once | allow_always | deny.
    allow_always persists the tool to the config allowlist."""
    perm_id: str
    decision: str


@dataclass
class SwapModel:
    """Switch model mid-session, history kept (cloud/remote providers)."""
    provider: str
    model: str


@dataclass
class Compact:
    """Summarize older turns now (manual /compact)."""


@dataclass
class Clear:
    """Drop the conversation (keep the system prompt); starts a fresh session id
    and resets the cost tally (/clear)."""


@dataclass
class RewindLastUser:
    """Drop the last user message and everything after it (/retry, /edit).
    The engine replies with the removed text so the UI can refill the composer."""


@dataclass
class SetMaxIterations:
    """Change how many tool-call rounds one turn may run, for this session only
    (between turns). The engine replies with a Notice stating the new cap."""
    max_iterations: int


@dataclass
class SetSystemPrompt:
    """Replace the persona part of the system prompt (TOOL_RULES are always
    appended by the engine). name selects a saved preset; text sets it inline."""
    name: Optional[str] = None
    text: Optional[str] = None


# ------------------------------ wire names ------------------------------

EVENT_TYPES = {
    "session_started": SessionStarted,
    "session_snapshot": SessionSnapshot,
    "turn_started": TurnStarted,
    "text_delta": TextDelta,
    "thinking_delta": ThinkingDelta,
    "segment_end": SegmentEnd,
    "tool_call_started": ToolCallStarted,
    "tool_call_result": ToolCallResult,
    "permission_required": PermissionRequired,
    "telemetry": Telemetry,
    "cost_update": CostUpdate,
    "turn_done": TurnDone,
    "compacted": Compacted,
    "model_swapped": ModelSwapped,
    "notice": Notice,
    "error": Error,
}

COMMAND_TYPES = {
    "send": Send,
    "interrupt": Interrupt,
    "permission_response": PermissionResponse,
    "swap_model": SwapModel,
    "compact": Compact,
    "clear": Clear,
    "rewind_last_user": RewindLastUser,
    "set_system_prompt": SetSystemPrompt,
    "set_max_iterations": SetMaxIterations,
}

# reverse maps: dataclass -> wire name (for serializers in the web layer)
EVENT_NAMES = {cls: name for name, cls in EVENT_TYPES.items()}
COMMAND_NAMES = {cls: name for name, cls in COMMAND_TYPES.items()}

Event = tuple(EVENT_TYPES.values())      # isinstance(x, Event)
Command = tuple(COMMAND_TYPES.values())  # isinstance(x, Command)
