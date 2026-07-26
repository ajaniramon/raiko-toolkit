import asyncio
import time

import pytest

from web import model_catalog as catalog


def _live_model(model_id="nano/model-one"):
    return {
        "id": model_id,
        "name": "Model One",
        "owner": "nano",
        "description": None,
        "context_window": 128000,
        "max_output_tokens": 8192,
        "input_price": 0.25,
        "output_price": 1.5,
        "currency": "USD",
        "price_unit": "per_million_tokens",
        "price_source": "provider",
        "category": "fast",
        "capabilities": {"tool_calling": True},
        "favorite": False,
    }


def test_catalog_cache_refreshes_only_after_24_hours(monkeypatch, cfg):
    cfg["nano"]["api_key"] = "secret"
    monotonic = [100.0]
    wall = [1_800_000_000.0]
    calls = []

    def fetch(provider, received_cfg):
        calls.append((provider, received_cfg))
        return [_live_model()], "nano_detailed_api"

    monkeypatch.setattr(catalog, "fetch_provider_catalog", fetch)
    cache = catalog.ModelCatalogCache(
        cfg,
        clock=lambda: monotonic[0],
        wall_clock=lambda: wall[0],
    )

    first = asyncio.run(cache.get("nano"))
    second = asyncio.run(cache.get("nano"))
    assert first["cache"] == "miss"
    assert second["cache"] == "hit"
    assert second["ttl_seconds"] == 86_400
    assert second["models"][0]["input_price"] == 0.25
    assert len(calls) == 1

    monotonic[0] += 86_401
    wall[0] += 86_401
    refreshed = asyncio.run(cache.get("nano"))
    assert refreshed["cache"] == "miss"
    assert len(calls) == 2


def test_expired_catalog_serves_stale_data_when_refresh_fails(monkeypatch, cfg):
    cfg["gemini"]["api_key"] = "secret"
    monotonic = [10.0]
    calls = [0]

    def fetch(provider, received_cfg):
        del provider, received_cfg
        calls[0] += 1
        if calls[0] > 1:
            raise catalog.ModelCatalogError("temporary provider outage")
        return [_live_model("gemini-test")], "gemini_models_api"

    monkeypatch.setattr(catalog, "fetch_provider_catalog", fetch)
    cache = catalog.ModelCatalogCache(cfg, clock=lambda: monotonic[0])
    asyncio.run(cache.get("gemini"))
    monotonic[0] += 86_401

    stale = asyncio.run(cache.get("gemini"))
    assert stale["cache"] == "stale"
    assert stale["stale"] is True
    assert stale["models"][0]["id"] == "gemini-test"
    assert "temporary provider outage" in stale["error"]


def test_concurrent_cache_misses_share_one_provider_refresh(monkeypatch, cfg):
    cfg["nano"]["api_key"] = "secret"
    calls = [0]

    def fetch(provider, received_cfg):
        del provider, received_cfg
        calls[0] += 1
        time.sleep(0.03)
        return [_live_model()], "nano_detailed_api"

    monkeypatch.setattr(catalog, "fetch_provider_catalog", fetch)
    cache = catalog.ModelCatalogCache(cfg)

    async def request_together():
        return await asyncio.gather(*(cache.get("nano") for _ in range(8)))

    payloads = asyncio.run(request_together())
    assert calls[0] == 1
    assert payloads[0]["cache"] == "miss"
    assert all(payload["count"] == 1 for payload in payloads)


