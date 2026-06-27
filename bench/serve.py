"""llama-server lifecycle for the benchmark.

Starts the server with --jinja (essential for tool-calling to work in
llama.cpp), waits for /health to say ok, and when finished kills the process
tree so the .exe doesn't keep occupying the port/VRAM.
"""

import subprocess
import time

import requests

from models import LLAMA_SERVER, HOST, PORT


def base_url():
    return f"http://{HOST}:{PORT}/v1"


def health_url():
    return f"http://{HOST}:{PORT}/health"


def start_server(model: dict, log=print):
    """Launches llama-server for `model`. Returns the Popen once /health is ok.

    Raises RuntimeError if the model doesn't load within the timeout or the
    process dies during startup.
    """
    cmd = [
        LLAMA_SERVER,
        "--model", model["path"],
        "--host", HOST,
        "--port", str(PORT),
        "--ctx-size", str(model.get("ctx", 16000)),
        "--n-gpu-layers", "999",
        "--jinja",
        "--alias", model["alias"],
    ]
    log(f"[serve] launching: {model['alias']}")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )

    deadline = time.time() + 240  # GPU load can take a while
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"llama-server died on startup (code {proc.returncode})")
        try:
            r = requests.get(health_url(), timeout=2)
            if r.status_code == 200 and r.json().get("status") == "ok":
                log(f"[serve] {model['alias']} ready at {base_url()}")
                return proc
        except (requests.RequestException, ValueError):
            pass
        time.sleep(1.5)

    stop_server(proc)
    raise RuntimeError(f"timeout waiting for /health of {model['alias']}")


def stop_server(proc, log=print):
    """Kills the process and its entire tree (Windows)."""
    if proc is None or proc.poll() is not None:
        return
    try:
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        proc.kill()
    try:
        proc.wait(timeout=15)
    except Exception:
        pass
    # margin for the OS to release the port before the next model
    time.sleep(2)
    log("[serve] server stopped")
