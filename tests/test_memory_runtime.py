from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from aeloon.core.config.schema import Config, PromptMemoryConfig
from aeloon.core.session.manager import Session, SessionManager
from aeloon.memory.prompt_store import PromptMemoryStore
from aeloon.memory.types import MemoryRuntimeDeps, TurnMemoryContext
from aeloon.providers.base import LLMProvider, LLMResponse


class DummyProvider(LLMProvider):
    async def chat(
        self,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, object] | None = None,
    ) -> LLMResponse:
        return LLMResponse(content=None)

    def get_default_model(self) -> str:
        return "test-model"


def _make_deps(tmp_path: Path) -> MemoryRuntimeDeps:
    return MemoryRuntimeDeps(
        workspace=tmp_path,
        provider=DummyProvider(),
        model="test-model",
        sessions=SessionManager(tmp_path),
        context_window_tokens=4096,
        build_messages=lambda **_kwargs: [],
        get_tool_definitions=lambda: [],
    )


class _DummyLocalMemory:
    def __init__(self) -> None:
        self.after_turn_calls: list[str | None] = []
        self.new_session_calls: list[int] = []

    async def prepare_turn(
        self,
        *,
        session: object,
        query: str,
        channel: str | None,
        chat_id: str | None,
        current_role: str,
    ) -> TurnMemoryContext:
        return TurnMemoryContext(history_start_index=2, runtime_lines=["Memory mode: local"])

    async def after_turn(
        self,
        *,
        session: object,
        raw_new_messages: list[dict[str, object]],
        persisted_new_messages: list[dict[str, object]],
        final_content: str | None,
    ) -> None:
        self.after_turn_calls.append(final_content)

    def pending_start_index(self, session: object) -> int:
        return 2

    def estimate_session_prompt_tokens(self, session: Session) -> tuple[int, str]:
        return (123, "mock")

    async def on_new_session(
        self,
        *,
        session: object,
        pending_messages: list[dict[str, object]],
    ) -> None:
        self.new_session_calls.append(len(pending_messages))

    async def maybe_compact_by_tokens(self, session: Session) -> None:
        return None

    async def close(self) -> None:
        return None


class _DummyProviderManager:
    def system_prompt_sections(self) -> list[str]:
        return ["# Provider\n\nOpenViking enabled"]

    def always_skill_names(self) -> list[str]:
        return ["openviking-memory"]

    async def prefetch(
        self,
        *,
        session: object,
        query: str,
        channel: str | None,
        chat_id: str | None,
        current_role: str,
    ) -> str:
        return "Provider recall"

    async def sync_turn(
        self,
        *,
        session: object,
        raw_new_messages: list[dict[str, object]],
        persisted_new_messages: list[dict[str, object]],
        final_content: str | None,
    ) -> None:
        return None

    async def on_pre_compress(
        self,
        *,
        session: object,
        pending_messages: list[dict[str, object]],
    ) -> None:
        return None

    async def on_memory_write(self, *, action: str, target: str, content: str) -> None:
        return None

    async def shutdown(self) -> None:
        return None


@pytest.mark.asyncio
async def test_memory_runtime_owns_local_memory_and_component_slots(tmp_path: Path) -> None:
    from aeloon.memory.runtime import MemoryRuntime

    runtime = MemoryRuntime(memory_config=Config().memory, deps=_make_deps(tmp_path))

    assert runtime.local_memory is not None
    assert runtime.prompt_memory is not None
    assert runtime.session_archive is not None
    assert runtime.provider_manager is None
    assert runtime.flush_coordinator is not None
    assert not hasattr(runtime, "backend")


