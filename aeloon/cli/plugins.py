"""Plugin bootstrap, catalog, runner, and CLI commands."""

from __future__ import annotations

import asyncio
import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import typer
from loguru import logger
from rich.table import Table

from aeloon.cli.app import app, command_catalog, console, ext_app
from aeloon.cli.registry import CommandCatalog, CommandSpec
from aeloon.utils.helpers import sync_workspace_templates

plugins_app = typer.Typer(help="Manage channel plugins")
app.add_typer(plugins_app, name="plugins")

plugin_cli_app = typer.Typer(help="Manage plugins")
app.add_typer(plugin_cli_app, name="plugin")

_MOUNTED_PLUGIN_CLI: set[str] = set()
_REGISTERED_PLUGIN_COMMANDS: set[str] = set()


def build_plugin_command_specs(registry: Any) -> tuple[CommandSpec, ...]:
    """Return command specs exposed by one plugin registry."""
    if registry is None:
        return ()

    cli_records = registry.cli_registrars
    cli_names = set(cli_records)
    specs: list[CommandSpec] = []

    for record in registry.commands.values():
        cli_record = cli_records.get(record.name)
        specs.append(
            CommandSpec(
                name=f"plugin_command:{record.plugin_id}:{record.name}",
                help=record.description or "(no description)",
                cli_path=("ext", record.name) if record.name in cli_names else None,
                slash_path=(record.name,),
            )
        )
        if cli_record and cli_record.commands:
            specs.extend(
                CommandSpec(
                    name=(
                        "plugin_subcommand:"
                        f"{record.plugin_id}:{cli_spec.group_name}:{cli_spec.command_name}"
                    ),
                    help=cli_spec.help,
                    slash_path=cli_spec.slash_path,
                    slash_paths=cli_spec.slash_paths,
                )
                for cli_spec in cli_record.commands
            )

    return tuple(specs)


def extend_catalog_with_plugin_commands(command_catalog: CommandCatalog, registry: Any) -> None:
    """Register plugin command specs into one catalog."""
    specs = build_plugin_command_specs(registry)
    command_catalog.extend(specs)
    if registry is None:
        return
    for record in registry.commands.values():
        command_catalog.register_handler(f"/{record.name}", record.handler)


def build_lightweight_plugin_registry():
    """Discover plugins and run register-only bootstrap for CLI metadata."""
    from aeloon.core.config.loader import get_aeloon_home, load_config
    from aeloon.plugins._sdk.api import PluginAPI
    from aeloon.plugins._sdk.discovery import PluginDiscovery
    from aeloon.plugins._sdk.loader import CircularDependencyError, PluginLoader
    from aeloon.plugins._sdk.registry import PluginRecord, PluginRegistry
    from aeloon.plugins._sdk.runtime import PluginRuntime
    from aeloon.plugins._sdk.state_store import PluginStateStore

    registry = PluginRegistry()
    discovery = PluginDiscovery(
        bundled_dir=Path(__file__).resolve().parent.parent / "plugins",
        workspace_dir=get_aeloon_home() / "plugins",
    )
    loader = PluginLoader()
    config = load_config()
    plugin_config = config.plugins if hasattr(config, "plugins") else {}
    state_store = PluginStateStore(get_aeloon_home() / "plugin_state.json")
    storage_base = config.workspace_path / ".aeloon" / "plugin_storage"
    dummy_loop = SimpleNamespace(provider=None, model="", workspace=config.workspace_path)

    logger.disable("aeloon")
    try:
        candidates = discovery.discover_all()
        valid = []
        for candidate in candidates:
            errors = loader.validate_candidate(candidate)
            if errors:
                logger.debug(
                    "Skipping plugin '{}' during CLI bootstrap: {}",
                    candidate.manifest.id,
                    "; ".join(errors),
                )
                continue
            if plugin_config.get(candidate.manifest.id, {}).get("enabled", True) is False:
                continue
            state = state_store.get(candidate.manifest.id)
            if state and not state.enabled:
                continue
            valid.append(candidate)

        try:
            ordered = loader.resolve_load_order(valid)
        except CircularDependencyError as exc:
            logger.debug("Skipping circular plugin set during CLI bootstrap: {}", exc)
            ordered = [c for c in valid if c.manifest.id not in exc.cycle_members]

        for candidate in ordered:
            plugin_id = candidate.manifest.id
            try:
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    cls = loader.load_plugin_class(candidate.manifest)
                    instance = loader.instantiate(cls)
                    runtime = PluginRuntime(
                        agent_loop=dummy_loop,
                        plugin_id=plugin_id,
                        config=plugin_config.get(plugin_id, {}),
                        storage_base=storage_base,
                    )
                    api = PluginAPI(
                        plugin_id=plugin_id,
                        version=candidate.manifest.version,
                        config=plugin_config.get(plugin_id, {}),
                        runtime=runtime,
                        registry=registry,
                    )
                    registry.add_plugin(
                        PluginRecord(
                            plugin_id=plugin_id,
                            manifest=candidate.manifest,
                            instance=instance,
                            api=api,
                        )
                    )
                    instance.register(api)
                    api._commit()
            except Exception as exc:
                registry.rollback_plugin(plugin_id)
                logger.debug("Skipping plugin '{}' during CLI bootstrap: {}", plugin_id, exc)
    finally:
        logger.enable("aeloon")

    return registry


