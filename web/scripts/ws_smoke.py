"""WS smoke client for `raiko web` — also the reference client for the HOMELAB
panel (this is exactly the handshake + command/event flow the AGENT tab speaks;
docs/ws-protocol.md documents the schema).

    python web/scripts/ws_smoke.py --provider anthropic --model claude-haiku-4-5 \
        --prompt "which files are in the cwd?" [--url http://127.0.0.1:8484] [--token X]

Completes one end-to-end turn: create session (POST) -> connect WS -> send ->
stream deltas/tool calls -> turn_done. Answers any permission_required with
allow_once (it's a smoke: watch what you ask it to do). Prints a telemetry line
count to prove the periodic feed works.
"""

import argparse
import asyncio
import json
import sys

import requests
import websockets

# Windows consoles default to cp1252, which can't print the arrows/icons below.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


async def run(base: str, token: str, provider: str, model: str, prompt: str):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = requests.post(f"{base}/api/sessions", headers=headers,
                      json={"provider": provider, "model": model}, timeout=30)
    r.raise_for_status()
    info = r.json()
    sid = info["session_id"]
    print(f"session {sid} · {info['provider']} · {info['model']} · "
          f"ctx {info.get('ctx_window')} · protocol {info.get('protocol_version')} · "
          f"exec {'ON' if info.get('exec_enabled') else 'off'}")

    ws_url = base.replace("http://", "ws://").replace("https://", "wss://")
    uri = f"{ws_url}/ws/{sid}" + (f"?token={token}" if token else "")
    telemetry_n = 0
    async with websockets.connect(uri) as ws:
        first = json.loads(await ws.recv())
        assert first["type"] == "session_started", first
        print(f"connected · protocol v{first['protocol_version']}")
        await ws.send(json.dumps({"type": "send", "text": prompt}))
        while True:
            e = json.loads(await ws.recv())
            t = e["type"]
            if t == "text_delta":
                print(e["text"], end="", flush=True)
            elif t == "thinking_delta":
                pass   # keep the smoke output readable
            elif t == "segment_end":
                print()
            elif t == "tool_call_started":
                print(f"🔧 {e['name']}({e['args'][:80]})")
            elif t == "tool_call_result":
                print(f"  ⎿ {'✓' if e['ok'] else '✗'} {e['summary']}")
            elif t == "permission_required":
                print(f"⚠ permission_required [{e['scope']}] {e['tool']}: {e['action']} -> allow_once")
                await ws.send(json.dumps({"type": "permission_response",
                                          "perm_id": e["perm_id"], "decision": "allow_once"}))
            elif t == "telemetry":
                telemetry_n += 1
            elif t == "cost_update":
                print(f"  $ turn={e['turn_usd']:.4f} session={e['session_usd']:.4f} "
                      f"({e['input_tokens']}↑/{e['output_tokens']}↓)")
            elif t == "notice":
                print(f"· {e['text']}")
            elif t == "error":
                print(f"✗ error: {e['message']}", file=sys.stderr)
            elif t == "turn_done":
                print(f"— turn_done: {e['reason']} · {e['output_tokens']} tok · "
                      f"{e['tok_s']:.1f} tok/s · {e['elapsed_s']:.1f}s · "
                      f"telemetry events so far: {telemetry_n}")
                return 0 if e["reason"] == "completed" else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8484")
    ap.add_argument("--token", default="")
    ap.add_argument("--provider", default="anthropic")
    ap.add_argument("--model", default="claude-haiku-4-5")
    ap.add_argument("--prompt", default="List the files in the current directory and summarize.")
    a = ap.parse_args()
    sys.exit(asyncio.run(run(a.url.rstrip("/"), a.token, a.provider, a.model, a.prompt)))


if __name__ == "__main__":
    main()
