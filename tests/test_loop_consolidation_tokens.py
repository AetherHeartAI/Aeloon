from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from aeloon.core.agent.loop import AgentLoop
from aeloon.core.bus.queue import MessageBus
from aeloon.core.session.manager import SessionManager
from aeloon.memory.backends import file as memory_module
from aeloon.memory.base import MemoryBackendDeps
from aeloon.providers.base import LLMResponse


def _make_loop(tmp_path, *, estimated_tokens: int, context_window_tokens: int) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.estimate_prompt_tokens.return_value = (estimated_tokens, "test-counter")
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="ok", tool_calls=[]))

    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        context_window_tokens=context_window_tokens,
    )
    object.__setattr__(loop.tools, "get_definitions", MagicMock(return_value=[]))
    return loop


def _file_backend(loop: AgentLoop) -> memory_module.FileMemoryBackend:
    backend = loop.memory_consolidator
    assert isinstance(backend, memory_module.FileMemoryBackend)
    return backend


@pytest.mark.asyncio
async def test_prompt_below_threshold_does_not_consolidate(tmp_path, monkeypatch) -> None:
    loop = _make_loop(tmp_path, estimated_tokens=100, context_window_tokens=200)
    backend = _file_backend(loop)
    consolidate_messages = AsyncMock(return_value=True)
    monkeypatch.setattr(backend, "consolidate_messages", consolidate_messages)

    await loop.process_direct("hello", session_key="cli:test")

    consolidate_messages.assert_not_awaited()


@pytest.mark.asyncio
async def test_prompt_above_threshold_triggers_consolidation(tmp_path, monkeypatch) -> None:
    loop = _make_loop(tmp_path, estimated_tokens=1000, context_window_tokens=200)
    backend = _file_backend(loop)
    consolidate_messages = AsyncMock(return_value=True)
    monkeypatch.setattr(backend, "consolidate_messages", consolidate_messages)
    session = loop.sessions.get_or_create("cli:test")
    session.messages = [
        {"role": "user", "content": "u1", "timestamp": "2026-01-01T00:00:00"},
        {"role": "assistant", "content": "a1", "timestamp": "2026-01-01T00:00:01"},
        {"role": "user", "content": "u2", "timestamp": "2026-01-01T00:00:02"},
    ]
    loop.sessions.save(session)
    monkeypatch.setattr(memory_module, "estimate_message_tokens", lambda _message: 500)

    await loop.process_direct("hello", session_key="cli:test")

    assert consolidate_messages.await_count >= 1


@pytest.mark.asyncio
async def test_prompt_above_threshold_archives_until_next_user_boundary(
    tmp_path, monkeypatch
) -> None:
    loop = _make_loop(tmp_path, estimated_tokens=1000, context_window_tokens=200)
    backend = _file_backend(loop)
    consolidate_messages = AsyncMock(return_value=True)
    monkeypatch.setattr(backend, "consolidate_messages", consolidate_messages)

    session = loop.sessions.get_or_create("cli:test")
    session.messages = [
        {"role": "user", "content": "u1", "timestamp": "2026-01-01T00:00:00"},
        {"role": "assistant", "content": "a1", "timestamp": "2026-01-01T00:00:01"},
        {"role": "user", "content": "u2", "timestamp": "2026-01-01T00:00:02"},
        {"role": "assistant", "content": "a2", "timestamp": "2026-01-01T00:00:03"},
        {"role": "user", "content": "u3", "timestamp": "2026-01-01T00:00:04"},
    ]
    loop.sessions.save(session)

    token_map = {"u1": 120, "a1": 120, "u2": 120, "a2": 120, "u3": 120}
    monkeypatch.setattr(
        memory_module, "estimate_message_tokens", lambda message: token_map[message["content"]]
    )

    await backend.maybe_consolidate_by_tokens(session)

    assert consolidate_messages.await_args is not None
    archived_chunk = cast(list[dict[str, object]], consolidate_messages.await_args.args[0])
    assert [message["content"] for message in archived_chunk] == ["u1", "a1", "u2", "a2"]
    assert session.last_consolidated == 4


@pytest.mark.asyncio
async def test_consolidation_loops_until_target_met(tmp_path, monkeypatch) -> None:
    """Verify maybe_consolidate_by_tokens keeps looping until under threshold."""
    loop = _make_loop(tmp_path, estimated_tokens=0, context_window_tokens=200)
    backend = _file_backend(loop)
    consolidate_messages = AsyncMock(return_value=True)
    monkeypatch.setattr(backend, "consolidate_messages", consolidate_messages)

    session = loop.sessions.get_or_create("cli:test")
    session.messages = [
        {"role": "user", "content": "u1", "timestamp": "2026-01-01T00:00:00"},
        {"role": "assistant", "content": "a1", "timestamp": "2026-01-01T00:00:01"},
        {"role": "user", "content": "u2", "timestamp": "2026-01-01T00:00:02"},
        {"role": "assistant", "content": "a2", "timestamp": "2026-01-01T00:00:03"},
        {"role": "user", "content": "u3", "timestamp": "2026-01-01T00:00:04"},
        {"role": "assistant", "content": "a3", "timestamp": "2026-01-01T00:00:05"},
        {"role": "user", "content": "u4", "timestamp": "2026-01-01T00:00:06"},
    ]
    loop.sessions.save(session)

    call_count = [0]

    def mock_estimate(_session):
        call_count[0] += 1
        if call_count[0] == 1:
            return (500, "test")
        if call_count[0] == 2:
            return (300, "test")
        return (80, "test")

    monkeypatch.setattr(backend, "estimate_session_prompt_tokens", mock_estimate)
    monkeypatch.setattr(memory_module, "estimate_message_tokens", lambda _m: 100)

    await backend.maybe_consolidate_by_tokens(session)

    assert consolidate_messages.await_count == 2
    assert session.last_consolidated == 6