def mount_plugin_cli_builders(parent_app: typer.Typer, registry: Any) -> None:
    """Attach plugin CLI builders idempotently."""
    for record in registry.cli_registrars.values():
        if record.name in _MOUNTED_PLUGIN_CLI:
            continue
        record.builder(parent_app)
        _MOUNTED_PLUGIN_CLI.add(record.name)


def register_plugin_command_specs(command_catalog: CommandCatalog, registry: Any) -> None:
    """Expose plugin slash commands through the shared command catalog."""
    for spec in build_plugin_command_specs(registry):
        if spec.name in _REGISTERED_PLUGIN_COMMANDS:
            continue
        command_catalog.register(spec)
        _REGISTERED_PLUGIN_COMMANDS.add(spec.name)


def register_plugin_cli(registry: Any) -> None:
    """Attach plugin CLI metadata and builders to the shared CLI."""
    register_plugin_command_specs(command_catalog, registry)
    mount_plugin_cli_builders(ext_app, registry)


@plugins_app.command("list")
def plugins_list() -> None:
    """List all discovered channels (built-in and plugins)."""
    from aeloon.channels.registry import discover_all, discover_channel_names
    from aeloon.core.config.loader import load_config

    config = load_config()
    builtin_names = set(discover_channel_names())
    all_channels = discover_all()

    table = Table(title="Channel Plugins")
    table.add_column("Name", style="cyan")
    table.add_column("Source", style="magenta")
    table.add_column("Enabled", style="green")

    for name in sorted(all_channels):
        cls = all_channels[name]
        source = "builtin" if name in builtin_names else "plugin"
        section = getattr(config.channels, name, None)
        if section is None:
            enabled = False
        elif isinstance(section, dict):
            enabled = section.get("enabled", False)
        else:
            enabled = getattr(section, "enabled", False)
        table.add_row(
            cls.display_name,
            source,
            "[green]yes[/green]" if enabled else "[dim]no[/dim]",
        )

    console.print(table)


@plugin_cli_app.command("install")
def plugin_install(
    path: str = typer.Argument(..., help="Path to plugin archive (.zip or .tar.gz)"),
) -> None:
    """Install a plugin from an archive."""
    from aeloon.core.config.loader import get_aeloon_home
    from aeloon.plugins._sdk.admin import install_plugin_archive
    from aeloon.plugins._sdk.state_store import PluginStateStore

    archive = Path(path).expanduser().resolve()
    aeloon_home = get_aeloon_home()
    result = install_plugin_archive(
        archive=archive,
        workspace_dir=aeloon_home / "plugins",
        state_store=PluginStateStore(aeloon_home / "plugin_state.json"),
    )
    if result.ok:
        console.print(f"[green]✓[/green] {result.message}")
    else:
        console.print(f"[red]✗[/red] {result.message}")
        raise typer.Exit(1)


