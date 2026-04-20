from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aeloon.core.agent.loop import AgentLoop
from aeloon.core.bus.events import InboundMessage
from aeloon.core.bus.queue import MessageBus
from aeloon.core.config.schema import Config


def _make_loop(tmp_path) -> tuple[AgentLoop, MessageBus]:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.config = Config.model_validate(
        {
            "agents": {"defaults": {"model": "anthropic/claude-opus-4-5"}},
            "providers": {
                "anthropic": {"apiKey": "anthropic-key"},
                "ollama": {"apiBase": "http://localhost:11434"},
            },
        }
    )
    loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path, model="test-model")
    object.__setattr__(
        loop.memory.local_memory,
        "maybe_compact_by_tokens",
        AsyncMock(return_value=None),
    )
    return loop, bus


@pytest.mark.asyncio
async def test_setting_help_and_output_mode_toggle(tmp_path) -> None:
    loop, _ = _make_loop(tmp_path)
    persisted = Config()

    with (
        patch("aeloon.core.config.loader.load_config", return_value=persisted),
        patch("aeloon.core.config.loader.save_config") as save_config,
    ):
        help_resp = await loop._process_message(
            InboundMessage(channel="cli", sender_id="u", chat_id="c", content="/setting")
        )
        mode_resp = await loop._process_message(
            InboundMessage(
                channel="cli", sender_id="u", chat_id="c", content="/setting output profile"
            )
        )

    assert help_resp is not None
    assert "output: normal" in help_resp.content.lower()
    assert mode_resp is not None
    assert "output mode set to profile" in mode_resp.content.lower()
    assert loop.runtime_settings.output_mode == "profile"
    assert loop.profiler.enabled is True
    assert persisted.agents.defaults.output_mode == "profile"
    save_config.assert_called_once()


@pytest.mark.asyncio
async def test_setting_output_profile_enables_profiler(tmp_path) -> None:
    loop, _ = _make_loop(tmp_path)
    persisted = Config()

    with (
        patch("aeloon.core.config.loader.load_config", return_value=persisted),
        patch("aeloon.core.config.loader.save_config"),
    ):
        response = await loop._process_message(
            InboundMessage(
                channel="cli", sender_id="u", chat_id="c", content="/setting output profile"
            )
        )

    assert response is not None
    assert "output mode set to profile" in response.content.lower()
    assert loop.runtime_settings.show_profile is True
    assert loop.profiler.enabled is True


@pytest.mark.asyncio
async def test_setting_output_deep_profile_enables_deep_profile_mode(tmp_path) -> None:
    loop, _ = _make_loop(tmp_path)
    persisted = Config()

    with (
        patch("aeloon.core.config.loader.load_config", return_value=persisted),
        patch("aeloon.core.config.loader.save_config"),
    ):
        response = await loop._process_message(
            InboundMessage(
                channel="cli",
                sender_id="u",
                chat_id="c",
                content="/setting output deep-profile",
            )
        )

    assert response is not None
    assert "output mode set to deep-profile" in response.content.lower()
    assert loop.runtime_settings.output_mode == "deep-profile"
    assert loop.runtime_settings.show_deep_profile is True
    assert loop.profiler.enabled is True
    assert persisted.agents.defaults.output_mode == "deep-profile"


@pytest.mark.asyncio
async def test_setting_output_equals_form_is_supported(tmp_path) -> None:
    loop, _ = _make_loop(tmp_path)
    persisted = Config()

    with (
        patch("aeloon.core.config.loader.load_config", return_value=persisted),
        patch("aeloon.core.config.loader.save_config"),
    ):
        response = await loop._process_message(
            InboundMessage(
                channel="cli", sender_id="u", chat_id="c", content="/setting output=profile"
            )
        )

    assert response is not None
    assert "output mode set to profile" in response.content.lower()
    assert loop.runtime_settings.output_mode == "profile"


