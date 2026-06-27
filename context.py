"""Tracker de tokens de contexto.

Estimación local con tiktoken (cl100k_base — aproximado pero suficiente para
modelos no-OpenAI), y conteo exacto a partir del campo x_nanogpt_pricing
que nano-gpt devuelve en el último chunk de cada respuesta.
"""

import tiktoken

_ENCODER = tiktoken.get_encoding("cl100k_base")

# Ventanas de contexto orientativas. Si el modelo no está, se usa default.
# Ajusta a mano si conoces los límites reales de tu modelo.
CONTEXT_WINDOWS = {
    "deepseek/deepseek-v4-flash": 128_000,
    "deepseek-ai/DeepSeek-R1": 128_000,
    "xiaomi/mimo-v2.5-pro-ultraspeed": 128_000,
    # modelos locales servidos por llama-server
    "qwythos": 16_000,
    "hauhau": 16_000,
    "qwen35-9b": 16_000,
    "gemma4-12b": 8_192,
    "default": 128_000,
}


def get_context_window(model: str) -> int:
    return CONTEXT_WINDOWS.get(model, CONTEXT_WINDOWS["default"])


def estimate_messages(messages) -> int:
    """Estima tokens de una lista de messages al estilo OpenAI chat."""
    total = 0
    for m in messages:
        total += 4  # overhead aprox por mensaje
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
        self.last_input = 0       # tokens del último request (exacto, provider)
        self.last_output = 0      # tokens de la última respuesta
        self.total_output = 0     # acumulado de outputs en la sesión
        self.last_was_exact = False

    def update_from_chunk_dict(self, chunk_dict: dict):
        # nano-gpt: campo propietario x_nanogpt_pricing
        pricing = chunk_dict.get("x_nanogpt_pricing")
        if pricing:
            self.last_input = pricing.get("inputTokens", self.last_input)
            self.last_output = pricing.get("outputTokens", 0)
            self.total_output += self.last_output
            self.last_was_exact = True
            return
        # OpenAI estándar (llama-server con stream_options.include_usage)
        usage = chunk_dict.get("usage")
        if usage:
            self.last_input = usage.get("prompt_tokens", self.last_input)
            self.last_output = usage.get("completion_tokens", 0)
            self.total_output += self.last_output
            self.last_was_exact = True

    def current(self, messages) -> tuple[int, bool]:
        """Devuelve (tokens_estimados_para_proximo_request, es_exacto).

        Después de cada respuesta del modelo, el siguiente request tendrá los
        messages anteriores + la respuesta + lo nuevo. Aquí estimamos con
        tiktoken sobre los messages actuales — es estimación porque cada modelo
        tokeniza distinto, pero da una idea fiable del % usado.
        """
        return estimate_messages(messages), False

    def format_label(self, messages) -> str:
        used, _ = self.current(messages)
        pct = 100 * used / self.limit if self.limit else 0
        used_str = f"{used/1000:.1f}k" if used >= 1000 else str(used)
        limit_str = f"{self.limit//1000}k"
        # incluye el último dato exacto del provider si lo tenemos
        exact = f" · exact={self.last_input}↑/{self.last_output}↓" if self.last_was_exact else ""
        return f"ctx ~{used_str} / {limit_str} ({pct:.0f}%){exact}"
