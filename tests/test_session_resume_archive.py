from __future__ import annotations

from pathlib import Path

import pytest

from aeloon.core.agent.commands._context import CommandContext
from aeloon.core.agent.commands.session import handle_resume, handle_sessions
from aeloon.core.bus.events import InboundMessage
from aeloon.core.session.manager import Session, SessionManager
from aeloon.memory.archive_db import SessionArchiveDB
from aeloon.memory.archive_service import SessionArchiveService


class _FakeMemory:
    def __init__(self, service: SessionArchiveService) -> None:
        self.session_archive = service
        self.finalize_calls: list[str] = []

    def pending_start_index(self, _session: object) -> int:
        return 0

    async def finalize_session(
        self,
        *,
        session: Session,
        pending_messages: list[dict[str, object]],
        reason: str,
    ) -> None:
        self.finalize_calls.append(reason)


class _FakeLoop:
    def __init__(self, sessions: SessionManager, memory: _FakeMemory) -> None:
        self.sessions = sessions
        self.memory = memory


def _archive_service(tmp_path: Path) -> SessionArchiveService:
    db = SessionArchiveDB(tmp_path / "archive.db")
    return SessionArchiveService(db=db, workspace=tmp_path)


def _make_session(key: str, *messages: tuple[str, str]) -> Session:
    session = Session(key=key)
    for role, content in messages:
        session.add_message(role, content)
    return session


@pytest.mark.asyncio
async def test_sessions_lists_recent_archived_sessions(tmp_path: Path) -> None:
    sessions = SessionManager(tmp_path)
    service = _archive_service(tmp_path)
    memory = _FakeMemory(service)
    loop = _FakeLoop(sessions=sessions, memory=memory)

    current = sessions.get_or_create("cli:test")
    archived = _make_session(
        "cli:history",
        ("user", "Earlier docker discussion."),
        ("assistant", "We used a bridge network."),
    )
    service.ingest_session_sync(archived)

    ctx = CommandContext.from_dispatch(
        agent_loop=loop,
        msg=InboundMessage(channel="cli", sender_id="user", chat_id="test", content="/sessions"),
        session_key="cli:test",
        is_builtin=True,
        send_progress=None,
    )

    result = await handle_sessions(ctx, "")

    assert result is not None
    assert "Recent archived sessions:" in result
    assert archived.archive_session_id in result
    assert current.archive_session_id not in result


@pytest.mark.asyncio
async def test_resume_restores_archived_session_into_current_route_key(tmp_path: Path) -> None:
    sessions = SessionManager(tmp_path)
    service = _archive_service(tmp_path)
    memory = _FakeMemory(service)
    loop = _FakeLoop(sessions=sessions, memory=memory)

    current = sessions.get_or_create("cli:test")
    current.add_message("user", "current conversation")
    current.add_message("assistant", "current answer")

    archived = _make_session(
        "cli:history",
        ("user", "Earlier docker discussion."),
        ("assistant", "We used a bridge network."),
    )
    service.ingest_session_sync(archived)

    ctx = CommandContext.from_dispatch(
        agent_loop=loop,
        msg=InboundMessage(
            channel="cli",
            sender_id="user",
            chat_id="test",
            content=f"/resume {archived.archive_session_id}",
        ),
        session_key="cli:test",
        is_builtin=True,
        send_progress=None,
    )

    result = await handle_resume(ctx, archived.archive_session_id)
    restored = sessions.get_or_create("cli:test")

    assert result == f"Resumed archived session: {archived.archive_session_id}"
    assert memory.finalize_calls == ["resume-session"]
    assert restored.archive_session_id == archived.archive_session_id
    assert restored.lineage_id == archived.lineage_id
    assert restored.messages[0]["content"] == "Earlier docker discussion."
    assert restored.messages[1]["content"] == "We used a bridge network."
    assert getattr(ctx, "_metadata")["_session_switch"] is True
    assert getattr(ctx, "_metadata")["session_key"] == "cli:test"
