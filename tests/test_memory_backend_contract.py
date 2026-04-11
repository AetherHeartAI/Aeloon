from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from aeloon.providers.base import LLMProvider, LLMResponse


class DummyProvider(LLMProvider):
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> LLMResponse:
        return LLMResponse(content=None)

    def get_default_model(self) -> str:
        return "test-model"


class ClassPathBackendConfig:
    @classmethod
    def model_validate(cls, raw_cfg: dict[str, Any]) -> "ClassPathBackendConfig":
        instance = cls()
        instance.class_path = raw_cfg.get("classPath")
        instance.label = raw_cfg["label"]
        return instance


def test_backend_registry_registers_and_builds_backend(tmp_path: Path) -> None:
    from aeloon.memory.base import (
        MemoryBackend,
        MemoryBackendConfig,
        MemoryBackendDeps,
        PreparedMemoryContext,
    )
    from aeloon.memory.registry import build_backend, register_backend, resolve_backend_class

    class DummyBackendConfig(MemoryBackendConfig):
        foo: str

    @register_backend
    class DummyBackend(MemoryBackend):
        backend_name = "dummy"
        config_model = DummyBackendConfig

        async def prepare_turn(
            self,
            *,
            session: Any,
            query: str,
            channel: str | None,
            chat_id: str | None,
            current_role: str,
        ) -> PreparedMemoryContext:
            return PreparedMemoryContext()

        async def after_turn(
            self,
            *,
            session: Any,
            raw_new_messages: list[dict[str, Any]],
            persisted_new_messages: list[dict[str, Any]],
            final_content: str | None,
        ) -> None:
            return None

    class ClassPathBackend(MemoryBackend):
        backend_name = "class-path"
        config_model = ClassPathBackendConfig

        async def prepare_turn(
            self,
            *,
            session: Any,
            query: str,
            channel: str | None,
            chat_id: str | None,
            current_role: str,
        ) -> PreparedMemoryContext:
            return PreparedMemoryContext()

        async def after_turn(
            self,
            *,
            session: Any,
            raw_new_messages: list[dict[str, Any]],
            persisted_new_messages: list[dict[str, Any]],
            final_content: str | None,
        ) -> None:
            return None

    globals()["ClassPathBackend"] = ClassPathBackend

    deps = MemoryBackendDeps(
        workspace=tmp_path,
        provider=DummyProvider(),
        model="test-model",
        sessions=object(),
        context_window_tokens=4096,
        build_messages=lambda *args, **kwargs: [],
        get_tool_definitions=lambda: [],
    )

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
