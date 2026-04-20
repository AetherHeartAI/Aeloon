"""Additive OpenViking provider."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aeloon.core.config.schema import Config
from aeloon.memory.providers.base import MemoryProvider
from aeloon.memory.providers.openviking_import import (
    load_openviking_seed_config,
    resolve_openviking_config_path,
)
from aeloon.memory.providers.openviking_service import OpenVikingProviderConfig, OpenVikingService
from aeloon.memory.providers.openviking_tools import (
    VikingAddResourceTool,
    VikingBrowseTool,
    VikingReadTool,
    VikingRememberTool,
    VikingSearchTool,
)
from aeloon.memory.types import MemoryRuntimeDeps, MessagePayload

if TYPE_CHECKING:
    from aeloon.core.agent.tools.base import Tool

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
    {
        "key": "mode",
        "description": "OpenViking mode",
        "default": "embedded",
        "choices": ["embedded", "http"],
    },
    {
        "key": "configPath",
        "description": "OpenViking config",
        "default": "~/.openviking/ov.conf",
    },
]


class OpenVikingProvider(MemoryProvider):
    name = "openviking"

    def __init__(self, config: dict[str, object], deps: MemoryRuntimeDeps):
        self.config = OpenVikingProviderConfig.model_validate(config)
        self.service = OpenVikingService(self.config, deps)

    def system_prompt_block(self) -> str:
        return (
            "# OpenViking Knowledge Base\n"
            "Active. Use viking_search to find information, viking_read for details "
            "(abstract/overview/full), viking_browse to explore, "
            "viking_remember to store facts, and viking_add_resource to ingest URLs/docs."
        )

    def build_tools(self) -> list["Tool"]:
        return [
            VikingSearchTool(self.service),
            VikingReadTool(self.service),
            VikingBrowseTool(self.service),
            VikingRememberTool(self.service),
            VikingAddResourceTool(self.service),
        ]

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
        return await self.service.build_recall_section(session=session, query=query)

    async def queue_prefetch(
        self,
        *,
        session: object,
        query: str,
        channel: str | None,
        chat_id: str | None,
        current_role: str,
    ) -> None:
        del channel, chat_id, current_role
        await self.service.queue_prefetch(session=session, query=query)

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
        reason: str | None = None,
    ) -> None:
        if reason not in {None, "compression"}:
            return
        await self.service.archive_pending_slice(
            session=session,
            pending_messages=pending_messages,
        )

    async def on_memory_write(
        self,
        *,
        action: str,
        target: str,
        content: str,
        session_key: str | None = None,
    ) -> None:
        await self.service.mirror_prompt_memory_write(
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
        await self.service.finalize_session(
            session=session,
            pending_messages=pending_messages,
            reason=reason,
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
        provider_values.setdefault("mode", "embedded")
        provider_values.setdefault("ovConfig", {"storage": {}})

    @classmethod
    def prepare_setup_values(
        cls,
        values: dict[str, object],
    ) -> tuple[dict[str, object], list[str]]:
        prepared = dict(values)
        raw_config_path = prepared.get("configPath")
        config_path = resolve_openviking_config_path(
            raw_config_path if isinstance(raw_config_path, str) else None
        )
        if not config_path.exists():
            raise ValueError(f"OpenViking config file not found: {config_path}")
        try:
            imported = load_openviking_seed_config(config_path)
        except (OSError, ValueError) as exc:
            raise ValueError(f"Failed to load OpenViking config: {exc}") from exc

        prepared["configPath"] = str(config_path)
        prepared["ovConfig"] = imported
        return prepared, [
            f"Imported OpenViking config from {config_path}",
            f"Mode: {prepared.get('mode', 'embedded')}",
        ]

    @classmethod
    def status_lines(cls, config: dict[str, object]) -> list[str]:
        lines = [f"Mode: {config.get('mode', 'embedded')}"]
        config_source = config.get("configPath")
        if isinstance(config_source, str) and config_source:
            lines.append(f"Config source: {config_source}")
        return lines

    def get_config_schema(self) -> list[dict[str, object]]:
        return self.config_schema()

    def save_config(self, values: dict[str, object], loaded_config: Config) -> None:
        self.save_setup_values(values, loaded_config)
