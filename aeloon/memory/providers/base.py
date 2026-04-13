"""Additive memory provider base types."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from aeloon.core.config.schema import Config


class MemoryProvider:
    """Base class for one additive external memory provider."""

    name: ClassVar[str]

    def system_prompt_block(self) -> str:
        return ""

    def always_skill_names(self) -> list[str]:
        return []

    async def prefetch(
        self,
        *,
        session: object,
        query: str,
        channel: str | None,
        chat_id: str | None,
        current_role: str,
    ) -> str:
        return ""

    async def queue_prefetch(
        self,
        *,
        session: object,
        query: str,
        channel: str | None,
        chat_id: str | None,
        current_role: str,
    ) -> None:
        return None

    async def sync_turn(
        self,
        *,
        session: object,
        raw_new_messages: list[dict[str, object]],
        persisted_new_messages: list[dict[str, object]],
        final_content: str | None,
    ) -> None:
        return None

    async def on_pre_compress(
        self,
        *,
        session: object,
        pending_messages: list[dict[str, object]],
    ) -> None:
        return None

    async def on_memory_write(self, *, action: str, target: str, content: str) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    def get_config_schema(self) -> list[dict[str, object]]:
        return []

    def save_config(self, values: dict[str, object], loaded_config: "Config") -> None:
        provider_values = loaded_config.memory.providers.setdefault(self.name, {})
        provider_values.update(values)
