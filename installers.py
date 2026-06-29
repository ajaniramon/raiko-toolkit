"""Optional dependency installers used by the configure wizard.

Downloads the external binaries the toolkit can use — the Jira CLI, HashiCorp
Vault and llama-server — picking the right release asset for the current OS /
arch (and CUDA, for llama-server). Everything is opt-in: the wizard only calls
these when the user clicks the button.

Targets the paths the rest of the app already looks in (Windows):
    jira  -> C:\\utils\\jira\\bin\\jira.exe   (tools._jira_bin default)
    vault -> C:\\utils\\vault.exe             (bench/vaultsvc.py)
    llama -> C:\\llamacpp\\llama-server.exe   (bench/models.json default)
On other OSes it installs under ~/.raiko/bin and returns the resolved path so
the caller can persist it.
"""

import os
import platform
import re
import subprocess
import tarfile
import tempfile
import zipfile


def detect() -> dict:
    """Return {os, arch, cuda} for the current machine."""
    sysname = platform.system().lower()
    osname = {"windows": "windows", "darwin": "macos", "linux": "linux"}.get(sysname, sysname)
    m = platform.machine().lower()
    arch = "arm64" if m in ("arm64", "aarch64") else ("x64" if m in ("x86_64", "amd64", "x64") else m)
    return {"os": osname, "arch": arch, "cuda": _cuda_version()}


def _cuda_version():
    """Driver's max CUDA version via nvidia-smi (float), or None if no NVIDIA GPU."""
    try:
        out = subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=5).stdout
        m = re.search(r"CUDA Version:\s*([0-9]+\.[0-9]+)", out)
        return float(m.group(1)) if m else None
    except Exception:
        return None


def describe() -> str:
    d = detect()
    gpu = f"CUDA {d['cuda']}" if d["cuda"] else "CPU only"
    return f"{d['os']} · {d['arch']} · {gpu}"


def _tools_dir() -> str:
    return r"C:\utils" if os.name == "nt" else os.path.join(os.path.expanduser("~"), ".raiko", "bin")


def _download(url: str, dest_path: str, log=print) -> str:
    import requests
    log(f"↓ {url.split('/')[-1]}")
    with requests.get(url, stream=True, timeout=180) as r:
        r.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
    return dest_path


def _extract(archive: str, dest_dir: str):
    os.makedirs(dest_dir, exist_ok=True)
    if archive.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest_dir)
    elif archive.endswith((".tar.gz", ".tgz")):
        with tarfile.open(archive) as t:
            t.extractall(dest_dir)
    else:
        raise ValueError(f"unknown archive type: {archive}")


def _find(root: str, exe: str):
    for base, _, files in os.walk(root):
        if exe in files:
            return os.path.join(base, exe)
    return None


def _gh_latest_assets(repo: str):
    import requests
    r = requests.get(f"https://api.github.com/repos/{repo}/releases/latest", timeout=20)
    r.raise_for_status()
    return [(a["name"], a["browser_download_url"]) for a in r.json().get("assets", [])]


def install_jira_cli(log=print):
    """Install ankitpokhrel/jira-cli. Returns (path_or_None, message)."""
    d = detect()
    os_tok = {"windows": "windows", "macos": "macOS", "linux": "linux"}[d["os"]]
    arch_tok = {"x64": "x86_64", "arm64": "arm64"}.get(d["arch"], d["arch"])
    cand = [u for n, u in _gh_latest_assets("ankitpokhrel/jira-cli")
            if os_tok in n and arch_tok in n]
    if not cand:
        return None, f"no jira-cli asset for {d['os']}/{d['arch']}"
    tmp = os.path.join(tempfile.gettempdir(), cand[0].split("/")[-1])
    _download(cand[0], tmp, log)
    base = os.path.join(_tools_dir(), "jira")
    _extract(tmp, base)
    exe = "jira.exe" if os.name == "nt" else "jira"
    path = _find(base, exe)
    if not path:
        return None, "jira-cli extracted but binary not found"
    if os.name != "nt":
        os.chmod(path, 0o755)
    return path, f"jira-cli → {path}"


