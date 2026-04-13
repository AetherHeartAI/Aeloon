from __future__ import annotations

from pathlib import Path

import pytest

from aeloon.core.config.schema import Config
from aeloon.core.session.manager import Session
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


def _make_deps(tmp_path: Path):
    from aeloon.core.session.manager import SessionManager
    from aeloon.memory.base import MemoryBackendDeps

    return MemoryBackendDeps(
        workspace=tmp_path,
        provider=DummyProvider(),
        model="test-model",
        sessions=SessionManager(tmp_path),
        context_window_tokens=4096,
        build_messages=lambda *args, **kwargs: [],
        get_tool_definitions=lambda: [],
    )


@pytest.mark.asyncio
async def test_memory_runtime_builds_backend_and_exposes_component_slots(tmp_path: Path) -> None:
    from aeloon.memory.base import MemoryBackend, MemoryBackendConfig, PreparedMemoryContext
    from aeloon.memory.registry import register_backend
    from aeloon.memory.runtime import MemoryRuntime

    class FakeConfig(MemoryBackendConfig):
        label: str = "fake"

    @register_backend
    class FakeBackend(MemoryBackend):
        backend_name = "fake-runtime-build"
        config_model = FakeConfig
        config: FakeConfig

        async def prepare_turn(
            self,
            *,
            session: object,
            query: str,
            channel: str | None,
            chat_id: str | None,
            current_role: str,
        ) -> PreparedMemoryContext:
            return PreparedMemoryContext(runtime_lines=[self.config.label])

        async def after_turn(
            self,
            *,
            session: object,
            raw_new_messages: list[dict[str, object]],
            persisted_new_messages: list[dict[str, object]],
            final_content: str | None,
        ) -> None:
            return None

    cfg = Config.model_validate(
        {
            "memory": {
                "provider": "fake-runtime-build",
                "providers": {"fake-runtime-build": {"label": "wired"}},
            }
        }
    )

    runtime = MemoryRuntime(memory_config=cfg.memory, deps=_make_deps(tmp_path))
    prepared = await runtime.prepare_turn(
        session=Session(key="cli:test"),
        query="hello",
        channel="cli",
        chat_id="direct",
        current_role="user",
    )

    assert prepared.runtime_lines == ["wired"]
    assert runtime.prompt_memory is not None
    assert runtime.session_archive is not None
    assert runtime.provider_manager is None
    assert runtime.flush_coordinator is not None


@pytest.mark.asyncio
async def test_memory_runtime_flush_delegates_to_flush_coordinator(tmp_path: Path) -> None:
    from aeloon.memory.base import MemoryBackend, MemoryBackendConfig, PreparedMemoryContext
    from aeloon.memory.runtime import MemoryRuntime

    class FakeBackend(MemoryBackend):
        backend_name = "fake-runtime-flush"
        config_model = MemoryBackendConfig

        async def prepare_turn(
            self,
            *,
            session: object,
            query: str,
            channel: str | None,
            chat_id: str | None,
            current_role: str,
        ) -> PreparedMemoryContext:
            return PreparedMemoryContext()

        async def after_turn(
            self,
            *,
            session: object,
            raw_new_messages: list[dict[str, object]],
            persisted_new_messages: list[dict[str, object]],
            final_content: str | None,
        ) -> None:
            return None

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

    runtime = MemoryRuntime.from_backend(
        FakeBackend(MemoryBackendConfig(), _make_deps(tmp_path)),
        flush_coordinator=DummyFlushCoordinator(),
    )

    await runtime.flush(
        session=Session(key="cli:test"),
        pending_messages=[{"role": "user", "content": "hello"}],
        reason="new-session",
    )

    assert runtime.flush_coordinator is not None
    assert runtime.flush_coordinator.calls == [(1, "new-session")]


@pytest.mark.asyncio
async def test_memory_runtime_on_shutdown_flushes_then_closes_components(tmp_path: Path) -> None:
    from aeloon.memory.base import MemoryBackend, MemoryBackendConfig, PreparedMemoryContext
    from aeloon.memory.runtime import MemoryRuntime

    events: list[str] = []

    class FakeBackend(MemoryBackend):
        backend_name = "fake-runtime-shutdown"
        config_model = MemoryBackendConfig

        async def prepare_turn(
            self,
            *,
            session: object,
            query: str,
            channel: str | None,
            chat_id: str | None,
            current_role: str,
        ) -> PreparedMemoryContext:
            return PreparedMemoryContext()

        async def after_turn(
            self,
            *,
            session: object,
            raw_new_messages: list[dict[str, object]],
            persisted_new_messages: list[dict[str, object]],
            final_content: str | None,
        ) -> None:
            return None

        async def close(self) -> None:
            events.append("backend-close")

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

    class DummyProviderManager:
        async def on_pre_compress(self, *, session: object, pending_messages) -> None:
            return None

        async def shutdown(self) -> None:
            events.append("provider-shutdown")

    runtime = MemoryRuntime.from_backend(
        FakeBackend(MemoryBackendConfig(), _make_deps(tmp_path)),
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
        "backend-close",
    ]


def test_agent_loop_uses_memory_runtime(tmp_path: Path) -> None:
    from unittest.mock import MagicMock

    from aeloon.core.agent.loop import AgentLoop
    from aeloon.core.bus.queue import MessageBus
    from aeloon.memory.runtime import MemoryRuntime

    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path, model="test-model")

    assert isinstance(loop.memory, MemoryRuntime)
    assert loop.memory_consolidator is loop.memory.backend
