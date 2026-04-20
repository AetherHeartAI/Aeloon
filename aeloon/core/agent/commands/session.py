"""Built-in session management slash commands."""

from __future__ import annotations

from datetime import datetime

from aeloon.core.agent.commands._context import CommandContext
from aeloon.core.agent.commands._decorator import slash_command
from aeloon.memory.archive_service import ArchivedSessionSnapshot, SessionArchiveService


def _format_archive_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")


def _archive_service(ctx: CommandContext) -> SessionArchiveService | None:
    memory = ctx.memory
    if memory is None:
        return None
    service = getattr(memory, "session_archive", None)
    if isinstance(service, SessionArchiveService):
        return service
    return None


def _resolve_snapshot(
    ctx: CommandContext,
    *,
    identifier: str,
    service: SessionArchiveService,
) -> ArchivedSessionSnapshot | None:
    snapshot = service.load_session_snapshot(identifier)
    if snapshot is not None:
        return snapshot
    for session in service.list_recent_sessions(limit=50):
        title = session.title or ""
        if title == identifier:
            return service.load_session_snapshot(session.session_id)
    return None


def _restore_conversation_messages(snapshot: ArchivedSessionSnapshot) -> list[dict[str, object]]:
    restored: list[dict[str, object]] = []
    for message in snapshot.conversation:
        entry: dict[str, object] = {
            "role": message.get("role"),
            "content": message.get("content", ""),
            "timestamp": datetime.now().isoformat(),
        }
        if "tool_calls" in message:
            entry["tool_calls"] = message["tool_calls"]
        if "tool_call_id" in message:
            entry["tool_call_id"] = message["tool_call_id"]
        tool_name = message.get("tool_name")
        if isinstance(tool_name, str) and tool_name:
            entry["name"] = tool_name
        restored.append(entry)
    return restored


async def _resume_archived_session(
    ctx: CommandContext,
    *,
    identifier: str,
    service: SessionArchiveService,
) -> str:
    snapshot = _resolve_snapshot(ctx, identifier=identifier, service=service)
    if snapshot is None:
        return f"Session not found: {identifier}"

    current = ctx.sessions.get_or_create(ctx.session_key)
    memory = ctx.memory
    if memory is None:
        raise RuntimeError("Memory manager is not available.")
    if current.archive_session_id != snapshot.session_id and current.messages:
        start_index = memory.pending_start_index(current)
        pending_messages = list(current.messages[start_index:])
        await memory.finalize_session(
            session=current,
            pending_messages=pending_messages,
            reason="resume-session",
        )

    current.archive_session_id = snapshot.session_id
    current.lineage_id = snapshot.lineage_id
    current.parent_archive_session_id = snapshot.parent_session_id
    current.created_at = datetime.fromtimestamp(snapshot.started_at)
    current.updated_at = datetime.fromtimestamp(snapshot.updated_at)
    current.ended_at = None
    current.end_reason = None
    current.metadata = dict(snapshot.metadata)
    current.memory_state = {}
    current.messages = _restore_conversation_messages(snapshot)
    ctx.sessions.save(current)
    ctx.sessions.invalidate(current.key)
    ctx.set_metadata("_session_switch", True)
    ctx.set_metadata("session_key", ctx.session_key)
    title_suffix = f" ({snapshot.title})" if snapshot.title else ""
    return f"Resumed archived session: {snapshot.session_id}{title_suffix}"


@slash_command(name="new", help="Start a new conversation", slash_path=("new",))
async def handle_new(ctx: CommandContext, _args_str: str) -> str | None:
    """Roll over the current session and finalize it for archive recall."""
    session = ctx.sessions.get_or_create(ctx.session_key)
    memory = ctx.memory
    if memory is None:
        raise RuntimeError("Memory manager is not available.")
    start_index = memory.pending_start_index(session)
    snapshot = list(session.messages[start_index:])

    previous, replacement = ctx.sessions.rollover(ctx.session_key, reason="new-session")

    if previous.messages:
        if ctx.send_progress is not None and snapshot:
            await ctx.send_progress("Finalizing previous session for archive recall...")
        await memory.finalize_session(
            session=previous,
            pending_messages=snapshot,
            reason="new-session",
        )
    if snapshot:
        await memory.on_new_session(
            session=previous,
            pending_messages=snapshot,
        )

    ctx.sessions.save(replacement)
    ctx.sessions.invalidate(replacement.key)

    return "New session started."