@plugin_cli_app.command("list")
def plugin_list() -> None:
    """List installed plugins with status."""
    from aeloon.core.config.loader import get_aeloon_home
    from aeloon.plugins._sdk.admin import collect_installed_plugin_entries
    from aeloon.plugins._sdk.state_store import PluginStateStore

    aeloon_home = get_aeloon_home()
    entries = collect_installed_plugin_entries(
        bundled_dir=Path(__file__).resolve().parent.parent / "plugins",
        workspace_dir=aeloon_home / "plugins",
        state_store=PluginStateStore(aeloon_home / "plugin_state.json"),
    )

    if not entries:
        console.print("[dim]No plugins installed.[/dim]")
        return

    table = Table(title="Installed Plugins")
    table.add_column("ID", style="cyan")
    table.add_column("Version")
    table.add_column("Status")
    table.add_column("Source", style="dim")

    status_icons = {"ok": "[green]ok[/green]", "deactivated": "[yellow]deactivated[/yellow]"}
    for entry in sorted(entries, key=lambda item: item.id):
        icon = status_icons.get(entry.status, f"[red]{entry.status}[/red]")
        table.add_row(entry.id, entry.version, icon, entry.source)

    console.print(table)


@plugin_cli_app.command("error")
def plugin_error(
    name: str = typer.Argument(None, help="Plugin ID to inspect"),
) -> None:
    """Show error details for broken plugins."""
    from aeloon.core.config.loader import get_aeloon_home
    from aeloon.plugins._sdk.admin import format_state_store_report
    from aeloon.plugins._sdk.state_store import PluginStateStore

    report = format_state_store_report(
        PluginStateStore(get_aeloon_home() / "plugin_state.json"),
        name,
    )
    if name and "not found in state store" in report:
        console.print(f"[red]{report}[/red]")
        raise typer.Exit(1)
    if report == "No plugins tracked.":
        console.print("[dim]No plugins tracked.[/dim]")
        return
    console.print(report)


