from __future__ import annotations

from pathlib import Path

import pytest

from aeloon.providers.base import LLMProvider, LLMResponse, ToolCallRequest


class _SaveMemoryProvider(LLMProvider):
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
        return LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(
                    id="call_1",
                    name="save_memory",
                    arguments={
                        "history_entry": "[2026-04-13 12:00] User prefers concise status updates.",
                        "memory_update": "## Stable facts\n\nUser prefers concise status updates.",
                    },
                )
            ],
        )

    def get_default_model(self) -> str:
        return "test-model"


class _NoToolProvider(LLMProvider):
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
        return LLMResponse(content="no-op", tool_calls=[])

    def get_default_model(self) -> str:
        return "test-model"


@pytest.mark.asyncio
async def test_local_memory_store_updates_memory_and_history(tmp_path: Path) -> None:
    from aeloon.memory.local_store import LocalMemoryStore

    store = LocalMemoryStore(
        directory=tmp_path / "memory",
        memory_file_name="MEMORY.md",
        history_file_name="HISTORY.md",
        max_failures_before_raw_archive=3,
    )

    ok = await store.consolidate(
        [
            {"role": "user", "content": "I prefer concise status updates."},
            {"role": "assistant", "content": "Noted."},
        ],
        _SaveMemoryProvider(),
        "test-model",
    )

    assert ok is True
    assert "concise status updates" in (tmp_path / "memory" / "MEMORY.md").read_text(
        encoding="utf-8"
    )
    assert "User prefers concise status updates." in (
        tmp_path / "memory" / "HISTORY.md"
    ).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_local_memory_store_raw_archives_after_repeated_failures(tmp_path: Path) -> None:
    from aeloon.memory.local_store import LocalMemoryStore

    store = LocalMemoryStore(
        directory=tmp_path / "memory",
        memory_file_name="MEMORY.md",
        history_file_name="HISTORY.md",
        max_failures_before_raw_archive=2,
    )

    first = await store.consolidate(
        [{"role": "user", "content": "first"}],
        _NoToolProvider(),
        "test-model",
    )
    second = await store.consolidate(
        [{"role": "assistant", "content": "second"}],
        _NoToolProvider(),
        "test-model",
    )

    history_text = (tmp_path / "memory" / "HISTORY.md").read_text(encoding="utf-8")

    assert first is False
    assert second is True
    assert "[RAW]" in history_text
