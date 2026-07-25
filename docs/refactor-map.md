# Mapa de refactor — extracción del engine (Fase 0)

> Leído del código real a fecha 2026-07-25 (rama `refactor/engine-extraction`).
> Referencias `archivo:línea` sobre el árbol actual, ANTES de mover nada.

## Los dos loops de agente que existen hoy

| | `tui.py` (Textual) | `agent.py` (headless) |
|---|---|---|
| Loop | `AgentTUI.agent_turn` `tui.py:2812` | `run()` `agent.py:207` |
| Streaming | `AgentTUI.stream_one` `tui.py:2916` | `stream_completion()` `agent.py:85` |
| Iteraciones | `cfg.max_iterations` (def. 8) | `MAX_ITERATIONS = 5` `agent.py:13` |
| Permisos | modal + allowlist persistida | ninguno (tools bloquean con `_danger_check`) |
| Sesiones / compaction / coste | sí | no |
| ThinkSplitter | `tui.py:394` | duplicado en `agent.py:41` |
| Config | `tui_config.json` (`load_config` `tui.py:274`) | env vars `AGENT_PROVIDER/BASE_URL/API_KEY/MODEL/DEBUG` `agent.py:19-32` |

`agent.py` es un subconjunto estricto: mismo protocolo OpenAI-compat, sin fallback de
tool-calls en texto, sin retries de think-leak. Colapsarlo sobre el engine (Fase 3) solo
requiere: política de permisos automática + desactivar persistencia de sesión.

## 1. Turn loop (a mover íntegro al engine)

`AgentTUI.agent_turn(text)` — `tui.py:2812-2894`. Corre en un **worker thread** de Textual
(`run_worker(..., thread=True, exclusive=True)`, lanzado desde
`MainScreen.on_composer_submitted` `tui.py:1936` y `retry_last` `tui.py:2336`).

Secuencia:
1. `_maybe_autocompact()` `tui.py:2596` — compacta inline si ctx ≥ 85% (`AUTO_COMPACT_PCT`
   `tui.py:192`).
2. Monta `UserMsg` (UI) y hace `messages.append({role:user})`.
3. Resetea contadores de turno: `_turn_tokens`, `_turn_cost`, `_stream_chars`,
   `_turn_start`, `_cancel.clear()`.
4. Bucle `for _ in range(max_iters)`:
   - `msg = self.stream_one()` (ver §2).
   - **Think-leak retry** `tui.py:2833-2846`: si `_think_leak_calls` y no hay
     `tool_calls`, reintenta el stream hasta `THINK_RETRIES` (`tui.py:187`) y como último
     recurso fabrica los tool_calls con ids `tk_{i}` desde el thinking.
   - Solo persiste `msg` si tiene content o tool_calls (Anthropic 400 si no) `tui.py:2850`.
   - Si `_cancel` → notice "⏹ stopped" y break.
   - Sin tool_calls → break (fin del turno).
   - Por cada tool_call: `render_tool_call` (UI) → `execute_tool` (§3) →
     `render_tool_result` (UI) → `messages.append({role:tool, tool_call_id, content})`.
5. `except`: acorta el error (mensaje especial para "parse tool call") y lo pinta.
6. `finally` `tui.py:2880-2894`: `busy=False`, notice tok/s + coste del turno,
   `update_ctx()` (statusbar), aviso de budget si `over_budget()`, **`save_session()`**
   (auto-save por turno).

Estado que toca (hoy atributos de `AgentTUI`, mañana estado de `engine.Session`):
`messages`, `busy`, `_cancel` (threading.Event), `_turn_tokens`, `_turn_cost`,
`_stream_chars`, `_turn_start`, `_think_leak_calls`, `cur_think`, `cur_content`,
`_phase`, `_pending_diff`, `session_cost/session_input/session_output`, `tracker`,
`client`, `cfg`, `session_id`, `mcp_tools/mcp_route/mcp_names`.

Puntos de acople con Textual dentro del loop (a sustituir por eventos):
`call_from_thread(self._chat_mount/UserMsg)`, `call_from_thread(self.write_log, …)`
(retries, stopped, tok/s, budget, errores), `call_from_thread(self.render_tool_call/result)`,
`call_from_thread(self.update_ctx)`.

## 2. Streaming (a mover íntegro al engine)

`AgentTUI.stream_one()` — `tui.py:2916-3029`.

