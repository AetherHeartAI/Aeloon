"""Provider orchestration for at most one additive external provider."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aeloon.core.config.schema import MemoryConfig
from aeloon.memory.providers.base import MemoryProvider
from aeloon.memory.providers.registry import MEMORY_PROVIDER_REGISTRY
from aeloon.memory.types import MemoryRuntimeDeps, MessagePayload

if TYPE_CHECKING:
    from aeloon.core.agent.tools.base import Tool


class ProviderManager:
    """Manage one additive external provider."""

    def __init__(self) -> None:
        self._provider: MemoryProvider | None = None

    @classmethod
    def from_config(cls, memory_config: MemoryConfig, deps: MemoryRuntimeDeps) -> "ProviderManager":
        manager = cls()
        if memory_config.provider:
            provider = manager.build_active_provider(
                memory_config.provider,
                memory_config.providers.get(memory_config.provider, {}),
                deps,
            )
            manager.add_provider(provider)
        return manager

    def add_provider(self, provider: MemoryProvider) -> None:
        if self._provider is not None:
            raise ValueError("Only one external memory provider is supported")
        self._provider = provider

    def build_active_provider(
        self,
        name: str,
        config: dict[str, object],
        deps: MemoryRuntimeDeps,
    ) -> MemoryProvider:
        return MEMORY_PROVIDER_REGISTRY.build(name, config, deps)

    def system_prompt_sections(self) -> list[str]:
        if self._provider is None:
            return []
        block = self._provider.system_prompt_block()
        return [block] if block else []

    def tools(self) -> list["Tool"]:
        if self._provider is None:
            return []
        return self._provider.build_tools()

    def always_skill_names(self) -> list[str]:
        if self._provider is None:
            return []
        return self._provider.always_skill_names()

    async def prefetch(
        self,
        *,
        session: object,
        query: str,
        channel: str | None,
        chat_id: str | None,
        current_role: str,
    ) -> str:
        if self._provider is None:
            return ""
        return await self._provider.prefetch(
            session=session,
            query=query,
            channel=channel,
            chat_id=chat_id,
            current_role=current_role,
        )

    async def queue_prefetch(
        self,
        *,
        session: object,
        query: str,
        channel: str | None,
        chat_id: str | None,
        current_role: str,
    ) -> None:
        if self._provider is None:
            return
        await self._provider.queue_prefetch(
            session=session,
            query=query,
            channel=channel,
            chat_id=chat_id,
            current_role=current_role,
        )

    async def sync_turn(
        self,
        *,
        session: object,
        raw_new_messages: list[MessagePayload],
        persisted_new_messages: list[MessagePayload],
        final_content: str | None,
    ) -> None:
        if self._provider is None:
            return
        await self._provider.sync_turn(
            session=session,
            raw_new_messages=raw_new_messages,
            persisted_new_messages=persisted_new_messages,
            final_content=final_content,
        )

    async def on_pre_compress(
        self,
        *,
        session: object,
        pending_messages: list[MessagePayload],
        reason: str | None = None,
    ) -> None:
        if self._provider is None:
            return
        await self._provider.on_pre_compress(
            session=session,
            pending_messages=pending_messages,
            reason=reason,
        )

    async def on_memory_write(
        self,
        *,
        action: str,
        target: str,
        content: str,
        session_key: str | None = None,
    ) -> None:
        if self._provider is None:
            return
        await self._provider.on_memory_write(
            action=action,
            target=target,
            content=content,
            session_key=session_key,
        )

    async def on_session_end(
        self,
        *,
        session: object,
        pending_messages: list[MessagePayload],
        reason: str | None = None,
    ) -> None:
        if self._provider is None:
            return
        await self._provider.on_session_end(
            session=session,
            pending_messages=pending_messages,
            reason=reason,
        )

    async def shutdown(self) -> None:
        if self._provider is None:
            return
        await self._provider.shutdown()
