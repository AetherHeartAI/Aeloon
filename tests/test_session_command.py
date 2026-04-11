from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from aeloon.core.agent.commands import CommandEnv
from aeloon.core.agent.commands.session import handle_new
from aeloon.core.bus.events import InboundMessage
from aeloon.core.session.manager import SessionManager


class _FakeMemoryManager:
    def __init__(self, start_index: int) -> None:
        self.start_index = start_index
        self.calls: list[list[dict[str, object]]] = []

    def pending_start_index(self, _session: object) -> int:
        return self.start_index

    async def run_new_session(
        self,
        *,
        session: object,
        pending_messages: list[dict[str, object]],
    ) -> None:
        self.calls.append([dict(message) for message in pending_messages])


class _FakeLoop:
    def __init__(self, sessions: SessionManager, memory: _FakeMemoryManager) -> None:
        self.sessions = sessions
        self.memory = memory


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
