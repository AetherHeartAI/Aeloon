from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from aeloon.core.session.manager import SessionManager
from aeloon.memory.base import (
    MemoryBackend,
    MemoryBackendConfig,
    MemoryBackendDeps,
    PreparedMemoryContext,
)
from aeloon.memory.errors import InvalidMemoryBackendClassError
from aeloon.memory.registry import build_backend, register_backend, resolve_backend_class
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


class ClassPathBackendConfig(MemoryBackendConfig):
    label: str


class ClassPathBackend(MemoryBackend):
    backend_name = "class-path-test"
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


def _make_deps(tmp_path: Path) -> MemoryBackendDeps:
    return MemoryBackendDeps(
        workspace=tmp_path,
        provider=DummyProvider(),
        model="test-model",
        sessions=SessionManager(tmp_path),
        context_window_tokens=4096,
        build_messages=lambda *args, **kwargs: [],
        get_tool_definitions=lambda: [],
    )


def test_memory_public_api_exports_registration_symbols() -> None:
    from aeloon.memory import (
        InvalidMemoryBackendClassError as ExportedInvalidMemoryBackendClassError,
        MissingMemoryBackendDependencyError,
        UnknownMemoryBackendError,
        register_backend as exported_register_backend,
    )

    assert callable(exported_register_backend)
    assert ExportedInvalidMemoryBackendClassError is InvalidMemoryBackendClassError
    assert issubclass(MissingMemoryBackendDependencyError, Exception)
    assert issubclass(UnknownMemoryBackendError, Exception)


def test_register_backend_allows_last_definition_to_win() -> None:
    class FirstBackend(MemoryBackend):
        backend_name = "duplicate-registration-test"
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
            return PreparedMemoryContext(runtime_lines=["first"])

        async def after_turn(
            self,
            *,
            session: object,
            raw_new_messages: list[dict[str, object]],
            persisted_new_messages: list[dict[str, object]],
            final_content: str | None,
        ) -> None:
            return None

    class SecondBackend(MemoryBackend):
        backend_name = "duplicate-registration-test"
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
            return PreparedMemoryContext(runtime_lines=["second"])

        async def after_turn(
            self,
            *,
            session: object,
            raw_new_messages: list[dict[str, object]],
            persisted_new_messages: list[dict[str, object]],
            final_content: str | None,
        ) -> None:
            return None

    register_backend(FirstBackend)
    register_backend(SecondBackend)

    assert resolve_backend_class("duplicate-registration-test", {}) is SecondBackend


def test_resolve_backend_class_rejects_malformed_class_path() -> None:
    with pytest.raises(InvalidMemoryBackendClassError, match="Invalid memory backend class path"):
        resolve_backend_class("ignored", {"classPath": "badpath"})


def test_resolve_backend_class_accepts_class_path_compat_key(tmp_path: Path) -> None:
    backend = build_backend(
        "ignored",
        {
            "class_path": f"{__name__}.ClassPathBackend",
            "label": "ok",
        },
        _make_deps(tmp_path),
    )

    assert isinstance(backend, ClassPathBackend)
    assert backend.config.label == "ok"
