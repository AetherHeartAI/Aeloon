"""Runtime-native local memory orchestration."""

from __future__ import annotations

import asyncio

from loguru import logger

from aeloon.core.config.schema import LocalMemoryConfig, PromptMemoryConfig
from aeloon.core.session.manager import Session
from aeloon.memory.local_store import LocalMemoryStore
from aeloon.memory.types import MemoryRuntimeDeps, MessagePayload, TurnMemoryContext
from aeloon.utils.helpers import estimate_message_tokens, estimate_prompt_tokens_chain


class LocalMemoryRuntime:
    def __init__(
        self,
        *,
        config: LocalMemoryConfig,
        prompt_config: PromptMemoryConfig,
        deps: MemoryRuntimeDeps,
    ) -> None:
        self.config = config
        self.prompt_config = prompt_config
        self.deps = deps
        self.store = LocalMemoryStore(
            directory=deps.workspace / prompt_config.directory,
            history_file_name=config.history_file,
            max_failures_before_raw_archive=config.max_failures_before_raw_archive,
        )
        self.sessions = deps.sessions
        self.context_window_tokens = deps.context_window_tokens
        self._build_messages = deps.build_messages
        self._get_tool_definitions = deps.get_tool_definitions
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def history_file(self):
        return self.store.history_file

    def get_lock(self, session_key: str) -> asyncio.Lock:
        return self._locks.setdefault(session_key, asyncio.Lock())

    def _last_compacted(self, session: object) -> int:
        if isinstance(session, Session):
            return session.last_compacted
        return 0

    def _set_last_compacted(self, session: object, value: int) -> None:
        if isinstance(session, Session):
            session.last_compacted = value

    async def prepare_turn(
        self,
        *,
        session: object,
        query: str,
        channel: str | None,
        chat_id: str | None,
        current_role: str,
    ) -> TurnMemoryContext:
        if isinstance(session, Session):
            await self.maybe_compact_by_tokens(session)

        return TurnMemoryContext(
            history_start_index=self.pending_start_index(session),
            runtime_lines=[
                "Memory mode: local archive",
                "Prompt memory owned by PromptMemoryStore",
                "Use session_search for cross-session recall; HISTORY.md is a compatibility artifact.",
            ],
        )

    async def after_turn(
        self,
        *,
        session: object,
        raw_new_messages: list[MessagePayload],
        persisted_new_messages: list[MessagePayload],
        final_content: str | None,
    ) -> None:
        if isinstance(session, Session):
            await self.maybe_compact_by_tokens(session)

    def pending_start_index(self, session: object) -> int:
        return self._last_compacted(session)

    async def on_new_session(
        self,
        *,
        session: object,
        pending_messages: list[MessagePayload],
    ) -> None:
        await self.archive_messages(pending_messages)

    async def consolidate_messages(self, messages: list[MessagePayload]) -> bool:
        return await self.store.consolidate(messages, self.deps.provider, self.deps.model)

    def pick_compaction_boundary(
        self,
        session: Session,
        tokens_to_remove: int,
    ) -> tuple[int, int] | None:
        start = self._last_compacted(session)
        if start >= len(session.messages) or tokens_to_remove <= 0:
            return None

        removed_tokens = 0
        last_boundary: tuple[int, int] | None = None
        for idx in range(start, len(session.messages)):
            message = session.messages[idx]
            if idx > start and message.get("role") == "user":
                last_boundary = (idx, removed_tokens)
                if removed_tokens >= tokens_to_remove:
                    return last_boundary
            removed_tokens += estimate_message_tokens(message)

        return last_boundary

    def estimate_session_prompt_tokens(self, session: Session) -> tuple[int, str]:
        history = session.get_history(max_messages=0)
        channel, chat_id = session.key.split(":", 1) if ":" in session.key else (None, None)
        probe_messages = self._build_messages(
            history=history,
            current_message="[token-probe]",
            channel=channel,
            chat_id=chat_id,
        )
        return estimate_prompt_tokens_chain(
            self.deps.provider,
            self.deps.model,
            probe_messages,
            self._get_tool_definitions(),
        )

    async def archive_messages(self, messages: list[MessagePayload]) -> bool:
        if not messages:
            return True
        for _ in range(self.config.max_failures_before_raw_archive):
            if await self.consolidate_messages(messages):
                return True
        return True

    async def maybe_compact_by_tokens(self, session: Session) -> None:
        if not session.messages or self.context_window_tokens <= 0:
            return

        lock = self.get_lock(session.key)
        async with lock:
            trigger = max(1, int(self.context_window_tokens * self.config.trigger_ratio))
            target = max(1, int(self.context_window_tokens * self.config.target_ratio))
            estimated, source = self.estimate_session_prompt_tokens(session)
            if estimated <= 0:
                return
            if estimated < trigger:
                logger.debug(
                    "Token compaction idle {}: {}/{} via {}",
                    session.key,
                    estimated,
                    self.context_window_tokens,
                    source,
                )
                return

            for round_num in range(self.config.max_consolidation_rounds):
                if estimated <= target:
                    return

                boundary = self.pick_compaction_boundary(session, max(1, estimated - target))
                if boundary is None:
                    logger.debug(
                        "Token compaction: no safe boundary for {} (round {})",
                        session.key,
                        round_num,
                    )
                    return

                end_idx = boundary[0]
                chunk = session.messages[self._last_compacted(session) : end_idx]
                if not chunk:
                    return

                if self.deps.flush_before_loss is not None:
                    await self.deps.flush_before_loss(
                        session=session,
                        pending_messages=chunk,
                        reason="compression",
                    )
                logger.info(
                    "Token compaction round {} for {}: {}/{} via {}, chunk={} msgs",
                    round_num,
                    session.key,
                    estimated,
                    self.context_window_tokens,
                    source,
                    len(chunk),
                )
                if not await self.consolidate_messages(chunk):
                    return
                self._set_last_compacted(session, end_idx)
                self.sessions.save(session)

                estimated, source = self.estimate_session_prompt_tokens(session)
                if estimated <= 0:
                    return

    async def close(self) -> None:
        return None
