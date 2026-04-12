from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from aeloon.core.agent.commands import CommandEnv
from aeloon.core.agent.commands.session import handle_new
from aeloon.core.bus.events import InboundMessage, OutboundMessage
from aeloon.core.session.manager import SessionManager


class _FakeMemoryManager:
    def __init__(self, start_index: int) -> None:
        self.start_index = start_index
        self.calls: list[list[dict[str, object]]] = []
        self.run_calls: list[list[dict[str, object]]] = []

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


class _FakeLoop:
    def __init__(self, sessions: SessionManager, memory: _FakeMemoryManager) -> None:
        self.sessions = sessions
        self.memory = memory
        self.bus = SimpleNamespace(publish_outbound=AsyncMock())


@pytest.mark.asyncio
async def test_handle_new_archives_only_pending_messages(tmp_path: Path) -> None:
    sessions = SessionManager(tmp_path)
    memory = _FakeMemoryManager(start_index=2)
    loop = _FakeLoop(sessions=sessions, memory=memory)
    env = CommandEnv(agent_loop=loop, channel_auth=MagicMock())

    session = sessions.get_or_create("cli:test")
    session.messages = [
        {"role": "user", "content": "old-1"},
        {"role": "assistant", "content": "old-2"},
        {"role": "user", "content": "new-1"},
        {"role": "assistant", "content": "new-2"},
    ]
    sessions.save(session)

    outbound = await handle_new(
        env,
        InboundMessage(channel="cli", sender_id="user", chat_id="test", content="/new"),
        "",
    )

    assert outbound.content == "New session started."
    assert memory.calls == [
        [
            {"role": "user", "content": "new-1"},
            {"role": "assistant", "content": "new-2"},
        ]
    ]
    assert memory.run_calls == []
    loop.bus.publish_outbound.assert_awaited_once()
    progress = loop.bus.publish_outbound.await_args.args[0]
    assert isinstance(progress, OutboundMessage)
    assert progress.content == "Archiving previous session history in the background..."
    assert progress.metadata is not None
    assert progress.metadata["_progress"] is True
    refreshed = sessions.get_or_create("cli:test")
    assert refreshed.messages == []
    assert refreshed.memory_state == {}


@pytest.mark.asyncio
async def test_handle_new_skips_archival_when_no_pending_messages(tmp_path: Path) -> None:
    sessions = SessionManager(tmp_path)
    memory = _FakeMemoryManager(start_index=0)
    loop = _FakeLoop(sessions=sessions, memory=memory)
    env = CommandEnv(agent_loop=loop, channel_auth=MagicMock())

    outbound = await handle_new(
        env,
        InboundMessage(channel="cli", sender_id="user", chat_id="test", content="/new"),
        "",
    )

    assert outbound.content == "New session started."
    assert memory.calls == []
    assert memory.run_calls == []
    loop.bus.publish_outbound.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_new_returns_without_waiting_for_blocking_run_new_session(
    tmp_path: Path,
) -> None:
    sessions = SessionManager(tmp_path)
    memory = _FakeMemoryManager(start_index=0)
    loop = _FakeLoop(sessions=sessions, memory=memory)
    env = CommandEnv(agent_loop=loop, channel_auth=MagicMock())

    session = sessions.get_or_create("cli:test")
    session.messages = [{"role": "user", "content": "pending"}]
    sessions.save(session)

    outbound = await asyncio.wait_for(
        handle_new(
            env,
            InboundMessage(channel="cli", sender_id="user", chat_id="test", content="/new"),
            "",
        ),
        timeout=0.1,
    )

    assert outbound.content == "New session started."
    assert memory.calls == [[{"role": "user", "content": "pending"}]]
    assert memory.run_calls == []
    loop.bus.publish_outbound.assert_awaited_once()
