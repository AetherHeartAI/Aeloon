"""Shared runtime helpers for CLI commands."""

from __future__ import annotations

import os
from pathlib import Path

import typer

from aeloon.cli.app import console
from aeloon.core.config.env import load_profile_env
from aeloon.core.config.schema import Config
from aeloon.providers.base import LLMProvider


def make_provider(config: Config) -> LLMProvider:
    """Create the appropriate LLM provider from config."""
    from aeloon.providers.azure_openai_provider import AzureOpenAIProvider
    from aeloon.providers.base import GenerationSettings
    from aeloon.providers.openai_codex_provider import OpenAICodexProvider

    model = config.agents.defaults.model
    provider_name = config.get_provider_name(model) or "custom"
    provider_config = config.get_provider(model)
    provider: LLMProvider

    if provider_name == "openai_codex" or model.startswith("openai-codex/"):
        provider = OpenAICodexProvider(default_model=model)
    elif provider_name == "custom":
        from aeloon.providers.custom_provider import CustomProvider

        provider = CustomProvider(
            api_key=provider_config.api_key if provider_config else "no-key",
            api_base=config.get_api_base(model) or "http://localhost:8000/v1",
            default_model=model,
            extra_headers=provider_config.extra_headers if provider_config else None,
        )
    elif provider_name == "azure_openai":
        if not provider_config or not provider_config.api_key or not provider_config.api_base:
            console.print("[red]Error: Azure OpenAI requires api_key and api_base.[/red]")
            console.print("Set them in ~/.aeloon/config.json under providers.azure_openai section")
            console.print("Use the model field to specify the deployment name.")
            raise typer.Exit(1)
        provider = AzureOpenAIProvider(
            api_key=provider_config.api_key,
            api_base=provider_config.api_base,
            default_model=model,
        )
    else:
        from aeloon.providers.litellm_provider import LiteLLMProvider
        from aeloon.providers.registry import find_by_name

        spec = find_by_name(provider_name)
        if (
            not model.startswith("bedrock/")
            and not (provider_config and provider_config.api_key)
            and not (spec and (spec.is_oauth or spec.is_local))
        ):
            console.print("[red]Error: No API key configured.[/red]")
            console.print("Set one in ~/.aeloon/config.json under providers section")
            raise typer.Exit(1)
        provider = LiteLLMProvider(
            api_key=provider_config.api_key if provider_config else None,
            api_base=config.get_api_base(model),
            default_model=model,
            extra_headers=provider_config.extra_headers if provider_config else None,
            provider_name=provider_name,
        )

    defaults = config.agents.defaults
    provider.generation = GenerationSettings(
        temperature=defaults.temperature,
        max_tokens=defaults.max_tokens,
        reasoning_effort=defaults.reasoning_effort,
    )
    provider._CHAT_TIMEOUT_S = defaults.chat_timeout
    return provider


def load_runtime_config(config: str | None = None, workspace: str | None = None) -> Config:
    """Load config and optionally override the active workspace."""
    from aeloon.core.config.loader import get_config_path, load_config, set_config_path

    config_path = None
    if config:
        config_path = Path(config).expanduser().resolve()
        if not config_path.exists():
            console.print(f"[red]Error: Config file not found: {config_path}[/red]")
            raise typer.Exit(1)
        set_config_path(config_path)
        console.print(f"[dim]Using config: {config_path}[/dim]")

    loaded = load_config(config_path)
    active_config_path = config_path or get_config_path()
    load_profile_env(active_config_path)
    if workspace:
        loaded.agents.defaults.workspace = workspace
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "true" if loaded.agents.defaults.fast else "false"
    return loaded


def print_deprecated_memory_window_notice(config: Config) -> None:
    """Warn when running with old memoryWindow-only config."""
    if config.agents.defaults.should_warn_deprecated_memory_window:
        console.print(
            "[yellow]Hint:[/yellow] Detected deprecated `memoryWindow` without "
            "`contextWindowTokens`. `memoryWindow` is ignored; run "
            "[cyan]aeloon onboard[/cyan] to refresh your config template."
        )
