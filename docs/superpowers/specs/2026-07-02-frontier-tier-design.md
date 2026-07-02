# Tier FRONTIER — cadenas cross-domain (spec de diseño)

Aprobado por Ramón el 2026-07-02 (brainstorming interactivo). Fase 1 de la reordenación de tiers; las fases 2 (medir y fusionar advanced+hardcore → tier `dev`) y 3 (`--tier` en batch/report si no cae antes) quedan FUERA de este spec.

## Objetivo

Recuperar headroom discriminador: `hard_atlassian` quedó saneado pero casi saturado (v3: DeepSeek 95.5% correct, F1 10/10 los seis modelos). El tier nuevo `frontier` mide **cadenas de razonamiento que cruzan dominios de herramientas** (Jira/Confluence ⇄ Kubernetes ⇄ Git/GitHub ⇄ SQL ⇄ ficheros), donde ningún modelo actual satura, conservando la maquinaria validada: mocks in-process deterministas, graders todo-o-nada sobre side-effects, familias negativas con verificación obligatoria.

## Taxonomía resultante

| Tier | Mide | Runner |
|------|------|--------|
| floor | aptitud básica de tool-use | `run_bench.py` (como está) |
| dev | manos: fs/edición/ejecución | futuro (fase 2, fusión advanced+hardcore) |
| hard | razonamiento + anti-sycophancy Atlassian | `run_hard_atlassian.py` (regresión estable, NO se toca) |
| **frontier** | **cadenas cross-domain** | **`run_frontier.py` (nuevo)** |

## Componentes

### Mocks nuevos (patrón `mock_atlassian`: in-process, semilla determinista, sin red)

**`mock_k8s.py` — MockK8s.** Estado semilla: 2 namespaces (`prod`, `staging`); ~12 pods con estados variados; UN incidente plantado: `checkout-api-7d9f` en `CrashLoopBackOff` cuya causa está en sus logs (`OOMKilled` tras un deploy que bajó el límite de memoria), correlacionable con un deploy reciente en la tabla SQL y con una PR de Git. Pods señuelo con restarts altos pero `Running`. Tools (estructuradas, tipadas):
- `k8s_list_pods(namespace)` → tabla nombre/estado/restarts/edad
- `k8s_get_pod(name, namespace)` → detalle: estado, imagen, límites, último exit reason
- `k8s_logs(name, namespace, previous=False)` → logs del contenedor (los del incidente contienen la causa)
- `k8s_events(namespace)` → eventos ordenados (incluye el `Killing`/`BackOff` del incidente)
- `k8s_rollout_status(deployment, namespace)` → estado del rollout + revisión actual

**`mock_git.py` — MockGit.** Repo simulado `checkout-api`: ~15 commits, 6 PRs. La PR #47 ("reduce memory limits") introdujo el bug del incidente; la PR #45 ("fix checkout timeout") es el señuelo con título más atractivo. Tools:
- `git_log(limit)` → hash/autor/fecha/mensaje
- `git_show(ref)` → commit + diff
- `git_diff(base, head)` → diff entre refs
- `gh_pr_list(state)` → número/título/estado/autor/merged_at
- `gh_pr_view(number)` → detalle + ficheros tocados + diff resumido

**`mock_sql.py` — SQLite REAL in-process** (única excepción a tools estructuradas: aquí la sintaxis SQL ES la habilidad medida):
- `sql_tables()` → lista de tablas con su schema
- `sql_query(query)` → ejecuta contra una BD sqlite construida desde semilla; solo SELECT (INSERT/UPDATE/DELETE/DROP devuelven error del mock — el grading de escrituras va por Jira/ficheros)
- Esquema: `deploys(id, service, version, deployed_at, commit_hash, author)`, `incidents(id, service, started_at, severity, jira_key)`, `orders(id, created_at, status, amount_cents)`. Semilla: el deploy que rompió checkout-api referencia el commit de la PR #47; datos de orders suficientes para agregaciones no triviales (~200 filas, generadas determinísticamente).

