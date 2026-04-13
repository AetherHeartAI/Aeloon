"""Tests for the status line feature (Plugin SDK StatusProvider).

Covers: StatusContext, StatusSegment, StatusProviderRecord,
StatusLineManager (fallback, providers, error handling),
PluginAPI.register_status_provider, PluginRegistry commit/rollback,
and StatusPlugin registration.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from aeloon.core.agent.loop import AgentLoop
from aeloon.core.bus.queue import MessageBus
from aeloon.core.config.schema import Config
from aeloon.plugins._sdk.api import PluginAPI
from aeloon.plugins._sdk.registry import PluginRegistry, RegistrationConflictError
from aeloon.plugins._sdk.status_line import StatusLineManager
from aeloon.plugins._sdk.types import (
    StatusContext,
    StatusProviderRecord,
    StatusSegment,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_loop(tmp_path: Path) -> AgentLoop:
    """Create a minimal AgentLoop for testing."""
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.config = Config.model_validate(
        {
            "agents": {"defaults": {"model": "anthropic/claude-opus-4-5", "provider": "anthropic"}},
            "providers": {"anthropic": {"apiKey": "anthropic-key"}},
        }
    )
    loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path, model="test-model")
    object.__setattr__(
        loop.memory.local_memory,
        "maybe_compact_by_tokens",
        MagicMock(return_value=None),
    )
    return loop


def _make_api(registry: PluginRegistry, plugin_id: str = "test.plugin") -> PluginAPI:
    """Create a PluginAPI wired to a real registry."""
    from aeloon.plugins._sdk.runtime import PluginRuntime

    mock_loop = MagicMock()
    mock_loop.provider = MagicMock()
    mock_loop.model = "test-model"
    runtime = PluginRuntime(
        agent_loop=mock_loop,
        plugin_id=plugin_id,
        config={},
        storage_base=Path("/tmp/test-storage"),
    )
    return PluginAPI(
        plugin_id=plugin_id,
        version="0.1.0",
        config={},
        runtime=runtime,
        registry=registry,
    )


# ---------------------------------------------------------------------------
# StatusContext
# ---------------------------------------------------------------------------


class TestStatusContext:
    def test_frozen(self) -> None:
        ctx = StatusContext(
            session_key="cli:direct",
            channel="cli",
            model="test-model",
            context_tokens_used=100,
            context_tokens_total=1000,
            terminal_width=80,
        )
        with pytest.raises(AttributeError):
            ctx.model = "other"  # type: ignore[misc]

    def test_fields(self) -> None:
        ctx = StatusContext(
            session_key="cli:direct",
            channel="cli",
            model="gpt-4",
            context_tokens_used=50,
            context_tokens_total=200,
            terminal_width=120,
        )
        assert ctx.session_key == "cli:direct"
        assert ctx.channel == "cli"
        assert ctx.model == "gpt-4"
        assert ctx.context_tokens_used == 50
        assert ctx.context_tokens_total == 200
        assert ctx.terminal_width == 120


# ---------------------------------------------------------------------------
# StatusSegment
# ---------------------------------------------------------------------------


class TestStatusSegment:
    def test_defaults(self) -> None:
        seg = StatusSegment(text="hello")
        assert seg.text == "hello"
        assert seg.style == ""
        assert seg.priority == 0

    def test_custom(self) -> None:
        seg = StatusSegment(text="warning", style="bold ansired", priority=10)
        assert seg.style == "bold ansired"
        assert seg.priority == 10


# ---------------------------------------------------------------------------
# StatusLineManager
# ---------------------------------------------------------------------------


class TestStatusLineManager:
    def test_fallback_without_registry(self, tmp_path: Path) -> None:
        """Before plugin boot, manager shows default Model + Context."""
        loop = _make_loop(tmp_path)
        mgr = StatusLineManager(loop)

        result = mgr.build_toolbar("cli", "direct")

        # Should be a list of (style, text) tuples
        text = "".join(part[1] for part in result)
        assert "Model:" in text
        assert "test-model" in text
        assert "Context:" in text

    def test_fallback_context_ratio_coloring(self, tmp_path: Path) -> None:
        """Fallback shows red when context >= 90%."""
        loop = _make_loop(tmp_path)
        session = loop.sessions.get_or_create("cli:direct")
        session.messages = [{"role": "user", "content": "x" * 1000}]
        loop.context_window_tokens = 65536

        mgr = StatusLineManager(loop)
        with patch.object(loop.memory, "estimate_session_prompt_tokens", return_value=(60000, "mock")):
            result = mgr.build_toolbar("cli", "direct")

        # Find the context part — should have ansired style
        styles = [part[0] for part in result]
        assert any("ansired" in s for s in styles)

    def test_with_single_provider(self, tmp_path: Path) -> None:
        """Manager aggregates segments from a registered provider."""
        loop = _make_loop(tmp_path)
        mgr = StatusLineManager(loop)

        registry = PluginRegistry()
        registry.commit_plugin(
            "test.plugin",
            status_providers=[
                StatusProviderRecord(
                    plugin_id="test.plugin",
                    name="simple",
                    provider=lambda ctx: [StatusSegment(text=f"[{ctx.model}]", priority=5)],
                    priority=0,
                )
            ],
        )
        mgr.set_registry(registry)

        result = mgr.build_toolbar("cli", "direct")
        text = "".join(part[1] for part in result)
        assert "[test-model]" in text

    def test_with_multiple_providers(self, tmp_path: Path) -> None:
        """Multiple providers are sorted by priority (highest first)."""
        loop = _make_loop(tmp_path)
        mgr = StatusLineManager(loop)

        registry = PluginRegistry()
        registry.commit_plugin(
            "test.plugin",
            status_providers=[
                StatusProviderRecord(
                    plugin_id="test.plugin",
                    name="low",
                    provider=lambda ctx: StatusSegment(text="LOW", priority=1),
                    priority=0,
                ),
                StatusProviderRecord(
                    plugin_id="test.plugin",
                    name="high",
                    provider=lambda ctx: StatusSegment(text="HIGH", priority=10),
                    priority=0,
                ),
            ],
        )
        mgr.set_registry(registry)

        result = mgr.build_toolbar("cli", "direct")
        text = "".join(part[1] for part in result)
        assert text.index("HIGH") < text.index("LOW")

    def test_provider_returning_string(self, tmp_path: Path) -> None:
        """Provider can return a bare string instead of StatusSegment."""
        loop = _make_loop(tmp_path)
        mgr = StatusLineManager(loop)

        registry = PluginRegistry()
        registry.commit_plugin(
            "test.plugin",
            status_providers=[
                StatusProviderRecord(
                    plugin_id="test.plugin",
                    name="string_provider",
                    provider=lambda ctx: "plain text",
                    priority=0,
                )
            ],
        )
        mgr.set_registry(registry)

        result = mgr.build_toolbar("cli", "direct")
        text = "".join(part[1] for part in result)
        assert "plain text" in text

    def test_provider_returning_single_segment(self, tmp_path: Path) -> None:
        """Provider can return a single StatusSegment (not a list)."""
        loop = _make_loop(tmp_path)
        mgr = StatusLineManager(loop)

        registry = PluginRegistry()
        registry.commit_plugin(
            "test.plugin",
            status_providers=[
                StatusProviderRecord(
                    plugin_id="test.plugin",
                    name="single_seg",
                    provider=lambda ctx: StatusSegment(text="single", style="bold"),
                    priority=0,
                )
            ],
        )
        mgr.set_registry(registry)

        result = mgr.build_toolbar("cli", "direct")
        text = "".join(part[1] for part in result)
        assert "single" in text

    def test_failing_provider_is_skipped(self, tmp_path: Path) -> None:
        """A failing provider does not crash the toolbar."""
        loop = _make_loop(tmp_path)
        mgr = StatusLineManager(loop)

        def _good(ctx: StatusContext) -> list[StatusSegment]:
            return [StatusSegment(text="OK")]

        def _bad(ctx: StatusContext) -> list[StatusSegment]:
            raise RuntimeError("boom")

        registry = PluginRegistry()
        registry.commit_plugin(
            "test.good",
            status_providers=[
                StatusProviderRecord(plugin_id="test.good", name="good", provider=_good, priority=5)
            ],
        )
        registry.commit_plugin(
            "test.bad",
            status_providers=[
                StatusProviderRecord(plugin_id="test.bad", name="bad", provider=_bad, priority=0)
            ],
        )
        mgr.set_registry(registry)

        result = mgr.build_toolbar("cli", "direct")
        text = "".join(part[1] for part in result)
        assert "OK" in text

    def test_empty_provider_result_uses_fallback(self, tmp_path: Path) -> None:
        """If providers return empty lists, fallback is shown."""
        loop = _make_loop(tmp_path)
        mgr = StatusLineManager(loop)

        registry = PluginRegistry()
        registry.commit_plugin(
            "test.plugin",
            status_providers=[
                StatusProviderRecord(
                    plugin_id="test.plugin",
                    name="empty",
                    provider=lambda ctx: [],
                    priority=0,
                )
            ],
        )
        mgr.set_registry(registry)

        result = mgr.build_toolbar("cli", "direct")
        text = "".join(part[1] for part in result)
        # Should fall back to default since no segments were contributed
        assert "Model:" in text
        assert "Context:" in text


# ---------------------------------------------------------------------------
# PluginAPI.register_status_provider
# ---------------------------------------------------------------------------


class TestPluginAPIStatusProvider:
    def test_register_and_commit(self) -> None:
        registry = PluginRegistry()
        api = _make_api(registry)

        def my_provider(ctx: StatusContext) -> str:
            return "test"

        api.register_status_provider("my_status", my_provider, priority=5)
        api._commit()

        providers = registry.status_providers
        assert len(providers) == 1
        assert providers[0].name == "my_status"
        assert providers[0].priority == 5
        assert providers[0].plugin_id == "test.plugin"

    def test_register_idempotent(self) -> None:
        """Re-registering the same name replaces the previous entry."""
        registry = PluginRegistry()
        api = _make_api(registry)

        api.register_status_provider("my_status", lambda ctx: "v1")
        api.register_status_provider("my_status", lambda ctx: "v2")
        api._commit()

        providers = registry.status_providers
        assert len(providers) == 1

    def test_clear_pending(self) -> None:
        registry = PluginRegistry()
        api = _make_api(registry)

        api.register_status_provider("my_status", lambda ctx: "test")
        api._clear_pending()
        api._commit()

        # Nothing should have been committed
        assert len(registry.status_providers) == 0


# ---------------------------------------------------------------------------
# PluginRegistry — status provider commit/rollback
# ---------------------------------------------------------------------------


class TestRegistryStatusProviders:
    def test_commit_and_query(self) -> None:
        registry = PluginRegistry()
        record = StatusProviderRecord(
            plugin_id="p1",
            name="status",
            provider=lambda ctx: "ok",
            priority=3,
        )
        registry.commit_plugin("p1", status_providers=[record])

        providers = registry.status_providers
        assert len(providers) == 1
        assert providers[0].name == "status"

    def test_conflict_detection(self) -> None:
        registry = PluginRegistry()
        rec1 = StatusProviderRecord(plugin_id="p1", name="status", provider=lambda ctx: "v1")
        rec2 = StatusProviderRecord(plugin_id="p2", name="status", provider=lambda ctx: "v2")
        registry.commit_plugin("p1", status_providers=[rec1])

        with pytest.raises(RegistrationConflictError, match="already registered"):
            registry.commit_plugin("p2", status_providers=[rec2])

    def test_rollback_removes_providers(self) -> None:
        registry = PluginRegistry()
        rec = StatusProviderRecord(plugin_id="p1", name="status", provider=lambda ctx: "ok")
        registry.commit_plugin("p1", status_providers=[rec])
        assert len(registry.status_providers) == 1

        registry.rollback_plugin("p1")
        assert len(registry.status_providers) == 0

    def test_ordered_by_priority(self) -> None:
        registry = PluginRegistry()
        registry.commit_plugin(
            "p1",
            status_providers=[
                StatusProviderRecord(
                    plugin_id="p1", name="low", provider=lambda ctx: "L", priority=1
                ),
                StatusProviderRecord(
                    plugin_id="p1", name="high", provider=lambda ctx: "H", priority=10
                ),
                StatusProviderRecord(
                    plugin_id="p1", name="mid", provider=lambda ctx: "M", priority=5
                ),
            ],
        )

        names = [p.name for p in registry.status_providers]
        assert names == ["high", "mid", "low"]


# ---------------------------------------------------------------------------
# StatusPlugin (built-in plugin)
# ---------------------------------------------------------------------------


class TestStatusPlugin:
    def test_registers_provider(self) -> None:
        from aeloon.plugins.StatusPannel.plugin import StatusPlugin

        registry = PluginRegistry()
        api = _make_api(registry, plugin_id="aeloon.status")

        plugin = StatusPlugin()
        plugin.register(api)
        api._commit()

        providers = registry.status_providers
        assert len(providers) == 1
        assert providers[0].name == "model_context"

    def test_provider_returns_segments(self) -> None:
        from aeloon.plugins.StatusPannel.plugin import StatusPlugin

        plugin = StatusPlugin()
        ctx = StatusContext(
            session_key="cli:direct",
            channel="cli",
            model="claude-opus-4-5",
            context_tokens_used=5000,
            context_tokens_total=65536,
            terminal_width=80,
        )

        segments = plugin._get_status(ctx)
        assert isinstance(segments, list)
        assert len(segments) >= 2

        text = " ".join(s.text for s in segments)
        assert "claude-opus-4-5" in text
        assert "5000" in text
        assert "65536" in text

    def test_provider_context_styling_high_ratio(self) -> None:
        from aeloon.plugins.StatusPannel.plugin import StatusPlugin

        plugin = StatusPlugin()
        ctx = StatusContext(
            session_key="cli:direct",
            channel="cli",
            model="test",
            context_tokens_used=60000,
            context_tokens_total=65536,
            terminal_width=80,
        )

        segments = plugin._get_status(ctx)
        # Context segment should have red style at 91% usage
        context_seg = [s for s in segments if "Context" in s.text][0]
        assert "ansired" in context_seg.style

    def test_provider_truncates_long_model_name(self) -> None:
        from aeloon.plugins.StatusPannel.plugin import StatusPlugin

        plugin = StatusPlugin()
        ctx = StatusContext(
            session_key="cli:direct",
            channel="cli",
            model="a" * 50,
            context_tokens_used=0,
            context_tokens_total=1000,
            terminal_width=40,
        )

        segments = plugin._get_status(ctx)
        model_seg = [s for s in segments if "Model" in s.text][0]
        assert len(model_seg.text) <= 50  # truncated
