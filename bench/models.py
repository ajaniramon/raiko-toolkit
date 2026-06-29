"""Registry of local models for the benchmark.

Machine-specific paths are NOT hardcoded here: they are read from `models.json`
(NOT versioned — see `models.example.json` for the shape). Copy the example to
`models.json` and fill in your llama-server path, your models folder, and one
entry per model:

    {
      "llama_server": "C:/llamacpp/llama-server.exe",
      "models_base": "C:/path/to/your/models",
      "models": [
        {"alias": "qwythos", "path": "C:/.../model.gguf", "ctx": 16000}
      ]
    }

`alias` is the name you'll see in the leaderboard (and the --alias that
llama-server receives). `ctx` must be <= what your VRAM can handle. The
LLAMA_SERVER / MODELS_BASE / MODELS values can also be overridden via the
LLAMA_SERVER and MODELS_BASE environment variables.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _config_path():
    """Where models.json lives. A frozen PyInstaller bundle runs from a read-only /
    relocatable dir, so __file__ is not writable — use ~/.raiko (the app's user home,
    overridable with RAIKO_HOME). Source runs keep using the repo's bench/models.json."""
    env = os.environ.get("RAIKO_HOME")
    if env:
        base = env
    elif getattr(sys, "frozen", False):
        base = os.path.join(os.path.expanduser("~"), ".raiko")
    else:
        base = HERE
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        pass
    return os.path.join(base, "models.json")


CONFIG_PATH = _config_path()


def _load_config():
    """Load local paths + model registry from models.json (NOT versioned).
    Returns an empty config if the file is missing, so the repo ships with no
    machine-specific paths baked in."""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


_CFG = _load_config()

LLAMA_SERVER = os.environ.get("LLAMA_SERVER", _CFG.get("llama_server", "llama-server"))
HOST = _CFG.get("host", "127.0.0.1")
PORT = int(_CFG.get("port", 25565))

# Base folder where the models live on disk. `discover()` walks it (tree)
# looking for .gguf files and keeps each one as a model.
MODELS_BASE = os.environ.get("MODELS_BASE", _CFG.get("models_base", ""))

# Curated registry, loaded from models.json (empty until you create that file).
MODELS = _CFG.get("models", [])


def gpu_mem():
    """Returns (free_GB, total_GB) of the GPU via nvidia-smi, or (None, None)."""
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
    """Estimates the largest ctx-size that fits in the free VRAM after loading the
    model weights. Conservative heuristic (rounds to multiples of 1024,
    cap 32768). If no GPU is detectable, 8192 by default."""
    free, _total = gpu_mem()
    try:
        weights = os.path.getsize(model_path) / 1e9
    except OSError:
        weights = 6.0
    if not free:
        return 8192
    budget = free - weights - reserve_gb          # GB left for the KV cache
    if budget <= 0.3:
        return 2048
    kv_per_1k = max(0.10, 0.12 * (weights / 6.0))  # GB per 1k tokens (approx)
    ctx = int((budget / kv_per_1k) * 1024)
    ctx = max(2048, min(32768, ctx))
    return (ctx // 1024) * 1024


def discover(base=MODELS_BASE):
    """Walks `base` (tree) and returns one model for each .gguf found.
    Excludes the mmproj files (vision projectors, not chat models).
    The alias is taken from the name of the folder containing the .gguf."""
    found = []
    if not os.path.isdir(base):
        return found
    for root, _dirs, files in os.walk(base):
        for f in sorted(files):
            if f.lower().endswith(".gguf") and "mmproj" not in f.lower():
                found.append({
                    "alias": os.path.basename(root) or os.path.splitext(f)[0],
                    "path": os.path.join(root, f),
                    "ctx": 8192,            # default, safe for the 4070
                    "discovered": True,
                })
    return found


def all_models(base=MODELS_BASE):
    """Curated registry (MODELS) + models discovered on disk that aren't already in it.
    Dedup by path; duplicate aliases are disambiguated with a suffix."""
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
    """Returns a model's dict by alias (searches registry + discovered)."""
    return next((m for m in all_models(base) if m["alias"] == alias), None)


def select(aliases=None):
    """Returns the list of models, filtered by a list of aliases if one is passed."""
    if not aliases:
        return list(MODELS)
    wanted = {a.strip() for a in aliases}
    chosen = [m for m in MODELS if m["alias"] in wanted]
    missing = wanted - {m["alias"] for m in chosen}
    if missing:
        raise SystemExit(f"Unknown aliases: {', '.join(sorted(missing))}. "
                         f"Available: {', '.join(m['alias'] for m in MODELS)}")
    return chosen
