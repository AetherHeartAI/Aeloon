from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from aeloon.core.config.schema import Config
from aeloon.core.session.manager import Session, SessionManager
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


class _ArchiveRecorder:
    def __init__(self, events: list[str] | None = None) -> None:
        self.events = events

    async def ingest_session(self, session: Session) -> None:
        if self.events is not None:
            self.events.append(f"archive:{session.end_reason or 'turn'}")

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_memory_runtime_drains_background_tasks_on_close(tmp_path: Path) -> None:
    from aeloon.memory.runtime import MemoryRuntime

    started = asyncio.Event()
    finished = asyncio.Event()
    events: list[str] = []

    class DummyLocalMemory:
        async def prepare_turn(
            self,
            *,
            session: object,
            query: str,
            channel: str | None,
            chat_id: str | None,
            current_role: str,
        ) -> TurnMemoryContext:
            return TurnMemoryContext()

        async def after_turn(
            self,
            *,
            session: object,
            query: str,
            raw_new_messages: list[dict[str, object]],
            persisted_new_messages: list[dict[str, object]],
            final_content: str | None,
        ) -> None:
            events.append("after_turn:start")
            started.set()
            await asyncio.sleep(0)
            events.append("after_turn:end")
            finished.set()

        def pending_start_index(self, session: object) -> int:
            return 0

        def estimate_session_prompt_tokens(self, session: Session) -> tuple[int, str]:
            return (0, "none")

        async def on_new_session(
            self,
            *,
            session: object,
            pending_messages: list[dict[str, object]],
        ) -> None:
            return None

        async def maybe_compact_by_tokens(self, session: Session) -> None:
            return None

        async def close(self) -> None:
            assert finished.is_set()
            events.append("close")

    runtime = MemoryRuntime(
        memory_config=Config().memory,
        deps=_make_deps(tmp_path),
        local_memory=DummyLocalMemory(),
        session_archive=_ArchiveRecorder(),
        flush_coordinator=None,
    )

    await runtime.after_turn(
        session=Session(key="cli:test"),
        query="hello",
        raw_new_messages=[],
        persisted_new_messages=[],
        final_content="ok",
    )
    await started.wait()
    await runtime.close()

    assert events == ["after_turn:start", "after_turn:end", "close"]


@pytest.mark.asyncio
async def test_memory_runtime_schedules_new_session_hook(tmp_path: Path) -> None:
    from aeloon.memory.runtime import MemoryRuntime

    archived = asyncio.Event()

    class DummyLocalMemory:
        async def prepare_turn(
            self,
            *,
            session: object,
            query: str,
            channel: str | None,
            chat_id: str | None,
            current_role: str,
        ) -> TurnMemoryContext:
            return TurnMemoryContext()

        async def after_turn(
            self,
            *,
            session: object,
            query: str,
            raw_new_messages: list[dict[str, object]],
            persisted_new_messages: list[dict[str, object]],
            final_content: str | None,
        ) -> None:
            return None

        def pending_start_index(self, session: object) -> int:
            return 0

        def estimate_session_prompt_tokens(self, session: Session) -> tuple[int, str]:
            return (0, "none")

        async def on_new_session(
            self,
            *,
            session: object,
            pending_messages: list[dict[str, object]],
        ) -> None:
            assert len(pending_messages) == 2
            archived.set()

        async def maybe_compact_by_tokens(self, session: Session) -> None:
            return None

        async def close(self) -> None:
            return None

    runtime = MemoryRuntime(
        memory_config=Config().memory,
        deps=_make_deps(tmp_path),
        local_memory=DummyLocalMemory(),
        session_archive=_ArchiveRecorder(),
        flush_coordinator=None,
    )

    await runtime.on_new_session(
        session=Session(key="cli:test"),
        pending_messages=[{"role": "user", "content": "u"}, {"role": "assistant", "content": "a"}],
    )
    await runtime.close()

    assert archived.is_set()


