from unittest.mock import AsyncMock, MagicMock

import pytest

from aeloon.core.agent.context import ContextBuilder
from aeloon.core.agent.loop import AgentLoop
from aeloon.core.bus.queue import MessageBus
from aeloon.core.session.manager import Session, SessionManager
from aeloon.memory.base import PreparedMemoryContext
from aeloon.providers.base import LLMResponse


def _mk_manager(tmp_path) -> SessionManager:
    return SessionManager(tmp_path)


def test_save_turn_skips_multimodal_user_when_only_runtime_context(tmp_path) -> None:
    manager = _mk_manager(tmp_path)
    session = Session(key="test:runtime-only")
    runtime = ContextBuilder._RUNTIME_CONTEXT_TAG + "\nCurrent Time: now (UTC)"

    manager.save_turn(
        session,
        [{"role": "user", "content": [{"type": "text", "text": runtime}]}],
        skip=0,
        runtime_context_tag=ContextBuilder._RUNTIME_CONTEXT_TAG,
    )
    assert session.messages == []


def test_save_turn_keeps_image_placeholder_with_path_after_runtime_strip(tmp_path) -> None:
    manager = _mk_manager(tmp_path)
    session = Session(key="test:image")
    runtime = ContextBuilder._RUNTIME_CONTEXT_TAG + "\nCurrent Time: now (UTC)"

    manager.save_turn(
        session,
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": runtime},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,abc"},
                        "_meta": {"path": "/media/feishu/photo.jpg"},
                    },
                ],
            }
        ],
        skip=0,
        runtime_context_tag=ContextBuilder._RUNTIME_CONTEXT_TAG,
    )
    assert session.messages[0]["content"] == [
        {"type": "text", "text": "[image: /media/feishu/photo.jpg]"}
    ]


def test_save_turn_keeps_image_placeholder_without_meta(tmp_path) -> None:
    manager = _mk_manager(tmp_path)
    session = Session(key="test:image-no-meta")
    runtime = ContextBuilder._RUNTIME_CONTEXT_TAG + "\nCurrent Time: now (UTC)"

    manager.save_turn(
        session,
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": runtime},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                ],
            }
        ],
        skip=0,
        runtime_context_tag=ContextBuilder._RUNTIME_CONTEXT_TAG,
    )
    assert session.messages[0]["content"] == [{"type": "text", "text": "[image]"}]


def test_save_turn_keeps_tool_results_under_16k(tmp_path) -> None:
    manager = _mk_manager(tmp_path)
    session = Session(key="test:tool-result")
    content = "x" * 12_000

    manager.save_turn(
        session,
        [{"role": "tool", "tool_call_id": "call_1", "name": "read_file", "content": content}],
        skip=0,
    )

    assert session.messages[0]["content"] == content


class _FakeMemoryManager:
    def __init__(self) -> None:
        self.prepare_called = False
        self.after_turn_called = False

    async def prepare_turn(self, **kwargs) -> PreparedMemoryContext:
        self.prepare_called = True
        return PreparedMemoryContext(
            system_sections=["# Memory Recall\n\nnone"],
            runtime_lines=["Memory backend: fake"],
            always_skill_names=[],
            history_start_index=0,
        )

    async def after_turn(self, **kwargs) -> None:
        self.after_turn_called = True

    def pending_start_index(self, session: Session) -> int:
        return 0

    async def on_new_session(self, **kwargs) -> None:
        return None

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_loop_uses_memory_manager_prepare_turn_before_llm(tmp_path) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="ok", tool_calls=[]))

    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
    )
    loop.tools.get_definitions = MagicMock(return_value=[])
    loop.memory = _FakeMemoryManager()

    await loop.process_direct("hello", session_key="cli:test")

    assert loop.memory.prepare_called is True
