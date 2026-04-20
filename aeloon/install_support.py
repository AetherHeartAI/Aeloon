"""Installer support helpers for provider selection and model discovery."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import httpx

from aeloon.providers.registry import PROVIDERS, find_by_name

_MODEL_CACHE_TTL_S = 60 * 60 * 6

_DEFAULT_API_BASES: dict[str, str] = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "gemini": "https://generativelanguage.googleapis.com",
    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
    "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "groq": "https://api.groq.com/openai/v1",
}

_RECOMMENDED_MODELS: dict[str, str] = {
    "custom": "gpt-4.1-mini",
    "azure_openai": "your-deployment-name",
    "openrouter": "anthropic/claude-sonnet-4",
    "aihubmix": "claude-sonnet-4-20250514",
    "siliconflow": "Qwen/Qwen3-Coder-480B-A35B-Instruct",
    "volcengine": "deepseek-v3-1-250821",
    "volcengine_coding_plan": "doubao-seed-1-6-thinking-250715",
    "byteplus": "deepseek-v3-1-250821",
    "byteplus_coding_plan": "doubao-seed-1-6-thinking-250715",
    "anthropic": "claude-sonnet-4-20250514",
    "openai": "gpt-4.1-mini",
    "openai_codex": "openai-codex/gpt-5.1-codex",
    "github_copilot": "github-copilot/gpt-4o",
    "deepseek": "deepseek-chat",
    "gemini": "gemini-2.5-pro",
    "zhipu": "glm-4.5",
    "dashscope": "qwen-max",
    "moonshot": "kimi-k2.5",
    "minimax": "MiniMax-M2.1",
    "vllm": "gpt-4.1-mini",
    "ollama": "llama3.2",
    "groq": "llama-3.3-70b-versatile",
}

_OPENAI_COMPATIBLE = {
    "custom",
    "openrouter",
    "aihubmix",
    "siliconflow",
    "volcengine",
    "volcengine_coding_plan",
    "byteplus",
    "byteplus_coding_plan",
    "openai",
    "deepseek",
    "zhipu",
    "dashscope",
    "moonshot",
    "minimax",
    "vllm",
    "groq",
}


def _normalize_provider(name: str) -> str:
    return name.strip().replace("-", "_")


def recommended_model(provider: str) -> str:
    return _RECOMMENDED_MODELS.get(_normalize_provider(provider), "anthropic/claude-sonnet-4")


def resolve_api_base(provider: str, api_base: str | None = None) -> str:
    if api_base and api_base.strip():
        return api_base.strip().rstrip("/")
    name = _normalize_provider(provider)
    spec = find_by_name(name)
    if spec and spec.default_api_base:
        return spec.default_api_base.rstrip("/")
    return _DEFAULT_API_BASES.get(name, "").rstrip("/")


def provider_records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for spec in PROVIDERS:
        records.append(
            {
                "name": spec.name,
                "label": spec.label,
                "oauth": spec.is_oauth,
                "local": spec.is_local,
                "default_api_base": resolve_api_base(spec.name),
                "recommended_model": recommended_model(spec.name),
            }
        )
    return records


def providers_text() -> str:
    lines: list[str] = []
    for item in provider_records():
        tags: list[str] = []
        if item.get("oauth"):
            tags.append("oauth")
        if item.get("local"):
            tags.append("local")
        tag_text = f" [{', '.join(tags)}]" if tags else ""
        lines.append(f"  - {item['name']:<22} {item['label']}{tag_text}")
        rec = item.get("recommended_model") or ""
        if rec:
            lines.append(f"      default model: {rec}")
    return "\n".join(lines)


def providers_menu_text() -> str:
    lines: list[str] = []
    for item in provider_records():
        tags: list[str] = []
        if item.get("oauth"):
            tags.append("oauth")
        if item.get("local"):
            tags.append("local")
        suffix = f" [{', '.join(tags)}]" if tags else ""
        rec = item.get("recommended_model") or ""
        label = item["label"] + suffix
        if rec:
            label += f" - {rec}"
        lines.append(f"{item['name']}\t{label}")
    return "\n".join(lines)


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        value = item.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _model_cache_path(provider: str, api_base: str, api_key: str | None) -> Path:
    from aeloon.core.config.loader import get_aeloon_home

    raw = f"{provider}|{api_base}|{api_key or ''}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return get_aeloon_home() / "cache" / "provider-models" / f"{provider}-{digest}.json"


def _load_model_cache(provider: str, api_base: str, api_key: str | None) -> dict[str, Any] | None:
    path = _model_cache_path(provider, api_base, api_key)
    if not path.exists():
        return None
    if time.time() - path.stat().st_mtime > _MODEL_CACHE_TTL_S:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _save_model_cache(
    provider: str, api_base: str, api_key: str | None, result: dict[str, Any]
) -> None:
    path = _model_cache_path(provider, api_base, api_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False)
    except OSError:
        return


def _parse_model_ids(payload: Any) -> list[str]:
    ids: list[str] = []
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    for key in ("id", "model", "name"):
                        value = item.get(key)
                        if isinstance(value, str):
                            ids.append(value)
                            break
        value = payload.get("value")
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    for key in ("id", "model", "name"):
                        raw = item.get(key)
                        if isinstance(raw, str):
                            ids.append(raw)
                            break
    return _dedupe_keep_order(ids)


async def _get_json(
    client: httpx.AsyncClient, url: str, headers: dict[str, str] | None = None
) -> dict[str, Any] | None:
    response = await client.get(url, headers=headers)
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, dict) else None


async def _detect_openai_compatible_models(
    client: httpx.AsyncClient, api_base: str, api_key: str | None
) -> list[str]:
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    candidates = [f"{api_base}/models"]
    if not api_base.endswith("/v1"):
        candidates.append(f"{api_base}/v1/models")
    for url in candidates:
        try:
            payload = await _get_json(client, url, headers=headers)
        except Exception:
            continue
        if payload:
            models = _parse_model_ids(payload)
            if models:
                return models
    return []


async def _detect_anthropic_models(
    client: httpx.AsyncClient, api_key: str | None, api_base: str
) -> list[str]:
    if not api_key:
        return []
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    try:
        payload = await _get_json(client, f"{api_base}/v1/models", headers=headers)
    except Exception:
        return []
    return _parse_model_ids(payload or {})


async def _detect_gemini_models(
    client: httpx.AsyncClient, api_key: str | None, api_base: str
) -> list[str]:
    if not api_key:
        return []
    url = f"{api_base}/v1beta/models?key={api_key}"
    try:
        payload = await _get_json(client, url)
    except Exception:
        return []
    models: list[str] = []
    if isinstance(payload, dict) and isinstance(payload.get("models"), list):
        for item in payload["models"]:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if isinstance(name, str):
                models.append(name.removeprefix("models/"))
    return _dedupe_keep_order(models)


async def _detect_ollama_models(client: httpx.AsyncClient, api_base: str) -> list[str]:
    candidates = [f"{api_base}/api/tags"]
    if not api_base.endswith("/v1"):
        candidates.append(f"{api_base}/v1/models")
    for url in candidates:
        try:
            payload = await _get_json(client, url)
        except Exception:
            continue
        models: list[str] = []
        if isinstance(payload, dict) and isinstance(payload.get("models"), list):
            for item in payload["models"]:
                if not isinstance(item, dict):
                    continue
                for key in ("model", "name", "id"):
                    raw = item.get(key)
                    if isinstance(raw, str):
                        models.append(raw)
                        break
        if not models:
            models = _parse_model_ids(payload or {})
        if models:
            return _dedupe_keep_order(models)
    return []


async def _detect_azure_models(
    client: httpx.AsyncClient, api_key: str | None, api_base: str
) -> list[str]:
    if not api_key or not api_base:
        return []
    headers = {"api-key": api_key}
    base = api_base.rstrip("/")
    if not base.endswith("/openai"):
        base = f"{base}/openai"
    for path in (
        f"{base}/models?api-version=2024-10-21",
        f"{base}/deployments?api-version=2024-10-21",
    ):
        try:
            payload = await _get_json(client, path, headers=headers)
        except Exception:
            continue
        models = _parse_model_ids(payload or {})
        if models:
            return models
    return []


async def detect_models(
    provider: str,
    api_key: str | None = None,
    api_base: str | None = None,
) -> dict[str, Any]:
    name = _normalize_provider(provider)
    resolved_base = resolve_api_base(name, api_base)
    recommended = recommended_model(name)

    cached = _load_model_cache(name, resolved_base, api_key)
    if cached is not None:
        cached.setdefault("provider", name)
        cached.setdefault("recommended", recommended)
        cached.setdefault("resolved_api_base", resolved_base)
        cached["cache_hit"] = True
        return cached

    if name in {"openai_codex", "github_copilot"}:
        return {
            "provider": name,
            "recommended": recommended,
            "resolved_api_base": resolved_base,
            "detected": False,
            "models": [],
            "message": "OAuth provider: model discovery is not available before login.",
        }

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        try:
            if name == "anthropic":
                models = await _detect_anthropic_models(client, api_key, resolved_base)
            elif name == "gemini":
                models = await _detect_gemini_models(client, api_key, resolved_base)
            elif name == "ollama":
                models = await _detect_ollama_models(client, resolved_base)
            elif name == "azure_openai":
                models = await _detect_azure_models(client, api_key, resolved_base)
            elif name in _OPENAI_COMPATIBLE:
                models = await _detect_openai_compatible_models(client, resolved_base, api_key)
            else:
                models = []
        except Exception as exc:
            return {
                "provider": name,
                "recommended": recommended,
                "resolved_api_base": resolved_base,
                "detected": False,
                "models": [],
                "message": f"Model detection failed: {exc}",
            }

    models = _dedupe_keep_order(models)
    result = {
        "provider": name,
        "recommended": recommended,
        "resolved_api_base": resolved_base,
        "detected": bool(models),
        "models": models,
        "message": "" if models else "No models detected; using recommended default.",
    }
    if models:
        _save_model_cache(name, resolved_base, api_key, result)
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Aeloon installer helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("providers", help="Print provider catalog as JSON")
    subparsers.add_parser("providers-text", help="Print provider catalog as text")
    subparsers.add_parser(
        "providers-menu", help="Print provider catalog as tab-separated menu entries"
    )

    recommended = subparsers.add_parser("recommended-model", help="Print recommended model")
    recommended.add_argument("--provider", required=True)

    detect = subparsers.add_parser("detect-models", help="Detect available models as JSON")
    detect.add_argument("--provider", required=True)
    detect.add_argument("--api-key", default="")
    detect.add_argument("--api-base", default="")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "providers":
        print(json.dumps({"providers": provider_records()}, ensure_ascii=False))
        return
    if args.command == "providers-text":
        print(providers_text())
        return
    if args.command == "providers-menu":
        print(providers_menu_text())
        return
    if args.command == "recommended-model":
        print(recommended_model(args.provider))
        return
    if args.command == "detect-models":
        result = asyncio.run(
            detect_models(
                args.provider, api_key=args.api_key or None, api_base=args.api_base or None
            )
        )
        print(json.dumps(result, ensure_ascii=False))
        return

    parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
