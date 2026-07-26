"""Tests for engine.skills: discovery order/precedence, lazy index, load, and
the skill tool schema. Uses RAIKO_HOME isolation from conftest.py so
<app_home>/skills is a throwaway tmp dir per test session."""

import logging
import os

import pytest

from engine import skills as sk
from engine.config import _app_home


def _write_skill(root, dirname, frontmatter_lines, body="Do the thing.\n"):
    """Create <root>/<dirname>/SKILL.md with the given frontmatter body lines
    (already YAML, no leading '---')."""
    d = os.path.join(root, dirname)
    os.makedirs(d, exist_ok=True)
    fm = "\n".join(frontmatter_lines)
    content = f"---\n{fm}\n---\n{body}"
    with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(content)
    return d


@pytest.fixture
def app_skills_dir():
    d = os.path.join(_app_home(), "skills")
    os.makedirs(d, exist_ok=True)
    return d


def test_discover_full_frontmatter(app_skills_dir):
    _write_skill(app_skills_dir, "greet",
                 ['name: greeter', 'description: "Greets the user warmly."'])
    found = sk.discover_skills({"skills": {"enabled": True}})
    names = {s.name: s for s in found}
    assert "greeter" in names
    s = names["greeter"]
    assert s.description == "Greets the user warmly."
    assert s.source == "raiko"
    assert s.path.endswith("SKILL.md")
    assert os.path.isdir(s.base_dir)


def test_discover_default_name_from_dirname(app_skills_dir):
    _write_skill(app_skills_dir, "my-cool-skill",
                 ['description: "No name field here."'])
    found = sk.discover_skills({"skills": {"enabled": True}})
    names = [s.name for s in found]
    assert "my-cool-skill" in names


def test_discover_skips_missing_description(app_skills_dir, caplog):
    _write_skill(app_skills_dir, "no-desc", ['name: nodesc'])
    with caplog.at_level(logging.WARNING):
        found = sk.discover_skills({"skills": {"enabled": True}})
    assert all(s.name != "nodesc" for s in found)
    assert any(r.levelno == logging.WARNING and "description" in r.getMessage() for r in caplog.records)


def test_discover_invalid_yaml_does_not_break_others(app_skills_dir, caplog):
    bad_dir = os.path.join(app_skills_dir, "broken")
    os.makedirs(bad_dir, exist_ok=True)
    with open(os.path.join(bad_dir, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write("---\nname: [unclosed\ndescription: broken\n---\nbody\n")
    _write_skill(app_skills_dir, "good", ['name: good', 'description: "Fine."'])
    with caplog.at_level(logging.WARNING):
        found = sk.discover_skills({"skills": {"enabled": True}})
    names = [s.name for s in found]
    assert "good" in names
    assert "broken" not in names


def test_enabled_false_returns_empty(app_skills_dir):
    _write_skill(app_skills_dir, "any", ['name: any', 'description: "d"'])
    assert sk.discover_skills({"skills": {"enabled": False}}) == []


def test_extra_paths(tmp_path):
    extra = tmp_path / "extra_skills"
    extra.mkdir()
    _write_skill(str(extra), "extra-one", ['name: extra-one', 'description: "From extra path."'])
    found = sk.discover_skills({"skills": {"enabled": True, "paths": [str(extra)]}})
    names = {s.name: s for s in found}
    assert "extra-one" in names
    assert names["extra-one"].source == "extra"


def test_precedence_first_wins(app_skills_dir, tmp_path, monkeypatch):
    # app_home skill named 'dup' should win over an ~/.agents/skills 'dup'
    _write_skill(app_skills_dir, "dup-app", ['name: dup', 'description: "From app_home."'])

    agents_home = tmp_path / "fake_home"
    agents_skills = agents_home / ".agents" / "skills"
    agents_skills.mkdir(parents=True)
    _write_skill(str(agents_skills), "dup-agents", ['name: dup', 'description: "From agents home."'])

    monkeypatch.setattr(sk.Path, "home", classmethod(lambda cls: agents_home))
    found = sk.discover_skills({"skills": {"enabled": True}})
    dups = [s for s in found if s.name == "dup"]
    assert len(dups) == 1
    assert dups[0].description == "From app_home."


def test_use_claude_skills_respected(tmp_path, monkeypatch):
    fake_home = tmp_path / "fake_home2"
    claude_skills = fake_home / ".claude" / "skills"
    claude_skills.mkdir(parents=True)
    _write_skill(str(claude_skills), "claude-one", ['name: claude-one', 'description: "Claude skill."'])
    monkeypatch.setattr(sk.Path, "home", classmethod(lambda cls: fake_home))

    without = sk.discover_skills({"skills": {"enabled": True, "use_claude_skills": False}})
    assert all(s.name != "claude-one" for s in without)

    with_it = sk.discover_skills({"skills": {"enabled": True, "use_claude_skills": True}})
    names = {s.name: s for s in with_it}
    assert "claude-one" in names
    assert names["claude-one"].source == "claude"


def test_skills_index_empty():
    assert sk.skills_index([]) == ""


def test_skills_index_format_and_truncation():
    long_desc = "x" * 250
    skills = [sk.Skill(name="a", description=long_desc, path="p", base_dir="b", source="raiko")]
    idx = sk.skills_index(skills, max_desc=200)
    assert "# Skills" in idx
    assert "<available_skills>" in idx
    assert "<name>a</name>" in idx
    assert ("x" * 200 + "…") in idx
    assert len(long_desc) > 200 and (long_desc) not in idx


def test_load_skill_happy_path(app_skills_dir):
    _write_skill(app_skills_dir, "loadme",
                 ['name: loadme', 'description: "d"'],
                 body="Step 1. Do X.\nStep 2. Do Y.\n")
    found = sk.discover_skills({"skills": {"enabled": True}})
    text = sk.load_skill(found, "loadme")
    assert text.startswith("# Skill: loadme")
    assert "Step 1. Do X." in text
    assert "---" not in text.split("\n\n", 1)[0]  # frontmatter stripped
    assert "Base directory for this skill:" in text
    match = next(s for s in found if s.name == "loadme")
    assert match.base_dir in text


def test_load_skill_unknown_lists_available(app_skills_dir):
    _write_skill(app_skills_dir, "one", ['name: one', 'description: "d"'])
    _write_skill(app_skills_dir, "two", ['name: two', 'description: "d"'])
    found = sk.discover_skills({"skills": {"enabled": True}})
    result = sk.load_skill(found, "nope")
    assert result.startswith("ERROR: unknown skill 'nope'")
    assert "one" in result and "two" in result


def test_load_skill_no_skills_available():
    assert sk.load_skill([], "anything") == "ERROR: no skills available"


def test_skill_tool_schema():
    schema = sk.skill_tool_schema()
    assert schema["type"] == "function"
    fn = schema["function"]
    assert fn["name"] == "skill"
    assert fn["parameters"]["required"] == ["name"]
    assert "name" in fn["parameters"]["properties"]
