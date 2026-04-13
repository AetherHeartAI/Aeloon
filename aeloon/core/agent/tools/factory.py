"""Tool registration helpers shared by agent and subagent."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from aeloon.core.agent.skills import BUILTIN_SKILLS_DIR
from aeloon.core.agent.tools.filesystem import (
    EditFileTool,
    ListDirTool,
    ReadFileTool,
    WriteFileTool,
)
from aeloon.core.agent.tools.registry import ToolRegistry
from aeloon.core.agent.tools.shell import ExecTool
from aeloon.core.agent.tools.web import WebFetchTool, WebSearchTool

if TYPE_CHECKING:
    from aeloon.core.config.schema import ExecToolConfig, WebSearchConfig
    from aeloon.memory.archive_service import SessionArchiveService
    from aeloon.memory.prompt_store import PromptMemoryStore
    from aeloon.memory.providers.manager import ProviderManager
    from aeloon.providers.base import LLMProvider


def register_core_tools(
    registry: ToolRegistry,
    *,
    workspace: Path,
    restrict_to_workspace: bool,
    exec_config: "ExecToolConfig",
    web_search_config: "WebSearchConfig",
    web_proxy: str | None,
    prompt_memory_store: "PromptMemoryStore | None" = None,
    session_archive_service: "SessionArchiveService | None" = None,
    provider_manager: "ProviderManager | None" = None,
    provider: "LLMProvider | None" = None,
    model: str | None = None,
) -> None:
    """Register the shared core tools set."""
    allowed_dir = workspace if restrict_to_workspace else None
    extra_read = [BUILTIN_SKILLS_DIR] if allowed_dir else None

    registry.register(
        ReadFileTool(workspace=workspace, allowed_dir=allowed_dir, extra_allowed_dirs=extra_read)
    )
    for cls in (WriteFileTool, EditFileTool, ListDirTool):
        registry.register(cls(workspace=workspace, allowed_dir=allowed_dir))

    registry.register(
        ExecTool(
            working_dir=str(workspace),
            timeout=exec_config.timeout,
            restrict_to_workspace=restrict_to_workspace,
            path_append=exec_config.path_append,
        )
    )
    registry.register(WebSearchTool(config=web_search_config, proxy=web_proxy))

    registry.register(
        WebFetchTool(
            proxy=web_proxy,
            fetch_timeout_s=web_search_config.fetch_timeout_s,
            fallback_fetch_timeout_s=web_search_config.fallback_fetch_timeout_s,
        )
    )
    if prompt_memory_store is not None:
        from aeloon.core.agent.tools.memory import MemoryTool

        registry.register(
            MemoryTool(
                prompt_memory_store,
                on_write=provider_manager.on_memory_write if provider_manager is not None else None,
            )
        )
    if session_archive_service is not None and provider is not None and model is not None:
        from aeloon.core.agent.tools.session_search import SessionSearchTool

        registry.register(
            SessionSearchTool(
                service=session_archive_service,
                provider=provider,
                model=model,
            )
        )
