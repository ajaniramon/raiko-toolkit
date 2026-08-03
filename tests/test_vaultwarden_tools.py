import base64
import json

import tools


def test_vaultwarden_tool_schema_replaces_hashicorp_tool():
    names = {entry["function"]["name"] for entry in tools.TOOLS}
    assert "vault_get_secret" not in names
    assert {
        "vaultwarden_status",
        "vaultwarden_get_secret",
        "vaultwarden_copy_secret",
        "vaultwarden_create_secret",
    } <= names


def test_vaultwarden_get_secret_reads_selected_field(monkeypatch):
    monkeypatch.setattr(
        tools,
        "_bw_exact_item",
        lambda name: (
            {
                "id": "item-id",
                "name": name,
                "fields": [{"name": "api-key", "value": "field-value"}],
            },
            None,
        ),
    )
    values = {"password": "pw-value", "totp": "totp-value", "notes": "note-value"}
    monkeypatch.setattr(
        tools,
        "_bw_run",
        lambda args, timeout=30, input_text=None: (0, values[args[1]], ""),
    )
    assert json.loads(tools.vaultwarden_get_secret("Example"))["value"] == "pw-value"
    assert json.loads(tools.vaultwarden_get_secret("Example", "totp"))["value"] == "totp-value"
    assert json.loads(tools.vaultwarden_get_secret("Example", "notes"))["value"] == "note-value"
    assert (
        json.loads(tools.vaultwarden_get_secret("Example", "custom", "api-key"))["value"]
        == "field-value"
    )


def test_vaultwarden_create_generates_without_returning_plaintext(monkeypatch):
    generated = "A" * 24
    captured = {}
    monkeypatch.setattr(tools, "_bw_unlocked_status", lambda: ({}, None))
    monkeypatch.setattr(tools, "_bw_search_exact", lambda name: ([], None))
    monkeypatch.setattr(tools.secrets, "choice", lambda alphabet: "A")

    def fake_json(args, timeout=30, input_text=None):
        assert args[:2] == ["create", "item"]
        assert len(args) == 2
        item = json.loads(base64.b64decode(input_text).decode())
        captured.update(item)
        return {"id": "item-id"}, None

    monkeypatch.setattr(tools, "_bw_json", fake_json)
    result = tools.vaultwarden_create_secret("Generated", length=24)
    parsed = json.loads(result)
    assert captured["login"]["password"] == generated
    assert generated not in result
    assert parsed["secretReturned"] is False
    assert parsed["id"] == "item-id"


def test_vaultwarden_copy_does_not_return_plaintext(monkeypatch):
    monkeypatch.setattr(
        tools, "_vaultwarden_item_value", lambda name, field, field_name: ("top-secret", None)
    )
    copied = []
    monkeypatch.setattr(
        tools,
        "_copy_secret_to_clipboard",
        lambda value, ttl: (copied.append((value, ttl)), None)[1],
    )
    result = tools.vaultwarden_copy_secret("Example", ttl_seconds=30)
    assert copied == [("top-secret", 30)]
    assert "top-secret" not in result
    assert result.startswith("OK:")
