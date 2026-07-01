# Design: retirar el tier Circuit + batería Jira/Confluence mockeada en Advanced

**Fecha:** 2026-07-01
**Estado:** aprobado (pendiente de revisión del spec por el autor)

## Contexto y motivación

El tier **Circuit** actual tiene 203 tasks, pero 201 son lecturas triviales de un
solo campo de Vault (`v_token_*` / `v_port_*`, 200 generadas + 1 `c_vault_read`) y
solo 2 son circuitos reales (`c_copy_payload`, `c_copy_config`, Vault → SSH al Mac).
Ese relleno se metió para llegar a "200+" sin martillear el Mac por SSH. No aporta
señal: no es una batería de circuitos variados.

Además el tier arrastra **dependencias de infraestructura** incompatibles con publicar
el benchmark en GitHub:
- Vault necesita el binario `vault` (dev server) — `bench/vaultsvc.py` + `installers.install_vault`.
- `copy_file_to_mac` necesita un Mac real por SSH — imposible sin infra.

Las tools que **más flaquean** en la práctica son **Jira y Confluence**, y para
empresas que un agente las opere bien es una señal valiosa. Hoy están montadas contra
servicios vivos (Jira CLI real; Confluence Cloud REST), así que no son graduables a
escala de forma determinista.

## Objetivo

1. **Matar el tier Circuit** y toda su dependencia de infra (Vault binario, SSH al Mac).
2. Construir dentro de **Advanced** una batería sólida (~150 tasks) de **Jira + Confluence
   mockeados in-process**, determinista y sin infra, de dificultad fácil→chunga, incluyendo
   cadenas multitool.
3. Conservar ~10 tasks **gated por un secreto de Vault** (Vault mockeado), para mantener el
   sabor "cruzar una frontera".
4. Que el bench sirva como instrumento para **medir y luego mejorar las descripciones** de
   las tools Jira/Confluence (mejorar descripción = editar `tools.py` y re-correr; el arnés
   no cambia).

Restricción dura: **cero dependencias de servidor/infra** — todo mockeado in-process,
determinista, ejecutable con un `git clone` + deps de Python.

## Decisiones tomadas (brainstorming)

- Un "circuito" = cualquier cadena de 2+ tools (no necesariamente desde Vault).
- Se **elimina** el tier Circuit; lo que vale se **fusiona en Advanced**.
- `copy_file_to_mac` → **fuera** (Mac real = dependencia dura).
- Vault → **mockeado in-process** + conservar ~10 tasks gated. El binario/instalador fuera.
- La batería vive **dentro de Advanced** (un solo runner).
- El mock se **reconstruye por task** desde una semilla determinista (como Advanced ya
  rehace el sandbox de FS por task).
- Capacidades cubiertas: **búsqueda/recall, lectura/extracción, escrituras, cadenas
  multitool cross-tool**.
- Tamaño objetivo: **~150 tasks** Atlassian + ~10 Vault.
- Botón del `--configure`: se mantiene pero pasa a **"Download Jira CLI"** (solo Jira).

## Arquitectura

### 1. Demolición (dependency-free total)

- **Borrar archivos:** `bench/run_circuit.py`, `bench/tasks_circuit.py`, `bench/vaultsvc.py`.
- **Borrar `copy_file_to_mac`** (solo existía dentro de `run_circuit.py`).
- **Vault binario fuera:** eliminar `installers.install_vault` y sus menciones en
  `installers.describe()` y el docstring del módulo.
- **`--configure` (`tui.py`):** el botón `dl_clis` pasa a bajar **solo Jira CLI**:
  - Label `"Jira + Vault CLIs"` → `"Jira CLI"` (línea ~1045).
  - Texto de status sin "+ Vault" (línea ~1099).
  - Quitar la entrada `("vault", installers.install_vault)` del worker `_dl_clis_worker`
    (línea ~1106); queda solo `("jira", installers.install_jira_cli)`.
  - El botón se conserva con etiqueta "Download Jira CLI".
- **Se conserva:** el tool `vault_get_secret` en `tools.py` (schema + impl real que apunta al
  `VAULT_ADDR` del usuario). No se bundlea servidor; quien tenga Vault lo apunta él mismo.
  El bench lo usa con impl mockeada.
- **Limpieza de referencias:** README, `docs/index.html` y `bench/make_charts.py` /
  `bench/combine_report.py` — quitar menciones al tier Circuit.

### 2. Mocks in-process (`bench/mock_atlassian.py`)

Tres fakes deterministas, **reconstruidos por task** desde la semilla:

- **`MockJira`**: store en memoria de issues (`key, project, type, status, assignee,
  reporter, summary, description, comments[], links[]`). Implementa las operaciones que
  exponen las tools reales: `search` (texto libre / subconjunto JQL), `get`, `assign`,
  `comment`.
- **`MockConfluence`**: store de espacios + páginas (`id, space, title, body, labels,
  ancestors, version`). Implementa `search` (texto / subconjunto CQL), `get` (por id/título/
  espacio), `user`, `create`, `comment`.
- **`MockVault`**: dict de secretos → fields (para las ~10 gated).

**Fidelidad crítica:** los mocks replican **la misma semántica de matching y el mismo
formato de salida** que las tools reales de `tools.py`, para medir al agente + la descripción,
no un mock divergente. En concreto:
- `jira_search`: partir la query en palabras, filtrar `len(w) >= 4`, OR entre términos;
  salida tabla `key · status · summary`.
- `confluence_search`: subconjunto CQL equivalente al real; misma salida.