@pytest.mark.asyncio
async def test_consolidation_continues_below_trigger_until_half_target(
    tmp_path, monkeypatch
) -> None:
    """Once triggered, consolidation should continue until it drops below half threshold."""
    loop = _make_loop(tmp_path, estimated_tokens=0, context_window_tokens=200)
    backend = _file_backend(loop)
    consolidate_messages = AsyncMock(return_value=True)
    monkeypatch.setattr(backend, "consolidate_messages", consolidate_messages)

    session = loop.sessions.get_or_create("cli:test")
    session.messages = [
        {"role": "user", "content": "u1", "timestamp": "2026-01-01T00:00:00"},
        {"role": "assistant", "content": "a1", "timestamp": "2026-01-01T00:00:01"},
        {"role": "user", "content": "u2", "timestamp": "2026-01-01T00:00:02"},
        {"role": "assistant", "content": "a2", "timestamp": "2026-01-01T00:00:03"},
        {"role": "user", "content": "u3", "timestamp": "2026-01-01T00:00:04"},
        {"role": "assistant", "content": "a3", "timestamp": "2026-01-01T00:00:05"},
        {"role": "user", "content": "u4", "timestamp": "2026-01-01T00:00:06"},
    ]
    loop.sessions.save(session)

    call_count = [0]

    def mock_estimate(_session):
        call_count[0] += 1
        if call_count[0] == 1:
            return (500, "test")
        if call_count[0] == 2:
            return (150, "test")
        return (80, "test")

    monkeypatch.setattr(backend, "estimate_session_prompt_tokens", mock_estimate)
    monkeypatch.setattr(memory_module, "estimate_message_tokens", lambda _m: 100)

    await backend.maybe_consolidate_by_tokens(session)

    assert consolidate_messages.await_count == 2
    assert session.last_consolidated == 6


@pytest.mark.asyncio
async def test_preflight_consolidation_before_llm_call(tmp_path, monkeypatch) -> None:
    """Verify preflight consolidation runs before the LLM call in process_direct."""
    order: list[str] = []

    loop = _make_loop(tmp_path, estimated_tokens=0, context_window_tokens=200)
    backend = _file_backend(loop)

    async def track_consolidate(messages):
        order.append("consolidate")
        return True

    monkeypatch.setattr(backend, "consolidate_messages", track_consolidate)

    async def track_llm(*args, **kwargs):
        order.append("llm")
        return LLMResponse(content="ok", tool_calls=[])

    monkeypatch.setattr(loop.provider, "chat_with_retry", track_llm)

    session = loop.sessions.get_or_create("cli:test")
    session.messages = [
        {"role": "user", "content": "u1", "timestamp": "2026-01-01T00:00:00"},
        {"role": "assistant", "content": "a1", "timestamp": "2026-01-01T00:00:01"},
        {"role": "user", "content": "u2", "timestamp": "2026-01-01T00:00:02"},
    ]
    loop.sessions.save(session)
    monkeypatch.setattr(memory_module, "estimate_message_tokens", lambda _m: 500)

    call_count = [0]

    def mock_estimate(_session):
        call_count[0] += 1
        return (1000 if call_count[0] <= 1 else 80, "test")

    monkeypatch.setattr(backend, "estimate_session_prompt_tokens", mock_estimate)

    await loop.process_direct("hello", session_key="cli:test")

    assert "consolidate" in order
    assert "llm" in order
    assert order.index("consolidate") < order.index("llm")


def test_file_backend_pending_start_index_uses_session_offset(tmp_path) -> None:
    from aeloon.memory.backends.file import FileMemoryBackend, FileMemoryConfig

    backend = FileMemoryBackend(
        FileMemoryConfig(),
        MemoryBackendDeps(
            workspace=tmp_path,
            provider=MagicMock(),
            model="test-model",
            sessions=SessionManager(tmp_path),
            context_window_tokens=4096,
            build_messages=lambda *args, **kwargs: [],
            get_tool_definitions=lambda: [],
        ),
    )
    session = backend.deps.sessions.get_or_create("cli:test")
    session.last_consolidated = 3

    assert backend.pending_start_index(session) == 3


def test_file_backend_uses_plain_lock_map(tmp_path) -> None:
    from aeloon.memory.backends.file import FileMemoryBackend, FileMemoryConfig

    backend = FileMemoryBackend(
        FileMemoryConfig(),
        MemoryBackendDeps(
            workspace=tmp_path,
            provider=MagicMock(),
            model="test-model",
            sessions=SessionManager(tmp_path),
            context_window_tokens=4096,
            build_messages=lambda *args, **kwargs: [],
            get_tool_definitions=lambda: [],
        ),
    )

    lock = backend.get_lock("cli:test")

    assert isinstance(backend._locks, dict)
    assert backend.get_lock("cli:test") is lock


@pytest.mark.asyncio
async def test_file_backend_prepare_turn_injects_backend_identity(tmp_path) -> None:
    from aeloon.memory.backends.file import FileMemoryBackend, FileMemoryConfig

    backend = FileMemoryBackend(
        FileMemoryConfig(),
        MemoryBackendDeps(
            workspace=tmp_path,
            provider=MagicMock(),
            model="test-model",
            sessions=SessionManager(tmp_path),
            context_window_tokens=4096,
            build_messages=lambda *args, **kwargs: [],
            get_tool_definitions=lambda: [],
        ),
    )

    prepared = await backend.prepare_turn(
        session=backend.deps.sessions.get_or_create("cli:test"),
        query="hello",
        channel="cli",
        chat_id="direct",
        current_role="user",
    )

    assert prepared.runtime_lines[0] == "Memory backend: file"
