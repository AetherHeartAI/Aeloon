"""Additive memory provider base types."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from aeloon.core.agent.tools.base import Tool
    from aeloon.core.config.schema import Config
    from aeloon.memory.types import MessagePayload


class MemoryProvider:
    """Base class for one additive external memory provider."""

    name: ClassVar[str]

    @classmethod
    def is_available(cls, loaded_config: "Config | None" = None) -> bool:
        return True

    def system_prompt_block(self) -> str:
        return ""

    def build_tools(self) -> list["Tool"]:
        return []

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
        reason: str | None = None,
    ) -> None:
        return None

    async def on_memory_write(
        self,
        *,
        action: str,
        target: str,
        content: str,
        session_key: str | None = None,
    ) -> None:
        return None

    async def on_session_end(
        self,
        *,
        session: object,
        pending_messages: list["MessagePayload"],
        reason: str | None = None,
    ) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    @classmethod
    def config_schema(cls) -> list[dict[str, object]]:
        return []

    @classmethod
    def save_setup_values(cls, values: dict[str, object], loaded_config: "Config") -> None:
        provider_values = loaded_config.memory.providers.setdefault(cls.name, {})
        provider_values.update(values)

    @classmethod
    def prepare_setup_values(
        cls,
        values: dict[str, object],
    ) -> tuple[dict[str, object], list[str]]:
        return dict(values), []

    @classmethod
    def status_lines(cls, config: dict[str, object]) -> list[str]:
        return []

    def get_config_schema(self) -> list[dict[str, object]]:
        return self.config_schema()

    def save_config(self, values: dict[str, object], loaded_config: "Config") -> None:
        self.save_setup_values(values, loaded_config)