- `_repair_history()` `tui.py:2896-2914`: sanea el historial para providers estrictos
  (drop de assistant vacíos, tool-result placeholder `[interrupted]` para calls huérfanas).
  Debe ir al engine tal cual (se usa antes de CADA request).
- Params por provider `tui.py:2918-2933`: `stream_options.include_usage` siempre;
  `extra_body` según provider — local: `chat_template_kwargs.enable_thinking`; nano:
  `reasoning`; gemini: `thinking_config` mapeado desde `reasoning_effort`. Esto es lógica
  de engine (no UI).
- Deltas de **thinking**: campo `reasoning_content`/`reasoning` (o `model_extra`)
  `tui.py:2969-2973`; `<think>` inline via `ThinkSplitter` `tui.py:394-429`; caso Gemini
  `extra_content.google.thought` `tui.py:2976-2980`. → evento `thinking_delta`.
- Deltas de **content** → hoy acumula en `cur_content` + `call_from_thread(update_live)`
  → evento `text_delta`.
- Tool-call deltas acumulados por `index` en dict `tool_calls` `tui.py:2984-2993`.
- **Interrupt**: chequea `self._cancel` por chunk y hace `stream.close()` `tui.py:2957`.
- Usage/coste: último chunk con `usage` o `x_nanogpt_pricing` → `tracker.update_from_chunk_dict`
  (`context.py:67`); si no hay usage, estima con tiktoken (`context.estimate_messages`,
  `context._ntok`) `tui.py:3000-3002`. Luego `_account_cost` (§6).
- **Fallback texto**: `parse_text_tool_calls` `tui.py:109-148` (+ `_balanced_objects`
  `tui.py:66`, `strip_tool_call_text` `tui.py:151`) recupera tool-calls emitidas como
  texto plano (ids `fb_{i}`); detección de think-leak `tui.py:3020`. Todo esto es lógica
  de modelo, va al engine (módulo p.ej. `engine/textcalls.py`).
- Acople UI: `_start_assistant`, `update_live`, `commit_live`, `_phase` (working bar).
  → se sustituyen por `text_delta`/`thinking_delta` + `tool_call_started`.

`agent.py:85-187` (`stream_completion`) es la versión pobre de lo mismo; se borra en Fase 3.

## 3. Tool dispatch (a mover íntegro al engine)

`AgentTUI.execute_tool(name, raw_args)` — `tui.py:2736-2788`:

1. Si `name in self.mcp_route` → `mcp_client.call_tool(url, orig, args)` con timeout.
2. `run_python/run_powershell/run_bash`: `danger_match(code)` (`tools.py:239`) → si hay
   fragmento peligroso, `_permit` (§4); aprobado → `args["allow_unsafe"]=True`.
3. `jira_assign/jira_comment` y `confluence_create/confluence_comment`: **siempre**
   piden permiso (writes externos) con snippet/código descriptivo `tui.py:2757-2778`.
4. `write_file/edit_file`: confinamiento a workspace — `_in_workspace` `tui.py:2693`
   (commonpath con `permissions.workspace` o cwd); fuera → `_permit`. Después
   `_run_with_diff` `tui.py:2790-2809`: lee before/after y deja
   `self._pending_diff = (path, unified_diff)` para la UI → en el engine el diff viaja
   DENTRO de `tool_call_result.diff`, se elimina el estado `_pending_diff`.
5. Resto → `tools.call_tool(name, args)` (`tools.py:1281`, dispatch table `tools.py:1247`).

Todo envuelto en `_with_timeout` `tui.py:2715-2734` (`TOOL_TIMEOUT = 60` `tui.py:190`,
daemon thread + join).

`tools.py` y `mcp_client.py` ya son agnósticos de UI — **no se tocan** (solo cambia quién
los llama). Carga MCP: `load_mcp_tools` `tui.py:2632` + `_mcp_servers_from_cfg`
`tui.py:2615` → engine (hoy corre en worker thread al montar MainScreen `tui.py:1905`).

## 4. Permisos — el punto que se vuelve async

Cadena actual (worker thread → UI → worker thread):

- `_permit(tool, snippet, code)` `tui.py:2669-2687`: orden de decisión =
  `skip_permissions` global → `cfg.permissions.deny` → `cfg.permissions.allow` →
  preguntar. "always" añade el tool a `permissions.allow` y `save_config` (persistencia
  de la allowlist en `tui_config.json`, clave `permissions` `tui.py:233`).
