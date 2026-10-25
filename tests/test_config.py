"""tui_config.json load/save: an unreadable file must never be overwritten with
the defaults (that is how API keys and the web token used to vanish)."""

import json

from engine import config


def test_unparseable_config_is_never_overwritten(tmp_path, monkeypatch):
    path = tmp_path / "tui_config.json"
    original = '{"openai": {"api_key": "sk-mia"},'      # trailing comma: invalid
    path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", str(path))
    cfg = config.load_config()
    assert cfg["openai"]["api_key"] == ""                # ran on the defaults
    assert config.save_config(cfg) is False
    assert path.read_text(encoding="utf-8") == original
    monkeypatch.setattr(config, "_LOAD_ERROR", None)


def test_valid_config_round_trips_atomically(tmp_path, monkeypatch):
    path = tmp_path / "tui_config.json"
    path.write_text(json.dumps({"openai": {"api_key": "sk-mia"}}), encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", str(path))
    cfg = config.load_config()
    cfg["budget_usd"] = 5
    assert config.save_config(cfg) is True
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["openai"]["api_key"] == "sk-mia" and saved["budget_usd"] == 5
    assert [p.name for p in tmp_path.iterdir()] == ["tui_config.json"]