@slash_command(
    name="compact",
    help="Compact the current session context",
    slash_path=("compact",),
)
async def handle_compact(ctx: CommandContext, args_str: str) -> str | None:
    """Shrink current context without ending the active session."""
    if args_str.strip():
        return "Usage: /compact"

    session = ctx.sessions.get_or_create(ctx.session_key)
    memory = ctx.memory
    if memory is None:
        raise RuntimeError("Memory manager is not available.")

    start_index = memory.pending_start_index(session)
    snapshot = list(session.messages[start_index:])
    if not snapshot:
        return "Session is already compacted."

    await memory.flush(
        session=session,
        pending_messages=snapshot,
        reason="compact",
    )
    session.last_compacted = len(session.messages)
    ctx.sessions.save(session)

    return f"Compacted {len(snapshot)} messages from the current session."


@slash_command(
    name="sessions",
    help="List or switch saved sessions",
    slash_path=("sessions",),
    slash_paths=(("sessions", "switch"), ("sessions", "switch", "<session-key>")),
)
async def handle_sessions(ctx: CommandContext, args_str: str) -> str | None:
    """List recent sessions or request a session switch."""
    args = args_str.split() if args_str else []
    service = _archive_service(ctx)
    current_session = ctx.sessions.get_or_create(ctx.session_key)
    if not args:
        if service is not None:
            sessions = service.list_recent_sessions(
                limit=10,
                current_session_id=current_session.archive_session_id,
                current_lineage_id=current_session.lineage_id,
            )
        else:
            sessions = []
        if not sessions:
            active_sessions = ctx.sessions.list_sessions()
            if not active_sessions:
                return "No saved sessions found."
            lines = ["Recent sessions:"]
            for item in active_sessions[:10]:
                key = str(item.get("key") or "")
                updated_at = str(item.get("updated_at") or "unknown")
                suffix = " (current)" if key == ctx.session_key else ""
                lines.append(f"- {key}{suffix} — {updated_at}")
            lines.extend(
                [
                    "",
                    "Usage:",
                    "- `/resume`",
                    "- `/resume switch <session-id-or-title>`",
                    "- `/sessions`",
                    "- `/sessions switch <session-id-or-title>`",
                ]
            )
            return "\n".join(lines)
        lines = ["Recent archived sessions:"]
        for item in sessions:
            preview = item.preview[:80] + ("..." if len(item.preview) > 80 else "")
            title_suffix = f" — {item.title}" if item.title else ""
            lines.append(
                f"- {item.session_id}{title_suffix} — {_format_archive_timestamp(item.updated_at)}"
            )
            if preview:
                lines.append(f"  {preview}")
        lines.extend(
            [
                "",
                "Usage:",
                "- `/resume`",
                "- `/resume switch <session-id-or-title>`",
                "- `/sessions`",
                "- `/sessions switch <session-id-or-title>`",
            ]
        )
        return "\n".join(lines)

    if len(args) == 2 and args[0].lower() == "switch":
        if service is not None:
            return await _resume_archived_session(ctx, identifier=args[1], service=service)
        active_sessions = ctx.sessions.list_sessions()
        target_key = args[1]
        if not any(item.get("key") == target_key for item in active_sessions):
            return f"Session not found: {target_key}"
        ctx.set_metadata("_session_switch", True)
        ctx.set_metadata("session_key", target_key)
        return f"Switching to session: {target_key}"

    if len(args) == 1 and service is not None:
        return await _resume_archived_session(ctx, identifier=args[0], service=service)

    return "Usage: /resume | /resume switch <session-id-or-title> | /sessions | /sessions switch <session-id-or-title>"


@slash_command(
    name="resume",
    help="Resume a saved session",
    slash_path=("resume",),
    slash_paths=(("resume", "switch"), ("resume", "switch", "<session-key>")),
)
async def handle_resume(ctx: CommandContext, args_str: str) -> str | None:
    """Resume a saved session (delegates to handle_sessions)."""
    return await handle_sessions(ctx, args_str)
