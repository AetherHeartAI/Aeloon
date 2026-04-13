"""Built-in informational slash commands."""

from __future__ import annotations

from aeloon.core.agent.commands._context import CommandContext
from aeloon.core.agent.commands._decorator import slash_command


@slash_command(name="help", help="Show available commands", slash_path=("help",))
async def handle_help(ctx: CommandContext, _args_str: str) -> str | None:
    """Render built-in and plugin slash command help."""
    plugin_catalog = ctx.plugin_catalog_fn()
    lines = [
        "# ♥️ aeloon",
        "",
        "## Commands",
        "",
    ]
    lines.extend(ctx.builtin_catalog.render_help_lines())

    plugin_lines = plugin_catalog.render_help_lines()
    if plugin_lines:
        lines.extend(["", "## Plugins", ""])
        lines.extend(plugin_lines)
    return "\n".join(lines)


@slash_command(name="status", help="Show channel status", slash_path=("status",))
async def handle_status(ctx: CommandContext, _args_str: str) -> str | None:
    """Show runtime, channel, and plugin state."""
    state_icons = {
        "pending": "⏳",
        "starting": "🔄",
        "running": "✅",
        "failed": "❌",
        "stopped": "⏹️",
    }

    lines: list[str] = ["Runtime Status:"]
    try:
        session = ctx.sessions.get_or_create(ctx.session_key)
        memory = ctx.memory
        estimated, _source = memory.estimate_session_prompt_tokens(session) if memory else (0, "none")
    except Exception:
        estimated = 0
    context_total = max(0, int(ctx.context_window_tokens))
    ratio = (estimated / context_total * 100) if context_total > 0 else 0.0
    lines.append(f"Model: {ctx.model}")
    lines.append(f"Context: {estimated}/{context_total} ({ratio:.0f}%)")

    lines.append("")
    lines.append("Channel Status:")
    if ctx.channel_manager is None:
        lines.append("Channel status is not available (no channel manager).")
    else:
        status = ctx.channel_manager.get_status()
        if not status:
            lines.append("No channels configured.")
        else:
            for name, info in status.items():
                state = info["state"]
                icon = state_icons.get(state, "❓")
                line = f"{icon} {info['display_name']} ({name}): {state}"
                if "error" in info:
                    line += f" — {info['error']}"
                lines.append(line)

    pm = ctx.plugin_manager
    if pm:
        service_lines: list[str] = []
        for full_id, service in sorted(pm.registry.services.items()):
            service_lines.append(f"- {full_id}: {service.status.value}")

        if service_lines:
            lines.append("")
            lines.append("Plugin Status:")
            lines.extend(service_lines)

    return "\n".join(lines)