@pytest.mark.asyncio
async def test_memory_runtime_prepare_turn_injects_prompt_memory_and_provider_recall(
    tmp_path: Path,
) -> None:
    from aeloon.memory.runtime import MemoryRuntime

    prompt_memory = PromptMemoryStore(tmp_path, PromptMemoryConfig())
    prompt_memory.add("memory", "Workspace uses concise progress updates.")
    runtime = MemoryRuntime(
        memory_config=Config().memory,
        deps=_make_deps(tmp_path),
        local_memory=_DummyLocalMemory(),
        prompt_memory=prompt_memory,
        provider_manager=_DummyProviderManager(),
    )

    prepared = await runtime.prepare_turn(
        session=Session(key="cli:test"),
        query="hello",
        channel="cli",
        chat_id="direct",
        current_role="user",
    )

    assert prepared.history_start_index == 2
    assert "memory" in prepared.always_skill_names
    assert "openviking-memory" in prepared.always_skill_names
    assert any("Workspace uses concise progress updates." in section for section in prepared.system_sections)
    assert any("Provider recall" in block for block in prepared.recalled_context_blocks)


@pytest.mark.asyncio
async def test_memory_runtime_flush_delegates_to_flush_coordinator(tmp_path: Path) -> None:
    from aeloon.memory.runtime import MemoryRuntime

    class DummyFlushCoordinator:
        def __init__(self) -> None:
            self.calls: list[tuple[int, str | None]] = []

        async def flush(
            self,
            *,
            session: object,
            pending_messages: list[dict[str, object]],
            reason: str | None = None,
        ) -> None:
            self.calls.append((len(pending_messages), reason))

        async def close(self) -> None:
            return None

    flush = DummyFlushCoordinator()
    runtime = MemoryRuntime(
        memory_config=Config().memory,
        deps=_make_deps(tmp_path),
        local_memory=_DummyLocalMemory(),
        flush_coordinator=flush,
    )

    await runtime.flush(
        session=Session(key="cli:test"),
        pending_messages=[{"role": "user", "content": "hello"}],
        reason="new-session",
    )

    assert flush.calls == [(1, "new-session")]


@pytest.mark.asyncio
async def test_memory_runtime_on_shutdown_flushes_then_closes_components(tmp_path: Path) -> None:
    from aeloon.memory.runtime import MemoryRuntime

    events: list[str] = []

    class DummyFlushCoordinator:
        async def flush(
            self,
            *,
            session: object,
            pending_messages: list[dict[str, object]],
            reason: str | None = None,
        ) -> None:
            events.append(f"flush:{reason}")

        async def close(self) -> None:
            events.append("flush-close")

    class DummyProviderManager(_DummyProviderManager):
        async def shutdown(self) -> None:
            events.append("provider-shutdown")

    class DummyLocalMemory(_DummyLocalMemory):
        async def close(self) -> None:
            events.append("local-close")

    runtime = MemoryRuntime(
        memory_config=Config().memory,
        deps=_make_deps(tmp_path),
        local_memory=DummyLocalMemory(),
        provider_manager=DummyProviderManager(),
        flush_coordinator=DummyFlushCoordinator(),
    )

    await runtime.on_shutdown(
        session=Session(key="cli:test"),
        pending_messages=[{"role": "assistant", "content": "bye"}],
        reason="shutdown",
    )

    assert events == [
        "flush:shutdown",
        "provider-shutdown",
        "flush-close",
        "local-close",
    ]


def test_agent_loop_uses_memory_runtime_with_local_memory(tmp_path: Path) -> None:
    from aeloon.core.agent.loop import AgentLoop
    from aeloon.core.bus.queue import MessageBus
    from aeloon.memory.runtime import MemoryRuntime

    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path, model="test-model")

    assert isinstance(loop.memory, MemoryRuntime)
    assert loop.memory.local_memory is not None
    assert not hasattr(loop, "memory_" "consolidator")


def test_memory_public_api_exports_backendless_symbols() -> None:
    from aeloon.memory import (
        LocalMemoryRuntime,
        LocalMemoryStore,
        MemoryRuntime,
        MemoryRuntimeDeps,
        TurnMemoryContext,
    )

    assert LocalMemoryRuntime is not None
    assert LocalMemoryStore is not None
    assert MemoryRuntime is not None
    assert MemoryRuntimeDeps is not None
    assert TurnMemoryContext is not None
