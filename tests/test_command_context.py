"""Tests for the unified CommandContext."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from aeloon.plugins._sdk.types import CommandContext


def _make_agent_loop():
    loop = MagicMock()
    loop.bus = MagicMock()
    loop.bus.publish_outbound = MagicMock()
    loop.sessions = MagicMock()
    loop.memory_consolidator = MagicMock()
    loop.profiler = MagicMock()
    loop.runtime_settings = MagicMock()
    loop.model = "test-model"
    loop.context_window_tokens = 8192
    loop.provider = MagicMock()
    loop.channels_config = MagicMock()
    loop.plugin_manager = MagicMock()
    loop._schedule_background = MagicMock()
    return loop


def _make_message(channel="cli", chat_id="c1", sender_id="u1", content="/test"):
    return SimpleNamespace(
        channel=channel,
        chat_id=chat_id,
        sender_id=sender_id,
        content=content,
        session_key=f"{channel}:{chat_id}",
        metadata={"foo": "bar"},
    )


class TestCommandContextFromDispatch:
    def test_basic_construction(self):
        ctx = CommandContext.from_dispatch(_make_agent_loop(), _make_message())
        assert ctx.session_key == "cli:c1"
        assert ctx.channel == "cli"
        assert ctx.chat_id == "c1"
        assert ctx.sender_id == "u1"
        assert ctx.inbound_metadata == {"foo": "bar"}
        assert ctx.is_builtin is False
        assert ctx.plugin_id is None
        assert ctx.plugin_config == {}

    def test_plugin_context(self):
        ctx = CommandContext.from_dispatch(
            _make_agent_loop(),
            _make_message(),
            plugin_id="my_plugin",
            plugin_config={"key": "val"},
        )
        assert ctx.plugin_id == "my_plugin"
        assert ctx.plugin_config == {"key": "val"}

    def test_builtin_flag(self):
        ctx = CommandContext.from_dispatch(
            _make_agent_loop(),
            _make_message(),
            is_builtin=True,
        )
        assert ctx.is_builtin is True

    def test_internal_state_initialized(self):
        ctx = CommandContext.from_dispatch(_make_agent_loop(), _make_message())
        assert ctx._metadata == {}
        assert ctx._media == []
        assert ctx._replied is False
        assert ctx._cancelled is False
        assert ctx._restart_requested is False
        assert ctx._inbound_message is not None

    @pytest.mark.asyncio
    async def test_reply_sends_outbound(self):
        loop = _make_agent_loop()
        msg = _make_message()
        ctx = CommandContext.from_dispatch(loop, msg)
        await ctx.reply("hello")
        loop.bus.publish_outbound.assert_called_once()
        outbound = loop.bus.publish_outbound.call_args[0][0]
        assert outbound.channel == "cli"
        assert outbound.chat_id == "c1"
        assert outbound.content == "hello"

    @pytest.mark.asyncio
    async def test_send_progress_sends_outbound(self):
        loop = _make_agent_loop()
        msg = _make_message()
        ctx = CommandContext.from_dispatch(loop, msg)
        await ctx.send_progress("working...")
        loop.bus.publish_outbound.assert_called_once()
        outbound = loop.bus.publish_outbound.call_args[0][0]
        assert outbound.content == "working..."


class TestCommandContextSideEffects:
    def test_set_metadata(self):
        ctx = CommandContext.from_dispatch(_make_agent_loop(), _make_message())
        ctx.set_metadata("_session_switch", True)
        ctx.set_metadata("session_key", "target")
        assert ctx._metadata == {"_session_switch": True, "session_key": "target"}

    def test_add_media(self):
        ctx = CommandContext.from_dispatch(_make_agent_loop(), _make_message())
        ctx.add_media(["/path/to/qr.png"])
        ctx.add_media(["/path/to/image2.png"])
        assert ctx._media == ["/path/to/qr.png", "/path/to/image2.png"]

    def test_cancel_session(self):
        ctx = CommandContext.from_dispatch(_make_agent_loop(), _make_message())
        assert ctx._cancelled is False
        ctx.cancel_session()
        assert ctx._cancelled is True

    def test_restart(self):
        ctx = CommandContext.from_dispatch(_make_agent_loop(), _make_message())
        assert ctx._restart_requested is False
        ctx.restart()
        assert ctx._restart_requested is True


class TestCommandContextProxiedProperties:
    def test_sessions(self):
        loop = _make_agent_loop()
        ctx = CommandContext.from_dispatch(loop, _make_message())
        assert ctx.sessions is loop.sessions

    def test_model(self):
        loop = _make_agent_loop()
        ctx = CommandContext.from_dispatch(loop, _make_message())
        assert ctx.model == "test-model"

    def test_context_window_tokens(self):
        loop = _make_agent_loop()
        ctx = CommandContext.from_dispatch(loop, _make_message())
        assert ctx.context_window_tokens == 8192

    def test_bus(self):
        loop = _make_agent_loop()
        ctx = CommandContext.from_dispatch(loop, _make_message())
        assert ctx.bus is loop.bus

    def test_missing_agent_loop_attribute_returns_none(self):
        loop = SimpleNamespace()
        ctx = CommandContext.from_dispatch(loop, _make_message())
        assert ctx.sessions is None
        assert ctx.model == ""
        assert ctx.context_window_tokens == 0


class TestCommandContextAsBusNamespace:
    def test_as_bus_namespace(self):
        loop = _make_agent_loop()
        ctx = CommandContext.from_dispatch(loop, _make_message())
        ns = ctx.as_bus_namespace()
        assert ns.bus is loop.bus
