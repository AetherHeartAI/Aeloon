"""Built-in control commands: /restart."""

from __future__ import annotations

from aeloon.core.agent.commands._context import CommandContext
from aeloon.core.agent.commands._decorator import slash_command


@slash_command(name="restart", help="Restart Aeloon", slash_path=("restart",))
async def handle_restart(ctx: CommandContext, _args: str) -> str | None:
    """Restart the Aeloon process in-place."""
    ctx.restart()
    return "Restarting..."