@pytest.mark.asyncio
async def test_setting_fast_toggle_persists_to_config(tmp_path) -> None:
    loop, _ = _make_loop(tmp_path)
    persisted = Config()

    with (
        patch("aeloon.core.config.loader.load_config", return_value=persisted),
        patch("aeloon.core.config.loader.save_config") as save_config,
    ):
        response = await loop._process_message(
            InboundMessage(channel="cli", sender_id="u", chat_id="c", content="/setting fast on")
        )

    assert response is not None
    assert "fast mode set to on and saved to config" in response.content.lower()
    assert loop.runtime_settings.fast is True
    assert persisted.agents.defaults.fast is True
    save_config.assert_called_once()


@pytest.mark.asyncio
async def test_setting_fast_equals_form_is_supported(tmp_path) -> None:
    loop, _ = _make_loop(tmp_path)
    persisted = Config()

    with (
        patch("aeloon.core.config.loader.load_config", return_value=persisted),
        patch("aeloon.core.config.loader.save_config"),
    ):
        response = await loop._process_message(
            InboundMessage(channel="cli", sender_id="u", chat_id="c", content="/setting fast=on")
        )

    assert response is not None
    assert "fast mode set to on and saved to config" in response.content.lower()
    assert loop.runtime_settings.fast is True


def test_agent_loop_uses_configured_output_mode_on_startup(tmp_path) -> None:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        output_mode="profile",
    )

    assert loop.runtime_settings.output_mode == "profile"
    assert loop.profiler.enabled is True


def test_agent_loop_uses_configured_deep_profile_mode_on_startup(tmp_path) -> None:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        output_mode="deep-profile",
    )

    assert loop.runtime_settings.output_mode == "deep-profile"
    assert loop.runtime_settings.show_deep_profile is True
    assert loop.profiler.enabled is True


@pytest.mark.asyncio
async def test_setting_models_lists_current_and_configured_models(tmp_path) -> None:
    loop, _ = _make_loop(tmp_path)

    response = await loop._process_message(
        InboundMessage(channel="cli", sender_id="u", chat_id="c", content="/setting models")
    )

    assert response is not None
    assert "available models:" in response.content.lower()
    assert "- test-model" in response.content
    assert "anthropic/" in response.content
    assert "ollama/" in response.content


def test_profiler_hotspot_report_keeps_top_80_percent() -> None:
    from aeloon.core.agent.profiler import AgentProfiler, ProfileReport, ProfileSample

    profiler = AgentProfiler(enabled=True)
    profiler._last_report = ProfileReport(
        total_ms=200.0,
        llm_calls=[ProfileSample(label="main", duration_ms=120.0)],
        tool_calls=[
            ProfileSample(label="read_file", duration_ms=50.0),
            ProfileSample(label="bash", duration_ms=20.0),
            ProfileSample(label="skill_x", duration_ms=10.0),
        ],
    )

    report = profiler.report_top_heavy()

    assert "Coverage: 170.0 / 200.0 ms (85%)" in report
    assert "LLM: main" in report
    assert "Tool: read_file" in report
    assert "Tool: bash" not in report


def test_profiler_deep_profile_keeps_all_llm_and_tools_plus_top_remaining() -> None:
    from aeloon.core.agent.profiler import AgentProfiler, ProfileReport, ProfileSample

    profiler = AgentProfiler(enabled=True)
    profiler._last_report = ProfileReport(
        total_ms=310.0,
        llm_calls=[ProfileSample(label="main", duration_ms=120.0)],
        tool_calls=[
            ProfileSample(label="read_file", duration_ms=50.0),
            ProfileSample(label="bash", duration_ms=20.0),
        ],
        context_build_ms=70.0,
        session_load_ms=30.0,
        session_save_ms=20.0,
    )

    report = profiler.report_deep_profile()

    assert "LLM Calls:" in report
    assert "1. main: 120.0 ms" in report
    assert "Tool Calls:" in report
    assert "1. read_file: 50.0 ms" in report
    assert "2. bash: 20.0 ms" in report
    assert "Other Hotspots:" in report
    assert "Coverage: 100.0 / 120.0 ms (83%)" in report
    assert "1. Context Build: 70.0 ms" in report
    assert "2. Session Load: 30.0 ms" in report
    assert "Session Save: 20.0 ms" not in report
