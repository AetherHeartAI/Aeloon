from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from aeloon.core.agent.commands._context import CommandContext
from aeloon.core.agent.commands.session import handle_new
from aeloon.core.bus.events import InboundMessage
from aeloon.core.session.manager import SessionManager


class _FakeMemoryManager:
    def __init__(self, start_index: int) -> None:
        self.start_index = start_index
        self.calls: list[list[dict[str, object]]] = []
        self.run_calls: list[list[dict[str, object]]] = []
        self.flush_calls: list[list[dict[str, object]]] = []
        self.finalize_calls: list[tuple[object, list[dict[str, object]], str | None]] = []

    def pending_start_index(self, _session: object) -> int:
        return self.start_index

    async def on_new_session(
        self,
        *,
        session: object,
        pending_messages: list[dict[str, object]],
    ) -> None:
        self.calls.append([dict(message) for message in pending_messages])

    async def run_new_session(
        self,
        *,
        session: object,
        pending_messages: list[dict[str, object]],
    ) -> None:
        self.run_calls.append([dict(message) for message in pending_messages])
        await asyncio.Future()

    async def flush(
        self,
        *,
        session: object,
        pending_messages: list[dict[str, object]],
        reason: str | None = None,
    ) -> None:
        self.flush_calls.append([dict(message) for message in pending_messages])

    async def finalize_session(
        self,
        *,
        session: object,
        pending_messages: list[dict[str, object]],
        reason: str,
    ) -> None:
        self.finalize_calls.append(
            (session, [dict(message) for message in pending_messages], reason)
        )


class _FakeLoop:
    def __init__(self, sessions: SessionManager, memory: _FakeMemoryManager) -> None:
        self.sessions = sessions
        self.memory = memory


@pytest.mark.asyncio
async def test_handle_new_archives_only_pending_messages(tmp_path: Path) -> None:
    sessions = SessionManager(tmp_path)
    memory = _FakeMemoryManager(start_index=2)
    loop = _FakeLoop(sessions=sessions, memory=memory)
    progress = AsyncMock()

    session = sessions.get_or_create("cli:test")
    session.messages = [
        {"role": "user", "content": "old-1"},
        {"role": "assistant", "content": "old-2"},
        {"role": "user", "content": "new-1"},
        {"role": "assistant", "content": "new-2"},
    ]
    session.last_consolidated = 1
    sessions.save(session)

    ctx = CommandContext.from_dispatch(
        agent_loop=loop,
        msg=InboundMessage(channel="cli", sender_id="user", chat_id="test", content="/new"),
        session_key="cli:test",
        is_builtin=True,
        send_progress=progress,
    )
    result = await asyncio.wait_for(handle_new(ctx, ""), timeout=0.1)
    replacement = sessions.get_or_create("cli:test")

    assert result == "New session started."
    progress.assert_awaited_once_with("Finalizing previous session for archive recall...")
    assert memory.calls == [
        [
            {"role": "user", "content": "new-1"},
            {"role": "assistant", "content": "new-2"},
        ]
    ]
    assert len(memory.finalize_calls) == 1
    finalized_session, finalized_messages, finalized_reason = memory.finalize_calls[0]
    assert finalized_reason == "new-session"
    assert finalized_messages == [
        {"role": "user", "content": "new-1"},
        {"role": "assistant", "content": "new-2"},
    ]
    assert memory.flush_calls == []
    assert memory.run_calls == []
    assert replacement.messages == []
    assert replacement.memory_state == {}
    assert hasattr(finalized_session, "archive_session_id")
    assert replacement.archive_session_id != finalized_session.archive_session_id


@pytest.mark.asyncio
async def test_handle_new_skips_archival_when_no_pending_messages(tmp_path: Path) -> None:
    sessions = SessionManager(tmp_path)
    memory = _FakeMemoryManager(start_index=0)
    loop = _FakeLoop(sessions=sessions, memory=memory)
    progress = AsyncMock()

    ctx = CommandContext.from_dispatch(
        agent_loop=loop,
        msg=InboundMessage(channel="cli", sender_id="user", chat_id="test", content="/new"),
        session_key="cli:test",
        is_builtin=True,
        send_progress=progress,
    )
    result = await asyncio.wait_for(handle_new(ctx, ""), timeout=0.1)

    assert result == "New session started."
    assert memory.calls == []
    assert memory.run_calls == []
    assert memory.flush_calls == []
    assert memory.finalize_calls == []
    progress.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_new_returns_without_waiting_for_blocking_run_new_session(
    tmp_path: Path,
) -> None:
    sessions = SessionManager(tmp_path)
    memory = _FakeMemoryManager(start_index=0)
    loop = _FakeLoop(sessions=sessions, memory=memory)
    progress = AsyncMock()

    session = sessions.get_or_create("cli:test")
    session.messages = [{"role": "user", "content": "pending"}]
    sessions.save(session)

    ctx = CommandContext.from_dispatch(
        agent_loop=loop,
        msg=InboundMessage(channel="cli", sender_id="user", chat_id="test", content="/new"),
        session_key="cli:test",
        is_builtin=True,
        send_progress=progress,
    )

    result = await asyncio.wait_for(handle_new(ctx, ""), timeout=0.1)

    assert result == "New session started."
    progress.assert_awaited_once_with("Finalizing previous session for archive recall...")
    assert memory.calls == [[{"role": "user", "content": "pending"}]]
    assert len(memory.finalize_calls) == 1
    assert memory.flush_calls == []
    assert memory.run_calls == []


@pytest.mark.asyncio
async def test_handle_new_rolls_to_fresh_archive_session(tmp_path: Path) -> None:
    sessions = SessionManager(tmp_path)
    memory = _FakeMemoryManager(start_index=0)
    loop = _FakeLoop(sessions=sessions, memory=memory)

    session = sessions.get_or_create("cli:test")
    session.add_message("user", "old turn")
    old_archive_id = session.archive_session_id

    ctx = CommandContext.from_dispatch(
        agent_loop=loop,
        msg=InboundMessage(channel="cli", sender_id="user", chat_id="test", content="/new"),
        session_key="cli:test",
        is_builtin=True,
        send_progress=None,
    )

    result = await asyncio.wait_for(handle_new(ctx, ""), timeout=0.1)
    replacement = sessions.get_or_create("cli:test")

    assert result == "New session started."
    assert replacement.archive_session_id != old_archive_id
    assert replacement.messages == []
