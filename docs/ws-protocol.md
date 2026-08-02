# raiko web — contrato WS/API (protocol v1.1)

**Fuente de verdad compartida** entre raiko (`engine/protocol.py`, `web/server.py`) y el
panel HOMELAB (pestaña AGENT + SERVICE MATRIX). El panel implementa ESTE documento; el
cliente de referencia es `web/scripts/ws_smoke.py`.

**Versionado:** `PROTOCOL_VERSION` (hoy `"1.1"`) viaja en `session_started.protocol_version`
y en la respuesta de `POST /api/sessions`. Un cambio incompatible en cualquier evento o
comando la incrementa — el cliente debe comprobarla al conectar y rehusar/avisar si no
coincide con la que implementa. Añadidos compatibles (un comando nuevo, un campo nuevo
en una respuesta REST) NO la mueven: un cliente que los ignore sigue funcionando.

## Arranque

```
raiko web [--host H] [--port P]        # o: python web/server.py
```

Config en `tui_config.json` → clave `web`:

```json
"web": {
  "host": "127.0.0.1",          // bind; no-loopback EXIGE token (si no, rehúsa arrancar)
  "port": 8484,
  "token": "",                  // Bearer token; vacío solo aceptable en loopback
  "allowed_origins": [],        // CORS whitelist explícita (el origen del panel HOMELAB); nunca '*'
  "allow_exec": false,          // false = run_python/run_powershell/run_bash deshabilitados por completo
  "max_live_sessions": 16,
  "session_ttl_seconds": 3600,
  "queue_size": 4096,
  "model_catalog_ttl_seconds": 86400,
  "max_iterations": 60             // rondas de tool calls por turno para una sesión web (techo 500)
}
```

## Auth y CORS

- Toda petición HTTP y el WS llevan `Authorization: Bearer <token>`.
  Los tokens en query string se rechazan para evitar que aparezcan en access
  logs, historial o métricas. Sin token válido: HTTP 401 / cierre WS con
  código `4401`.
- CORS: solo los orígenes de `web.allowed_origins` (p.ej. `http://homelab.local:5173`).
  Sin lista configurada no se añade middleware CORS (mismo-origen/no-navegador).

## API REST

### `GET /api/health`

Devuelve estado, `protocol_version`, versión del engine, uptime y número de
sesiones vivas. Se usa para el SERVICE MATRIX.

### `GET /api/capabilities`

Lista proveedores/modelos configurados, presets, política exec, estado MCP y
las skills disponibles (`skills`: `[{"name","description"}]`, mismo discovery
que `GET /api/skills` pero resumido). Nunca expone keys, tokens ni base URLs.

Incluye `project_roots`: las carpetas raíz bajo las que el panel puede elegir
directorio de trabajo (lista vacía = la función está apagada en este servidor).

También `default_max_iterations` (rondas de tool calls por turno que recibe una
sesión nueva, de `web.max_iterations`) y `max_iterations_limit` (el techo que el
servidor acepta). El panel los usa para precargar y acotar su control sin
codificar el número.

### `GET /api/providers/{provider}/models`

Catálogo autenticado y buscable del proveedor. Nano usa su modo
`detailed=true` (precios reales de la cuenta); Gemini y Anthropic usan sus APIs
nativas de modelos; OpenAI y los proveedores compatibles usan `/models`.
Devuelve IDs, nombre, propietario, contexto, capacidades y precios de
entrada/salida por millón cuando el proveedor los ofrece. Los precios de la
tabla local se marcan con `price_source: "raiko_table"`.

El resultado vive solo en memoria durante 24 horas
(`web.model_catalog_ttl_seconds`). Al expirar se renueva una sola vez aunque
lleguen peticiones concurrentes. Si falla la renovación se sirve el último
catálogo como `stale` y se reintenta pasados cinco minutos. Keys, tokens y URLs
no forman parte de la respuesta.

### `GET /api/skills`

Descubre las Agent Skills (`engine/skills.discover_skills`, mismo mecanismo
que usa el system prompt del engine) en cada petición — es un scan de disco
barato, así que refleja skills añadidas/editadas sin reiniciar `raiko web`.

```json
{ "skills": [{"name","description","source","path"}] }
```

`source` ∈ `raiko` \| `agents` \| `claude` \| `extra` (según el root de donde
se descubrió). `path` es la ruta absoluta al `SKILL.md`.

### `GET /api/skills/{name}`

