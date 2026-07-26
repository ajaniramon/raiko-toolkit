"""Live model discovery and short-lived in-memory catalogs for Raiko web.

Provider credentials never leave this process.  The browser only receives the
model metadata needed to render a searchable picker.
"""

from __future__ import annotations

import asyncio
import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Callable

import requests

import pricing
from engine.config import resolve_key


MODEL_CATALOG_TTL_SECONDS = 24 * 60 * 60
MODEL_CATALOG_RETRY_SECONDS = 5 * 60
MODEL_CATALOG_TIMEOUT_SECONDS = 20
SUPPORTED_LIVE_CATALOGS = {
    "nano",
    "gemini",
    "anthropic",
    "openai",
    "xai",
    "openrouter",
    "remote",
    "vllm",
}


class ModelCatalogError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class _CachedCatalog:
    payload: dict
    expires_at_monotonic: float


def _number(value) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _configured_models(cfg: dict, provider: str) -> list[str]:
    pcfg = cfg.get(provider) or {}
    favorites = cfg.get("favorites") or {}
    result: list[str] = []
    for model in [
        pcfg.get("model"),
        *(pcfg.get("models") or []),
        *(favorites.get(provider) or []),
    ]:
        if isinstance(model, str) and model.strip() and model not in result:
            result.append(model)
    return result


def _static_prices(model_id: str, cfg: dict) -> tuple[float | None, float | None]:
    found = pricing.price_for(model_id, cfg.get("pricing") or {})
    return found if found else (None, None)


def _model_entry(
    *,
    provider: str,
    model_id: str,
    cfg: dict,
    name: str | None = None,
    owner: str | None = None,
    description: str | None = None,
    context_window: int | None = None,
    max_output_tokens: int | None = None,
    input_price=None,
    output_price=None,
    price_source: str | None = None,
    category: str | None = None,
    capabilities: dict | None = None,
) -> dict:
    parsed_input = _number(input_price)
    parsed_output = _number(output_price)
    if parsed_input is None and parsed_output is None:
        parsed_input, parsed_output = _static_prices(model_id, cfg)
        if parsed_input is not None or parsed_output is not None:
            price_source = "raiko_table"
    favorites = set((cfg.get("favorites") or {}).get(provider) or [])
    return {
        "id": model_id,
        "name": name or model_id,
        "owner": owner,
        "description": description,
        "context_window": context_window,
        "max_output_tokens": max_output_tokens,
        "input_price": parsed_input,
        "output_price": parsed_output,
        "currency": "USD" if parsed_input is not None or parsed_output is not None else None,
        "price_unit": (
            "per_million_tokens"
            if parsed_input is not None or parsed_output is not None
            else None
        ),
        "price_source": price_source,
        "category": category,
        "capabilities": capabilities or {},
        "favorite": model_id in favorites,
    }


def _sort_models(models: list[dict]) -> list[dict]:
    unique: dict[str, dict] = {}
    for model in models:
        model_id = model.get("id")
        if isinstance(model_id, str) and model_id and model_id not in unique:
            unique[model_id] = model
    return sorted(
        unique.values(),
        key=lambda item: (
            not item.get("favorite", False),
            str(item.get("name") or item["id"]).casefold(),
            item["id"].casefold(),
        ),
    )


