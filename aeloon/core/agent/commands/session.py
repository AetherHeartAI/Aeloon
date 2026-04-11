"""Built-in session management slash commands."""

from __future__ import annotations

from aeloon.core.agent.commands import slash_command
from aeloon.plugins._sdk.types import CommandContext


@slash_command(name="new", help="Start a new conversation", slash_path=("new",))
async def handle_new(ctx: CommandContext, _args: str) -> str | None:
    """Clear the current session and archive unconsolidated history."""
    session = ctx.sessions.get_or_create(ctx.session_key)
    snapshot = session.messages[session.last_consolidated :]
    session.clear()
    ctx.sessions.save(session)
    ctx.sessions.invalidate(session.key)

    if snapshot:
        ctx.schedule_background(ctx.memory_consolidator.archive_messages(snapshot))

    return "New session started."


@slash_command(
    name="resume",
    help="Resume a saved session",
    slash_path=("resume",),
    slash_paths=(("resume", "switch"), ("resume", "switch", "<session-key>")),
)
@slash_command(
    name="sessions",
    help="List or switch saved sessions",
    slash_path=("sessions",),
    slash_paths=(("sessions", "switch"), ("sessions", "switch", "<session-key>")),
)
async def handle_sessions(ctx: CommandContext, args_str: str) -> str | None:
    """List recent sessions or request a session switch."""
    args = args_str.split() if args_str else []
    sessions = ctx.sessions.list_sessions()
    if not args:
        if not sessions:
            return "No saved sessions found."
        lines = ["Recent sessions:"]
        for item in sessions[:10]:
            key = str(item.get("key") or "")
            updated_at = str(item.get("updated_at") or "unknown")
            suffix = " (current)" if key == ctx.session_key else ""
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
        return "\n".join(lines)

    if len(args) == 2 and args[0].lower() == "switch":
        target_key = args[1]
        if not any(item.get("key") == target_key for item in sessions):
            return f"Session not found: {target_key}"
        ctx.set_metadata("_session_switch", True)
        ctx.set_metadata("session_key", target_key)
        return f"Switching to session: {target_key}"

    return (
        "Usage: /resume | /resume switch <session-key> | /sessions | /sessions switch <session-key>"
    )