Devuelve una skill concreta con su `SKILL.md` **íntegro** (frontmatter +
cuerpo, tal cual está en disco — el panel quiere el fichero completo, no la
versión que el engine renderiza para el modelo):

```json
{ "name", "description", "source", "path", "content" }
```

`name` se valida contra la lista de skills descubiertas (nunca se construye
una ruta de fichero a partir del parámetro de la URL). Sin coincidencia →
404 `{"error": "unknown skill"}`.

### `GET /api/projects`

Carpetas en las que una sesión puede trabajar: cada root de
`web.project_roots` y sus hijos directos (los ocultos no se listan).

```json
{ "roots": ["C:/Users/tu/Desktop"],
  "projects": [{"path","name","root","sessions"}],
  "truncated": false }
```
`sessions` = cuántas sesiones guardadas hay en esa carpeta, para pintar el
"3 sesiones en este proyecto" sin una segunda llamada. Sin `web.project_roots`
configurado la lista va vacía y **ningún** `cwd` es aceptable: las sesiones
corren en el directorio del proceso.

### `GET /api/sessions`
```json
{ "saved": [{"id","title","provider","model","updated","cwd",
             "max_iterations","messages"}],
  "live":  [{"session_id","provider","model","busy","connected",
             "engine_session_id","permission_mode","max_iterations","cwd"}],
  "cwd": "" }
```
`saved` = sesiones persistidas en disco (para el Continue/New del panel). `live` =
sesiones adjuntables ahora mismo por WS.

`?cwd=<carpeta>` filtra ambas listas a esa carpeta ("sesiones de este proyecto").
La comparación normaliza la ruta (absoluta, symlinks resueltos, mayúsculas en
Windows), así que da igual cómo se escriba. Las sesiones guardadas antes de que
existiera `cwd` traen `"cwd": ""`: salen en el listado completo y son resumibles,
pero no casan con ningún filtro de carpeta.

### `POST /api/sessions`
Body: `{"provider": "...", "model": "...", "ctx_window"?: int,
"resume"?: "<saved id>", "permission_mode"?: "ask" | "yolo", "cwd"?: "<carpeta>",
"max_iterations"?: int}`.
Con `resume`, provider/model se toman de la sesión guardada si se omiten. `provider:
"local"` se adjunta a un llama-server YA corriendo (nunca lo arranca).
`permission_mode` es por sesión y por defecto vale `"ask"`: `"ask"` pausa el turno
y solicita confirmación web para operaciones sensibles; `"yolo"` las autoriza sin
interacción. La política dura `web.allow_exec` y la denylist siguen teniendo prioridad.

`cwd` es la carpeta de trabajo de la sesión: las tools resuelven ahí las rutas
relativas y los `run_*` se ejecutan ahí, igual que si hubieras hecho `cd`. El
servidor la valida (realpath + debe estar dentro de un `web.project_roots`), así
que `..`, symlinks y rutas arbitrarias se rechazan con 400. Si se omite y hay
`resume`, se recupera la carpeta de la sesión guardada **solo si** sigue dentro
de los roots; si no, la sesión cae al directorio del proceso y la respuesta lo
avisa en `cwd_note`. Sin `cwd` ni `resume`: directorio del proceso.

`max_iterations` es el número de **rondas de tool calls** que un turno puede
ejecutar en esta sesión (una ronda = una respuesta del modelo, que puede llevar
varias tool calls). Es por sesión: el servidor nunca lo escribe en la config
compartida, así que dos sesiones vivas pueden correr con topes distintos.
Prioridad: el valor del body → el de la sesión resumida → `web.max_iterations`
(60 por defecto). Fuera de `1..500` → 400. Al agotarse, el turno acaba con
`turn_done{reason:"max_iterations"}` precedido de un `notice{kind:"warning"}` que
nombra el tope.

201 → `{"session_id", "provider", "model", "ctx_window", "protocol_version",
"exec_enabled", "permission_mode", "max_iterations", "cwd", "cwd_note",
"mcp_tools", "mcp_servers", "mcp_error"}`.
Errores: 400 (body/params/configure/`cwd`/`max_iterations` inválido), 401,
404 (`resume` desconocido).

### `GET /api/sessions/{saved_session_id}`

Devuelve la sesión persistida completa, incluido su historial.

### `DELETE /api/sessions/{session_id}`

Elimina una sesión viva o una sesión guardada.

## WebSocket `WS /ws/{session_id}`