def _request_json(url: str, *, headers: dict | None = None, params: dict | None = None) -> dict:
    response = requests.get(
        url,
        headers=headers,
        params=params,
        timeout=MODEL_CATALOG_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise ValueError("provider returned an invalid model catalog")
    return body


def _nano_catalog(provider: str, pcfg: dict, cfg: dict) -> list[dict]:
    body = _request_json(
        f"{pcfg['base_url'].rstrip('/')}/models",
        headers={"Authorization": f"Bearer {resolve_key(provider, pcfg)}"},
        params={"detailed": "true"},
    )
    return [
        _model_entry(
            provider=provider,
            model_id=item["id"],
            cfg=cfg,
            name=item.get("name"),
            owner=item.get("owned_by"),
            description=item.get("description"),
            context_window=item.get("context_length"),
            max_output_tokens=item.get("max_output_tokens"),
            input_price=(item.get("pricing") or {}).get("prompt"),
            output_price=(item.get("pricing") or {}).get("completion"),
            price_source="provider",
            category=item.get("category"),
            capabilities=item.get("capabilities"),
        )
        for item in body.get("data", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]


def _gemini_catalog(provider: str, pcfg: dict, cfg: dict) -> list[dict]:
    models: list[dict] = []
    page_token = None
    while True:
        params = {"pageSize": 1000}
        if page_token:
            params["pageToken"] = page_token
        body = _request_json(
            "https://generativelanguage.googleapis.com/v1beta/models",
            headers={"x-goog-api-key": resolve_key(provider, pcfg)},
            params=params,
        )
        for item in body.get("models", []):
            if not isinstance(item, dict):
                continue
            methods = item.get("supportedGenerationMethods") or []
            raw_id = item.get("name")
            if "generateContent" not in methods or not isinstance(raw_id, str):
                continue
            model_id = raw_id.removeprefix("models/")
            lowered = model_id.casefold()
            if any(
                marker in lowered
                for marker in (
                    "-tts",
                    "-image",
                    "nano-banana",
                    "lyria-",
                    "robotics-",
                    "computer-use",
                    "deep-research",
                    "antigravity",
                    "gemini-omni",
                )
            ):
                continue
            models.append(
                _model_entry(
                    provider=provider,
                    model_id=model_id,
                    cfg=cfg,
                    name=item.get("displayName"),
                    owner="google",
                    description=item.get("description"),
                    context_window=item.get("inputTokenLimit"),
                    max_output_tokens=item.get("outputTokenLimit"),
                    capabilities={"generate_content": True},
                )
            )
        page_token = body.get("nextPageToken")
        if not page_token:
            return models


def _anthropic_catalog(provider: str, pcfg: dict, cfg: dict) -> list[dict]:
    models: list[dict] = []
    after_id = None
    while True:
        params = {"limit": 1000}
        if after_id:
            params["after_id"] = after_id
        body = _request_json(
            f"{pcfg['base_url'].rstrip('/')}/models",
            headers={
                "x-api-key": resolve_key(provider, pcfg),
                "anthropic-version": "2023-06-01",
            },
            params=params,
        )
        page = body.get("data", [])
        for item in page:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            models.append(
                _model_entry(
                    provider=provider,
                    model_id=item["id"],
                    cfg=cfg,
                    name=item.get("display_name"),
                    owner="anthropic",
                )
            )
        if not body.get("has_more") or not page:
            return models
        after_id = body.get("last_id") or page[-1].get("id")
        if not after_id:
            return models


def _is_openai_text_model(model_id: str) -> bool:
    lowered = model_id.casefold()
    excluded = (
        "audio",
        "realtime",
        "transcribe",
        "tts",
        "image",
        "embedding",
        "moderation",
        "whisper",
        "dall-e",
        "sora",
        "search-preview",
        "computer-use",
        "deep-research",
        "codex",
    )
    return not any(marker in lowered for marker in excluded)


def _openai_compatible_catalog(provider: str, pcfg: dict, cfg: dict) -> list[dict]:
    headers = {}
    key = resolve_key(provider, pcfg)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    body = _request_json(
        f"{pcfg['base_url'].rstrip('/')}/models",
        headers=headers,
    )
    models = []
    for item in body.get("data", []):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        if provider == "openai" and not _is_openai_text_model(item["id"]):
            continue
        models.append(
            _model_entry(
                provider=provider,
                model_id=item["id"],
                cfg=cfg,
                name=item.get("name"),
                owner=item.get("owned_by"),
                description=item.get("description"),
                context_window=item.get("context_length"),
                max_output_tokens=item.get("max_output_tokens"),
                category=item.get("category"),
                capabilities=item.get("capabilities"),
            )
        )
    return models


def fetch_provider_catalog(provider: str, cfg: dict) -> tuple[list[dict], str]:
    pcfg = cfg.get(provider)
    if provider not in SUPPORTED_LIVE_CATALOGS or not isinstance(pcfg, dict):
        raise ModelCatalogError("live model discovery is not supported", 404)
    try:
        if provider == "nano":
            models = _nano_catalog(provider, pcfg, cfg)
            source = "nano_detailed_api"
        elif provider == "gemini":
            models = _gemini_catalog(provider, pcfg, cfg)
            source = "gemini_models_api"
        elif provider == "anthropic":
            models = _anthropic_catalog(provider, pcfg, cfg)
            source = "anthropic_models_api"
        else:
            models = _openai_compatible_catalog(provider, pcfg, cfg)
            source = "openai_compatible_api"
    except requests.RequestException as error:
        message = str(error).splitlines()[0][:180] or type(error).__name__
        raise ModelCatalogError(f"model discovery failed: {message}") from error
    except (KeyError, TypeError, ValueError) as error:
        raise ModelCatalogError("provider returned an invalid model catalog") from error
    models = _sort_models(models)
    if not models:
        raise ModelCatalogError("provider returned an empty model catalog")
    return models, source


class ModelCatalogCache:
    def __init__(
        self,
        cfg: dict,
        *,
        ttl_seconds: int = MODEL_CATALOG_TTL_SECONDS,
        retry_seconds: int = MODEL_CATALOG_RETRY_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ):
        self.cfg = cfg
        self.ttl_seconds = max(60, int(ttl_seconds))
        self.retry_seconds = max(10, int(retry_seconds))
        self._clock = clock
        self._wall_clock = wall_clock
        self._cache: dict[str, _CachedCatalog] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _fallback(self, provider: str) -> list[dict]:
        return _sort_models(
            [
                _model_entry(provider=provider, model_id=model, cfg=self.cfg)
                for model in _configured_models(self.cfg, provider)
            ]
        )

    async def get(self, provider: str) -> dict:
        now = self._clock()
        cached = self._cache.get(provider)
        if cached and now < cached.expires_at_monotonic:
            payload = deepcopy(cached.payload)
            payload["cache"] = "hit"
            return payload

        lock = self._locks.setdefault(provider, asyncio.Lock())
        async with lock:
            now = self._clock()
            cached = self._cache.get(provider)
            if cached and now < cached.expires_at_monotonic:
                payload = deepcopy(cached.payload)
                payload["cache"] = "hit"
                return payload
            try:
                models, source = await asyncio.to_thread(
                    fetch_provider_catalog,
                    provider,
                    self.cfg,
                )
                ttl = self.ttl_seconds
                stale = False
                error = None
                cache_state = "miss"
            except ModelCatalogError as caught:
                ttl = self.retry_seconds
                stale = True
                error = str(caught)
                cache_state = "stale"
                if cached and cached.payload.get("models"):
                    models = cached.payload["models"]
                    source = cached.payload.get("source") or "stale_cache"
                else:
                    models = self._fallback(provider)
                    source = "configuration"
                if not models:
                    raise

            fetched_at = self._wall_clock()
            payload = {
                "provider": provider,
                "models": deepcopy(models),
                "count": len(models),
                "source": source,
                "fetched_at": fetched_at,
                "expires_at": fetched_at + ttl,
                "ttl_seconds": self.ttl_seconds,
                "cache": cache_state,
                "stale": stale,
                "error": error,
            }
            self._cache[provider] = _CachedCatalog(
                payload=payload,
                expires_at_monotonic=now + ttl,
            )
            return deepcopy(payload)
