"""Runtime-facing memory manager."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine

from loguru import logger

from aeloon.core.config.schema import MemoryConfig
from aeloon.memory import backends as _builtin_backends  # noqa: F401
from aeloon.memory.base import MemoryBackend, MemoryBackendDeps, PreparedMemoryContext
from aeloon.memory.registry import build_backend
from aeloon.memory.types import MessagePayload


class MemoryManager:
    """Own the active backend and its asynchronous lifecycle hooks."""

    def __init__(self, memory_config: MemoryConfig, deps: MemoryBackendDeps):
        raw_cfg = memory_config.backends.get(memory_config.backend, {})
        self.backend = build_backend(memory_config.backend, raw_cfg, deps)
        self._background_tasks: list[asyncio.Task[None]] = []
        self._closing = False

    @classmethod
    def from_backend(cls, backend: MemoryBackend) -> "MemoryManager":
        """Build a manager around an already-constructed backend for tests."""
        manager = cls.__new__(cls)
        manager.backend = backend
        manager._background_tasks = []
        manager._closing = False
        return manager

    async def prepare_turn(
        self,
        *,
        session: object,
        query: str,
        channel: str | None,
        chat_id: str | None,
        current_role: str,
    ) -> PreparedMemoryContext:
        """Delegate turn preparation to the active backend."""
        return await self.backend.prepare_turn(
            session=session,
            query=query,
            channel=channel,
            chat_id=chat_id,
            current_role=current_role,
        )

    def pending_start_index(self, session: object) -> int:
        """Expose backend-owned pending history boundaries."""
        return self.backend.pending_start_index(session)

    async def after_turn(
        self,
        *,
        session: object,
        raw_new_messages: list[MessagePayload],
        persisted_new_messages: list[MessagePayload],
        final_content: str | None,
    ) -> None:
        """Schedule backend post-turn work in the background."""
        self._track_task(
            self.backend.after_turn(
                session=session,
                raw_new_messages=raw_new_messages,
                persisted_new_messages=persisted_new_messages,
                final_content=final_content,
            )
        )

    async def on_new_session(
        self,
        *,
        session: object,
        pending_messages: list[MessagePayload],
    ) -> None:
        """Schedule backend archival/reset work in the background."""
        self._track_task(
            self.backend.on_new_session(
                session=session,
                pending_messages=pending_messages,
            )
        )

    async def run_new_session(
        self,
        *,
        session: object,
        pending_messages: list[MessagePayload],
    ) -> None:
        """Run backend archival/reset work synchronously for blocking callers."""
        await self.backend.on_new_session(
            session=session,
            pending_messages=pending_messages,
        )

    async def close(self) -> None:
        """Drain pending backend work before closing the backend."""
        self._closing = True
        while self._background_tasks:
            pending = list(self._background_tasks)
            await asyncio.gather(*pending, return_exceptions=True)
            self._background_tasks = [task for task in self._background_tasks if not task.done()]
        await self.backend.close()

    def _track_task(self, coro: Coroutine[object, object, None]) -> None:
        if self._closing:
            coro.close()
            raise RuntimeError("Memory manager is closing")
        task = asyncio.create_task(coro)
        self._background_tasks.append(task)
        task.add_done_callback(self._remove_task)

    def _remove_task(self, task: asyncio.Task[None]) -> None:
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            # Cancellation during shutdown is expected and should not be logged as an error.
            exc = None
        if exc is not None:
            logger.opt(exception=exc).error("Memory backend background task failed")
        if task in self._background_tasks:
            self._background_tasks.remove(task)
