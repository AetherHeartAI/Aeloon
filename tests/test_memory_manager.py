from __future__ import annotations

import asyncio
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
