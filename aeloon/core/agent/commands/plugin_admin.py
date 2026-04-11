"""Built-in plugin administration slash commands."""

from __future__ import annotations

from pathlib import Path

from aeloon.core.agent.commands import slash_command
from aeloon.plugins._sdk.types import CommandContext


async def _plugin_list(ctx: CommandContext) -> str:
    """List all plugins with status."""
    from aeloon.plugins._sdk.admin import format_runtime_plugin_list

    pm = ctx.plugin_manager
    return (
        format_runtime_plugin_list(pm)
        if pm
        else "Plugins:\n──────────────────────────────────────────────────\n  (plugin manager not available)\n"
    )


async def _plugin_install(ctx: CommandContext, archive_path: str) -> str:
    """Install a plugin from an archive."""
    from aeloon.core.config.loader import get_aeloon_home
    from aeloon.plugins._sdk.admin import install_plugin_archive

    path = Path(archive_path).expanduser().resolve()
    workspace_dir = get_aeloon_home() / "plugins"
    pm = ctx.plugin_manager
    state_store = pm._state_store if pm and pm._state_store else None
    if state_store is None:
        return "Plugin manager not available."

    result = install_plugin_archive(
        archive=path,
        workspace_dir=workspace_dir,
        state_store=state_store,
    )
    return result.message


async def _plugin_error(ctx: CommandContext, name: str) -> str:
    """Show error details for broken plugins."""
    from aeloon.plugins._sdk.admin import format_plugin_errors

    pm = ctx.plugin_manager
    if not pm:
        return "Plugin manager not available."
    return format_plugin_errors(pm, name)


async def _plugin_remove(ctx: CommandContext, name: str) -> str:
    """Remove a workspace-installed plugin."""
    from aeloon.core.config.loader import get_aeloon_home
    from aeloon.plugins._sdk.admin import remove_workspace_plugin

    workspace_dir = get_aeloon_home() / "plugins"
    pm = ctx.plugin_manager
    state_store = pm._state_store if pm and pm._state_store else None
    if state_store is None:
        return "Plugin manager not available."
    result = remove_workspace_plugin(
        name=name,
        workspace_dir=workspace_dir,
        state_store=state_store,
    )
    return result.message


async def _plugin_activate(ctx: CommandContext, name: str) -> str:
    """Activate a plugin."""
    from aeloon.plugins._sdk.admin import set_plugin_enabled

    pm = ctx.plugin_manager
    result = set_plugin_enabled(
        name=name,
        enabled=True,
        state_store=pm._state_store if pm else None,
    )
    return result.message


async def _plugin_deactivate(ctx: CommandContext, name: str) -> str:
    """Deactivate a plugin."""
    from aeloon.plugins._sdk.admin import set_plugin_enabled

    pm = ctx.plugin_manager
    result = set_plugin_enabled(
        name=name,
        enabled=False,
        state_store=pm._state_store if pm else None,
    )
    return result.message


@slash_command(
    name="plugin",
    help="Manage plugins.",
    slash_path=("plugin",),
    slash_paths=(
        ("plugin", "list"),
        ("plugin", "error"),
        ("plugin", "error", "<name>"),
        ("plugin", "install"),
        ("plugin", "install", "<archive-path>"),
        ("plugin", "remove"),
        ("plugin", "remove", "<name>"),
        ("plugin", "activate"),
        ("plugin", "activate", "<name>"),
        ("plugin", "deactivate"),
        ("plugin", "deactivate", "<name>"),
    ),
)
async def handle_plugin(ctx: CommandContext, args_str: str) -> str | None:
    """Handle `/plugin` slash command."""
    args = args_str.split() if args_str else []
    sub = args[0] if args else "list"
    rest = args[1:] if len(args) > 1 else []

    if sub == "list":
        return await _plugin_list(ctx)
    if sub == "install" and rest:
        return await _plugin_install(ctx, " ".join(rest))
    if sub == "error":
        name = rest[0] if rest else ""
        return await _plugin_error(ctx, name)
    if sub == "remove" and rest:
        return await _plugin_remove(ctx, rest[0])
    if sub == "activate" and rest:
        return await _plugin_activate(ctx, rest[0])
    if sub == "deactivate" and rest:
        return await _plugin_deactivate(ctx, rest[0])

    return (
        "Usage:\n"
        "- `/plugin list` — List installed plugins\n"
        "- `/plugin install <archive-path>` — Install a plugin\n"
        "- `/plugin error [name]` — Show error details\n"
        "- `/plugin remove <name>` — Remove a plugin\n"
        "- `/plugin activate <name>` — Activate a plugin\n"
        "- `/plugin deactivate <name>` — Deactivate a plugin"
    )
