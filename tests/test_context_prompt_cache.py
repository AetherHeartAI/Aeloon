"""Tests for cache-friendly prompt construction."""

from __future__ import annotations

import datetime as datetime_module
from datetime import datetime as real_datetime
from importlib.resources import files as pkg_files
from pathlib import Path

import pytest

from aeloon.core.agent.context import ContextBuilder
from aeloon.core.config.schema import MemoryConfig
from aeloon.core.session.manager import Session, SessionManager
from aeloon.memory.runtime import MemoryRuntime
from aeloon.memory.types import MemoryRuntimeDeps, TurnMemoryContext
from aeloon.providers.base import LLMProvider, LLMResponse
from aeloon.utils.helpers import sync_workspace_templates


class _FakeDatetime(real_datetime):
    current = real_datetime(2026, 2, 24, 13, 59)

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return cls.current


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


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


def test_bootstrap_files_are_backed_by_templates() -> None:
    template_dir = pkg_files("aeloon") / "resources" / "templates"

    for filename in ContextBuilder.BOOTSTRAP_FILES:
        assert (template_dir / filename).is_file(), f"missing bootstrap template: {filename}"


def test_non_file_template_sync_skips_markdown_memory_files(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)

    sync_workspace_templates(workspace, include_file_memory=False)

    assert (workspace / "AGENTS.md").is_file()
    assert not (workspace / "memory" / "MEMORY.md").exists()
    assert not (workspace / "memory" / "HISTORY.md").exists()


def test_template_sync_with_file_memory_keeps_history_retired(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)

    sync_workspace_templates(workspace)

    assert (workspace / "memory" / "MEMORY.md").is_file()
    assert (workspace / "memory" / "USER.md").is_file()
    assert not (workspace / "memory" / "HISTORY.md").exists()


def test_system_prompt_stays_stable_when_clock_changes(tmp_path, monkeypatch) -> None:
    """System prompt should not change just because wall clock minute changes."""
    monkeypatch.setattr(datetime_module, "datetime", _FakeDatetime)

    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    _FakeDatetime.current = real_datetime(2026, 2, 24, 13, 59)
    prompt1 = builder.build_system_prompt()

    _FakeDatetime.current = real_datetime(2026, 2, 24, 14, 0)
    prompt2 = builder.build_system_prompt()

    assert prompt1 == prompt2


def test_context_builder_accepts_backend_runtime_lines(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    prompt = builder.build_system_prompt(
        runtime_lines=["Memory backend: fake"],
        extra_system_sections=["# Memory Recall\n\nnone"],
        extra_always_skills=[],
    )

    assert "Memory backend: fake" in prompt
    assert "# Memory Recall" in prompt
    assert "MEMORY.md" not in prompt
    assert "HISTORY.md" not in prompt


def test_context_builder_includes_plugin_catalog_when_set(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    builder.set_plugin_catalog("# Plugins\n\n## demo\nA demo plugin")
    prompt = builder.build_system_prompt()

    assert "# Plugins" in prompt
    assert "## demo" in prompt


class _FakePromptLocalMemory:
    async def prepare_turn(
        self,
        *,
        session: object,
        query: str,
        channel: str | None,
        chat_id: str | None,
        current_role: str,
    ) -> TurnMemoryContext:
        return TurnMemoryContext(
            system_sections=["# Memory Recall\n\nnone"],
            runtime_lines=["Memory mode: fake"],
            always_skill_names=[],
        )

    async def after_turn(
        self,
        *,
        session: object,
        query: str,
        raw_new_messages: list[dict[str, object]],
        persisted_new_messages: list[dict[str, object]],
        final_content: str | None,
    ) -> None:
        return None

    def pending_start_index(self, session: object) -> int:
        return 0

    def estimate_session_prompt_tokens(self, session: Session) -> tuple[int, str]:
        return (0, "none")

    async def on_new_session(
        self,
        *,
        session: object,
        pending_messages: list[dict[str, object]],
    ) -> None:
        return None

    async def maybe_compact_by_tokens(self, session: Session) -> None:
        return None

    async def close(self) -> None:
        return None


async def _make_prepared_context(tmp_path: Path) -> TurnMemoryContext:
    workspace = _make_workspace(tmp_path)
    deps = MemoryRuntimeDeps(
        workspace=workspace,
        provider=_DummyProvider(),
        model="test-model",
        sessions=SessionManager(workspace),
        context_window_tokens=4096,
        build_messages=lambda **_kwargs: [],
        get_tool_definitions=lambda: [],
    )
    runtime = MemoryRuntime(
        memory_config=MemoryConfig.model_validate(
            {
                "prompt": {"enabled": False},
                "archive": {"enabled": False},
                "flush": {"enabled": False},
            }
        ),
        deps=deps,
        local_memory=_FakePromptLocalMemory(),
        prompt_memory=None,
        session_archive=None,
        flush_coordinator=None,
    )
    try:
        return await runtime.prepare_turn(
            session=Session(key="cli:test"),
            query="hello",
            channel="cli",
            chat_id="direct",
            current_role="user",
        )
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_fake_backend_prompt_stays_free_of_file_memory_defaults(tmp_path) -> None:
    workspace = tmp_path / "fake-backend"
    workspace.mkdir(parents=True)
    builder = ContextBuilder(workspace)
    prepared = await _make_prepared_context(tmp_path)

    prompt = builder.build_system_prompt(
        extra_system_sections=prepared.system_sections,
        runtime_lines=prepared.runtime_lines,
        extra_always_skills=prepared.always_skill_names,
    )

    assert "Memory mode: fake" in prompt
    assert "# Memory Recall" in prompt
    assert "MEMORY.md" not in prompt
    assert "HISTORY.md" not in prompt
    assert "### Skill: memory" not in prompt
    assert "Long-term facts" not in prompt


def test_runtime_context_is_separate_untrusted_user_message(tmp_path) -> None:
    """Runtime metadata should be merged with the user message."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    messages = builder.build_messages(
        history=[],
        current_message="Return exactly: OK",
        channel="cli",
        chat_id="direct",
    )

    assert messages[0]["role"] == "system"
    assert "## Current Session" not in messages[0]["content"]

    # Runtime context is now merged with user message into a single message
    assert messages[-1]["role"] == "user"
    user_content = messages[-1]["content"]
    assert isinstance(user_content, str)
    assert ContextBuilder._RUNTIME_CONTEXT_TAG in user_content
    assert "Current Time:" in user_content
    assert "Channel: cli" in user_content
    assert "Chat ID: direct" in user_content
    assert "Return exactly: OK" in user_content