- `ask_permission` `tui.py:2657-2667`: **bloquea el worker con `threading.Event`**;
  `call_from_thread(push_screen(PermissionScreen, callback))`; el modal
  (`PermissionScreen` `tui.py:776-822`, teclas y/a/n) resuelve `once|always|deny`.

Diseño Fase 1/2: el engine emite `permission_required{perm_id,…}` y `await`ea un
`permission_response{perm_id, decision}`. La TUI implementa el hook mostrando el modal;
headless lo resuelve con política; web lo reenvía por WS. La decisión allow/deny/allowlist
(**`_permit` menos el prompt**) queda en el engine; solo el "preguntar" es del frontend.

Workspace: `_workspace`/`_in_workspace` `tui.py:2689-2699` → engine.
`/permissions` (`show_permissions` `tui.py:2701`) es render puro sobre `cfg` → queda en TUI.

## 5. Sesiones

- Storage módulo-level `tui.py:342-389`: `SESSIONS_DIR` (`_app_home()/sessions`),
  `list_sessions`, `load_session`, `write_session`, `delete_session`. JSON por sesión:
  `{id, updated, provider, model, title, ctx_window, messages, base_url?}`.
- `save_session` `tui.py:2225-2244`: auto-save al final de cada turno (`agent_turn`
  finally), tras compaction, tras swap y tras apply_persona. `session_id` =
  timestamp en el primer save.
- Resume: `resume_session` `tui.py:2195-2223` (local re-arranca server vía
  `choose_model` → `_apply_resume` `tui.py:2188`; cloud reconecta directo);
  Continue/New: `StartScreen` `tui.py:1249` + `SessionListScreen` `tui.py:1287`.
- `/clear`: `clear_context` `tui.py:2498` — conserva system prompt, resetea
  `session_id` y el tally de coste.
- `/retry` y `/edit`: `_rewind_to_last_user` `tui.py:2320` — trunca `messages` en el
  último user. Mutación de historial → operación del engine.

Al engine: storage + save/resume/clear/rewind (mutaciones de `messages`).
En la TUI: las pantallas de listado y el render de historial (`render_history`
`tui.py:2290`, deriva widgets de `messages` — se queda porque solo lee).

## 6. Coste / budget

- `_account_cost` `tui.py:3114-3134`: por request — coste reportado por provider
  (`x_nanogpt_pricing` / `usage.cost` OpenRouter) gana; si no, `pricing.cost_usd`
  (`pricing.py:43`, tabla + overrides `cfg.pricing`). Self-hosted (`local/remote/vllm`)
  = $0. Acumula `_turn_cost` y `session_cost/input/output`.
- `over_budget` `tui.py:3136`: `budget_usd` (0 = off). Se chequea al **enviar**
  (`on_composer_submitted` `tui.py:1947` bloquea el turno) y al **acabar** el turno
  (aviso). → engine: evento `cost_update` tras cada request + `turn_done{budget_exceeded}`.
- Statusbar: `update_ctx` `tui.py:3152` + `_cost_label` `tui.py:3140` — render puro,
  se queda en TUI alimentado por `cost_update`.

## 7. Compaction

- `_do_compact(auto)` `tui.py:2538`: summariza vía `client.chat.completions.create`
  (no-stream, max_tokens=1200) sobre `_transcript` `tui.py:2515` (aplanado, cap 24k
  chars); deja `[system, user(resumen)]`. → engine.
- Manual `/compact`: `compact()` `tui.py:2565` (worker propio, posee `busy`).
- Auto: `_maybe_autocompact` `tui.py:2596` — inline al inicio de `agent_turn`, umbral
  `AUTO_COMPACT_PCT = 0.85` sobre `tracker.current()` (estimación tiktoken,
  `context.py:84`).
- `_enough_to_compact` `tui.py:2534`: ≥4 mensajes reales.
- UI post-compact: `_after_compact` `tui.py:2585` (clear log + resumen pintado) → pasa a
  ser reacción de la TUI a un evento (p.ej. notice/`turn_done` de un comando `compact`).

## 8. Conexión / proveedores / swap de modelo

- `configure(provider, model, ctx_limit)` `tui.py:2113-2128`: construye `OpenAI(...)`
  client + `ContextTracker` + límite de ctx por provider + `mcp_servers`. Es la
  "apertura de sesión" del engine (`session_started`).
