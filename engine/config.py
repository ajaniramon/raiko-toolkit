"""Shared config: app home, tui_config.json load/save, provider maps, system
prompt assembly and loop constants. Moved verbatim from tui.py (Fase 2) so the
engine, the TUI and the web layer read the same configuration."""

import json
import os
import sys

MAX_ITERATIONS = 8
# How many times to retry a turn when the model leaks its tool call into the
# thinking and emits no real tool_call (qwen sometimes "thinks" the call and stops).
THINK_RETRIES = 2
# Hard ceiling for a single tool call so a hung tool / unreachable MCP can't freeze
# the turn. Above every tool's own timeout (run_python 20s, powershell/shell 25s).
TOOL_TIMEOUT = 60
# Auto-compact the conversation once context usage crosses this fraction of the window.
AUTO_COMPACT_PCT = 0.85


def _app_home():
    """Writable base dir for user data (config + sessions).

    A frozen PyInstaller bundle runs from a read-only / relocatable directory, so
    __file__ points inside the bundle — not a safe place to persist anything (the
    user never finds it and a reinstall wipes it). Write under ~/.raiko instead, the
    same home the optional-dependency installers already use. Source/dev runs keep
    using the repo directory so an existing tui_config.json is still picked up.
    Override either with the RAIKO_HOME env var.
    """
    env = os.environ.get("RAIKO_HOME")
    if env:
        base = env
    elif getattr(sys, "frozen", False):
        base = os.path.join(os.path.expanduser("~"), ".raiko")
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        pass
    return base


CONFIG_PATH = os.path.join(_app_home(), "tui_config.json")

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
    # Google Gemini via its OpenAI-compatible endpoint (same openai SDK)
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
               "api_key": "", "model": "gemini-2.5-flash", "ctx_window": 1048576,
               "reasoning_effort": "low",   # off | low | medium | high (2.5 thinking budget)
               "models": ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite",
                          "gemini-2.0-flash", "gemini-2.0-flash-lite"]},
    # remote llama.cpp: the URL is requested on selection and remembered here
    "remote": {"base_url": "", "api_key": "sk-noop", "model": "", "ctx_window": 16000},
    # vLLM: an OpenAI-compatible server (vllm serve …). URL requested on selection.
    "vllm": {"base_url": "", "api_key": "sk-noop", "model": "", "ctx_window": 131072},
    # MCP client: connect raiko to external MCP servers. Each server's tools are
    # exposed to the agent name-prefixed. Edit in tui_config.json:
    #   "mcp": {"enabled": true, "servers": [{"name":"fs","url":"http://localhost:8765/mcp","prefix":"fs_"}]}
    "mcp": {"enabled": False, "servers": []},
    "tavily_api_key": "",   # for the web_search tool (free key at tavily.com)
    "auto_compact": True,   # auto-summarize older turns when the context fills up
    "max_iterations": 8,    # max tool-call rounds per turn (agent loop cap)
    "budget_usd": 0,        # session spend cap in USD (0 = off); blocks new turns when hit
    "pricing": {},          # override/extend the model price table: {"model-substr": [in$/1M, out$/1M]}
    # tool permissions: `allow`/`deny` are tool names (Always-allow persists here);
    # `workspace` confines write_file/edit_file (empty = the launch directory).
    "permissions": {"allow": [], "deny": [], "workspace": ""},
    # web layer (raiko web): bind/token/CORS/exec policy. Loopback + no exec by default.
    "web": {"host": "127.0.0.1", "port": 8484, "token": "",
            "allowed_origins": [], "allow_exec": False,
            "max_live_sessions": 16, "session_ttl_seconds": 3600,
            "queue_size": 4096},
    # named system-prompt presets (persona text; "" = built-in default persona).
    # TOOL_RULES are always appended. Edit/add here or via the F6 editor in-app.
    "system_prompts": {"default": ""},
    "active_system_prompt": "default",   # used for NEW sessions
    "last": {"provider": None, "model": None},   # last used (default on startup)
    "favorites": {"nano": [], "xai": [], "openrouter": [], "openai": [], "anthropic": [],
                  "gemini": [], "remote": [], "vllm": []},
}

