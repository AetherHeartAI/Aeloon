"""Memory CLI commands."""

from __future__ import annotations

from pathlib import Path

import typer

from aeloon.cli.app import console
from aeloon.core.config.env import parse_env_file
from aeloon.core.config.loader import load_config, save_config, set_config_path
from aeloon.core.config.paths import (
    get_archive_db_path,
    get_env_path,
    get_profile_name,
    get_prompt_memory_dir,
)
from aeloon.core.config.schema import Config
from aeloon.core.session.manager import SessionManager
from aeloon.memory.archive_service import SessionArchiveService
from aeloon.memory.providers import MEMORY_PROVIDER_REGISTRY

memory_app = typer.Typer(help="Manage layered memory providers and status.")


def _provider_schema(provider_name: str) -> list[dict[str, object]]:
    spec = MEMORY_PROVIDER_REGISTRY.get(provider_name)
    if spec is None:
        raise typer.BadParameter(f"Unknown memory provider: {provider_name}")
    return spec.provider_cls.config_schema()


def _save_provider_values(provider_name: str, values: dict[str, object], loaded: Config) -> None:
    spec = MEMORY_PROVIDER_REGISTRY.get(provider_name)
    if spec is None:
        raise typer.BadParameter(f"Unknown memory provider: {provider_name}")
    spec.provider_cls.save_setup_values(values, loaded)


def _prepare_provider_setup_values(
    provider_name: str,
    values: dict[str, object],
) -> tuple[dict[str, object], list[str]]:
    spec = MEMORY_PROVIDER_REGISTRY.get(provider_name)
    if spec is None:
        raise typer.BadParameter(f"Unknown memory provider: {provider_name}")
    try:
        return spec.provider_cls.prepare_setup_values(values)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _provider_status_lines(provider_name: str, config: dict[str, object]) -> list[str]:
    spec = MEMORY_PROVIDER_REGISTRY.get(provider_name)
    if spec is None:
        raise typer.BadParameter(f"Unknown memory provider: {provider_name}")
    return spec.provider_cls.status_lines(config)


def _load_config_for_memory(
    config: str | None, workspace: str | None = None
) -> tuple[Config, Path]:
    config_path = Path(config).expanduser().resolve() if config else None
    if config_path is not None:
        set_config_path(config_path)
    loaded = load_config(config_path)
    if workspace:
        loaded.agents.defaults.workspace = workspace
    active_config_path = config_path or Path.cwd() / ".unused"
    if config_path is None:
        from aeloon.core.config.loader import get_config_path

        active_config_path = get_config_path()
    return loaded, active_config_path


def _write_env_values(env_path: Path, values: dict[str, str]) -> None:
    current = parse_env_file(env_path) if env_path.exists() else {}
    current.update(values)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(f"{key}={value}" for key, value in sorted(current.items()))
    env_path.write_text(payload + ("\n" if payload else ""), encoding="utf-8")


@memory_app.command("setup")
def setup_memory(
    provider_name: str = typer.Argument(..., help="Provider to configure, e.g. openviking"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace override"),
) -> None:
    loaded, config_path = _load_config_for_memory(config, workspace)
    secret_values: dict[str, str] = {}
    non_secret_values: dict[str, object] = {}
    for field in _provider_schema(provider_name):
        key = str(field["key"])
        default = field.get("default")
        prompt = str(field.get("description") or key)
        hide = bool(field.get("secret"))
        value = typer.prompt(prompt, default=str(default or ""), hide_input=hide)
        if field.get("env_var"):
            secret_values[str(field["env_var"])] = value
        else:
            non_secret_values[key] = value
    non_secret_values, setup_notes = _prepare_provider_setup_values(
        provider_name, non_secret_values
    )
    loaded.memory.provider = provider_name
    _save_provider_values(provider_name, non_secret_values, loaded)
    save_config(loaded, config_path)
    if secret_values:
        _write_env_values(get_env_path(config_path=config_path), secret_values)
    console.print(f"Memory provider configured: {provider_name}")
    for note in setup_notes:
        console.print(note)


@memory_app.command("status")
def memory_status(
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
) -> None:
    loaded, config_path = _load_config_for_memory(config)
    profile = get_profile_name(config_path) or "default"
    provider_name = loaded.memory.provider or "off"
    console.print(f"Active profile: {profile}")
    console.print(f"Prompt memory: {'on' if loaded.memory.prompt.enabled else 'off'}")
    console.print(f"Archive: {'on' if loaded.memory.archive.enabled else 'off'}")
    console.print(f"Provider: {provider_name}")
    if provider_name != "off":
        provider_config = loaded.memory.providers.get(provider_name, {})
        for line in _provider_status_lines(provider_name, provider_config):
            console.print(line)
    console.print(f"Prompt memory dir: {get_prompt_memory_dir(config_path=config_path)}")
    console.print(f"Archive DB: {get_archive_db_path(config_path=config_path)}")
    console.print(f"Env file: {get_env_path(config_path=config_path)}")


@memory_app.command("off")
def memory_off(
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
) -> None:
    loaded, config_path = _load_config_for_memory(config)
    loaded.memory.provider = None
    save_config(loaded, config_path)
    console.print("Memory provider disabled.")


@memory_app.command("reindex")
def memory_reindex(
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace override"),
) -> None:
    loaded, _config_path = _load_config_for_memory(config, workspace)
    sessions = SessionManager(loaded.workspace_path)
    archive = SessionArchiveService(workspace=loaded.workspace_path)
    count = 0
    for item in sessions.list_sessions():
        key = str(item.get("key") or "")
        if not key:
            continue
        archive.ingest_session_sync(sessions.get_or_create(key))
        count += 1
    console.print(f"Archive reindex complete: {count} session(s).")