JSON en ambos sentidos; cada mensaje lleva `"type"` más los campos del payload.
Al conectar, el servidor envía `session_started` y después `session_snapshot`.
Cierres: `4401` sin auth, `4404` sesión desconocida y `4409` si ya existe otro
cliente propietario. Si el cliente desconecta a mitad de turno, el turno se
interrumpe y cualquier permiso pendiente se deniega inmediatamente.

### Comandos (cliente → servidor)

| type | payload | notas |
|---|---|---|
| `send` | `{text}` | inicia un turno; error si ya hay uno corriendo; `turn_done{budget_exceeded}` si el budget está agotado |
| `interrupt` | `{}` | detiene el turno en curso (no-op si idle) |
| `permission_response` | `{perm_id, decision}` | una de las opciones incluidas en `permission_required.allowed_decisions` |
| `swap_model` | `{provider?, model}` | cambia de modelo conservando historial (no mid-turn) |
| `compact` | `{}` | resume turnos antiguos (→ evento `compacted`) |
| `clear` | `{}` | vacía la conversación (conserva system prompt) |
| `rewind_last_user` | `{}` | retira el último mensaje de usuario; responde `notice{kind:"rewind", text:<texto retirado>}` |
| `set_system_prompt` | `{name?, text?}` | `text` gana; `name` refiere a un preset guardado |
| `set_max_iterations` | `{max_iterations}` | rondas de tool calls por turno, solo para esta sesión (no mid-turn); `1..500`; responde `notice` con el tope efectivo |

### Eventos (servidor → cliente)

| type | payload |
|---|---|
| `session_started` | `{session_id, provider, model, ctx_window, protocol_version, connection_id}` |
| `session_snapshot` | `{session_id, engine_session_id, busy, provider, model, ctx_window, messages, input_tokens, output_tokens, session_usd}` — estado canónico al conectar/reconectar y después de mutaciones de historial |
| `turn_started` | `{text}` — eco del mensaje del usuario, tras el posible auto-compact |
| `thinking_delta` / `text_delta` | `{text}` — streaming del razonamiento / la respuesta |
| `segment_end` | `{content, thinking}` — textos FINALES del segmento (pueden diferir de la concatenación de deltas si se recuperó un tool-call emitido como texto); cierra el bloque vivo |
| `tool_call_started` | `{call_id, name, args}` (`args` = JSON string crudo del modelo) |
| `tool_call_result` | `{call_id, name, ok, summary, result, diff?, path?}` — `diff` = unified diff en `write_file`/`edit_file`; `ok=false` en ERROR/DENIED |
| `permission_required` | `{perm_id, tool, action, detail, scope, allowed_decisions}` — el turno queda EN PAUSA hasta el `permission_response` con ese `perm_id` (timeout 300 s → deny). `scope`: `danger` \| `workspace` \| `external_write` |
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
3. **Política por sesión**: en modo `"ask"`, con exec habilitado, TODO `run_*` y todo write
   (`write_file`, `edit_file`, `jira_*` de escritura, `confluence_create/comment`)
   emite `permission_required` en **cada** llamada — el atajo de la allowlist de
   `tui_config.json` no aplica a clientes remotos (se concedió al modal de la TUI).
   La denylist (`permissions.deny`) y el confinamiento a `permissions.workspace`
   siguen aplicando. Para estas operaciones `allowed_decisions` es
   `["allow_once","deny"]`; no se ofrece un “always” que no tendría efecto.
   En modo `"yolo"` esas confirmaciones se aprueban automáticamente. La denylist
   y `web.allow_exec=false` no se pueden anular seleccionando YOLO.
4. Token comparado con `secrets.compare_digest`; CORS solo por lista blanca.
5. **Carpeta de trabajo acotada**: el `cwd` que manda el cliente se resuelve con
   `realpath` y debe caer dentro de `web.project_roots` — `..` y symlinks no
   escapan, y sin roots configurados no se acepta ninguno. El `cwd` guardado de
   una sesión (que pudo crearse desde la TUI en cualquier punto del disco) pasa
   la misma validación al resumirla. Es un límite de *elección de carpeta*, no
   de escritura: quien confina las escrituras sigue siendo el workspace, que por
   defecto es ese mismo `cwd`.

## Smoke / desarrollo del panel

```
raiko web
python web/scripts/ws_smoke.py --provider anthropic --model claude-haiku-4-5 \
    --prompt "list the files in the cwd"
```

Criterio de compatibilidad: si el panel habla lo que hace `ws_smoke.py`, habla el contrato.