@plugin_cli_app.command("remove")
def plugin_remove(
    name: str = typer.Argument(..., help="Plugin ID to remove"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
) -> None:
    """Remove a workspace-installed plugin."""
    from aeloon.core.config.loader import get_aeloon_home
    from aeloon.plugins._sdk.admin import remove_workspace_plugin
    from aeloon.plugins._sdk.state_store import PluginStateStore

    if not force and not typer.confirm(f"Remove plugin '{name}'?"):
        console.print("[dim]Cancelled.[/dim]")
        raise typer.Exit(0)

    aeloon_home = get_aeloon_home()
    result = remove_workspace_plugin(
        name=name,
        workspace_dir=aeloon_home / "plugins",
        state_store=PluginStateStore(aeloon_home / "plugin_state.json"),
    )
    if result.ok:
        console.print(f"[green]✓[/green] {result.message}")
    else:
        console.print(f"[red]✗[/red] {result.message}")
        raise typer.Exit(1)


@plugin_cli_app.command("activate")
def plugin_activate(
    name: str = typer.Argument(..., help="Plugin ID to activate"),
) -> None:
    """Activate a plugin."""
    from aeloon.core.config.loader import get_aeloon_home
    from aeloon.plugins._sdk.admin import set_plugin_enabled
    from aeloon.plugins._sdk.state_store import PluginStateStore

    result = set_plugin_enabled(
        name=name,
        enabled=True,
        state_store=PluginStateStore(get_aeloon_home() / "plugin_state.json"),
    )
    if result.ok:
        console.print(f"[green]✓[/green] {result.message}")
    else:
        console.print(f"[red]✗[/red] {result.message}")
        raise typer.Exit(1)


@plugin_cli_app.command("deactivate")
def plugin_deactivate(
    name: str = typer.Argument(..., help="Plugin ID to deactivate"),
) -> None:
    """Deactivate a plugin."""
    from aeloon.core.config.loader import get_aeloon_home
    from aeloon.plugins._sdk.admin import set_plugin_enabled
    from aeloon.plugins._sdk.state_store import PluginStateStore

    result = set_plugin_enabled(
        name=name,
        enabled=False,
        state_store=PluginStateStore(get_aeloon_home() / "plugin_state.json"),
    )
    if result.ok:
        console.print(f"[green]✓[/green] {result.message}")
    else:
        console.print(f"[red]✗[/red] {result.message}")
        raise typer.Exit(1)


def run_plugin_cli_command(
    *,
    plugin_command: str,
    args: str,
    session_id: str | None,
    workspace: str | None,
    config: str | None,
) -> None:
    """Synchronously run one registered plugin command."""
    asyncio.run(
        _run_plugin_cli_command(
            plugin_command=plugin_command,
            args=args,
            session_id=session_id,
            workspace=workspace,
            config=config,
        )
    )


async def _run_plugin_cli_command(
    *,
    plugin_command: str,
    args: str,
    session_id: str | None,
    workspace: str | None,
    config: str | None,
) -> None:
    """Boot the runtime, invoke one plugin command, and print results."""
    from aeloon.cli.flows.helpers import boot_plugins
    from aeloon.cli.runtime_helpers import (
        load_runtime_config,
        make_provider,
        print_deprecated_memory_window_notice,
    )
    from aeloon.core.agent.loop import AgentLoop
    from aeloon.core.bus.queue import MessageBus
    from aeloon.core.config.paths import get_cron_dir
    from aeloon.plugins._sdk.types import CommandContext
    from aeloon.services.cron.service import CronService

    loaded_config = load_runtime_config(config, workspace)
    print_deprecated_memory_window_notice(loaded_config)
    sync_workspace_templates(loaded_config.workspace_path)

    bus = MessageBus()
    provider = make_provider(loaded_config)
    cron = CronService(get_cron_dir() / "jobs.json")

    agent_loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=loaded_config.workspace_path,
        model=loaded_config.agents.defaults.model,
        max_iterations=loaded_config.agents.defaults.max_tool_iterations,
        context_window_tokens=loaded_config.agents.defaults.context_window_tokens,
        web_search_config=loaded_config.tools.web.search,
        web_proxy=loaded_config.tools.web.proxy or None,
        exec_config=loaded_config.tools.exec,
        cron_service=cron,
        restrict_to_workspace=loaded_config.tools.restrict_to_workspace,
        mcp_servers=loaded_config.tools.mcp_servers,
        channels_config=loaded_config.channels,
        output_mode=loaded_config.agents.defaults.output_mode,
        fast=loaded_config.agents.defaults.fast,
    )

    session_key = session_id or f"cli:ext:{plugin_command}"
    printed_replies: list[str] = []

    async def _reply(text: str) -> None:
        printed_replies.append(text)
        console.print(text)

    async def _progress(text: str, *, tool_hint: bool = False) -> None:
        if tool_hint:
            console.print(f"[dim]{text}[/dim]")
            return
        console.print(f"[dim]{text}[/dim]")

    try:
        agent_loop.plugin_manager = await boot_plugins(agent_loop, loaded_config, quiet=True)
        plugin_manager = agent_loop.plugin_manager
        if plugin_manager is None:
            raise RuntimeError("No plugins loaded.")

        record = plugin_manager.registry.commands.get(plugin_command)
        if record is None:
            raise RuntimeError(f"Plugin command not found: {plugin_command}")

        ctx = CommandContext(
            session_key=session_key,
            channel="cli",
            reply=_reply,
            send_progress=_progress,
            plugin_config=plugin_manager._plugin_config.get(record.plugin_id, {}),
        )

        result = await record.handler(ctx, args)
        if result is not None and result not in printed_replies:
            console.print(result)
    finally:
        plugin_manager = getattr(agent_loop, "plugin_manager", None)
        if plugin_manager:
            await plugin_manager.shutdown()
        await agent_loop.close_mcp()
