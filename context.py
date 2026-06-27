"""Context token tracker.

Local estimation with tiktoken (cl100k_base — approximate but good enough for
non-OpenAI models), and exact counting from the x_nanogpt_pricing field
that nano-gpt returns in the last chunk of each response.
"""

import tiktoken

_ENCODER = tiktoken.get_encoding("cl100k_base")

# Indicative context windows. If the model is not listed, the default is used.
# Adjust by hand if you know the real limits of your model.
CONTEXT_WINDOWS = {
    "deepseek/deepseek-v4-flash": 128_000,
    "deepseek-ai/DeepSeek-R1": 128_000,
    "xiaomi/mimo-v2.5-pro-ultraspeed": 128_000,
    # local models served by llama-server
    "qwythos": 16_000,
    "hauhau": 16_000,
    "qwen35-9b": 16_000,
    "gemma4-12b": 8_192,
    "default": 128_000,
}


def get_context_window(model: str) -> int:
    return CONTEXT_WINDOWS.get(model, CONTEXT_WINDOWS["default"])


def estimate_messages(messages) -> int:
    """Estimate tokens of an OpenAI chat-style list of messages."""
    total = 0
    for m in messages:
        total += 4  # approx overhead per message
        for k, v in m.items():
            if k == "role":
                total += 1
                continue
            if isinstance(v, str):
                total += len(_ENCODER.encode(v))
            elif isinstance(v, list):
                # tool_calls
                for tc in v:
                    fn = tc.get("function", {})
                    total += len(_ENCODER.encode(fn.get("name", "")))
                    total += len(_ENCODER.encode(fn.get("arguments", "")))
    return total


class ContextTracker:
    def __init__(self, model: str):
        self.model = model
        self.limit = get_context_window(model)
        self.last_input = 0       # tokens of the last request (exact, from provider)
        self.last_output = 0      # tokens of the last response
        self.total_output = 0     # accumulated outputs in the session
        self.last_was_exact = False

    def update_from_chunk_dict(self, chunk_dict: dict):
        # nano-gpt: proprietary x_nanogpt_pricing field
        pricing = chunk_dict.get("x_nanogpt_pricing")
        if pricing:
            self.last_input = pricing.get("inputTokens", self.last_input)
            self.last_output = pricing.get("outputTokens", 0)
            self.total_output += self.last_output
            self.last_was_exact = True
            return
        # standard OpenAI (llama-server with stream_options.include_usage)
        usage = chunk_dict.get("usage")
        if usage:
            self.last_input = usage.get("prompt_tokens", self.last_input)
            self.last_output = usage.get("completion_tokens", 0)
            self.total_output += self.last_output
            self.last_was_exact = True

    def current(self, messages) -> tuple[int, bool]:
        """Returns (estimated_tokens_for_next_request, is_exact).

        After each model response, the next request will have the
        previous messages + the response + the new content. Here we estimate with
        tiktoken over the current messages — it's an estimate because each model
        tokenizes differently, but it gives a reliable idea of the % used.
        """
        return estimate_messages(messages), False

    def format_label(self, messages) -> str:
        used, _ = self.current(messages)
        pct = 100 * used / self.limit if self.limit else 0
        used_str = f"{used/1000:.1f}k" if used >= 1000 else str(used)
        limit_str = f"{self.limit//1000}k"
        # include the last exact data from the provider if we have it
        exact = f" · exact={self.last_input}↑/{self.last_output}↓" if self.last_was_exact else ""
        return f"ctx ~{used_str} / {limit_str} ({pct:.0f}%){exact}"
