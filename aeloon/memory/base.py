"""Base contracts for memory backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from aeloon.core.session.manager import SessionManager
from aeloon.memory.types import MessagePayload, ToolDefinition
from aeloon.providers.base import LLMProvider


class MemoryBackendConfig(BaseModel):
    """Base config model for one memory backend."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    class_path: str | None = Field(
        default=None,
        alias="classPath",
        description="Canonical key is classPath; class_path remains supported for compatibility.",
    )


@dataclass(slots=True)
class MemoryBackendDeps:
    """Dependencies injected into backends at construction time."""

    workspace: Path
    provider: LLMProvider
    model: str
    sessions: SessionManager
    context_window_tokens: int
    build_messages: Callable[..., list[MessagePayload]]
    get_tool_definitions: Callable[[], list[ToolDefinition]]


@dataclass(slots=True)
class PreparedMemoryContext:
    """Backend-provided prompt/runtime data for a turn."""

    history_start_index: int = 0
    system_sections: list[str] = field(default_factory=list)
    runtime_lines: list[str] = field(default_factory=list)
    always_skill_names: list[str] = field(default_factory=list)
    recalled_context_blocks: list[str] = field(default_factory=list)


class MemoryBackend(ABC):
    """Abstract base class for backend-owned memory behavior."""

    backend_name: ClassVar[str]
    config_model: ClassVar[type[MemoryBackendConfig]] = MemoryBackendConfig
    hidden_skill_names: ClassVar[list[str]] = []

    def __init__(self, config: MemoryBackendConfig, deps: MemoryBackendDeps):
        self.config = config
        self.deps = deps

    @abstractmethod
    async def prepare_turn(
        self,
        *,
        session: object,
        query: str,
        channel: str | None,
        chat_id: str | None,
        current_role: str,
    ) -> PreparedMemoryContext:
        """Return backend-specific prompt/runtime context for a turn."""

    @abstractmethod
    async def after_turn(
        self,
        *,
        session: object,
        raw_new_messages: list[MessagePayload],
        persisted_new_messages: list[MessagePayload],
        final_content: str | None,
    ) -> None:
        """Run backend-specific post-turn persistence."""

    def pending_start_index(self, session: object) -> int:
        """Return the first still-pending message index for /new archival."""
        raise NotImplementedError(f"{type(self).__name__} must implement pending_start_index()")

    async def on_new_session(
        self,
        *,
        session: object,
        pending_messages: list[MessagePayload],
    ) -> None:
        """Handle session reset archival behavior."""
        return None

    async def close(self) -> None:
        """Release backend resources and finish pending work."""
        return None
