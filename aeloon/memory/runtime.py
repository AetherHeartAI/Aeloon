"""Layered memory runtime orchestration."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from typing import Protocol

from loguru import logger

from aeloon.core.config.paths import get_archive_db_path
from aeloon.core.config.schema import MemoryConfig
from aeloon.core.session.manager import Session
from aeloon.memory.archive_service import SessionArchiveService
from aeloon.memory.flush import MemoryFlushCoordinator
from aeloon.memory.local_runtime import LocalMemoryRuntime
from aeloon.memory.prompt_store import PromptMemoryStore
from aeloon.memory.providers.manager import ProviderManager
from aeloon.memory.security import build_memory_context_block
from aeloon.memory.types import MemoryRuntimeDeps, MessagePayload, TurnMemoryContext


class LocalMemoryProtocol(Protocol):
    async def prepare_turn(
        self,
        *,
        session: object,
        query: str,
        channel: str | None,
        chat_id: str | None,
        current_role: str,
    ) -> TurnMemoryContext: ...

    async def after_turn(
        self,
        *,
        session: object,
        raw_new_messages: list[MessagePayload],
        persisted_new_messages: list[MessagePayload],
        final_content: str | None,
    ) -> None: ...

    def pending_start_index(self, session: object) -> int: ...

    def estimate_session_prompt_tokens(self, session: Session) -> tuple[int, str]: ...

    async def on_new_session(
        self,
        *,
        session: object,
        pending_messages: list[MessagePayload],
    ) -> None: ...

    async def maybe_compact_by_tokens(self, session: Session) -> None: ...

    async def close(self) -> None: ...


class FlushCoordinatorProtocol(Protocol):
    async def flush(
        self,
        *,
        session: object,
        pending_messages: list[MessagePayload],
        reason: str | None = None,
    ) -> None: ...

    async def close(self) -> None: ...


class ProviderManagerProtocol(Protocol):
    def system_prompt_sections(self) -> list[str]: ...

    def always_skill_names(self) -> list[str]: ...

    async def prefetch(
        self,
        *,
        session: object,
        query: str,
        channel: str | None,
        chat_id: str | None,
        current_role: str,
    ) -> str: ...

    async def sync_turn(
        self,
        *,
        session: object,
        raw_new_messages: list[MessagePayload],
        persisted_new_messages: list[MessagePayload],
        final_content: str | None,
    ) -> None: ...

    async def on_pre_compress(
        self,
        *,
        session: object,
        pending_messages: list[MessagePayload],
    ) -> None: ...

    async def on_memory_write(self, *, action: str, target: str, content: str) -> None: ...

    async def shutdown(self) -> None: ...


class SessionArchiveProtocol(Protocol):
    async def ingest_session(self, session: Session) -> None: ...

    async def close(self) -> None: ...


class MemoryRuntime:
    def __init__(
        self,
        memory_config: MemoryConfig,
        deps: MemoryRuntimeDeps,
        *,
        local_memory: LocalMemoryProtocol | None = None,
        prompt_memory: PromptMemoryStore | None = None,
        session_archive: SessionArchiveProtocol | None = None,
        provider_manager: ProviderManagerProtocol | None = None,
        flush_coordinator: FlushCoordinatorProtocol | None = None,
    ) -> None:
        self.local_memory = local_memory or LocalMemoryRuntime(
            config=memory_config.local,
            prompt_config=memory_config.prompt,
            deps=deps,
        )
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
        if self.provider_manager is None and memory_config.provider:
            self.provider_manager = ProviderManager.from_config(memory_config, deps)
        self.flush_coordinator = flush_coordinator
        if self.flush_coordinator is None and memory_config.flush.enabled and self.prompt_memory is not None:
            self.flush_coordinator = MemoryFlushCoordinator(
                provider=deps.provider,
                model=deps.model,
                prompt_store=self.prompt_memory,
            )
        self._background_tasks: list[asyncio.Task[None]] = []
        self._closing = False

    def set_flush_before_loss(
        self,
        callback: Callable[..., Awaitable[None]],
    ) -> None:
        deps = getattr(self.local_memory, "deps", None)
        if isinstance(deps, MemoryRuntimeDeps):
            deps.flush_before_loss = callback

    async def prepare_turn(
        self,
        *,
        session: object,
        query: str,
        channel: str | None,
        chat_id: str | None,
        current_role: str,
    ) -> TurnMemoryContext:
        prepared = await self.local_memory.prepare_turn(
            session=session,
            query=query,
            channel=channel,
            chat_id=chat_id,
            current_role=current_role,
        )

        sections = list(prepared.system_sections)
        skills = list(prepared.always_skill_names)
        if self.prompt_memory is not None:
            self.prompt_memory.refresh_snapshot()
            prompt_sections = self.prompt_memory.system_prompt_sections()
            sections = [*prompt_sections, *sections]
            if prompt_sections:
                skills = list(dict.fromkeys([*skills, "memory"]))

        recalled_blocks = list(prepared.recalled_context_blocks)
        if self.provider_manager is not None:
            sections.extend(self.provider_manager.system_prompt_sections())
            provider_recall = await self.provider_manager.prefetch(
                session=session,
                query=query,
                channel=channel,
                chat_id=chat_id,
                current_role=current_role,
            )
            if provider_recall:
                recalled_blocks.append(build_memory_context_block(provider_recall))
            skills = list(dict.fromkeys([*skills, *self.provider_manager.always_skill_names()]))

        return TurnMemoryContext(
            history_start_index=prepared.history_start_index,
            system_sections=sections,
            runtime_lines=list(prepared.runtime_lines),
            always_skill_names=skills,
            recalled_context_blocks=recalled_blocks,
        )

    def pending_start_index(self, session: object) -> int:
        return self.local_memory.pending_start_index(session)

    def estimate_session_prompt_tokens(self, session: Session) -> tuple[int, str]:
        return self.local_memory.estimate_session_prompt_tokens(session)

    async def after_turn(
        self,
        *,
        session: object,
        raw_new_messages: list[MessagePayload],
        persisted_new_messages: list[MessagePayload],
        final_content: str | None,
    ) -> None:
        async def _after_turn_task() -> None:
            if self.session_archive is not None and isinstance(session, Session):
                await self.session_archive.ingest_session(session)
            await self.local_memory.after_turn(
                session=session,
                raw_new_messages=raw_new_messages,
                persisted_new_messages=persisted_new_messages,
                final_content=final_content,
            )
            if self.provider_manager is not None:
                await self.provider_manager.sync_turn(
                    session=session,
                    raw_new_messages=raw_new_messages,
                    persisted_new_messages=persisted_new_messages,
                    final_content=final_content,
                )

        self._track_task(_after_turn_task())

    async def on_new_session(
        self,
        *,
        session: object,
        pending_messages: list[MessagePayload],
    ) -> None:
        self._track_task(
            self.local_memory.on_new_session(
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
        await self.local_memory.on_new_session(
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
        if self.flush_coordinator is None:
            if self.provider_manager is not None:
                await self.provider_manager.on_pre_compress(
                    session=session,
                    pending_messages=list(pending_messages or []),
                )
            return
        pending = list(pending_messages or [])
        await self.flush_coordinator.flush(
            session=session,
            pending_messages=pending,
            reason=reason,
        )
        if self.provider_manager is not None:
            await self.provider_manager.on_pre_compress(
                session=session,
                pending_messages=pending,
            )

    async def on_shutdown(
        self,
        *,
        session: object | None = None,
        pending_messages: list[MessagePayload] | None = None,
        reason: str | None = None,
    ) -> None:
        if session is not None:
            await self.flush(
                session=session,
                pending_messages=pending_messages,
                reason=reason,
            )
        await self.close()

    async def close(self) -> None:
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
        await self.local_memory.close()

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
            logger.opt(exception=exc).error("Memory runtime background task failed")
        if task in self._background_tasks:
            self._background_tasks.remove(task)
