from __future__ import annotations

from pathlib import Path

import pytest

from aeloon.core.config.schema import Config
from aeloon.core.session.manager import Session, SessionManager
from aeloon.memory.types import MemoryRuntimeDeps
from aeloon.providers.base import LLMProvider, LLMResponse


class _DummyProvider(LLMProvider):
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
        return LLMResponse(content=None)

    def get_default_model(self) -> str:
        return "test-model"


def _make_deps(tmp_path: Path) -> MemoryRuntimeDeps:
    return MemoryRuntimeDeps(
        workspace=tmp_path,
        provider=_DummyProvider(),
        model="test-model",
        sessions=SessionManager(tmp_path),
        context_window_tokens=4096,
        build_messages=lambda **_kwargs: [],
        get_tool_definitions=lambda: [],
    )


def test_provider_manager_rejects_second_external_provider() -> None:
    from aeloon.memory.providers.base import MemoryProvider
    from aeloon.memory.providers.manager import ProviderManager

    class ProviderA(MemoryProvider):
        name = "a"

    class ProviderB(MemoryProvider):
        name = "b"

    manager = ProviderManager()
    manager.add_provider(ProviderA())

    with pytest.raises(ValueError, match="Only one external memory provider"):
        manager.add_provider(ProviderB())


@pytest.mark.asyncio
async def test_runtime_provider_mode_is_additive_not_replacement(
    tmp_path: Path, monkeypatch
) -> None:
    from aeloon.memory.providers.base import MemoryProvider
    from aeloon.memory.runtime import MemoryRuntime

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "MEMORY.md").write_text("Local prompt memory stays active.", encoding="utf-8")

    class FakeProvider(MemoryProvider):
        name = "openviking"

        async def prefetch(
            self,
            *,
            session: object,
            query: str,
            channel: str | None,
            chat_id: str | None,
            current_role: str,
        ) -> str:
            return "# OpenViking Recall\n\n- Provider recall hit."

        def always_skill_names(self) -> list[str]:
            return ["openviking-memory"]

    monkeypatch.setattr(
        "aeloon.memory.providers.manager.ProviderManager.build_active_provider",
        lambda self, name, config, deps: FakeProvider(),
    )

    cfg = Config.model_validate(
        {
            "memory": {
                "provider": "openviking",
                "providers": {"openviking": {"ovConfig": {"storage": {}}}},
            }
        }
    )

    runtime = MemoryRuntime(memory_config=cfg.memory, deps=_make_deps(tmp_path))
    prepared = await runtime.prepare_turn(
        session=Session(key="cli:test"),
        query="hello",
        channel="cli",
        chat_id="direct",
        current_role="user",
    )

    assert runtime.local_memory is not None
    assert any(
        "Local prompt memory stays active." in section for section in prepared.system_sections
    )
    assert prepared.recalled_context_blocks
    assert "Provider recall hit." in prepared.recalled_context_blocks[0]
    assert "openviking-memory" in prepared.always_skill_names
