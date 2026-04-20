"""Shared types for the memory subsystem."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from aeloon.core.session.manager import SessionManager
from aeloon.providers.base import LLMProvider

MessagePayload = dict[str, object]
ToolDefinition = dict[str, object]


@dataclass(slots=True)
class MemoryRuntimeDeps:
    workspace: Path
    provider: LLMProvider
    model: str
    sessions: SessionManager
    context_window_tokens: int
    build_messages: Callable[..., list[MessagePayload]]
    get_tool_definitions: Callable[[], list[ToolDefinition]]
    flush_before_loss: Callable[..., Awaitable[None]] | None = None


@dataclass(slots=True)
class TurnMemoryContext:
    history_start_index: int = 0
    system_sections: list[str] = field(default_factory=list)
    runtime_lines: list[str] = field(default_factory=list)
    always_skill_names: list[str] = field(default_factory=list)
    recalled_context_blocks: list[str] = field(default_factory=list)