**Inyección limpia (sin tocar `tools.py`):** `harness.run_task` gana un parámetro opcional
`dispatch`. Cuando se pasa, la ejecución de tools usa ese dispatch en vez del `call_tool`
global. El runner de Advanced construye **por-task** un
`dispatch = DISPATCH_base ⊕ overrides_mock` para `jira_*` / `confluence_*` /
`vault_get_secret`. Los **schemas/descripciones** siguen viniendo de `TOOLS` (producción),
de modo que afinar una descripción es editar `tools.py` y re-correr — el arnés no cambia.

### 3. Semilla (`bench/fixtures_atlassian.py`)

Determinista (sin `random`/`Date`), núcleo curado a mano + familias generadas:

- **Jira:** ~3 proyectos (`OPS`, `WEB`, `DATA`), ~120 issues repartidos en tipos
  (Bug/Task/Story/Incident), estados (To Do / In Progress / In Review / Done / Blocked),
  assignees/reporters de un set fijo de usuarios, comments y links a páginas de Confluence.
  Términos solapados a propósito (test de precisión de query) y algún "needle" con token único.
- **Confluence:** ~3 espacios (`ENG`, `RUNBOOKS`, `HR`), ~80 páginas con jerarquía, labels,
  cuerpos con datos extraíbles (owners, puertos, fechas, decisiones) y enlaces a keys de Jira.
- **Cross-links:** el incidente `OPS-XX` referencia el runbook "Outage Playbook", que lista al
  on-call → base de las cadenas multitool.
- **Vault:** ~10 secretos que devuelven una key de issue / email / page-id que **gatea** una
  acción Atlassian posterior.

Funciones: `build_jira_seed()`, `build_confluence_seed()`, `build_vault_seed()` — devuelven
estructuras frescas por-task.

### 4. Tasks (`bench/tasks_atlassian.py`)

`build_atlassian_tasks(...)` produce ~150 tasks + ~10 Vault. Cada task lleva `id`,
`category`, `difficulty`, `prompt`, `expect_tools`, `check`, `negative=False`.

| Familia | Aprox | Dificultad |
|---|---|---|
| Búsqueda/recall (`jira_search`, `confluence_search`, count/filter, JQL/CQL) | ~40 | fácil→chunga |
| Lectura/extracción (`jira_get`, `confluence_get` → dato del cuerpo) | ~35 | fácil→media |
| Escrituras atómicas (`jira_assign`, `jira_comment`, `confluence_create`, `confluence_comment`) | ~30 | media |
| Cadenas multitool cross-tool (Jira↔Confluence↔files, write terminal) | ~45 | media→chunga |
| Vault-gated (secret → acción Atlassian/file) | ~10 | media→chunga |

### 5. Grading

- **Read/search:** helpers de `tasks.py` (`contains`, `has_number`) + un matcher de keys de
  issue / títulos de página.
- **Writes / chains:** verificar el **efecto sobre el store mock** tras la corrida (assignee
  cambiado, comment presente, página creada con título/cuerpo esperado, archivo local con el
  resultado). Mismo espíritu "verifica el efecto real" que hoy, pero contra el mock.
- El grader de estas tasks recibe el **contexto por-task** (el/los store mock) en vez del
  `root` de FS. Se generaliza el `grader_root` de `run_task` a un contexto por-task
  (`grader_ctx`): para tasks FS es el `root`, para tasks Atlassian/Vault es el store.

### 6. Runner Advanced (`bench/run_adv.py`)

- El loop de `run_suite` **ramifica por tipo de task**:
  - Tasks FS (las actuales): construir sandbox de FS por task (como hoy).
  - Tasks Atlassian/Vault: construir stores mock frescos por task, componer el `dispatch`
    inyectado, y pasar el store como `grader_ctx`.
- `FULL_TOOLS` expone además los schemas de `jira_*`, `confluence_*` y `vault_get_secret`.
- `write_report` gana desglose por `category` **y** `difficulty`.

### 7. Fuera de alcance (follow-up)

Afinar las **descripciones** de las tools Jira/Confluence se hace *después* de tener el bench:
editar `tools.py` y re-correr para medir antes/después. No forma parte de esta entrega.

## Componentes y límites

- `mock_atlassian.py` — fakes + `build_jira_impls(store)` / `build_confluence_impls(store)` /
  `build_vault_impls(store)` que devuelven `{nombre_tool: callable}`. No conoce las tasks.
- `fixtures_atlassian.py` — solo datos semilla. No conoce las tasks ni el mock.
- `tasks_atlassian.py` — define tasks + checks. Consume seeds y (para checks de write) el store.
- `run_adv.py` — orquesta: por task decide setup (FS vs mock), compone dispatch, corre, gradúa.
- `harness.run_task` — gana `dispatch` y `grader_ctx` opcionales; comportamiento por defecto
  intacto (tasks existentes no cambian).

## Testing

- **Unit del mock:** `MockJira.search`/`get`/`assign`/`comment` y equivalentes de Confluence
  devuelven el formato esperado y aplican la semántica de matching documentada; comparar
  contra ejemplos de salida de las tools reales.
- **Determinismo de la semilla:** dos construcciones consecutivas producen estructuras
  idénticas.
- **Un smoke del runner** con `--limit` sobre unas pocas tasks Atlassian de cada familia,
  verificando que el grading lee correctamente el store tras writes.
- **Regresión:** las tasks FS de Advanced siguen pasando sin cambios (dispatch/grader_ctx por
  defecto no altera su ruta).

## Riesgos y mitigaciones

- **Divergencia mock vs real:** si el mock no replica la semántica de search, el bench mide
  otra cosa. Mitigación: tests que comparan salidas contra las de las tools reales y anotan
  cualquier quirk replicado.
- **Bloat de Advanced:** ~150 tasks nuevas engordan el tier. Aceptado; el desglose por
  categoría/dificultad mantiene la legibilidad del report.
