from __future__ import annotations

import pytest

from aeloon.cli.flows.agent import _finalize_session_before_shutdown
from aeloon.core.session.manager import SessionManager


class _FakeMemory:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def pending_start_index(self, _session: object) -> int:
        return 0

    async def finalize_session(
        self,
        *,
        session: object,
        pending_messages: list[dict[str, object]],
        reason: str,
    ) -> None:
        self.events.append(f"finalize:{reason}:{len(pending_messages)}")


class _FakeLoop:
    def __init__(self, sessions: SessionManager, memory: _FakeMemory) -> None:
        self.sessions = sessions
        self.memory = memory


@pytest.mark.asyncio
async def test_finalize_session_before_shutdown_delegates_to_memory_runtime(tmp_path) -> None:
    events: list[str] = []
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:test")
    session.add_message("user", "hello")
    session.add_message("assistant", "world")
    memory = _FakeMemory(events)
    loop = _FakeLoop(sessions=sessions, memory=memory)

    await _finalize_session_before_shutdown(loop, "cli:test", reason="cli-shutdown")

    assert events == ["finalize:cli-shutdown:2"]


@pytest.mark.asyncio
async def test_finalize_happens_before_plugin_shutdown_in_cli_sequence(tmp_path) -> None:
    events: list[str] = []
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:test")
    session.add_message("user", "hello")
    memory = _FakeMemory(events)
    loop = _FakeLoop(sessions=sessions, memory=memory)

    await _finalize_session_before_shutdown(loop, "cli:test", reason="cli-shutdown")
    events.append("plugin-shutdown")

    assert events[:2] == ["finalize:cli-shutdown:1", "plugin-shutdown"]