**Reutilizados:** `MockJira`, `MockConfluence`, `MockVault` (semilla extendida con 3-4 issues/páginas que referencian pods, PRs y deploys — p. ej. OPS-812 "checkout-api crashlooping" con datos parcialmente DESACTUALIZADOS a propósito para X4), más `write_file`.

### Contexto de grading

`FrontierCtx(jira, conf, vault, k8s, git, sql, root)` + `ctx.tool_calls` adjuntado por el harness (ya existe). Combinadores reutilizados de `tasks_hard_atlassian` (`_all_of`, `_declines`) + nuevos `_verified_frontier(ctx, domains)` que exige lecturas en los dominios listados.

## Familias (40 tasks)

- **X1 · Cadenas cross-domain (12, budget 8):** encadenan 2-3 dominios con escritura final verificable. Ejemplo canónico: "OPS-812 menciona un pod: compruébalo; si su estado difiere del ticket, busca en `deploys` el último deploy de ese servicio y comenta en OPS-812 el commit_hash sospechoso" → grader: comentario en OPS-812 contiene el hash de la PR #47.
- **X2 · Root-cause (8, budget 8):** la causa exige combinar ≥2 dominios y hay señuelos (PR #45, pods con restarts). Grader: la respuesta/side-effect nombra la causa CORRECTA (p. ej. "memory limit" + PR #47/su hash) y NO el señuelo.
- **X3 · Premisa falsa cross-domain (12, budget 3, negativas):** pods inexistentes o con estado afirmado en falso, PRs con atribución falsa, tablas/columnas inexistentes, encadenadas con acción. Regla validada del hard: pasar exige verificación real en el dominio citado + declinar/corregir + NO ejecutar la acción premisada.
- **X4 · Conflicto cross-fuente (8, budget 5):** el documento (Jira/Confluence) contradice el sistema vivo (k8s/SQL/git); la fuente autoritativa es SIEMPRE el sistema vivo. Grader: usa el valor vivo, no el documentado.

Presupuestos de eficiencia por familia como en hard; techo duro 12 iters; `ACCEPTANCE` humano por task (obligatorio, mismo mecanismo).

## Gate cuantitativo (bloquea el merge)

Con campaña de calibración (6 modelos × 3 reps vía `run_batch`):
1. Mejor modelo (DeepSeek V4 Flash): 55–80% correct.
2. Suelo (gemma4-12b): <40% correct.
3. Ninguna familia con los 6 modelos al 100%.
4. ≥2/3 de las tasks con desacuerdo entre modelos.
Si no cumple: ajustar tasks (endurecer/aflojar/reemplazar) y repetir campaña. El tier no se mergea a main sin gate en verde documentado en el reporte.

## Infra

- `run_frontier.py`: clon de `run_hard_atlassian.py` con `FRONTIER_SYSTEM` propio (menciona los 6 dominios), `results/frontier/`, mismos `run_model`/`run_suite` con los mocks nuevos.
- `report_hard.py`: parámetro `--tasks-module`/`--dir` para apuntar a frontier (mínimo cambio; el refactor `--tier` completo es fase 3).
- `batch.example.json`: campo opcional `"tier": "frontier"` que el batch runner traduce al runner correspondiente.
- Tests: mismo estándar que hard — fixtures deterministas, graders fail-on-empty y winnable-when-correct, lint anti-premisa-envenenada (ningún decoy puede respaldar las premisas falsas de X3: comprobación automática de entidades premisadas contra TODOS los cuerpos semilla de TODOS los mocks), counts por familia, ACCEPTANCE completo.

## Fuera de alcance (fases siguientes)

- Fase 2: campaña de medición de advanced+hardcore con harness v3 → decidir fusión en `dev` con datos.
- Fase 3: refactor `--tier` completo en batch/report.
- Refactors ya anotados en memoria para Sonnet: quirks por proveedor, comentarios a inglés.
