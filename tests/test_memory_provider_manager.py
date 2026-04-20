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


def test_memory_provider_registry_builds_registered_provider(tmp_path: Path) -> None:
    from aeloon.memory.providers.base import MemoryProvider
    from aeloon.memory.providers.registry import MemoryProviderRegistry, MemoryProviderSpec

    class FakeProvider(MemoryProvider):
        name = "fake"

        def __init__(self, config: dict[str, object], deps: MemoryRuntimeDeps):
            self.config = config
            self.deps = deps

    registry = MemoryProviderRegistry()
    registry.register(
        MemoryProviderSpec(
            name="fake",
            provider_cls=FakeProvider,
            description="fake provider",
        )
    )

    provider = registry.build("fake", {"x": 1}, _make_deps(tmp_path))

    assert isinstance(provider, FakeProvider)
    assert provider.config == {"x": 1}


def test_memory_provider_registry_rejects_unknown_provider(tmp_path: Path) -> None:
    from aeloon.memory.providers.registry import MemoryProviderRegistry

    registry = MemoryProviderRegistry()

    with pytest.raises(ValueError, match="Unknown memory provider"):
        registry.build("missing", {}, _make_deps(tmp_path))


def test_provider_manager_returns_provider_tools() -> None:
    from aeloon.core.agent.tools.base import Tool
    from aeloon.memory.providers.base import MemoryProvider
    from aeloon.memory.providers.manager import ProviderManager

    class FakeTool(Tool):
        @property
        def name(self) -> str:
            return "fake_tool"

        @property
        def description(self) -> str:
            return "fake"

        @property
        def parameters(self) -> dict[str, object]:
            return {"type": "object", "properties": {}, "required": []}

        async def execute(self, **kwargs: object) -> str:
            return "ok"

    class FakeProvider(MemoryProvider):
        name = "fake"

        def build_tools(self) -> list[Tool]:
            return [FakeTool()]

    manager = ProviderManager()
    manager.add_provider(FakeProvider())

    assert [tool.name for tool in manager.tools()] == ["fake_tool"]


@pytest.mark.asyncio
async def test_provider_manager_forwards_queue_prefetch() -> None:
    from aeloon.memory.providers.base import MemoryProvider
    from aeloon.memory.providers.manager import ProviderManager

    class FakeProvider(MemoryProvider):
        name = "fake"

        def __init__(self) -> None:
            self.calls: list[str] = []

        async def queue_prefetch(
            self,
            *,
            session: object,
            query: str,
            channel: str | None,
            chat_id: str | None,
            current_role: str,
        ) -> None:
            self.calls.append(query)

    provider = FakeProvider()
    manager = ProviderManager()
    manager.add_provider(provider)

    await manager.queue_prefetch(
        session=Session(key="cli:test"),
        query="prefetch me",
        channel="cli",
        chat_id="direct",
        current_role="user",
    )

    assert provider.calls == ["prefetch me"]


@pytest.mark.asyncio
async def test_provider_manager_forwards_session_end() -> None:
    from aeloon.memory.providers.base import MemoryProvider
    from aeloon.memory.providers.manager import ProviderManager

    class FakeProvider(MemoryProvider):
        name = "fake"

        def __init__(self) -> None:
            self.calls: list[tuple[int, str | None]] = []

        async def on_session_end(
            self,
            *,
            session: object,
            pending_messages: list[dict[str, object]],
            reason: str | None = None,
        ) -> None:
            self.calls.append((len(pending_messages), reason))

    provider = FakeProvider()
    manager = ProviderManager()
    manager.add_provider(provider)

    await manager.on_session_end(
        session=Session(key="cli:test"),
        pending_messages=[{"role": "user", "content": "hello"}],
        reason="shutdown",
    )

    assert provider.calls == [(1, "shutdown")]


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
                "archive": {"enabled": False},
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
