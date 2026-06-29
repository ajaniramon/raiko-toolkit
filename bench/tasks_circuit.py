"""CIRCUIT tier: the agent pulls a secret from Vault and copies a file to the Mac.

Each task verifies the REAL effect: it reads the file back over SSH on the Mac
(with the known credentials) and cleans up afterward. The remote paths include
the model alias so they don't collide between runs.
"""

import paramiko

from tasks import has_number, contains

PAYLOAD_TOKEN = "NEBULA-PAYLOAD-7788"

# Vault-only retrieval tasks (no Mac needed): a deterministic set of KV v2 secrets
# is seeded locally and the agent must read specific fields back. This is what
# lets the circuit tier reach 200+ tasks without hammering the real Mac over SSH
# (only the handful of copy_file_to_mac tasks below are infra-bound).
_OWNERS = ["alice", "bob", "carol", "dan", "erin", "frank", "grace", "heidi"]
_ENVS = ["dev", "staging", "prod", "qa"]


def extra_vault_secrets(n=100):
    """Returns {vault_path: {fields}} — deterministic, seeded by the runner."""
    secrets = {}
    for i in range(n):
        secrets[f"secret/data/svc{i:03d}"] = {
            "token": f"tok-{i:04d}",
            "port": str(9000 + i),
            "owner": _OWNERS[i % len(_OWNERS)],
            "env": _ENVS[i % len(_ENVS)],
        }
    return secrets


def _verify_remote(verify, remote_path, expected_substr, cleanup=True):
    """Connects to the Mac, checks that remote_path contains expected_substr and deletes it."""
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        c.connect(verify["host"], int(verify.get("port", 22)),
                  verify["username"], verify["password"], timeout=15)
        _, o, _ = c.exec_command("cat " + remote_path)
        content = o.read().decode("utf-8", "replace")
        ok = expected_substr in content
        if cleanup:
            c.exec_command("rm -f " + remote_path)
        return ok
    except Exception:
        return False
    finally:
        c.close()


def build_circuit_tasks(verify, alias):
    """verify: {host, port, username, password}. alias: for unique remote paths."""
    tasks = []
    home = f"/Users/{verify['username']}"

    def add(id, prompt, expect, check):
        tasks.append({"id": id, "category": "circuit", "prompt": prompt,
                      "expect_tools": expect, "check": check, "negative": False})

    # 1) Vault only: read the secret and report the username
    add("c_vault_read",
        "Use the Vault tool to read the secret stored at path 'secret/data/mac', "
        "and report the value of its 'username' field.",
        ["vault_get_secret"],
        lambda a, r: verify["username"].lower() in a.lower())

    # 2) full circuit: Vault secret -> copy payload.txt to the Mac
    rp_payload = f"{home}/agent_drop_{alias}_payload.txt"
    add("c_copy_payload",
        "Do this as a two-step circuit: (1) retrieve the Mac SSH credentials from Vault "
        "at path 'secret/data/mac' — the secret contains the fields host, port, username "
        "and password. (2) Using exactly those credentials, copy the local file "
        f"'payload.txt' to the Mac at the remote path '{rp_payload}'.",
        ["vault_get_secret", "copy_file_to_mac"],
        (lambda rp: lambda a, r: _verify_remote(verify, rp, PAYLOAD_TOKEN))(rp_payload))

    # 3) full circuit with another file (config.ini)
    rp_conf = f"{home}/agent_drop_{alias}_config.ini"
    add("c_copy_config",
        "Retrieve the Mac SSH credentials from Vault (path 'secret/data/mac'), then use them "
        f"to copy the local file 'config.ini' to the Mac at the remote path '{rp_conf}'.",
        ["vault_get_secret", "copy_file_to_mac"],
        (lambda rp: lambda a, r: _verify_remote(verify, rp, "port=8080"))(rp_conf))

    # ---- Vault-only retrieval family (local; no Mac) ----
    for path, data in extra_vault_secrets().items():
        sid = path.split("/")[-1]
        add(f"v_token_{sid}",
            f"Use the Vault tool to read the secret at path '{path}' and report the value of "
            f"its 'token' field.",
            ["vault_get_secret"],
            (lambda v: lambda a, r: contains(a, v))(data["token"]))
        add(f"v_port_{sid}",
            f"Use the Vault tool to read the secret at path '{path}' and report the value of "
            f"its 'port' field.",
            ["vault_get_secret"],
            (lambda v: lambda a, r: has_number(a, int(v)))(data["port"]))

    return tasks
