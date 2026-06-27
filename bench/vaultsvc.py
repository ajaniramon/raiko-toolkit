"""Lifecycle of a local dev Vault for the benchmark circuit.

Starts `vault server -dev` (auto-unseal, fixed root token), waits for /health,
allows seeding KV v2 secrets, and kills it when done.
"""

import subprocess
import time

import requests

VAULT_BIN = r"C:\utils\vault.exe"
ADDR = "http://127.0.0.1:8200"
TOKEN = "root"


def start_vault(log=print):
    proc = subprocess.Popen(
        [VAULT_BIN, "server", "-dev",
         f"-dev-root-token-id={TOKEN}", "-dev-listen-address=127.0.0.1:8200"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    deadline = time.time() + 30
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"vault died on startup (code {proc.returncode})")
        try:
            r = requests.get(f"{ADDR}/v1/sys/health", timeout=2)
            if r.status_code in (200, 429, 473, 501):
                log(f"[vault] ready at {ADDR}")
                return proc
        except requests.RequestException:
            pass
        time.sleep(1)
    stop_vault(proc)
    raise RuntimeError("timeout waiting for vault /health")


def seed_secret(path, data, log=print):
    """Writes a KV v2 secret. path like 'secret/data/mac'."""
    r = requests.post(f"{ADDR}/v1/{path}", headers={"X-Vault-Token": TOKEN},
                      json={"data": data}, timeout=10)
    if r.status_code not in (200, 204):
        raise RuntimeError(f"seed failed {r.status_code}: {r.text[:200]}")
    log(f"[vault] secret written to {path}")


def stop_vault(proc, log=print):
    if proc and proc.poll() is None:
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
    time.sleep(1)
    log("[vault] stopped")