# OpenAI-compatible providers served via API (GPU sidebar OFF; model's ctx)
CLOUD = {"nano", "xai", "openrouter", "openai", "anthropic", "gemini", "remote", "vllm"}
# providers whose endpoint is a user-supplied URL (no API key, list models live)
URL_PROVIDERS = {"remote", "vllm"}

# The persona is user-configurable (via presets); TOOL_RULES are mechanical rules
# that are ALWAYS appended so a custom persona can't break tool-calling.
DEFAULT_PERSONA = (
    "You are an agent with access to file, execution and system tools. Use them when needed. "
    "Before each tool call, briefly explain in 1-2 sentences what you are about to do and why."
)
TOOL_RULES = (
    "TOOL ARGUMENTS RULE: every tool call's arguments must be ONE valid JSON object — escape "
    "newlines as \\n and double quotes as \\\". Keep run_python / run_shell code SHORT (a few "
    "lines). If you need a longer or multi-line script, do NOT paste it as a tool argument: "
    "first save it to a file with write_file, then run it with run_python or run_shell. This "
    "avoids invalid-JSON errors from large code blocks.\n"
    "When you have the answer, give it directly. Format your final answers in clean Markdown."
)


def build_system_prompt(persona):
    """Effective system prompt = (persona or default) + the fixed TOOL_RULES."""
    persona = (persona or "").strip() or DEFAULT_PERSONA
    return f"{persona}\n{TOOL_RULES}"


# back-compat alias (used as the built-in default)
SYSTEM_PROMPT = build_system_prompt(DEFAULT_PERSONA)


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
    sp = cfg.setdefault("system_prompts", {"default": ""})
    if not isinstance(sp, dict) or not sp:
        cfg["system_prompts"] = sp = {"default": ""}
    if cfg.setdefault("active_system_prompt", "default") not in sp:
        cfg["active_system_prompt"] = next(iter(sp))
    # make the web_search tool's key available to tools.py without leaking it in source
    if cfg.get("tavily_api_key") and not os.environ.get("TAVILY_API_KEY"):
        os.environ["TAVILY_API_KEY"] = cfg["tavily_api_key"]
    # same for the Jira CLI token, so the jira_search/jira_get tools work in-session
    jira_cfg = cfg.get("jira", {}) if isinstance(cfg.get("jira"), dict) else {}
    jira_token = jira_cfg.get("api_token")
    if jira_token and not os.environ.get("JIRA_API_TOKEN"):
        os.environ["JIRA_API_TOKEN"] = jira_token
    if jira_cfg.get("cli_path") and not os.environ.get("JIRA_CLI"):
        os.environ["JIRA_CLI"] = jira_cfg["cli_path"]
    # Confluence reuses the same Atlassian token (account-scoped); it only needs the
    # site base url + login email, which the confluence_* tools read from the env.
    conf = cfg.get("confluence", {}) if isinstance(cfg.get("confluence"), dict) else {}
    if conf.get("base_url") and not os.environ.get("CONFLUENCE_BASE_URL"):
        os.environ["CONFLUENCE_BASE_URL"] = conf["base_url"]
    if conf.get("email") and not os.environ.get("CONFLUENCE_EMAIL"):
        os.environ["CONFLUENCE_EMAIL"] = conf["email"]
    if conf.get("space") and not os.environ.get("CONFLUENCE_SPACE"):
        os.environ["CONFLUENCE_SPACE"] = conf["space"]
    return cfg


def save_config(cfg):
    """Persist config to CONFIG_PATH. Returns True on success, False on failure so
    callers can surface it (a silent failure is how a 'saved' config vanishes)."""
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        return True
    except Exception:
        return False


# A provider's API key can live in tui_config.json or in the conventional env var.
PROVIDER_ENV = {
    "anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY", "xai": "XAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY", "nano": "NANO_GPT_API_KEY",
}


def resolve_key(provider, pcfg):
    """API key for a provider: the config value, else the conventional env var
    (so a key exported in the shell just works, without writing it to disk)."""
    return (pcfg or {}).get("api_key") or os.environ.get(PROVIDER_ENV.get(provider, ""), "") or ""
