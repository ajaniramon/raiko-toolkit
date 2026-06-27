"""Registro de modelos locales para el benchmark.

Para añadir un modelo nuevo: copia una línea y apunta `path` a su .gguf.
`alias` es el nombre que verás en el leaderboard (y el --alias que recibe
llama-server). `ctx` debe ser <= a lo que aguante tu VRAM.
"""

import os

LLAMA_SERVER = r"C:\llamacpp\llama-server.exe"
HOST = "127.0.0.1"
PORT = 25565

# Carpeta base donde viven los modelos en disco. `discover()` la recorre (tree)
# buscando ficheros .gguf y se queda con cada uno como un modelo.
MODELS_BASE = r"C:\Users\reimon\.lmstudio\models"

MODELS = [
    {
        "alias": "qwythos",
        "path": r"C:\Users\reimon\.lmstudio\models\mradermacher\Qwythos\Qwythos-9B-Claude-Mythos-5-1M-MTP-Q4_K_M.gguf",
        "ctx": 16000,
    },
    {
        "alias": "hauhau",
        "path": r"C:\Users\reimon\.lmstudio\models\HauhauCS\Qwen3.5-9B-Uncensored-HauhauCS-Aggressive\Qwen3.5-9B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf",
        "ctx": 16000,
    },
    # --- modelos modernos descargados para comparar (familias distintas) ---
    {
        "alias": "qwen35-9b",
        "path": r"C:\Users\reimon\.lmstudio\models\mradermacher\Qwen3.5-9B\Qwen3.5-9B-Q4_K_M.gguf",
        "ctx": 16000,
    },
    {
        "alias": "gemma4-12b",
        "path": r"C:\Users\reimon\.lmstudio\models\mradermacher\Gemma4-12B\gemma-4-12b-it-Q4_K_M.gguf",
        "ctx": 8192,   # 12B: ctx menor para no apurar la VRAM de la 4070
    },
    # (Nemotron Nano v2 descartado: es de 2025. GLM pequeño descartado: el único
    #  es GLM-4.6V-Flash 9B de dic-2025 y es de visión; los GLM de 2026 son 700B+.)
    # Añade aquí más modelos cuando quieras compararlos:
    # {"alias": "loquesea", "path": r"C:\ruta\al\modelo.gguf", "ctx": 16000},
]


def gpu_mem():
    """Devuelve (free_GB, total_GB) de la GPU vía nvidia-smi, o (None, None)."""
    import subprocess
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=4).stdout.strip().splitlines()[0]
        free, total = [float(x) for x in out.split(",")]
        return free / 1024.0, total / 1024.0
    except Exception:
        return None, None


def optimal_ctx(model_path, reserve_gb=1.5):
    """Estima el ctx-size más grande que cabe en la VRAM libre tras cargar los
    pesos del modelo. Heurístico conservador (redondea a múltiplos de 1024,
    cap 32768). Si no hay GPU detectable, 8192 por defecto."""
    free, _total = gpu_mem()
    try:
        weights = os.path.getsize(model_path) / 1e9
    except OSError:
        weights = 6.0
    if not free:
        return 8192
    budget = free - weights - reserve_gb          # GB que quedan para el KV cache
    if budget <= 0.3:
        return 2048
    kv_per_1k = max(0.10, 0.12 * (weights / 6.0))  # GB por cada 1k tokens (aprox)
    ctx = int((budget / kv_per_1k) * 1024)
    ctx = max(2048, min(32768, ctx))
    return (ctx // 1024) * 1024


def discover(base=MODELS_BASE):
    """Recorre `base` (tree) y devuelve un modelo por cada .gguf encontrado.
    Excluye los mmproj (proyectores de visión, no son modelos de chat).
    El alias se toma del nombre de la carpeta que contiene el .gguf."""
    found = []
    if not os.path.isdir(base):
        return found
    for root, _dirs, files in os.walk(base):
        for f in sorted(files):
            if f.lower().endswith(".gguf") and "mmproj" not in f.lower():
                found.append({
                    "alias": os.path.basename(root) or os.path.splitext(f)[0],
                    "path": os.path.join(root, f),
                    "ctx": 8192,            # por defecto, seguro para la 4070
                    "discovered": True,
                })
    return found


def all_models(base=MODELS_BASE):
    """Registro curado (MODELS) + modelos descubiertos en disco que no estén ya.
    Dedup por ruta; alias duplicados se desambiguan con sufijo."""
    known_paths = {os.path.normcase(m["path"]) for m in MODELS}
    seen_alias = {m["alias"] for m in MODELS}
    out = [dict(m) for m in MODELS]
    for m in discover(base):
        if os.path.normcase(m["path"]) in known_paths:
            continue
        alias, i = m["alias"], 2
        while alias in seen_alias:
            alias = f"{m['alias']}-{i}"
            i += 1
        seen_alias.add(alias)
        out.append(dict(m, alias=alias))
    return out


def find(alias, base=MODELS_BASE):
    """Devuelve el dict de un modelo por alias (busca en registro + descubiertos)."""
    return next((m for m in all_models(base) if m["alias"] == alias), None)


def select(aliases=None):
    """Devuelve la lista de modelos, filtrada por una lista de alias si se pasa."""
    if not aliases:
        return list(MODELS)
    wanted = {a.strip() for a in aliases}
    chosen = [m for m in MODELS if m["alias"] in wanted]
    missing = wanted - {m["alias"] for m in chosen}
    if missing:
        raise SystemExit(f"Alias desconocidos: {', '.join(sorted(missing))}. "
                         f"Disponibles: {', '.join(m['alias'] for m in MODELS)}")
    return chosen