@pytest.mark.asyncio
async def test_memory_runtime_on_shutdown_flushes_before_close(tmp_path: Path) -> None:
    from aeloon.memory.runtime import MemoryRuntime

    events: list[str] = []

    class DummyLocalMemory:
        async def prepare_turn(
            self,
            *,
            session: object,
            query: str,
            channel: str | None,
            chat_id: str | None,
            current_role: str,
        ) -> TurnMemoryContext:
            return TurnMemoryContext()

        async def after_turn(
            self,
            *,
            session: object,
            query: str,
            raw_new_messages: list[dict[str, object]],
            persisted_new_messages: list[dict[str, object]],
            final_content: str | None,
        ) -> None:
            return None

        def pending_start_index(self, session: object) -> int:
            return 0

        def estimate_session_prompt_tokens(self, session: Session) -> tuple[int, str]:
            return (0, "none")

        async def on_new_session(
            self,
            *,
            session: object,
            pending_messages: list[dict[str, object]],
        ) -> None:
            return None

        async def maybe_compact_by_tokens(self, session: Session) -> None:
            return None

        async def close(self) -> None:
            events.append("local-close")

    class Flush:
        async def flush(
            self, *, session: object, pending_messages, reason: str | None = None
        ) -> None:
            events.append(f"flush:{reason}")

        async def close(self) -> None:
            events.append("flush-close")

    runtime = MemoryRuntime(
        memory_config=Config().memory,
        deps=_make_deps(tmp_path),
        local_memory=DummyLocalMemory(),
        session_archive=_ArchiveRecorder(events),
        flush_coordinator=Flush(),
    )

    await runtime.on_shutdown(
        session=Session(key="cli:test"),
        pending_messages=[{"role": "user", "content": "bye"}],
        reason="gateway-shutdown",
    )

    assert events == [
        "flush:gateway-shutdown",
        "archive:gateway-shutdown",
        "flush-close",
        "local-close",
    ]


@pytest.mark.asyncio
async def test_memory_runtime_finalize_session_archives_end_reason(tmp_path: Path) -> None:
    from aeloon.memory.runtime import MemoryRuntime

    events: list[str] = []

    class DummyLocalMemory:
        async def prepare_turn(
            self,
            *,
            session: object,
            query: str,
            channel: str | None,
            chat_id: str | None,
            current_role: str,
        ) -> TurnMemoryContext:
            return TurnMemoryContext()

        async def after_turn(
            self,
            *,
            session: object,
            query: str,
            raw_new_messages: list[dict[str, object]],
            persisted_new_messages: list[dict[str, object]],
            final_content: str | None,
        ) -> None:
            return None

        def pending_start_index(self, session: object) -> int:
            return 0

        def estimate_session_prompt_tokens(self, session: Session) -> tuple[int, str]:
            return (0, "none")

        async def on_new_session(
            self,
            *,
            session: object,
            pending_messages: list[dict[str, object]],
        ) -> None:
            return None

        async def maybe_compact_by_tokens(self, session: Session) -> None:
            return None

        async def close(self) -> None:
            return None

    class Flush:
        async def flush(
            self, *, session: object, pending_messages, reason: str | None = None
        ) -> None:
            events.append(f"flush:{reason}")

        async def close(self) -> None:
            return None

    runtime = MemoryRuntime(
        memory_config=Config().memory,
        deps=_make_deps(tmp_path),
        local_memory=DummyLocalMemory(),
        session_archive=_ArchiveRecorder(events),
        flush_coordinator=Flush(),
    )
    session = Session(key="cli:test")

    await runtime.finalize_session(
        session=session,
        pending_messages=[{"role": "user", "content": "bye"}],
        reason="cli-shutdown",
    )

    assert session.end_reason == "cli-shutdown"
    assert session.ended_at is not None
    assert events == ["flush:cli-shutdown", "archive:cli-shutdown"]
