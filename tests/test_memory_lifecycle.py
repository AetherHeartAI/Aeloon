from __future__ import annotations

import asyncio

import pytest

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


def _make_deps(tmp_path):
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
async def test_memory_manager_drains_background_tasks_on_close(tmp_path) -> None:
    from aeloon.memory.base import MemoryBackend, MemoryBackendConfig, PreparedMemoryContext
    from aeloon.memory.manager import MemoryManager

    started = asyncio.Event()
    finished = asyncio.Event()
    events: list[str] = []

    class FakeBackend(MemoryBackend):
        backend_name = "fake-close"
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
            events.append("after_turn:start")
            started.set()
            await asyncio.sleep(0)
            events.append("after_turn:end")
            finished.set()

        async def close(self) -> None:
            assert finished.is_set()
            events.append("close")

    backend = FakeBackend(MemoryBackendConfig(), _make_deps(tmp_path))
    manager = MemoryManager.from_backend(backend)

    await manager.after_turn(
        session=Session(key="cli:test"),
        raw_new_messages=[],
        persisted_new_messages=[],
        final_content="ok",
    )
    await started.wait()
    await manager.close()

    assert events == ["after_turn:start", "after_turn:end", "close"]


@pytest.mark.asyncio
async def test_memory_manager_schedules_new_session_hook(tmp_path) -> None:
    from aeloon.memory.base import MemoryBackend, MemoryBackendConfig, PreparedMemoryContext
    from aeloon.memory.manager import MemoryManager

    archived = asyncio.Event()

    class FakeBackend(MemoryBackend):
        backend_name = "fake-new-session"
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

        async def on_new_session(
            self,
            *,
            session: object,
            pending_messages: list[dict[str, object]],
        ) -> None:
            assert len(pending_messages) == 2
            archived.set()

    backend = FakeBackend(MemoryBackendConfig(), _make_deps(tmp_path))
    manager = MemoryManager.from_backend(backend)

    await manager.on_new_session(
        session=Session(key="cli:test"),
        pending_messages=[{"role": "user", "content": "u"}, {"role": "assistant", "content": "a"}],
    )
    await manager.close()

    assert archived.is_set()
