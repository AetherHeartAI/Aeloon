"""Tests for /profile command and profile message emission."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from aeloon.core.agent.loop import AgentLoop
from aeloon.core.bus.events import InboundMessage
from aeloon.core.bus.queue import MessageBus
from aeloon.providers.base import LLMResponse


def _make_loop(tmp_path) -> tuple[AgentLoop, MessageBus]:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path, model="test-model")
    loop.memory_consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=None)
    return loop, bus


@pytest.mark.asyncio
async def test_profile_slash_command_toggle(tmp_path) -> None:
    loop, _ = _make_loop(tmp_path)

    on_msg = InboundMessage(channel="cli", sender_id="u", chat_id="c", content="/profile on")
    off_msg = InboundMessage(channel="cli", sender_id="u", chat_id="c", content="/profile off")

    on_resp = await loop._process_message(on_msg)
    off_resp = await loop._process_message(off_msg)

    assert on_resp is not None
    assert "enabled" in on_resp.content.lower()
    assert off_resp is not None
    assert "disabled" in off_resp.content.lower()
    assert loop.profiler.enabled is False


@pytest.mark.asyncio
async def test_profile_slash_command_status(tmp_path) -> None:
    loop, _ = _make_loop(tmp_path)
    msg = InboundMessage(channel="cli", sender_id="u", chat_id="c", content="/profile")

    resp = await loop._process_message(msg)

    assert resp is not None
    assert "profiling is disabled" in resp.content.lower()
    assert "no profiling report available yet" in resp.content.lower()


@pytest.mark.asyncio
async def test_profile_slash_command_status_uses_deep_profile_report(tmp_path) -> None:
    loop, _ = _make_loop(tmp_path)
    loop.profiler.enabled = True
    loop.runtime_settings.output_mode = "deep-profile"
    from aeloon.core.agent.profiler import ProfileReport

    loop.profiler._last_report = ProfileReport()
    loop.profiler.report_deep_profile = MagicMock(return_value="Deep Profile Report")
    msg = InboundMessage(channel="cli", sender_id="u", chat_id="c", content="/profile")

    resp = await loop._process_message(msg)

    assert resp is not None
    assert "profiling is enabled" in resp.content.lower()
    assert "current profile mode: deep-profile." in resp.content.lower()
    assert "Deep Profile Report" in resp.content
    assert loop.profiler.report_deep_profile.call_count >= 1


@pytest.mark.asyncio
async def test_profile_enabled_emits_metadata_report(tmp_path) -> None:
    loop, bus = _make_loop(tmp_path)
    loop.profiler.enabled = True
    loop.provider.chat_with_retry = AsyncMock(
        return_value=LLMResponse(content="hello", tool_calls=[])
    )

    msg = InboundMessage(channel="cli", sender_id="u", chat_id="c", content="hello")
    response = await loop._process_message(msg)

    assert response is not None
    # Consume "Thinking..." progress emitted before the first LLM call
    thinking_msg = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
    assert thinking_msg.content == "Thinking..."
    profile_msg = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
    assert profile_msg.metadata.get("_profile") is True
    assert profile_msg.metadata.get("_progress") is True
    assert "Profile Report" in profile_msg.content
