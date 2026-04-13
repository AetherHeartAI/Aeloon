from __future__ import annotations

from pathlib import Path

import pytest

from aeloon.core.session.manager import Session
from aeloon.providers.base import LLMResponse, ToolCallRequest


class _FlushProvider:
    def __init__(self) -> None:
        self.messages: list[list[dict[str, object]]] = []
        self.tools: list[list[dict[str, object]] | None] = []

    async def chat_with_retry(self, *, messages, tools=None, model=None, **kwargs) -> LLMResponse:
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
async def test_file_backend_calls_flush_before_consolidation(tmp_path: Path) -> None:
    from aeloon.core.session.manager import SessionManager
    from aeloon.memory.backends.file import FileMemoryBackend, FileMemoryConfig
    from aeloon.memory.base import MemoryBackendDeps

    calls: list[tuple[str, int]] = []

    async def _flush_before_loss(*, session, pending_messages, reason) -> None:
        calls.append((reason, len(pending_messages)))

    backend = FileMemoryBackend(
        FileMemoryConfig(triggerRatio=0.0, targetRatio=0.0, maxConsolidationRounds=1),
        MemoryBackendDeps(
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
    backend.consolidate_messages = lambda messages: __import__("asyncio").sleep(0, result=True)  # type: ignore[method-assign]
    backend.estimate_session_prompt_tokens = lambda _session: (10, "test")  # type: ignore[method-assign]
    backend.pick_consolidation_boundary = lambda _session, _tokens: (2, 5)  # type: ignore[method-assign]
    session = Session(key="cli:test")
    session.messages = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "second"},
        {"role": "user", "content": "third"},
    ]

    await backend.maybe_consolidate_by_tokens(session)

    assert calls
    assert calls[0][0] == "compression"


@pytest.mark.asyncio
async def test_cli_and_gateway_flush_helpers_flush_pending_messages(tmp_path: Path) -> None:
    from aeloon.cli.flows.agent import _flush_session_before_shutdown
    from aeloon.cli.flows.gateway import _flush_cached_sessions_before_shutdown
    from aeloon.core.session.manager import SessionManager

    class _Memory:
        def __init__(self) -> None:
            self.calls: list[tuple[str, int, str]] = []

        def pending_start_index(self, _session: Session) -> int:
            return 1

        async def flush(
            self, *, session: Session, pending_messages, reason: str | None = None
        ) -> None:
            self.calls.append((session.key, len(pending_messages), str(reason)))

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

    await _flush_session_before_shutdown(loop, "cli:test", reason="cli-shutdown")
    await _flush_cached_sessions_before_shutdown(loop, reason="gateway-shutdown")

    assert loop.memory.calls == [
        ("cli:test", 1, "cli-shutdown"),
        ("cli:test", 1, "gateway-shutdown"),
    ]
