"""Built-in /outputs slash command for listing workspace artifacts."""

from __future__ import annotations

from aeloon.cli.registry import CommandSpec
from aeloon.core.agent.commands import BuiltinHandlerMap, CommandEnv
from aeloon.core.bus.events import InboundMessage, OutboundMessage

SPECS: tuple[CommandSpec, ...] = (
    CommandSpec(name="outputs", help="List recent output artifacts", slash_path=("outputs",)),
)


async def handle_outputs(env: CommandEnv, msg: InboundMessage, args_str: str) -> OutboundMessage:
    """Show the most recent workspace output artifacts."""
    om = env.output_manager
    if om is None:
        content = "Output manager is not available."
    else:
        sub = args_str.strip().lower()
        if sub in ("help", "--help", "-h"):
            content = _HELP
        else:
            limit = 20
            if sub.isdigit():
                limit = int(sub)

            entries = om.list_recent(limit=limit)
            if not entries:
                content = "No output artifacts recorded yet."
            else:
                lines = ["## Recent Outputs", ""]
                for entry in entries:
                    path = entry.get("path", "?")
                    title = entry.get("title", "")
                    ts = entry.get("ts", "")[:16]
                    cat = entry.get("category", "")
                    lines.append(f"- `{path}` — {title}  [{cat}, {ts}]")
                content = "\n".join(lines)

    return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=content)


_HELP = """\
## /outputs

List recent output artifacts produced by the agent and plugins.

**Usage:**
- `/outputs` — Show the 20 most recent outputs
- `/outputs <N>` — Show the N most recent outputs
- `/outputs help` — Show this help
"""


HANDLERS: BuiltinHandlerMap = {"outputs": handle_outputs}
