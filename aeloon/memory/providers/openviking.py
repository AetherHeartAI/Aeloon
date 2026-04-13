"""Additive OpenViking provider."""

from __future__ import annotations

from aeloon.core.config.schema import Config
from aeloon.memory.providers.base import MemoryProvider
from aeloon.memory.providers.openviking_service import OpenVikingProviderConfig, OpenVikingService
from aeloon.memory.types import MemoryRuntimeDeps, MessagePayload

OPENVIKING_CONFIG_SCHEMA: list[dict[str, object]] = [
    {
        "key": "endpoint",
        "description": "OpenViking endpoint",
        "default": "http://127.0.0.1:1933",
        "env_var": "OPENVIKING_ENDPOINT",
    },
    {
        "key": "api_key",
        "description": "OpenViking API key",
        "secret": True,
        "default": "",
        "env_var": "OPENVIKING_API_KEY",
    },
    {
        "key": "storageSubdir",
        "description": "Workspace storage subdirectory",
        "default": "openviking_memory",
    },
    {
        "key": "searchMode",
        "description": "Recall mode",
        "default": "search",
        "choices": ["search", "find"],
    },
]


class OpenVikingProvider(MemoryProvider):
    name = "openviking"

    def __init__(self, config: dict[str, object], deps: MemoryRuntimeDeps):
        self.config = OpenVikingProviderConfig.model_validate(config)
        self.service = OpenVikingService(self.config, deps)

    def always_skill_names(self) -> list[str]:
        return ["openviking-memory"]

    async def prefetch(
        self,
        *,
        session: object,
        query: str,
        channel: str | None,
        chat_id: str | None,
        current_role: str,
    ) -> str:
        return await self.service.build_recall_section(session=session, query=query)

    async def sync_turn(
        self,
        *,
        session: object,
        raw_new_messages: list[MessagePayload],
        persisted_new_messages: list[MessagePayload],
        final_content: str | None,
    ) -> None:
        await self.service.mirror_turn(
            session=session,
            persisted_new_messages=persisted_new_messages,
        )

    async def on_pre_compress(
        self,
        *,
        session: object,
        pending_messages: list[MessagePayload],
    ) -> None:
        await self.service.archive_pending_slice(
            session=session,
            pending_messages=pending_messages,
        )

    async def shutdown(self) -> None:
        await self.service.shutdown()

    @classmethod
    def config_schema(cls) -> list[dict[str, object]]:
        return list(OPENVIKING_CONFIG_SCHEMA)

    @classmethod
    def save_setup_values(cls, values: dict[str, object], loaded_config: Config) -> None:
        provider_values = loaded_config.memory.providers.setdefault(cls.name, {})
        provider_values.update(values)
        provider_values.setdefault("ovConfig", {"storage": {}})

    def get_config_schema(self) -> list[dict[str, object]]:
        return self.config_schema()

    def save_config(self, values: dict[str, object], loaded_config: Config) -> None:
        self.save_setup_values(values, loaded_config)
