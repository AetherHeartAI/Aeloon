"""Built-in channel management slash commands."""

from __future__ import annotations

from aeloon.core.agent.commands import slash_command
from aeloon.plugins._sdk.types import CommandContext


def _channel_enabled(ctx: CommandContext, name: str) -> bool:
    """Return whether one channel is enabled in runtime config."""
    config = ctx.channels_config
    if config is None:
        return False
    section = getattr(config, name, None)
    if section is None:
        return False
    if isinstance(section, dict):
        return section.get("enabled", False)
    return getattr(section, "enabled", False)


@slash_command(
    name="wechat",
    help="WeChat login management",
    slash_path=("wechat",),
    slash_paths=(("wechat", "login"), ("wechat", "logout"), ("wechat", "status")),
)
async def handle_wechat(ctx: CommandContext, args_str: str) -> str | None:
    """Handle `/wechat` slash command."""
    result = await ctx.channel_auth.handle_wechat_command(
        ctx._inbound_message,
        args_str.split() if args_str else [],
        ctx.as_bus_namespace(),
    )
    if result.metadata:
        for k, v in result.metadata.items():
            ctx.set_metadata(k, v)
    if result.media:
        ctx.add_media(result.media)
    return result.content


@slash_command(
    name="feishu",
    help="Feishu login management",
    slash_path=("feishu",),
    slash_paths=(("feishu", "login"), ("feishu", "logout"), ("feishu", "status")),
)
async def handle_feishu(ctx: CommandContext, args_str: str) -> str | None:
    """Handle `/feishu` slash command."""
    result = await ctx.channel_auth.handle_feishu_command(
        ctx._inbound_message,
        args_str.split() if args_str else [],
    )
    if result.metadata:
        for k, v in result.metadata.items():
            ctx.set_metadata(k, v)
    if result.media:
        ctx.add_media(result.media)
    return result.content


@slash_command(
    name="channel",
    help="Manage one channel.",
    slash_path=("channel",),
    slash_paths=(
        ("channel", "list"),
        ("channel", "status"),
        ("channel", "status", "<name>"),
        ("channel", "wechat"),
        ("channel", "wechat", "login"),
        ("channel", "wechat", "logout"),
        ("channel", "wechat", "status"),
        ("channel", "feishu"),
        ("channel", "feishu", "login"),
        ("channel", "feishu", "logout"),
        ("channel", "feishu", "status"),
        ("channel", "whatsapp"),
        ("channel", "whatsapp", "login"),
    ),
)
async def handle_channel(ctx: CommandContext, args_str: str) -> str | None:
    """Handle `/channel` slash command."""
    from aeloon.channels.registry import discover_all

    args = args_str.split() if args_str else []
    if not args:
        return "Usage: /channel list | /channel status [name] | /channel <wechat|feishu|whatsapp> <action>"

    subcommand = args[0].lower()
    if subcommand == "list":
        lines = ["# Channels", ""]
        for name, cls in sorted(discover_all().items()):
            enabled = _channel_enabled(ctx, name)
            lines.append(
                f"- `{name}` ({cls.display_name}) — {'enabled' if enabled else 'disabled'}"
            )
        return "\n".join(lines)

    if subcommand == "status":
        target = args[1].lower() if len(args) > 1 else None
        lines = ["# Channel Status", ""]
        for name, cls in sorted(discover_all().items()):
            if target and name != target:
                continue
            enabled = _channel_enabled(ctx, name)
            lines.append(
                f"- `{name}` ({cls.display_name}) — {'enabled' if enabled else 'disabled'}"
            )
        if len(lines) == 2:
            lines.append(f"Unknown channel: {target}")
        return "\n".join(lines)

    channel_name = subcommand
    remainder = " ".join(args[1:])
    if channel_name == "wechat":
        return await handle_wechat(ctx, remainder)
    if channel_name == "feishu":
        return await handle_feishu(ctx, remainder)
    if channel_name == "whatsapp":
        return "Use `aeloon channel whatsapp login` for WhatsApp login."

    return f"Unknown channel: {channel_name}. Use /channel list to see available channels."
