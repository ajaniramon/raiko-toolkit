# raiko web — contrato WS/API (protocol v1.0)

**Fuente de verdad compartida** entre raiko (`engine/protocol.py`, `web/server.py`) y el
panel HOMELAB (pestaña AGENT + SERVICE MATRIX). El panel implementa ESTE documento; el
cliente de referencia es `web/scripts/ws_smoke.py`.

**Versionado:** `PROTOCOL_VERSION` (hoy `"1.0"`) viaja en `session_started.protocol_version`
y en la respuesta de `POST /api/sessions`. Un cambio incompatible en cualquier evento o
comando la incrementa — el cliente debe comprobarla al conectar y rehusar/avisar si no
coincide con la que implementa.

## Arranque

```
raiko web [--host H] [--port P]        # o: python web/server.py
```

Config en `tui_config.json` → clave `web`:

```json
"web": {
  "host": "127.0.0.1",          // bind; no-loopback EXIGE token (si no, rehúsa arrancar)
  "port": 8484,
  "token": "",                  // Bearer/query token; vacío solo aceptable en loopback
  "allowed_origins": [],        // CORS whitelist explícita (el origen del panel HOMELAB); nunca '*'
  "allow_exec": false           // false = run_python/run_powershell/run_bash deshabilitados por completo
}
```

## Auth y CORS

- Toda petición HTTP y el WS llevan `Authorization: Bearer <token>` **o** `?token=<token>`.
  Sin token válido: HTTP 401 / cierre WS con código `4401`.
- CORS: solo los orígenes de `web.allowed_origins` (p.ej. `http://homelab.local:5173`).
  Sin lista configurada no se añade middleware CORS (mismo-origen/no-navegador).

## API REST

### `GET /api/sessions`
```json
{ "saved": [{"id","title","provider","model","updated","messages"}],
  "live":  [{"session_id","provider","model","busy","engine_session_id"}] }
```
`saved` = sesiones persistidas en disco (para el Continue/New del panel). `live` =
sesiones adjuntables ahora mismo por WS.

### `POST /api/sessions`
Body: `{"provider": "...", "model": "...", "ctx_window"?: int, "resume"?: "<saved id>"}`.
Con `resume`, provider/model se toman de la sesión guardada si se omiten. `provider:
"local"` se adjunta a un llama-server YA corriendo (nunca lo arranca).

201 → `{"session_id", "provider", "model", "ctx_window", "protocol_version", "exec_enabled"}`.
Errores: 400 (body/params/configure), 401, 404 (`resume` desconocido).

## WebSocket `WS /ws/{session_id}`

JSON en ambos sentidos; cada mensaje lleva `"type"` más los campos del payload.
Al conectar, el servidor envía `session_started`. Cierres: `4401` sin auth, `4404`
sesión desconocida. Si el cliente desconecta a mitad de turno, el turno se interrumpe.

### Comandos (cliente → servidor)

| type | payload | notas |
|---|---|---|
| `send` | `{text}` | inicia un turno; error si ya hay uno corriendo; `turn_done{budget_exceeded}` si el budget está agotado |
| `interrupt` | `{}` | detiene el turno en curso (no-op si idle) |
| `permission_response` | `{perm_id, decision}` | `allow_once` \| `allow_always` \| `deny` |
| `swap_model` | `{provider?, model}` | cambia de modelo conservando historial (no mid-turn) |
| `compact` | `{}` | resume turnos antiguos (→ evento `compacted`) |
| `clear` | `{}` | vacía la conversación (conserva system prompt) |
| `rewind_last_user` | `{}` | retira el último mensaje de usuario; responde `notice{kind:"rewind", text:<texto retirado>}` |
| `set_system_prompt` | `{name?, text?}` | `text` gana; `name` refiere a un preset guardado |

### Eventos (servidor → cliente)

| type | payload |
|---|---|
| `session_started` | `{session_id, provider, model, ctx_window, protocol_version}` |
| `turn_started` | `{text}` — eco del mensaje del usuario, tras el posible auto-compact |
| `thinking_delta` / `text_delta` | `{text}` — streaming del razonamiento / la respuesta |
| `segment_end` | `{content, thinking}` — textos FINALES del segmento (pueden diferir de la concatenación de deltas si se recuperó un tool-call emitido como texto); cierra el bloque vivo |
| `tool_call_started` | `{call_id, name, args}` (`args` = JSON string crudo del modelo) |
| `tool_call_result` | `{call_id, name, ok, summary, result, diff?, path?}` — `diff` = unified diff en `write_file`/`edit_file`; `ok=false` en ERROR/DENIED |
| `permission_required` | `{perm_id, tool, action, detail, scope}` — el turno queda EN PAUSA hasta el `permission_response` con ese `perm_id` (timeout 300 s → deny). `scope`: `danger` \| `workspace` \| `external_write` |
| `cost_update` | `{input_tokens, output_tokens, turn_usd, session_usd, budget_usd?}` — tras cada request al modelo |
| `turn_done` | `{reason, elapsed_s, output_tokens, tok_s, turn_usd}` — `reason`: `completed` \| `interrupted` \| `error` \| `max_iterations` \| `budget_exceeded` |
| `compacted` | `{before_messages, after_messages, summary, auto}` |
| `model_swapped` | `{old_model, new_model, provider}` |
| `telemetry` | `{gpu_name, gpu_util, vram_used_mb, vram_total_mb, ram_used_gb, ram_total_gb, cpu, temp_c, power_w, tok_s?}` — cada ~2 s; campos `null` si el sensor no existe |
| `notice` | `{text, kind}` — informativo (`info` \| `warning` \| `rewind`) |
| `error` | `{message}` |

### Flujo de un turno

```
send → turn_started → [thinking_delta|text_delta]* → segment_end
     → ( tool_call_started → [permission_required ⇢ permission_response]? → tool_call_result )*
     → [más segmentos…] → cost_update → turn_done
```

## Seguridad (resumen operativo)

Los exec tools sobre red son **RCE en la máquina**. Por eso:

1. **Bind loopback por defecto**; `raiko web` **rehúsa arrancar** en un host no-loopback
   sin `web.token` (exit 2).
2. `web.allow_exec=false` (default): `run_*` devuelve
   `ERROR: tool '…' is disabled on this interface by policy` aunque esté en la allowlist.
3. **Doble confirmación web**: con exec habilitado, TODO `run_*` y todo write
   (`write_file`, `edit_file`, `jira_*` de escritura, `confluence_create/comment`)
   emite `permission_required` en **cada** llamada — el atajo de la allowlist de
   `tui_config.json` no aplica a clientes remotos (se concedió al modal de la TUI).
   La denylist (`permissions.deny`) y el confinamiento a `permissions.workspace`
   siguen aplicando.
4. Token comparado con `secrets.compare_digest`; CORS solo por lista blanca.

## Smoke / desarrollo del panel

```
raiko web
python web/scripts/ws_smoke.py --provider anthropic --model claude-haiku-4-5 \
    --prompt "list the files in the cwd"
```

Criterio de compatibilidad: si el panel habla lo que hace `ws_smoke.py`, habla el contrato.
