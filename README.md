<div align="center">

<img src="logo.png" alt="raiko-toolkit" width="340">

**R**eimon **A**I **K**razy **O**rchestrator — a local-first, multi-provider LLM agent toolkit.

![Python](https://img.shields.io/badge/python-3.10+-3776AB?logo=python&logoColor=white)
![TUI](https://img.shields.io/badge/TUI-Textual-5f5fd7)
![Local](https://img.shields.io/badge/local-llama.cpp-ff5f5f)
![Tools](https://img.shields.io/badge/tools-MCP-46c2f5?logo=modelcontextprotocol&logoColor=white)
![Providers](https://img.shields.io/badge/providers-7-e7b94e)
![Platform](https://img.shields.io/badge/platform-Windows%20%C2%B7%20macOS-9b93b8)

</div>

---

A tool-using agent you can point at **anything** — your own GGUF models on the GPU via
`llama.cpp`, a remote llama-server, or the big cloud APIs (OpenAI, Anthropic, xAI,
OpenRouter, nano-gpt) — wrapped in a polished terminal UI, backed by a portable tool
layer, an **MCP tool server with a web backoffice**, and a rigorous **tool-calling
benchmark** that decides which local model is actually worth running.

---

## ✨ Highlights

| | |
|---|---|
| 🖥️ **Multi-provider TUI** | One wizard, 7 providers: `nano-gpt`, **local llama.cpp** (your GPU), **remote llama.cpp** (enter a URL), `OpenAI`, `Anthropic`, `xAI`, `OpenRouter`. |
| 📊 **Live telemetry** | In local mode, real-time GPU-util sparkline + VRAM/CPU/RAM bars + temp/power/tok-s, straight from `nvidia-smi` + `psutil`. |
| 🧩 **Streaming + tool theatre** | Thinking streamed live, tool calls rendered with colors, final answers as clean Markdown, tok/s on every turn. |
| 🔐 **Permission gating** | Claude-Code-style allow/always/deny prompts for flagged operations (`--dangerously-skip-permissions` to opt out). |
| 🛰️ **MCP tool server** | Serve the whole toolset over MCP (`/mcp`) with a web **backoffice** to enable/disable tools and author custom shell tools — no redeploy of the agent. |
| 🏁 **Decisive benchmark** | 4 tiers, deterministic decoding, programmatic graders, resumable runs, and a leaderboard that tells you which model to burn your VRAM on. |
| ⚙️ **Zero hardcoded secrets** | Every key/path/host lives in gitignored local config; the repo ships with safe placeholders and `*.example.json` templates. |

---

## 🏗️ Architecture

```mermaid
flowchart LR
  you([you]) --> tui["🖥️ Textual TUI<br/>(tui.py)"]
  cli["⌨️ headless agent<br/>(agent.py)"]
  tui -->|OpenAI-compatible| P{provider}
  cli -->|OpenAI-compatible| P
  P --> nano[nano-gpt]
  P --> local["llama.cpp<br/>local GPU"]
  P --> remote["llama.cpp<br/>remote URL"]
  P --> cloud["OpenAI · Anthropic<br/>xAI · OpenRouter"]
  tui --> T["🧰 tools.py<br/>read-only + exec tools"]
  tui -->|mac_* prefix| M["🛰️ MCP server<br/>+ web backoffice"]
  M --> T2["tools on the host<br/>(file · shell · python)"]
  B["🏁 benchmark<br/>(bench/)"] --> local
```

---

## 🖥️ The TUI — `tui.py`

A [Textual](https://textual.textualize.io/) app. Launch it and a startup wizard walks you
through **provider → model → context**:

```bash
python tui.py                       # interactive wizard
python tui.py --provider local --model qwen35-9b
python tui.py --dangerously-skip-permissions
```

- **Provider wizard** with back-navigation, last-used highlighting, and per-provider
  **favorites** (`f`).
- **Remote llama.cpp**: pick it, paste a URL, and it lists the served models.
- **API-key prompt**: choose a cloud provider with no key on file and it asks for one
  (saved to `tui_config.json`).
- **Local mode**: computes the optimal `ctx-size` for your GPU (overridable), **starts the
  llama-server only if it isn't already running**, and stops it on exit.
- **Settings (`F2`)**: open and edit `tui_config.json` in-app, with JSON validation.
- **MCP routing**: remote tools are auto-discovered and exposed with a `mac_` prefix.

There's also a headless agent loop in **`agent.py`** (same providers via env vars) for
scripting:

```bash
AGENT_PROVIDER=llamacpp AGENT_BASE_URL=http://localhost:25565/v1 \
AGENT_API_KEY=sk-noop AGENT_MODEL=qwen35-9b python agent.py
```

---

## 🧰 Tools — `tools.py`

A portable, stdlib-only tool layer shared by the TUI, the agent, and the MCP server.

**Read-only:** `read_file` · `list_dir` · `get_current_directory` · `grep` · `find_files`
· `read_lines` · `head` · `tail` · `count_lines` · `stat_path` · `tree` · `find_in_files`

**Write / execute (gated):** `write_file` · `edit_file` · `run_python` · `run_powershell`
· `vault_get_secret`

Execution tools run in a subprocess with **timeouts** and a **denylist** that blocks only
genuinely destructive operations (`rm -rf`, `format`, `shutdown`, registry edits); normal
subprocess/network use is allowed. Anything flagged triggers a permission prompt in the TUI.

> Host-specific capabilities (e.g. operating a remote Mac) are **not** shipped here — the
> agent reaches them through the MCP server, whose file/shell tools already run on that host.

---

## 🛰️ MCP server + backoffice — `mcp_server/server.py`

Exposes the toolset over the **Model Context Protocol** (streamable-HTTP) and serves a web
**backoffice** to manage it live.

```bash
python mcp_server/server.py --http --port 8765
#   MCP endpoint  →  http://<host>:8765/mcp
#   Backoffice    →  http://<host>:8765/
```

The backoffice lets you **enable/disable** any tool, see the **full description the model
receives**, and author **custom shell tools** (`name + description + command with {args}`) —
it saves to `tools_config.json` and hot-restarts the server. Point the TUI at it via the
`mcp` section of `tui_config.json`.

---

## 🏁 Benchmark harness — `bench/`

A decisive, reproducible tool-calling benchmark for local GGUF models. Deterministic
decoding (`temperature=0, seed=42`), **programmatic graders** (no vibes), **resumable**
runs (per-task JSONL + fsync), thinking **ON/OFF** per model, and a weighted leaderboard
(`70% correctness · 20% tool-selection · 10% efficiency − penalties`).

```bash
python bench/run_bench.py            # basic tier (read-only tools)
python bench/run_adv.py              # advanced: write/edit/python/powershell
python bench/run_circuit.py          # circuit: Vault secret → remote copy
python bench/run_hard.py             # hardcore: real dev/sysadmin incidents
```

It serves one model at a time (auto `--jinja`, kills between runs so VRAM never stacks),
and any model is one line in `bench/models.json`.

### Overall leaderboard — basic tier (104 read-only tool tasks)

![Leaderboard](assets/leaderboard.png)

### The key finding: thinking is not free

Enabling the model's own "thinking" only helps the Mythos merge; it **hurts** every other
model — sometimes badly.

![Thinking impact](assets/thinking_impact.png)

### The 3 finalists, stress-tested across all tiers

They ace the easy tiers and **flake under real pressure** (multi-step incidents, secret
hunting, multi-file edits).

![Across tiers](assets/tiers.png)

> **Verdict:** **`qwen3.5-9b` with thinking OFF** is the best all-rounder — it ties on the
> basic tier, wins advanced/circuit/hardcore, and is faster and cheaper than the rest.
> `gemma4-12b` (nothink) is a close second. The Mythos merge (`qwythos`) only keeps up
> *with* thinking, and fades first under load.

Regenerate the charts after a new run:

```bash
python bench/make_charts.py          # needs matplotlib
```

---

## 🚀 Quickstart

```bash
git clone <your-fork-url> raiko-toolkit && cd raiko-toolkit
python -m venv .venv && .venv\Scripts\activate        # Windows
pip install -r requirements.txt

# create your local config from the templates (these are gitignored)
copy tui_config.example.json tui_config.json          # add your API keys
copy bench\models.example.json bench\models.json       # add your .gguf paths

python tui.py
```

> **Local llama.cpp** needs [`llama-server`](https://github.com/ggml-org/llama.cpp) built
> with tool support; the benchmark launches it with `--jinja` (required for tool-calling).

---

## 🔧 Configuration (all gitignored)

| File | What it holds | Template |
|---|---|---|
| `tui_config.json` | Provider base-urls, **API keys**, default model, MCP url, favorites | `tui_config.example.json` |
| `bench/models.json` | `llama-server` path, models folder, one entry per GGUF model | `bench/models.example.json` |
| `mcp_server/tools_config.json` | Enabled/disabled tools + custom shell tools | *(auto-created)* |
| `mac-credentials.txt` | (optional, benchmark only) `user:pass` + host for the circuit tier | — |

No secrets, keys, IPs, or machine-specific paths live in the source — only in these files.

---

## 📂 Layout

```
raiko-toolkit/
├─ tui.py              # Textual TUI (multi-provider, telemetry, MCP routing)
├─ agent.py           # headless agent loop (same providers via env)
├─ tools.py           # portable read-only + execution tool layer
├─ context.py         # token / context-window tracker
├─ mcp_client.py      # MCP client (loads remote tools into the agent)
├─ mcp_server/        # FastMCP server + web backoffice
├─ bench/             # benchmark harness, tiers, graders, charts
└─ assets/            # README charts
```
