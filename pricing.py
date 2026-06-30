"""Approximate USD pricing for cloud models, used for a live cost estimate in the
TUI. Prices are per 1M tokens (input, output) and matched as a substring of the
model id (longest match wins). They drift — override/extend them with a `pricing`
map in tui_config.json. Provider-reported cost (nano-gpt, OpenRouter) is preferred
when available; this table is the fallback. Self-hosted providers cost nothing.
"""

# model-id substring -> (input $/1M, output $/1M)
DEFAULT_PRICES = {
    # OpenAI
    "gpt-5-mini": (0.25, 2.0), "gpt-5": (1.25, 10.0),
    "gpt-4o-mini": (0.15, 0.60), "gpt-4o": (2.5, 10.0), "o3-mini": (1.1, 4.4), "o3": (2.0, 8.0),
    # Anthropic
    "claude-opus-4": (5.0, 25.0), "claude-sonnet-4": (3.0, 15.0), "claude-haiku-4": (1.0, 5.0),
    "claude-fable-5": (10.0, 50.0), "claude-mythos-5": (10.0, 50.0),
    # Google Gemini
    "gemini-2.5-pro": (1.25, 10.0), "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50), "gemini-2.0-flash-lite": (0.075, 0.30),
    "gemini-2.0-flash": (0.10, 0.40),
    # xAI
    "grok-4": (3.0, 15.0), "grok-3-mini": (0.30, 0.50), "grok-3": (3.0, 15.0),
    # DeepSeek
    "deepseek-v4-pro": (0.55, 2.19), "deepseek-v4-flash": (0.27, 1.10), "deepseek": (0.27, 1.10),
}


def price_for(model: str, overrides: dict = None):
    """Return (in, out) $/1M for a model id, or None if unknown."""
    m = (model or "").lower()
    table = dict(DEFAULT_PRICES)
    for k, v in (overrides or {}).items():
        try:
            table[k.lower()] = (float(v[0]), float(v[1]))
        except (TypeError, ValueError, IndexError):
            pass
    best_key = None
    for key in table:
        if key in m and (best_key is None or len(key) > len(best_key)):
            best_key = key
    return table[best_key] if best_key else None


def cost_usd(model: str, in_tok: int, out_tok: int, overrides: dict = None):
    """Estimated USD cost of a call, or None if the model isn't in the table."""
    pr = price_for(model, overrides)
    if not pr:
        return None
    return in_tok / 1_000_000 * pr[0] + out_tok / 1_000_000 * pr[1]


def fmt_usd(amount: float) -> str:
    """Compact money formatting: $0.0042, $1.37, $12.40."""
    if amount is None:
        return "$?"
    if amount < 1:
        return f"${amount:.4f}".rstrip("0").rstrip(".") if amount else "$0"
    return f"${amount:,.2f}"
