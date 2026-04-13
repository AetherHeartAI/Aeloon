"""Layered memory runtime orchestration."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from dataclasses import replace
from typing import Protocol

from loguru import logger

import aeloon.memory.backends as _builtin_backends  # noqa: F401

from aeloon.core.config.schema import MemoryConfig, PromptMemoryConfig
from aeloon.core.config.paths import get_archive_db_path
from aeloon.core.session.manager import Session
from aeloon.memory.base import MemoryBackend, MemoryBackendDeps, PreparedMemoryContext
from aeloon.memory.archive_service import SessionArchiveService
from aeloon.memory.prompt_store import PromptMemoryStore
from aeloon.memory.registry import build_backend
from aeloon.memory.types import MessagePayload


class FlushCoordinatorProtocol(Protocol):
    """Flush coordination contract."""

    async def flush(
        self,
        *,
        session: object,
        pending_messages: list[MessagePayload],
        reason: str | None = None,
    ) -> None:
        """Persist memory before context loss."""

    async def close(self) -> None:
        """Release flush coordinator resources."""


class ProviderManagerProtocol(Protocol):
    """Provider-manager shutdown contract."""

    async def shutdown(self) -> None:
        """Shut down provider resources."""


class SessionArchiveProtocol(Protocol):
    """Archive ingestion contract."""

    async def ingest_session(self, session: Session) -> None:
        """Ingest one persisted session snapshot."""

    async def close(self) -> None:
        """Close archive resources."""


class MemoryRuntime:
    """Own the memory backend facade and layered runtime slots."""

    def __init__(
        self,
        memory_config: MemoryConfig,
        deps: MemoryBackendDeps,
        *,
        backend: MemoryBackend | None = None,
        prompt_memory: PromptMemoryStore | None = None,
        session_archive: SessionArchiveProtocol | None = None,
        provider_manager: ProviderManagerProtocol | None = None,
        flush_coordinator: FlushCoordinatorProtocol | None = None,
    ):
        raw_cfg = memory_config.backends.get(memory_config.backend, {})
        self.backend = backend or build_backend(memory_config.backend, raw_cfg, deps)
        self.prompt_memory = prompt_memory
        if self.prompt_memory is None and memory_config.prompt.enabled:
            self.prompt_memory = PromptMemoryStore(deps.workspace, memory_config.prompt)
        self.session_archive = session_archive
        if self.session_archive is None and memory_config.archive.enabled:
            self.session_archive = SessionArchiveService(
                workspace=deps.workspace,
                db_path=get_archive_db_path(),
            )
        self.provider_manager = provider_manager
        self.flush_coordinator = flush_coordinator
        self._background_tasks: list[asyncio.Task[None]] = []
        self._closing = False

    @classmethod
    def from_backend(
        cls,
        backend: MemoryBackend,
        *,
        prompt_memory: PromptMemoryStore | None = None,
        session_archive: SessionArchiveProtocol | None = None,
        provider_manager: ProviderManagerProtocol | None = None,
        flush_coordinator: FlushCoordinatorProtocol | None = None,
    ) -> "MemoryRuntime":
        """Build a runtime around an already-constructed backend for tests."""
        runtime = cls.__new__(cls)
        runtime.backend = backend
        runtime.prompt_memory = prompt_memory
        backend_deps = getattr(backend, "deps", None)
        if runtime.prompt_memory is None and isinstance(backend_deps, MemoryBackendDeps):
            runtime.prompt_memory = PromptMemoryStore(backend.deps.workspace, PromptMemoryConfig())
        runtime.session_archive = session_archive
        runtime.provider_manager = provider_manager
        runtime.flush_coordinator = flush_coordinator
        runtime._background_tasks = []
        runtime._closing = False
        return runtime

    async def prepare_turn(
        self,
        *,
        session: object,
        query: str,
        channel: str | None,
        chat_id: str | None,
        current_role: str,
    ) -> PreparedMemoryContext:
        """Delegate turn preparation to the compatibility backend."""
        prepared = await self.backend.prepare_turn(
            session=session,
            query=query,
            channel=channel,
            chat_id=chat_id,
            current_role=current_role,
        )
        if self.prompt_memory is None:
            return prepared
        self.prompt_memory.refresh_snapshot()
        prompt_sections = self.prompt_memory.system_prompt_sections()
        sections = [*prompt_sections, *prepared.system_sections]
        skills = list(prepared.always_skill_names)
        if prompt_sections:
            skills = list(dict.fromkeys([*skills, "memory"]))
        return replace(prepared, system_sections=sections, always_skill_names=skills)

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
        async def _after_turn_task() -> None:
            if self.session_archive is not None and isinstance(session, Session):
                await self.session_archive.ingest_session(session)
            await self.backend.after_turn(
                session=session,
                raw_new_messages=raw_new_messages,
                persisted_new_messages=persisted_new_messages,
                final_content=final_content,
            )

        self._track_task(
            _after_turn_task()
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

    async def flush(
        self,
        *,
        session: object,
        pending_messages: list[MessagePayload] | None = None,
        reason: str | None = None,
    ) -> None:
        """Flush runtime-owned memory before context loss."""
        if self.flush_coordinator is None:
            return
        await self.flush_coordinator.flush(
            session=session,
            pending_messages=list(pending_messages or []),
            reason=reason,
        )

    async def on_shutdown(
        self,
        *,
        session: object | None = None,
        pending_messages: list[MessagePayload] | None = None,
        reason: str | None = None,
    ) -> None:
        """Run shutdown-time memory hooks, then close resources."""
        if session is not None:
            await self.flush(
                session=session,
                pending_messages=pending_messages,
                reason=reason,
            )
        await self.close()

    async def close(self) -> None:
        """Drain pending backend work before closing runtime resources."""
        self._closing = True
        while self._background_tasks:
            pending = list(self._background_tasks)
            await asyncio.gather(*pending, return_exceptions=True)
            self._background_tasks = [task for task in self._background_tasks if not task.done()]
        if self.session_archive is not None:
            await self.session_archive.close()
        if self.provider_manager is not None:
            await self.provider_manager.shutdown()
        if self.flush_coordinator is not None:
            await self.flush_coordinator.close()
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
            exc = None
        if exc is not None:
            logger.opt(exception=exc).error("Memory backend background task failed")
        if task in self._background_tasks:
            self._background_tasks.remove(task)
