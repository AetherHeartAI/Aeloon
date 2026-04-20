from __future__ import annotations

from pathlib import Path

import pytest

from aeloon.core.session.manager import Session
from aeloon.providers.base import LLMProvider, LLMResponse, ToolCallRequest


class _FlushProvider(LLMProvider):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[list[dict[str, object]]] = []
        self.tools: list[list[dict[str, object]] | None] = []

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
        self.messages.append(messages)
        self.tools.append(tools)
        return LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(
                    id="call_1",
                    name="memory",
                    arguments={
                        "action": "add",
                        "target": "user",
                        "content": "Prefers concise status updates.",
                    },
                )
            ],
        )

    def get_default_model(self) -> str:
        return "test-model"


@pytest.mark.asyncio
async def test_memory_flush_coordinator_writes_prompt_memory(tmp_path: Path) -> None:
    from aeloon.core.config.schema import PromptMemoryConfig
    from aeloon.memory.flush import MemoryFlushCoordinator
    from aeloon.memory.prompt_store import PromptMemoryStore

    store = PromptMemoryStore(tmp_path, PromptMemoryConfig())
    provider = _FlushProvider()
    coordinator = MemoryFlushCoordinator(provider=provider, model="test-model", prompt_store=store)
    session = Session(key="cli:test")
    session.messages = [
        {"role": "user", "content": "I prefer concise status updates."},
        {"role": "assistant", "content": "Noted."},
    ]

    await coordinator.flush(
        session=session,
        pending_messages=list(session.messages),
        reason="new-session",
    )

    user_text = (tmp_path / "memory" / "USER.md").read_text(encoding="utf-8")
    assert "Prefers concise status updates." in user_text
    assert provider.messages
    assert provider.tools[0]


@pytest.mark.asyncio
async def test_local_memory_runtime_calls_flush_before_compaction(tmp_path: Path) -> None:
    from aeloon.core.config.schema import LocalMemoryConfig, PromptMemoryConfig
    from aeloon.core.session.manager import SessionManager
    from aeloon.memory import local_runtime as local_runtime_module
    from aeloon.memory.local_runtime import LocalMemoryRuntime
    from aeloon.memory.types import MemoryRuntimeDeps

    calls: list[tuple[str, int]] = []

    async def _flush_before_loss(*, session, pending_messages, reason) -> None:
        calls.append((reason, len(pending_messages)))

    runtime = LocalMemoryRuntime(
        config=LocalMemoryConfig(triggerRatio=0.0, targetRatio=0.0, maxConsolidationRounds=1),
        prompt_config=PromptMemoryConfig(),
        deps=MemoryRuntimeDeps(
            workspace=tmp_path,
            provider=_FlushProvider(),
            model="test-model",
            sessions=SessionManager(tmp_path),
            context_window_tokens=1,
            build_messages=lambda **_kwargs: [],
            get_tool_definitions=lambda: [],
            flush_before_loss=_flush_before_loss,
        ),
    )
    runtime.consolidate_messages = (  # type: ignore[assignment,method-assign]
        lambda messages, output_summary="": __import__("asyncio").sleep(0, result=True)
    )
    runtime.estimate_session_prompt_tokens = lambda _session: (10, "test")  # type: ignore[assignment,method-assign]
    runtime.pick_compaction_boundary = lambda _session, _tokens: (2, 5)  # type: ignore[assignment,method-assign]
    session = Session(key="cli:test")
    session.messages = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "second"},
        {"role": "user", "content": "third"},
    ]

    original = local_runtime_module.estimate_message_tokens
    local_runtime_module.estimate_message_tokens = lambda _message: 1  # type: ignore[assignment]
    try:
        await runtime.maybe_compact_by_tokens(session)
    finally:
        local_runtime_module.estimate_message_tokens = original

    assert calls
    assert calls[0][0] == "compression"


@pytest.mark.asyncio
async def test_cli_and_gateway_flush_helpers_flush_pending_messages(tmp_path: Path) -> None:
    from aeloon.cli.flows.agent import _finalize_session_before_shutdown
    from aeloon.cli.flows.gateway import _flush_cached_sessions_before_shutdown
    from aeloon.core.session.manager import SessionManager

    class _Memory:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, int, str]] = []

        def pending_start_index(self, _session: Session) -> int:
            return 1

        async def finalize_session(
            self, *, session: Session, pending_messages, reason: str | None = None
        ) -> None:
            self.calls.append(("finalize", session.key, len(pending_messages), str(reason)))

        async def flush(
            self, *, session: Session, pending_messages, reason: str | None = None
        ) -> None:
            self.calls.append(("flush", session.key, len(pending_messages), str(reason)))

    class _Loop:
        def __init__(self) -> None:
            self.sessions = SessionManager(tmp_path)
            self.memory = _Memory()

    loop = _Loop()
    session = loop.sessions.get_or_create("cli:test")
    session.messages = [
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "new"},
    ]
    loop.sessions.save(session)

    await _finalize_session_before_shutdown(loop, "cli:test", reason="cli-shutdown")
    await _flush_cached_sessions_before_shutdown(loop, reason="gateway-shutdown")

    assert loop.memory.calls == [
        ("finalize", "cli:test", 1, "cli-shutdown"),
        ("flush", "cli:test", 1, "gateway-shutdown"),
    ]
