from __future__ import annotations

from pathlib import Path

import pytest

from aeloon.core.session.manager import SessionManager
from aeloon.providers.base import LLMProvider, LLMResponse


class DummyProvider(LLMProvider):
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


def _make_deps(tmp_path: Path):
    from aeloon.memory.base import MemoryBackendDeps

    return MemoryBackendDeps(
        workspace=tmp_path,
        provider=DummyProvider(),
        model="test-model",
        sessions=SessionManager(tmp_path),
        context_window_tokens=4096,
        build_messages=lambda *args, **kwargs: [],
        get_tool_definitions=lambda: [],
    )


def test_backend_registry_registers_and_builds_backend(tmp_path: Path) -> None:
    from aeloon.memory.base import (
        MemoryBackend,
        MemoryBackendConfig,
        PreparedMemoryContext,
    )
    from aeloon.memory.registry import build_backend, register_backend, resolve_backend_class

    class DummyBackendConfig(MemoryBackendConfig):
        foo: str

    class ClassPathBackendConfig(MemoryBackendConfig):
        label: str

    @register_backend
    class DummyBackend(MemoryBackend):
        backend_name = "dummy"
        config_model = DummyBackendConfig
        config: DummyBackendConfig

        async def prepare_turn(
            self,
            *,
            session: object,
            query: str,
            channel: str | None,
            chat_id: str | None,
            current_role: str,
        ) -> PreparedMemoryContext:
            return PreparedMemoryContext()

        async def after_turn(
            self,
            *,
            session: object,
            raw_new_messages: list[dict[str, object]],
            persisted_new_messages: list[dict[str, object]],
            final_content: str | None,
        ) -> None:
            return None

    class ClassPathBackend(MemoryBackend):
        backend_name = "class-path"
        config_model = ClassPathBackendConfig
        config: ClassPathBackendConfig

        async def prepare_turn(
            self,
            *,
            session: object,
            query: str,
            channel: str | None,
            chat_id: str | None,
            current_role: str,
        ) -> PreparedMemoryContext:
            return PreparedMemoryContext()

        async def after_turn(
            self,
            *,
            session: object,
            raw_new_messages: list[dict[str, object]],
            persisted_new_messages: list[dict[str, object]],
            final_content: str | None,
        ) -> None:
            return None

    globals()["ClassPathBackend"] = ClassPathBackend

    deps = _make_deps(tmp_path)

    assert resolve_backend_class("dummy", {}) is DummyBackend

    backend = build_backend("dummy", {"foo": "bar"}, deps)
    assert isinstance(backend, DummyBackend)
    assert backend.config.foo == "bar"

    class_path_backend = build_backend(
        "ignored",
        {
            "classPath": f"{__name__}.ClassPathBackend",
            "label": "via-class-path",
        },
        deps,
    )

    assert isinstance(class_path_backend, ClassPathBackend)
    assert class_path_backend.config.label == "via-class-path"


def test_unknown_backend_raises_value_error() -> None:
    from aeloon.memory.registry import resolve_backend_class

    with pytest.raises(ValueError, match="Unknown memory backend"):
        resolve_backend_class("missing", {})


def test_memory_backend_defaults_hidden_skill_names_to_empty_list(tmp_path: Path) -> None:
    from aeloon.memory.base import (
        MemoryBackend,
        MemoryBackendConfig,
        PreparedMemoryContext,
    )

    class DummyBackend(MemoryBackend):
        backend_name = "contract-hidden-skills"
        config_model = MemoryBackendConfig

        async def prepare_turn(
            self,
            *,
            session: object,
            query: str,
            channel: str | None,
            chat_id: str | None,
            current_role: str,
        ) -> PreparedMemoryContext:
            return PreparedMemoryContext()

        async def after_turn(
            self,
            *,
            session: object,
            raw_new_messages: list[dict[str, object]],
            persisted_new_messages: list[dict[str, object]],
            final_content: str | None,
        ) -> None:
            return None

    deps = _make_deps(tmp_path)
    backend = DummyBackend(MemoryBackendConfig(), deps)

    assert backend.hidden_skill_names == []


def test_memory_backend_pending_start_index_requires_override(tmp_path: Path) -> None:
    from aeloon.memory.base import (
        MemoryBackend,
        MemoryBackendConfig,
        PreparedMemoryContext,
    )

    class DummyBackend(MemoryBackend):
        backend_name = "contract-pending-start"
        config_model = MemoryBackendConfig

        async def prepare_turn(
            self,
            *,
            session: object,
            query: str,
            channel: str | None,
            chat_id: str | None,
            current_role: str,
        ) -> PreparedMemoryContext:
            return PreparedMemoryContext()

        async def after_turn(
            self,
            *,
            session: object,
            raw_new_messages: list[dict[str, object]],
            persisted_new_messages: list[dict[str, object]],
            final_content: str | None,
        ) -> None:
            return None

    deps = _make_deps(tmp_path)
    backend = DummyBackend(MemoryBackendConfig(), deps)

    with pytest.raises(NotImplementedError, match="must implement pending_start_index"):
        backend.pending_start_index(object())