def install_vault(log=print):
    """Install HashiCorp Vault. Returns (path_or_None, message)."""
    import requests
    d = detect()
    os_tok = {"windows": "windows", "macos": "darwin", "linux": "linux"}[d["os"]]
    arch_tok = {"x64": "amd64", "arm64": "arm64"}.get(d["arch"], d["arch"])
    data = requests.get("https://api.releases.hashicorp.com/v1/releases/vault/latest", timeout=20).json()
    url = next((b["url"] for b in data.get("builds", [])
                if b.get("os") == os_tok and b.get("arch") == arch_tok), None)
    if not url:
        return None, f"no vault build for {d['os']}/{d['arch']}"
    tmp = os.path.join(tempfile.gettempdir(), url.split("/")[-1])
    _download(url, tmp, log)
    dest = _tools_dir()
    _extract(tmp, dest)   # the vault zip holds a bare vault / vault.exe
    exe = "vault.exe" if os.name == "nt" else "vault"
    path = os.path.join(dest, exe)
    if not os.path.isfile(path):
        path = _find(dest, exe)
    if not path:
        return None, "vault extracted but binary not found"
    if os.name != "nt":
        os.chmod(path, 0o755)
    return path, f"vault → {path}"


def set_llama_path_in_models_json(path: str, repo_root: str = None):
    """Point bench/models.json's llama_server at a freshly installed binary."""
    import json
    root = repo_root or os.path.dirname(os.path.abspath(__file__))
    p = os.path.join(root, "bench", "models.json")
    try:
        cfg = json.load(open(p, encoding="utf-8")) if os.path.isfile(p) else {}
    except Exception:
        cfg = {}
    cfg["llama_server"] = path
    cfg.setdefault("host", "127.0.0.1")
    cfg.setdefault("port", 25565)
    cfg.setdefault("models_base", "")
    cfg.setdefault("models", [])
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(cfg, open(p, "w", encoding="utf-8"), indent=2)


def install_llama_server(log=print):
    """Install llama-server, picking CUDA (≤ driver) or CPU for this machine.
    Returns (path_or_None, message)."""
    d = detect()
    ot = {"windows": "win", "macos": "macos", "linux": "ubuntu"}[d["os"]]
    # Windows ships .zip; Linux/macOS ship .tar.gz. The CPU build is tagged "cpu"
    # only on Windows — on Linux/macOS the generic build is the plain
    # llama-…-bin-<os>-<arch> asset (no accelerator token).
    ext = ".zip" if d["os"] == "windows" else ".tar.gz"
    # Niche accelerator variants we can't assume the machine has; "cudart-…" is the
    # CUDA runtime, not the server, so require the asset to start with "llama-".
    EXCLUDE = ("rocm", "sycl", "openvino", "vulkan", "s390x", "musa", "hip")
    assets = [(n, u) for n, u in _gh_latest_assets("ggml-org/llama.cpp")
              if f"-bin-{ot}-" in n and n.endswith(ext) and n.startswith("llama-")
              and d["arch"] in n and not any(x in n for x in EXCLUDE)]

    def cuda_of(name):
        m = re.search(r"cuda-([0-9]+\.[0-9]+)", name)
        return float(m.group(1)) if m else 0.0

    chosen = None
    if d["cuda"]:
        cudas = [(n, u) for n, u in assets if "cuda" in n]
        usable = [c for c in cudas if cuda_of(c[0]) <= d["cuda"]] or cudas
        if usable:
            chosen = max(usable, key=lambda c: cuda_of(c[0]))
    if not chosen:
        # CPU / generic build: prefer an explicit "cpu" tag (Windows), else the
        # plain non-CUDA asset (Linux/macOS).
        cpu = [(n, u) for n, u in assets if "cuda" not in n]
        cpu.sort(key=lambda c: (0 if "cpu" in c[0] else 1, len(c[0])))
        chosen = cpu[0] if cpu else None
    if not chosen:
        return None, f"no llama-server build for {d['os']}/{d['arch']}"
    name, url = chosen
    tmp = os.path.join(tempfile.gettempdir(), name)
    _download(url, tmp, log)
    dest = r"C:\llamacpp" if os.name == "nt" else os.path.join(_tools_dir(), "llamacpp")
    _extract(tmp, dest)
    exe = "llama-server.exe" if os.name == "nt" else "llama-server"
    path = _find(dest, exe)
    if not path:
        return None, "llama-server extracted but binary not found"
    if os.name != "nt":
        os.chmod(path, 0o755)
    return path, f"llama-server → {path}  ({name})"
