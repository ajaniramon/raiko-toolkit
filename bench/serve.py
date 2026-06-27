"""Ciclo de vida de llama-server para el benchmark.

Arranca el servidor con --jinja (imprescindible para que el tool-calling
funcione en llama.cpp), espera a que /health diga ok, y al terminar mata el
árbol de procesos para no dejar el .exe ocupando puerto/VRAM.
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
    """Lanza llama-server para `model`. Devuelve el Popen ya con /health ok.

    Lanza RuntimeError si el modelo no carga dentro del timeout o el proceso
    muere durante el arranque.
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
    log(f"[serve] lanzando: {model['alias']}")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )

    deadline = time.time() + 240  # carga en GPU puede tardar
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"llama-server murió al arrancar (code {proc.returncode})")
        try:
            r = requests.get(health_url(), timeout=2)
            if r.status_code == 200 and r.json().get("status") == "ok":
                log(f"[serve] {model['alias']} listo en {base_url()}")
                return proc
        except (requests.RequestException, ValueError):
            pass
        time.sleep(1.5)

    stop_server(proc)
    raise RuntimeError(f"timeout esperando /health de {model['alias']}")


def stop_server(proc, log=print):
    """Mata el proceso y todo su árbol (Windows)."""
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
    # margen para que el SO libere el puerto antes del siguiente modelo
    time.sleep(2)
    log("[serve] servidor parado")
