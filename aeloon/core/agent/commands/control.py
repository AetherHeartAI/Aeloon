"""Built-in control slash commands (/stop, /restart)."""

from __future__ import annotations

from aeloon.core.agent.commands import slash_command
from aeloon.plugins._sdk.types import CommandContext


@slash_command(name="stop", help="Stop the current task", slash_path=("stop",))
async def handle_stop(ctx: CommandContext, _args: str) -> str | None:
    ctx.cancel_session()
    return None


@slash_command(name="restart", help="Restart Aeloon", slash_path=("restart",))
async def handle_restart(ctx: CommandContext, _args: str) -> str | None:
    ctx.restart()
    return "Restarting..."
