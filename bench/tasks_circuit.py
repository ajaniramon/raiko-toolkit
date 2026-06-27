"""Tier CIRCUITO: el agente saca un secreto de Vault y copia un fichero al Mac.

Cada tarea verifica el efecto REAL: lee de vuelta el fichero por SSH en el Mac
(con las credenciales conocidas) y limpia después. Las rutas remotas incluyen el
alias del modelo para que no colisionen entre runs.
"""

import paramiko

PAYLOAD_TOKEN = "NEBULA-PAYLOAD-7788"


def _verify_remote(verify, remote_path, expected_substr, cleanup=True):
    """Conecta al Mac, comprueba que remote_path contiene expected_substr y lo borra."""
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
    """verify: {host, port, username, password}. alias: para rutas remotas únicas."""
    tasks = []
    home = f"/Users/{verify['username']}"

    def add(id, prompt, expect, check):
        tasks.append({"id": id, "category": "circuit", "prompt": prompt,
                      "expect_tools": expect, "check": check, "negative": False})

    # 1) solo Vault: leer el secreto y reportar el usuario
    add("c_vault_read",
        "Use the Vault tool to read the secret stored at path 'secret/data/mac', "
        "and report the value of its 'username' field.",
        ["vault_get_secret"],
        lambda a, r: verify["username"].lower() in a.lower())

    # 2) circuito completo: secreto de Vault -> copiar payload.txt al Mac
    rp_payload = f"{home}/agent_drop_{alias}_payload.txt"
    add("c_copy_payload",
        "Do this as a two-step circuit: (1) retrieve the Mac SSH credentials from Vault "
        "at path 'secret/data/mac' — the secret contains the fields host, port, username "
        "and password. (2) Using exactly those credentials, copy the local file "
        f"'payload.txt' to the Mac at the remote path '{rp_payload}'.",
        ["vault_get_secret", "copy_file_to_mac"],
        (lambda rp: lambda a, r: _verify_remote(verify, rp, PAYLOAD_TOKEN))(rp_payload))

    # 3) circuito completo con otro fichero (config.ini)
    rp_conf = f"{home}/agent_drop_{alias}_config.ini"
    add("c_copy_config",
        "Retrieve the Mac SSH credentials from Vault (path 'secret/data/mac'), then use them "
        f"to copy the local file 'config.ini' to the Mac at the remote path '{rp_conf}'.",
        ["vault_get_secret", "copy_file_to_mac"],
        (lambda rp: lambda a, r: _verify_remote(verify, rp, "port=8080"))(rp_conf))

    return tasks
