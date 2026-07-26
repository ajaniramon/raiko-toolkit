---
name: repo-briefing
description: Orient yourself in an unfamiliar repository and produce a concise briefing (purpose, layout, entry points, how to run it). Use when asked "what is this repo", for an overview/briefing of a codebase, or before working in a repo you have not explored this session.
---

# Repo briefing

Explore the repository with READ-ONLY tools and produce a short, structured briefing. Do not modify anything.

## Steps

1. `tree` with depth 2 on the repo root to see the layout. Ignore vendored/build dirs (node_modules, .venv, dist, __pycache__).
2. `head` (first ~60 lines) of the README if there is one.
3. Identify the stack from manifests: look for pyproject.toml / package.json / go.mod / Cargo.toml / pom.xml and `head` the one you find. Note declared entry points / scripts.
4. If entry points are still unclear, `grep` for `def main|if __name__|func main|public static void main` (pick the pattern matching the stack).
5. Stop exploring once you can fill the report — do NOT read every file, and never read files whole when `head` is enough.

## Report format

- **Purpose** — 1-2 sentences: what this project is.
- **Stack** — language(s), key frameworks/deps.
- **Layout** — 3-6 bullets: the directories that matter and what lives in each.
- **Entry points** — how it starts (scripts, main modules, CLIs).
- **How to run / test** — the commands, if the manifests declare them; say "not declared" otherwise.

Keep the whole briefing under ~25 lines. Answer in the user's language.