def test_nano_detailed_catalog_keeps_live_prices_and_favorites(monkeypatch, cfg):
    cfg["nano"]["api_key"] = "secret"
    cfg["favorites"]["nano"] = ["vendor/favorite"]

    monkeypatch.setattr(
        catalog,
        "_request_json",
        lambda *args, **kwargs: {
            "data": [
                {
                    "id": "vendor/other",
                    "name": "Other",
                    "owned_by": "vendor",
                    "context_length": 64_000,
                    "pricing": {
                        "prompt": 0.12,
                        "completion": 0.8,
                        "unit": "per_million_tokens",
                    },
                },
                {
                    "id": "vendor/favorite",
                    "name": "Favorite",
                    "owned_by": "vendor",
                    "pricing": {
                        "prompt": 1,
                        "completion": 3,
                        "unit": "per_million_tokens",
                    },
                },
            ]
        },
    )

    models, source = catalog.fetch_provider_catalog("nano", cfg)
    assert source == "nano_detailed_api"
    assert [item["id"] for item in models] == ["vendor/favorite", "vendor/other"]
    assert models[0]["favorite"] is True
    assert models[1]["input_price"] == 0.12
    assert models[1]["output_price"] == 0.8


def test_gemini_catalog_filters_non_chat_generation_models(monkeypatch, cfg):
    cfg["gemini"]["api_key"] = "secret"
    monkeypatch.setattr(
        catalog,
        "_request_json",
        lambda *args, **kwargs: {
            "models": [
                {
                    "name": "models/gemini-3.5-flash",
                    "displayName": "Gemini 3.5 Flash",
                    "supportedGenerationMethods": ["generateContent"],
                    "inputTokenLimit": 1_048_576,
                },
                {
                    "name": "models/gemini-3.1-flash-image",
                    "displayName": "Gemini image",
                    "supportedGenerationMethods": ["generateContent"],
                },
                {
                    "name": "models/gemini-omni-flash-preview",
                    "displayName": "Gemini Omni",
                    "supportedGenerationMethods": ["generateContent"],
                },
                {
                    "name": "models/text-embedding-004",
                    "supportedGenerationMethods": ["embedContent"],
                },
            ]
        },
    )

    models, source = catalog.fetch_provider_catalog("gemini", cfg)
    assert source == "gemini_models_api"
    assert [item["id"] for item in models] == ["gemini-3.5-flash"]
    assert models[0]["context_window"] == 1_048_576


def test_anthropic_catalog_uses_native_model_metadata(monkeypatch, cfg):
    cfg["anthropic"]["api_key"] = "secret"
    monkeypatch.setattr(
        catalog,
        "_request_json",
        lambda *args, **kwargs: {
            "data": [
                {
                    "id": "claude-sonnet-4-6",
                    "display_name": "Claude Sonnet 4.6",
                }
            ],
            "has_more": False,
        },
    )

    models, source = catalog.fetch_provider_catalog("anthropic", cfg)
    assert source == "anthropic_models_api"
    assert models[0]["id"] == "claude-sonnet-4-6"
    assert models[0]["owner"] == "anthropic"
    assert models[0]["input_price"] == 3
    assert models[0]["output_price"] == 15
    assert models[0]["price_source"] == "raiko_table"


def test_openai_catalog_filters_non_text_endpoints(monkeypatch, cfg):
    cfg["openai"]["api_key"] = "secret"
    monkeypatch.setattr(
        catalog,
        "_request_json",
        lambda *args, **kwargs: {
            "data": [
                {"id": "gpt-5-mini", "owned_by": "openai"},
                {"id": "text-embedding-3-large", "owned_by": "openai"},
                {"id": "gpt-realtime", "owned_by": "openai"},
                {"id": "gpt-5-codex", "owned_by": "openai"},
            ]
        },
    )

    models, source = catalog.fetch_provider_catalog("openai", cfg)
    assert source == "openai_compatible_api"
    assert [item["id"] for item in models] == ["gpt-5-mini"]
    assert models[0]["input_price"] == 0.25
    assert models[0]["output_price"] == 2


def test_empty_live_catalog_raises_instead_of_caching_success(monkeypatch, cfg):
    cfg["openai"]["api_key"] = "secret"
    monkeypatch.setattr(
        catalog,
        "_request_json",
        lambda *args, **kwargs: {"data": []},
    )
    with pytest.raises(catalog.ModelCatalogError, match="empty"):
        catalog.fetch_provider_catalog("openai", cfg)
