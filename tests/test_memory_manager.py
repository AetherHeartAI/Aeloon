from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from pathlib import Path
from types import SimpleNamespace

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


def _background_task(
    events: list[str],
    finished_event: asyncio.Event,
) -> asyncio.Task[None]:
    async def _runner() -> None:
        await asyncio.sleep(0)
        events.append("task-2-end")
        finished_event.set()

    return asyncio.create_task(_runner())


@pytest.mark.asyncio
async def test_memory_manager_builds_backend_from_config(tmp_path: Path) -> None:
    from aeloon.memory.base import MemoryBackend, MemoryBackendConfig, PreparedMemoryContext
    from aeloon.memory.manager import MemoryManager
    from aeloon.memory.registry import register_backend

    class FakeConfig(MemoryBackendConfig):
        label: str = "fake"

    @register_backend
    class FakeBackend(MemoryBackend):
        backend_name = "fake-manager-build"
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
                "backend": "fake-manager-build",
                "backends": {"fake-manager-build": {"label": "wired"}},
            }
        }
    )

    manager = MemoryManager(memory_config=cfg.memory, deps=_make_deps(tmp_path))
    prepared = await manager.prepare_turn(
        session=Session(key="cli:test"),
        query="hello",
        channel="cli",
        chat_id="direct",
        current_role="user",
    )

    assert prepared.runtime_lines == ["wired"]


@pytest.mark.asyncio
async def test_memory_manager_delegates_prepare_turn(tmp_path: Path) -> None:
    from aeloon.memory.base import MemoryBackend, MemoryBackendConfig, PreparedMemoryContext
    from aeloon.memory.manager import MemoryManager

    class FakeBackend(MemoryBackend):
        backend_name = "fake-prepare"
        config_model = MemoryBackendConfig

        def __init__(self) -> None:
            self.prepare_calls: list[tuple[str, str | None, str | None, str]] = []

        async def prepare_turn(
            self,
            *,
            session: object,
            query: str,
            channel: str | None,
            chat_id: str | None,
            current_role: str,
        ) -> PreparedMemoryContext:
            self.prepare_calls.append((query, channel, chat_id, current_role))
            return PreparedMemoryContext(history_start_index=7)

        async def after_turn(
            self,
            *,
            session: object,
            raw_new_messages: list[dict[str, object]],
            persisted_new_messages: list[dict[str, object]],
            final_content: str | None,
        ) -> None:
            return None

    backend = FakeBackend()
    manager = MemoryManager.from_backend(backend)

    prepared = await manager.prepare_turn(
        session=Session(key="cli:test"),
        query="hello",
        channel="cli",
        chat_id="direct",
        current_role="user",
    )

    assert prepared.history_start_index == 7
    assert backend.prepare_calls == [("hello", "cli", "direct", "user")]


@pytest.mark.asyncio
async def test_memory_manager_run_new_session_awaits_backend(tmp_path: Path) -> None:
    from aeloon.memory.base import MemoryBackend, MemoryBackendConfig, PreparedMemoryContext
    from aeloon.memory.manager import MemoryManager

    events: list[str] = []

    class FakeBackend(MemoryBackend):
        backend_name = "fake-run-new-session"
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
            events.append("start")
            await asyncio.sleep(0)
            events.append("end")

    backend = FakeBackend(MemoryBackendConfig(), _make_deps(tmp_path))
    manager = MemoryManager.from_backend(backend)

    await manager.run_new_session(
        session=Session(key="cli:test"),
        pending_messages=[{"role": "user", "content": "hello"}],
    )

    assert events == ["start", "end"]


@pytest.mark.asyncio
async def test_memory_manager_logs_background_task_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aeloon.memory import manager as manager_module
    from aeloon.memory.base import MemoryBackend, MemoryBackendConfig, PreparedMemoryContext
    from aeloon.memory.manager import MemoryManager

    logged: list[str] = []

    class _FakeLogSink:
        def error(self, message: str) -> None:
            logged.append(message)

    def _opt(*, exception: BaseException | None = None) -> _FakeLogSink:
        assert isinstance(exception, RuntimeError)
        assert str(exception) == "boom"
        return _FakeLogSink()

    monkeypatch.setattr(manager_module, "logger", SimpleNamespace(opt=_opt))

    class FakeBackend(MemoryBackend):
        backend_name = "fake-background-failure"
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
            raise RuntimeError("boom")

    backend = FakeBackend(MemoryBackendConfig(), _make_deps(tmp_path))
    manager = MemoryManager.from_backend(backend)

    await manager.after_turn(
        session=Session(key="cli:test"),
        raw_new_messages=[],
        persisted_new_messages=[],
        final_content=None,
    )
    for _ in range(5):
        if logged:
            break
        await asyncio.sleep(0)

    assert logged == ["Memory backend background task failed"]


@pytest.mark.asyncio
async def test_memory_manager_close_drains_tasks_added_during_drain(tmp_path: Path) -> None:
    from aeloon.memory.base import MemoryBackend, MemoryBackendConfig, PreparedMemoryContext
    from aeloon.memory.manager import MemoryManager

    events: list[str] = []
    first_task_started = asyncio.Event()
    allow_first_task_to_finish = asyncio.Event()
    second_task_finished = asyncio.Event()
    manager_holder: dict[str, MemoryManager] = {}

    class FakeBackend(MemoryBackend):
        backend_name = "fake-close-drain"
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
            events.append("task-1-start")
            first_task_started.set()
            await allow_first_task_to_finish.wait()
            second_task = _background_task(events, second_task_finished)
            manager = manager_holder["manager"]
            manager._background_tasks.append(second_task)
            second_task.add_done_callback(manager._remove_task)
            events.append("task-1-end")

        async def close(self) -> None:
            events.append("backend-close")

    backend = FakeBackend(MemoryBackendConfig(), _make_deps(tmp_path))
    manager = MemoryManager.from_backend(backend)
    manager_holder["manager"] = manager

    await manager.after_turn(
        session=Session(key="cli:test"),
        raw_new_messages=[],
        persisted_new_messages=[],
        final_content=None,
    )
    await first_task_started.wait()

    close_task = asyncio.create_task(manager.close())
    await asyncio.sleep(0)
    allow_first_task_to_finish.set()
    await close_task

    assert second_task_finished.is_set()
    assert events == ["task-1-start", "task-1-end", "task-2-end", "backend-close"]


@pytest.mark.asyncio
async def test_memory_manager_rejects_new_tasks_while_closing(tmp_path: Path) -> None:
    from aeloon.memory.base import MemoryBackend, MemoryBackendConfig, PreparedMemoryContext
    from aeloon.memory.manager import MemoryManager

    class FakeBackend(MemoryBackend):
        backend_name = "fake-close-reject"
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

    backend = FakeBackend(MemoryBackendConfig(), _make_deps(tmp_path))
    manager = MemoryManager.from_backend(backend)
    manager._closing = True

    async def _should_never_run() -> None:
        raise AssertionError("background task should have been closed")

    coro: Coroutine[object, object, None] = _should_never_run()

    with pytest.raises(RuntimeError, match="Memory manager is closing"):
        manager._track_task(coro)

    assert getattr(coro, "cr_frame", None) is None