- `resolve_key` `tui.py:336`, `_PROVIDER_ENV` `tui.py:329`, `CLOUD`/`URL_PROVIDERS`
  `tui.py:244-246`, `build_system_prompt`/`DEFAULT_PERSONA`/`TOOL_RULES` `tui.py:250-271`,
  `load_config`/`save_config` `tui.py:274-325`, `_app_home` `tui.py:159` → engine
  (módulo config compartido).
- `swap_model` `tui.py:2277-2288` (F3): re-`configure` con historial intacto + save.
  → comando `swap_model` del engine; la TUI conserva la pantalla de selección.
- Arranque local (llama-server): `local_running`/`_loaded_local_model`/`choose_model`/
  `start_local_with_ctx`/`_start_local` `tui.py:2099-2186` sobre `bench/serve.py` y
  `bench/models.py` (registry). El arranque del server puede quedarse como utilidad
  aparte del engine (lo usan TUI y `raiko web` por igual); el wizard es 100% TUI.

## 9. Telemetría (sidebar local)

`poll_usage` `tui.py:3171-3204`: `nvidia-smi --query-gpu=…` + `psutil` cada 1 s (timer de
MainScreen `tui.py:1902`), pinta sparklines. Para la Fase 4 la **recolección** (subprocess
nvidia-smi + psutil + tok/s desde `_stream_chars`) se extrae a una función/generador del
engine (evento `telemetry`); el pintado queda en cada frontend.

## 10. Qué se queda en tui.py (adaptador)

- Todas las Screens/widgets: wizard (Provider/ApiKey/RemoteUrl/Model/Ctx/Loading),
  Start/SessionList, Settings/Configure, SystemPrompt, ToolLog, Permission (modal),
  MainScreen, Composer + CommandHint (slash), WorkingBar, UsageSidebar, bloques de chat.
- Render: `render_history`, `render_tool_call/result` (consumen eventos),
  `_tool_color/_tool_arg_summary/_result_summary`, `update_ctx`, `write_log`,
  `_chat_mount`, live blocks.
- Mapeo teclas/comandos → comandos del engine: Enter=send, Esc=interrupt,
  F3=swap_model, `/compact`, `/clear`, `/retry`, `/edit`, F6=set_system_prompt.

## 11. Dependencias/estado compartido a vigilar en la Fase 2

1. **Threading**: hoy el turno corre en thread y habla con la UI vía `call_from_thread`.
   El engine será async (o mantendrá el thread y expondrá una cola de eventos); la TUI
   debe seguir sin bloquear su event loop. El permiso es el único punto UI→engine
   a mitad de turno.
2. `_cancel` es un `threading.Event` compartido (lo setea Composer/`action_interrupt`
   `tui.py:1915`, lo lee `stream_one` por chunk). → comando `interrupt`.
3. `_pending_diff` es un side-channel entre `_run_with_diff` y `render_tool_result` →
   muere; el diff viaja en el evento `tool_call_result`.
4. `_think_leak_calls` es estado entre `stream_one` y `agent_turn` → interno del engine.
5. `cfg` se muta y persiste desde varios sitios (permisos "always", favorites, last,
   presets). El engine solo debe escribir `permissions.allow`; el resto es del frontend.
6. `busy` guarda doble función (gate de envío + working bar) → el engine expone estado
   de turno; la TUI deriva `busy` de `session_started…turn_done`.
7. `tracker.limit` se ajusta post-configure en resume (`ctx_window` guardado) y en
   local (`_pending_ctx`). El engine debe aceptar `ctx_limit` en la creación de sesión.

## 12. Checklist de regresión TUI (gate de la Fase 2)

- Wizard provider → (key/URL) → model → (ctx local) → chat.
- Turno con tool call (colores, summary de args, nota de resultado).
- write_file/edit_file: diff inline; fuera de workspace → prompt.
- run_powershell con op peligrosa → modal allow/always/deny; "always" persiste en
  `tui_config.json → permissions.allow`; deny devuelve `DENIED by user…` al modelo.
- Esc interrumpe a mitad de stream y a mitad de cadena de tools; historial queda
  reparado (`[interrupted]`).
- `/compact` y auto-compact al 85%; resumen pintado; `save_session` posterior.
- F3 swap de modelo (cloud) con historial intacto.
- Continue/New: resume cloud y local (re-arranque del server), delete de sesión.
- Coste/budget en statusbar; bloqueo de turno al superar `budget_usd`.
- `/retry`, `/edit`, `/export`, `/clear`, `/permissions`, F4 tool log, F6 prompt.
- Sidebar GPU en local (telemetría 1 s).
