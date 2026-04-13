"""Additive OpenViking provider wrapper."""

from __future__ import annotations

from aeloon.core.config.schema import Config
from aeloon.memory.backends.openviking import OpenVikingMemoryBackend, OpenVikingMemoryConfig
from aeloon.memory.base import MemoryBackendDeps
from aeloon.memory.providers.base import MemoryProvider

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
    """Wrap the existing OpenViking backend as an additive provider."""

    name = "openviking"

    def __init__(self, config: dict[str, object], deps: MemoryBackendDeps):
        self.config = OpenVikingMemoryConfig.model_validate(config)
        self.backend = OpenVikingMemoryBackend(self.config, deps)

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
        prepared = await self.backend.prepare_turn(
            session=session,
            query=query,
            channel=channel,
            chat_id=chat_id,
            current_role=current_role,
        )
        return "\n\n".join(prepared.system_sections)

    async def sync_turn(
        self,
        *,
        session: object,
        raw_new_messages: list[dict[str, object]],
        persisted_new_messages: list[dict[str, object]],
        final_content: str | None,
    ) -> None:
        await self.backend.after_turn(
            session=session,
            raw_new_messages=raw_new_messages,
            persisted_new_messages=persisted_new_messages,
            final_content=final_content,
        )

    async def on_pre_compress(
        self,
        *,
        session: object,
        pending_messages: list[dict[str, object]],
    ) -> None:
        await self.backend.on_new_session(session=session, pending_messages=pending_messages)

    async def shutdown(self) -> None:
        await self.backend.close()

    @classmethod
    def config_schema(cls) -> list[dict[str, object]]:
        return list(OPENVIKING_CONFIG_SCHEMA)

    @classmethod
    def save_setup_values(cls, values: dict[str, object], loaded_config: Config) -> None:
        provider_values = loaded_config.memory.providers.setdefault(cls.name, {})
        provider_values.update(values)

    def get_config_schema(self) -> list[dict[str, object]]:
        return self.config_schema()

    def save_config(self, values: dict[str, object], loaded_config: Config) -> None:
        self.save_setup_values(values, loaded_config)
