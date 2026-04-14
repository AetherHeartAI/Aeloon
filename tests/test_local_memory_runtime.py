from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from aeloon.core.config.schema import LocalMemoryConfig, PromptMemoryConfig
from aeloon.core.session.manager import Session, SessionManager
from aeloon.memory.types import MemoryRuntimeDeps
from aeloon.providers.base import LLMProvider, LLMResponse


class _RuntimeProvider(LLMProvider):
    def __init__(self, estimated_tokens: int) -> None:
        super().__init__()
        self.estimated_tokens = estimated_tokens

    async def chat(
        self,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, object] | None = None,
    ) -> LLMResponse:
        return LLMResponse(content="ok")

    def get_default_model(self) -> str:
        return "test-model"

    def estimate_prompt_tokens(
        self,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]] | None,
        model: str | None,
    ) -> tuple[int, str]:
        return (self.estimated_tokens, "mock")


def _make_deps(tmp_path: Path, *, estimated_tokens: int, context_window_tokens: int) -> MemoryRuntimeDeps:
    return MemoryRuntimeDeps(
        workspace=tmp_path,
        provider=_RuntimeProvider(estimated_tokens),
        model="test-model",
        sessions=SessionManager(tmp_path),
        context_window_tokens=context_window_tokens,
        build_messages=lambda **_kwargs: [],
        get_tool_definitions=lambda: [],
    )


def test_local_memory_runtime_pending_start_index_reads_local_state(tmp_path: Path) -> None:
    from aeloon.memory.local_runtime import LocalMemoryRuntime

    runtime = LocalMemoryRuntime(
        config=LocalMemoryConfig(),
        prompt_config=PromptMemoryConfig(),
        deps=_make_deps(tmp_path, estimated_tokens=10, context_window_tokens=200),
    )
    session = Session(key="cli:test")
    session.last_compacted = 3

    assert runtime.pending_start_index(session) == 3


def test_local_memory_runtime_migrates_legacy_file_state(tmp_path: Path) -> None:
    from aeloon.memory.local_runtime import LocalMemoryRuntime

    runtime = LocalMemoryRuntime(
        config=LocalMemoryConfig(),
        prompt_config=PromptMemoryConfig(),
        deps=_make_deps(tmp_path, estimated_tokens=10, context_window_tokens=200),
    )
    session = Session(key="cli:test", memory_state={"file": {"last_consolidated": 4}})

    assert runtime.pending_start_index(session) == 4
    assert session.last_compacted == 4


@pytest.mark.asyncio
async def test_local_memory_runtime_moves_prompt_start_after_compaction(tmp_path: Path) -> None:
    from aeloon.memory import local_runtime as local_runtime_module
    from aeloon.memory.local_runtime import LocalMemoryRuntime

    runtime = LocalMemoryRuntime(
        config=LocalMemoryConfig(triggerRatio=0.0, targetRatio=0.0, maxConsolidationRounds=1),
        prompt_config=PromptMemoryConfig(),
        deps=_make_deps(tmp_path, estimated_tokens=10, context_window_tokens=1),
    )
    runtime.consolidate_messages = AsyncMock(return_value=True)  # type: ignore[method-assign]
    session = Session(key="cli:test")
    session.messages = [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "two"},
        {"role": "user", "content": "three"},
    ]

    original = local_runtime_module.estimate_message_tokens
    local_runtime_module.estimate_message_tokens = MagicMock(return_value=1)
    try:
        await runtime.maybe_compact_by_tokens(session)
    finally:
        local_runtime_module.estimate_message_tokens = original

    assert runtime.pending_start_index(session) == 2


@pytest.mark.asyncio
async def test_local_memory_runtime_prepare_turn_reports_local_memory(tmp_path: Path) -> None:
    from aeloon.memory.local_runtime import LocalMemoryRuntime

    runtime = LocalMemoryRuntime(
        config=LocalMemoryConfig(triggerRatio=10.0),
        prompt_config=PromptMemoryConfig(),
        deps=_make_deps(tmp_path, estimated_tokens=10, context_window_tokens=200),
    )

    prepared = await runtime.prepare_turn(
        session=Session(key="cli:test"),
        query="hello",
        channel="cli",
        chat_id="direct",
        current_role="user",
    )

    assert prepared.runtime_lines[0] == "Memory mode: local archive"
    assert prepared.runtime_lines[1] == "Prompt memory owned by PromptMemoryStore"
    assert prepared.history_start_index == 0
