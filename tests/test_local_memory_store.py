from __future__ import annotations

from pathlib import Path

import pytest

from aeloon.memory.local_store import LocalMemoryStore
from aeloon.providers.base import LLMProvider, LLMResponse, ToolCallRequest


class _ArchiveOnlyProvider(LLMProvider):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

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
        self.calls += 1
        return LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(
                    id="call_1",
                    name="save_history",
                    arguments={
                        "history_entry": "summary entry",
                    },
                )
            ],
        )

    def get_default_model(self) -> str:
        return "test-model"


@pytest.mark.asyncio
async def test_local_consolidation_only_appends_history(tmp_path: Path) -> None:
    provider = _ArchiveOnlyProvider()
    store = LocalMemoryStore(
        directory=tmp_path / "memory",
        history_file_name="HISTORY.md",
        max_failures_before_raw_archive=3,
    )
    memory_file = tmp_path / "memory" / "MEMORY.md"
    memory_file.parent.mkdir(parents=True, exist_ok=True)
    memory_file.write_text("Existing prompt memory\n", encoding="utf-8")

    success = await store.consolidate(
        [{"role": "user", "content": "remember this"}],
        provider,
        "test-model",
    )

    assert success is True
    assert provider.calls == 0
    assert memory_file.read_text(encoding="utf-8") == "Existing prompt memory\n"
    assert not (tmp_path / "memory" / "HISTORY.md").exists()
