"""Built-in session management slash commands."""

from __future__ import annotations

from aeloon.cli.registry import CommandSpec
from aeloon.core.agent.commands import BuiltinHandlerMap, CommandEnv
from aeloon.core.bus.events import InboundMessage, OutboundMessage

SPECS: tuple[CommandSpec, ...] = (
    CommandSpec(name="new", help="Start a new conversation", slash_path=("new",)),
    CommandSpec(
        name="resume",
        help="Resume a saved session",
        slash_path=("resume",),
        slash_paths=(("resume", "switch"), ("resume", "switch", "<session-key>")),
    ),
    CommandSpec(
        name="sessions",
        help="List or switch saved sessions",
        slash_path=("sessions",),
        slash_paths=(("sessions", "switch"), ("sessions", "switch", "<session-key>")),
    ),
)


async def handle_new(env: CommandEnv, msg: InboundMessage, _args_str: str) -> OutboundMessage:
    """Clear the current session and archive unconsolidated history."""
    session = env.sessions.get_or_create(msg.session_key)
    start_index = env.memory.pending_start_index(session)
    snapshot = list(session.messages[start_index:])
    if snapshot and hasattr(memory, "flush"):
        await memory.flush(
            session=session,
            pending_messages=snapshot,
            reason="new-session",
        )
    session.clear()
    env.sessions.save(session)
    env.sessions.invalidate(session.key)

    if snapshot:
        if env.bus is not None:
            await env.bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content="Archiving previous session history in the background...",
                    metadata={
                        **(msg.metadata or {}),
                        "_progress": True,
                    },
                )
            )
        await env.memory.on_new_session(
            session=session,
            pending_messages=snapshot,
        )

    return OutboundMessage(
        channel=msg.channel,
        chat_id=msg.chat_id,
        content="New session started.",
    )


async def handle_sessions(env: CommandEnv, msg: InboundMessage, args_str: str) -> OutboundMessage:
    """List recent sessions or request a session switch."""
    args = args_str.split() if args_str else []
    sessions = env.sessions.list_sessions()
    if not args:
        if not sessions:
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="No saved sessions found.",
            )
        lines = ["Recent sessions:"]
        for item in sessions[:10]:
            key = str(item.get("key") or "")
            updated_at = str(item.get("updated_at") or "unknown")
            suffix = " (current)" if key == msg.session_key else ""
            lines.append(f"- {key}{suffix} — {updated_at}")
        lines.extend(
            [
                "",
                "Usage:",
                "- `/resume`",
                "- `/resume switch <session-key>`",
                "- `/sessions`",
                "- `/sessions switch <session-key>`",
            ]
        )
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content="\n".join(lines),
        )

    if len(args) == 2 and args[0].lower() == "switch":
        target_key = args[1]
        if not any(item.get("key") == target_key for item in sessions):
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=f"Session not found: {target_key}",
            )
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=f"Switching to session: {target_key}",
            metadata={
                **(msg.metadata or {}),
                "_session_switch": True,
                "session_key": target_key,
            },
        )

    return OutboundMessage(
        channel=msg.channel,
        chat_id=msg.chat_id,
        content=(
            "Usage: /resume | /resume switch <session-key> | "
            "/sessions | /sessions switch <session-key>"
        ),
    )


HANDLERS: BuiltinHandlerMap = {
    "new": handle_new,
    "resume": handle_sessions,
    "sessions": handle_sessions,
}
