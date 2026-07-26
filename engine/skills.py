"""Agent Skills: discover SKILL.md files (Anthropic's Agent Skills standard,
frontmatter + markdown body), expose a lazy index (name+description only) for
the system prompt, and load a skill's full body on demand via the `skill` tool.

Layout per skill: a directory containing SKILL.md, with YAML frontmatter
(`name`, `description`) followed by the instructions body. Optional resources
(scripts/, references/, ...) live alongside SKILL.md and are resolved by the
model against `base_dir`.
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from engine.config import _app_home

logger = logging.getLogger(__name__)


@dataclass
class Skill:
    name: str
    description: str
    path: str       # absolute path to SKILL.md
    base_dir: str   # parent directory of SKILL.md
    source: str     # "raiko" | "agents" | "claude" | "extra"


def _parse_frontmatter(text):
    """Split '---\\n...yaml...\\n---\\nbody' into (meta dict, body str).
    Returns (None, text) if there is no valid frontmatter block."""
    if not text.lstrip().startswith("---"):
        return None, text
    stripped = text.lstrip("﻿")
    # first line must be exactly '---' (allow trailing whitespace/CR)
    lines = stripped.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return None, text
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return None, text
    yaml_block = "".join(lines[1:end_idx])
    body = "".join(lines[end_idx + 1:])
    meta = yaml.safe_load(yaml_block)
    if meta is None:
        meta = {}
    if not isinstance(meta, dict):
        return None, text
    return meta, body


def _scan_root(root, source, seen):
    """Yield Skill objects for every SKILL.md under `root`, skipping names
    already in `seen`. Never raises: a missing/unreadable root or an
    individual bad file is logged and skipped."""
    out = []
    try:
        root_path = Path(root)
        if not root_path.is_dir():
            return out
        matches = sorted(root_path.glob("**/SKILL.md"))
    except Exception as e:
        logger.warning("skills: cannot scan %s: %s", root, e)
        return out
    for skill_md in matches:
        try:
            text = skill_md.read_text(encoding="utf-8")
            meta, _ = _parse_frontmatter(text)
            if meta is None:
                logger.warning("skills: %s has no valid frontmatter, skipping", skill_md)
                continue
            name = meta.get("name")
            if not name:
                name = skill_md.parent.name
            name = str(name)
            description = meta.get("description")
            if not description:
                logger.warning("skills: %s missing 'description', skipping", skill_md)
                continue
            if name in seen:
                continue
            seen.add(name)
            out.append(Skill(name=name, description=str(description),
                              path=str(skill_md.resolve()),
                              base_dir=str(skill_md.parent.resolve()),
                              source=source))
        except Exception as e:
            logger.warning("skills: error reading %s: %s", skill_md, e)
            continue
    return out


def discover_skills(cfg: dict) -> list:
    """Discover skills from the configured roots, first-seen-name wins on
    collision. Order: <app_home>/skills, ~/.agents/skills,
    ~/.claude/skills (if enabled), then each of `paths` (expanduser)."""
    scfg = cfg.get("skills", {}) if isinstance(cfg.get("skills"), dict) else {}
    if not scfg.get("enabled", True):
        return []
    paths = scfg.get("paths", []) or []
    use_claude_skills = bool(scfg.get("use_claude_skills", False))

    seen = set()
    skills = []
    skills += _scan_root(os.path.join(_app_home(), "skills"), "raiko", seen)
    skills += _scan_root(str(Path.home() / ".agents" / "skills"), "agents", seen)
    if use_claude_skills:
        skills += _scan_root(str(Path.home() / ".claude" / "skills"), "claude", seen)
    for p in paths:
        skills += _scan_root(str(Path(p).expanduser()), "extra", seen)
    return skills


def skills_index(skills: list, max_desc: int = 200) -> str:
    """System-prompt block listing available skills (name + truncated
    description only — bodies are loaded lazily via the `skill` tool)."""
    if not skills:
        return ""
    items = []
    for s in skills:
        desc = s.description
        if len(desc) > max_desc:
            desc = desc[:max_desc] + "…"
        items.append(f"  <skill>\n    <name>{s.name}</name>\n    <description>{desc}</description>\n  </skill>")
    body = "\n".join(items)
    return (
        "# Skills\n\n"
        "Skills are packaged instructions for specific tasks. When a task matches a "
        "skill's description, call the `skill` tool with its name BEFORE attempting "
        "the task, and follow the loaded instructions.\n\n"
        "<available_skills>\n"
        f"{body}\n"
        "</available_skills>"
    )


def load_skill(skills: list, name: str) -> str:
    """Full instructions for a skill by exact name (frontmatter stripped),
    plus the base directory relative resources resolve against."""
    if not skills:
        return "ERROR: no skills available"
    match = next((s for s in skills if s.name == name), None)
    if match is None:
        available = ", ".join(s.name for s in skills)
        return f"ERROR: unknown skill '{name}'. Available skills: {available}"
    try:
        text = Path(match.path).read_text(encoding="utf-8")
    except Exception as e:
        return f"ERROR: cannot read skill '{name}' at {match.path}: {e}"
    _, body = _parse_frontmatter(text)
    return (
        f"# Skill: {name}\n\n"
        f"{body.strip()}\n\n"
        f"Base directory for this skill: {match.base_dir}\n"
        "Relative paths mentioned above (scripts/, references/, ...) resolve against this base directory."
    )


def skill_tool_schema() -> dict:
    """OpenAI-style tool schema for the `skill` tool."""
    return {
        "type": "function",
        "function": {
            "name": "skill",
            "description": (
                "Load a skill's full instructions. Use when the current task matches "
                "one of the skills listed in the system prompt under <available_skills>."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Exact name of the skill to load"},
                },
                "required": ["name"],
            },
        },
    }
