from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aeloon.core.agent.loop import AgentLoop
from aeloon.core.bus.queue import MessageBus
from aeloon.core.config.schema import Config
from aeloon.core.session.manager import SessionManager
from aeloon.memory import local_runtime as memory_module
from aeloon.memory.local_runtime import LocalMemoryRuntime
from aeloon.memory.types import MemoryRuntimeDeps
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


def _local_memory(loop: AgentLoop) -> LocalMemoryRuntime:
    runtime = loop.memory.local_memory
    assert isinstance(runtime, LocalMemoryRuntime)
    return runtime


@pytest.mark.asyncio
async def test_prompt_below_threshold_does_not_compact(tmp_path, monkeypatch) -> None:
    loop = _make_loop(tmp_path, estimated_tokens=100, context_window_tokens=200)
    local_memory = _local_memory(loop)
    compact_messages = AsyncMock(return_value=True)
    monkeypatch.setattr(local_memory, "consolidate_messages", compact_messages)

    await loop.process_direct("hello", session_key="cli:test")

    compact_messages.assert_not_awaited()


@pytest.mark.asyncio
async def test_prompt_above_threshold_triggers_compaction(tmp_path, monkeypatch) -> None:
    loop = _make_loop(tmp_path, estimated_tokens=1000, context_window_tokens=200)
    local_memory = _local_memory(loop)
    compact_messages = AsyncMock(return_value=True)
    monkeypatch.setattr(local_memory, "consolidate_messages", compact_messages)
    session = loop.sessions.get_or_create("cli:test")
    session.messages = [
        {"role": "user", "content": "u1", "timestamp": "2026-01-01T00:00:00"},
        {"role": "assistant", "content": "a1", "timestamp": "2026-01-01T00:00:01"},
        {"role": "user", "content": "u2", "timestamp": "2026-01-01T00:00:02"},
    ]
    loop.sessions.save(session)
    monkeypatch.setattr(memory_module, "estimate_message_tokens", lambda _message: 500)

    await loop.process_direct("hello", session_key="cli:test")

    assert compact_messages.await_count >= 1


@pytest.mark.asyncio
async def test_prompt_above_threshold_archives_until_next_user_boundary(
    tmp_path, monkeypatch
) -> None:
    loop = _make_loop(tmp_path, estimated_tokens=1000, context_window_tokens=200)
    local_memory = _local_memory(loop)
    compact_messages = AsyncMock(return_value=True)
    monkeypatch.setattr(local_memory, "consolidate_messages", compact_messages)

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

    await local_memory.maybe_compact_by_tokens(session)

    assert compact_messages.await_args is not None
    archived_chunk = compact_messages.await_args.args[0]
    assert [message["content"] for message in archived_chunk] == ["u1", "a1", "u2", "a2"]
    assert session.last_compacted == 4


@pytest.mark.asyncio
async def test_compaction_loops_until_target_met(tmp_path, monkeypatch) -> None:
    loop = _make_loop(tmp_path, estimated_tokens=0, context_window_tokens=200)
    local_memory = _local_memory(loop)
    compact_messages = AsyncMock(return_value=True)
    monkeypatch.setattr(local_memory, "consolidate_messages", compact_messages)

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

    monkeypatch.setattr(local_memory, "estimate_session_prompt_tokens", mock_estimate)
    monkeypatch.setattr(memory_module, "estimate_message_tokens", lambda _m: 100)

    await local_memory.maybe_compact_by_tokens(session)

    assert compact_messages.await_count == 2
    assert session.last_compacted == 6


@pytest.mark.asyncio
async def test_compaction_continues_until_under_target(tmp_path, monkeypatch) -> None:
    loop = _make_loop(tmp_path, estimated_tokens=0, context_window_tokens=200)
    local_memory = _local_memory(loop)
    compact_messages = AsyncMock(return_value=True)
    monkeypatch.setattr(local_memory, "consolidate_messages", compact_messages)

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

    monkeypatch.setattr(local_memory, "estimate_session_prompt_tokens", mock_estimate)
    monkeypatch.setattr(memory_module, "estimate_message_tokens", lambda _m: 100)

    await local_memory.maybe_compact_by_tokens(session)

    assert compact_messages.await_count == 2
    assert session.last_compacted == 6


@pytest.mark.asyncio
async def test_preflight_compaction_before_llm_call(tmp_path, monkeypatch) -> None:
    order: list[str] = []

    loop = _make_loop(tmp_path, estimated_tokens=0, context_window_tokens=200)
    local_memory = _local_memory(loop)

    async def track_consolidate(messages):
        order.append("compact")
        return True

    monkeypatch.setattr(local_memory, "consolidate_messages", track_consolidate)

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
    monkeypatch.setattr(loop.memory, "after_turn", AsyncMock(return_value=None))

    call_count = [0]

    def mock_estimate(_session):
        call_count[0] += 1
        return (1000 if call_count[0] <= 1 else 80, "test")

    monkeypatch.setattr(local_memory, "estimate_session_prompt_tokens", mock_estimate)

    await loop.process_direct("hello", session_key="cli:test")

    assert "compact" in order
    assert "llm" in order
    assert order.index("compact") < len(order) - 1
    assert order[-1] == "llm"


def test_local_memory_runtime_pending_start_index_uses_session_offset(tmp_path) -> None:
    local_memory = LocalMemoryRuntime(
        config=Config().memory.local,
        prompt_config=Config().memory.prompt,
        deps=MemoryRuntimeDeps(
            workspace=tmp_path,
            provider=MagicMock(),
            model="test-model",
            sessions=SessionManager(tmp_path),
            context_window_tokens=4096,
            build_messages=lambda **_kwargs: [],
            get_tool_definitions=lambda: [],
        ),
    )
    session = local_memory.deps.sessions.get_or_create("cli:test")
    session.last_compacted = 3

    assert local_memory.pending_start_index(session) == 3


def test_local_memory_runtime_uses_plain_lock_map(tmp_path) -> None:
    local_memory = LocalMemoryRuntime(
        config=Config().memory.local,
        prompt_config=Config().memory.prompt,
        deps=MemoryRuntimeDeps(
            workspace=tmp_path,
            provider=MagicMock(),
            model="test-model",
            sessions=SessionManager(tmp_path),
            context_window_tokens=4096,
            build_messages=lambda **_kwargs: [],
            get_tool_definitions=lambda: [],
        ),
    )

    lock = local_memory.get_lock("cli:test")

    assert isinstance(local_memory._locks, dict)
    assert local_memory.get_lock("cli:test") is lock


@pytest.mark.asyncio
async def test_local_memory_runtime_prepare_turn_injects_runtime_identity(tmp_path) -> None:
    local_memory = LocalMemoryRuntime(
        config=Config().memory.local,
        prompt_config=Config().memory.prompt,
        deps=MemoryRuntimeDeps(
            workspace=tmp_path,
            provider=MagicMock(),
            model="test-model",
            sessions=SessionManager(tmp_path),
            context_window_tokens=4096,
            build_messages=lambda **_kwargs: [],
            get_tool_definitions=lambda: [],
        ),
    )

    prepared = await local_memory.prepare_turn(
        session=local_memory.deps.sessions.get_or_create("cli:test"),
        query="hello",
        channel="cli",
        chat_id="direct",
        current_role="user",
    )

    assert prepared.runtime_lines[0] == "Memory mode: local"
