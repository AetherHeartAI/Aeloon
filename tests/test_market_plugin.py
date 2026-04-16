"""Tests for the Aeloon Market Plugin (SP3 — Market Plugin Migration).

Covers: config schema, manifest loading, plugin registration,
plugin activation, tool wrappers, and service lifecycle.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aeloon.plugins.MarketResearch.enrichers.article_reader import ArticleReadResult

from aeloon.core.agent.turn import TurnContext
from aeloon.plugins._sdk.api import PluginAPI
from aeloon.plugins._sdk.discovery import PluginDiscovery
from aeloon.plugins._sdk.hooks import HookDispatcher
from aeloon.plugins._sdk.loader import PluginLoader
from aeloon.plugins._sdk.manager import PluginManager
from aeloon.plugins._sdk.manifest import load_manifest
from aeloon.plugins._sdk.registry import PluginRegistry
from aeloon.plugins._sdk.runtime import PluginRuntime

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_agent_loop() -> MagicMock:
    """Mock AgentLoop with provider."""
    loop = MagicMock()
    loop.provider = MagicMock()
    loop.provider.chat = AsyncMock(return_value=MagicMock(content="test response"))
    loop.model = "test-model"
    loop.profiler = MagicMock(enabled=False)
    return loop


@pytest.fixture
def market_plugin_dir(tmp_path: Path) -> Path:
    """Create a market plugin directory with manifest."""
    plugin_path = tmp_path / "aeloon.market"
    plugin_path.mkdir()
    manifest = {
        "id": "aeloon.market",
        "name": "Market Agent",
        "version": "0.1.0",
        "description": "Market intelligence",
        "author": "AetherHeart",
        "entry": "aeloon.plugins.MarketResearch.plugin:MarketPlugin",
        "provides": {
            "commands": ["market"],
            "tools": [
                "market_collect_news",
                "market_read_article",
                "market_build_events",
                "market_analyze_events",
            ],
            "services": ["fast_news", "slow_digest"],
        },
        "requires": {"aeloon_version": ">=0.1.0"},
    }
    (plugin_path / "aeloon.plugin.json").write_text(json.dumps(manifest))
    return plugin_path


# ---------------------------------------------------------------------------
# TestMarketConfig
# ---------------------------------------------------------------------------


class TestMarketConfig:
    """Test MarketConfig Pydantic schema."""

    def test_default_values(self) -> None:
        """MarketConfig defaults are sensible."""
        from aeloon.plugins.MarketResearch.config import MarketConfig

        cfg = MarketConfig()
        assert cfg.enabled is False
        assert cfg.storage_dir == "~/.aeloon/market"
        assert cfg.llm.default_reasoning_effort == "low"

    def test_fast_news_defaults(self) -> None:
        """FastNewsServiceConfig has correct defaults."""
        from aeloon.plugins.MarketResearch.config import MarketConfig

        cfg = MarketConfig()
        assert cfg.fast_news.auto_start is False
        assert cfg.fast_news.poll_interval_seconds == 5

    def test_slow_digest_defaults(self) -> None:
        """SlowDigestServiceConfig has correct defaults."""
        from aeloon.plugins.MarketResearch.config import MarketConfig

        cfg = MarketConfig()
        assert cfg.slow_digest.auto_start is False
        assert cfg.slow_digest.period_hours == 12

    def test_custom_values(self) -> None:
        """MarketConfig accepts custom values."""
        from aeloon.plugins.MarketResearch.config import MarketConfig

        cfg = MarketConfig(
            enabled=True,
            storage_dir="/tmp/market",
        )
        assert cfg.enabled is True
        assert cfg.storage_dir == "/tmp/market"


# ---------------------------------------------------------------------------
# TestMarketManifest
# ---------------------------------------------------------------------------


class TestMarketManifest:
    """Test manifest loading."""

    def test_load_bundled_manifest(self) -> None:
        """Load the actual aeloon.plugin.json from aeloon/plugins/MarketResearch/."""
        manifest_path = (
            Path(__file__).parent.parent / "aeloon" / "plugins" / "market" / "aeloon.plugin.json"
        )
        if not manifest_path.exists():
            pytest.skip("Bundled manifest not found")
        m = load_manifest(manifest_path)
        assert m.id == "aeloon.market"
        assert m.name == "Market Agent"
        assert "market" in m.provides.commands

    def test_manifest_from_fixture(self, market_plugin_dir: Path) -> None:
        """Load manifest from test fixture."""
        m = load_manifest(market_plugin_dir / "aeloon.plugin.json")
        assert m.id == "aeloon.market"
        assert m.version == "0.1.0"
        assert m.entry == "aeloon.plugins.MarketResearch.plugin:MarketPlugin"


# ---------------------------------------------------------------------------
# TestMarketPluginRegistration
# ---------------------------------------------------------------------------


class TestMarketPluginRegistration:
    """Test MarketPlugin.register() correctly registers command, tools, services."""

    def test_register_creates_pending_records(self, mock_agent_loop: MagicMock) -> None:
        """register() populates pending commands, tools, services, CLI, and config schema."""
        from aeloon.plugins.MarketResearch.plugin import MarketPlugin

        registry = PluginRegistry()
        runtime = PluginRuntime(
            agent_loop=mock_agent_loop,
            plugin_id="aeloon.market",
            config={"enabled": True},
            storage_base=Path("/tmp"),
        )
        api = PluginAPI(
            plugin_id="aeloon.market",
            version="0.1.0",
            config={"enabled": True},
            runtime=runtime,
            registry=registry,
        )

        plugin = MarketPlugin()
        plugin.register(api)

        # Check pending records
        assert any(r.name == "market" for r in api._pending_commands)
        assert any(r.name == "market" for r in api._pending_cli)
        assert len(api._pending_config_schemas) == 1
        # Tools
        assert len(api._pending_tools) == 4
        tool_names = {r.name for r in api._pending_tools}
        assert "market_collect_news" in tool_names
        assert "market_read_article" in tool_names
        assert "market_build_events" in tool_names
        assert "market_analyze_events" in tool_names
        # Services
        assert len(api._pending_services) == 2
        service_names = {r.name for r in api._pending_services}
        assert "fast_news" in service_names
        assert "slow_digest" in service_names

    def test_commit_after_register(self, mock_agent_loop: MagicMock) -> None:
        """After commit, command and tools are in registry."""
        from aeloon.plugins.MarketResearch.plugin import MarketPlugin

        registry = PluginRegistry()
        runtime = PluginRuntime(
            agent_loop=mock_agent_loop,
            plugin_id="aeloon.market",
            config={"enabled": True},
            storage_base=Path("/tmp"),
        )
        api = PluginAPI(
            plugin_id="aeloon.market",
            version="0.1.0",
            config={"enabled": True},
            runtime=runtime,
            registry=registry,
        )

        plugin = MarketPlugin()
        plugin.register(api)
        api._commit()

        assert "market" in registry.commands
        assert registry.commands["market"].plugin_id == "aeloon.market"
        assert "market_collect_news" in registry.tools
        assert "aeloon.market.fast_news" in registry.services


# ---------------------------------------------------------------------------
# TestMarketPluginActivation
# ---------------------------------------------------------------------------


class TestMarketPluginActivation:
    """Test full plugin boot lifecycle with mocked agent loop."""

    @pytest.mark.asyncio
    async def test_boot_discovers_and_activates(
        self, market_plugin_dir: Path, mock_agent_loop: MagicMock
    ) -> None:
        """Full boot: discover → register → activate."""
        from aeloon.plugins.MarketResearch.plugin import MarketPlugin

        with patch("aeloon.plugins._sdk.loader.importlib.import_module") as mock_import:
            mod = MagicMock()
            mod.MarketPlugin = MarketPlugin
            mock_import.return_value = mod

            registry = PluginRegistry()
            discovery = PluginDiscovery(bundled_dir=market_plugin_dir.parent)
            loader = PluginLoader()
            hooks = HookDispatcher()
            manager = PluginManager(
                registry=registry,
                discovery=discovery,
                loader=loader,
                hook_dispatcher=hooks,
                agent_loop=mock_agent_loop,
                plugin_config={"aeloon.market": {"enabled": True}},
                storage_base=Path("/tmp"),
            )

            result = await manager.boot()
            assert "aeloon.market" in result.loaded
            assert "market" in registry.commands


# ---------------------------------------------------------------------------
# TestMarketToolWrappers
# ---------------------------------------------------------------------------


class TestMarketToolWrappers:
    """Test tool execute() delegates to toolkit."""

    def test_collect_news_tool_has_correct_schema(self, mock_agent_loop: MagicMock) -> None:
        """MarketCollectNewsTool has name, description, parameters."""
        from aeloon.plugins.MarketResearch.plugin import MarketPlugin
        from aeloon.plugins.MarketResearch.tools import MarketCollectNewsTool

        plugin = MarketPlugin()
        tool = MarketCollectNewsTool(plugin=plugin)
        assert tool.name == "market_collect_news"
        assert "scope" in tool.parameters["properties"]

    def test_read_article_tool_requires_url(self, mock_agent_loop: MagicMock) -> None:
        """MarketReadArticleTool requires url parameter."""
        from aeloon.plugins.MarketResearch.plugin import MarketPlugin
        from aeloon.plugins.MarketResearch.tools import MarketReadArticleTool

        plugin = MarketPlugin()
        tool = MarketReadArticleTool(plugin=plugin)
        assert tool.name == "market_read_article"
        assert "url" in tool.parameters.get("required", [])


# ---------------------------------------------------------------------------
# TestMarketServiceLifecycle
# ---------------------------------------------------------------------------


class TestMarketServiceLifecycle:
    """Test service wrappers."""

    def test_fast_news_service_is_plugin_service(self) -> None:
        """FastNewsService inherits PluginService."""
        from aeloon.plugins.MarketResearch.services import FastNewsService

        from aeloon.plugins._sdk.base import PluginService

        svc = FastNewsService()
        assert isinstance(svc, PluginService)

    def test_slow_digest_service_is_plugin_service(self) -> None:
        """SlowDigestService inherits PluginService."""
        from aeloon.plugins.MarketResearch.services import SlowDigestService

        from aeloon.plugins._sdk.base import PluginService

        svc = SlowDigestService()
        assert isinstance(svc, PluginService)

    @pytest.mark.asyncio
    async def test_stop_without_start_is_safe(self) -> None:
        """Stopping a service that hasn't started should not raise."""
        from aeloon.plugins.MarketResearch.services import FastNewsService

        svc = FastNewsService()
        await svc.stop()  # Should not raise


# ---------------------------------------------------------------------------
# TestRuntimeConfigCleanup
# ---------------------------------------------------------------------------


class TestHotlistFetcher:
    """Test hotlist batching behavior."""

    def test_fetch_one_batches_article_reads_and_preserves_rank_order(self) -> None:
        from aeloon.plugins.MarketResearch.sources.hotlists import HotlistConfig, HotlistFetcher

        fetcher = HotlistFetcher(configs=[HotlistConfig(id="wallstreetcn-hot", name="WS Hot")])
        raw_items = [
            {"title": "Title 1", "url": "https://example.com/1"},
            {"title": "Title 2", "url": "https://example.com/2"},
            {"title": "Title 3", "url": "https://example.com/3"},
        ]
        payload = json.dumps({"items": raw_items})
        starts: list[float] = []
        progress_events: list[str] = []

        class _Article:
            def __init__(self, idx: int) -> None:
                self.published_at = f"2026-04-05 10:00:0{idx}"
                self.summary = f"summary-{idx}"
                self.content_text = f"content-{idx}"

        def _read(url: str, content_limit: int | None = None, progress_cb=None):
            starts.append(time.monotonic())
            if progress_cb is not None:
                progress_cb(f"reader:{url}")
            time.sleep(0.05)
            idx = url.rsplit("/", 1)[-1]
            return _Article(int(idx))

        fetcher.article_reader = MagicMock()
        fetcher.article_reader.read.side_effect = _read

        with patch(
            "aeloon.plugins.MarketResearch.sources.hotlists.fetch_text", return_value=(payload, {})
        ):
            signals = fetcher.fetch_one(
                HotlistConfig(id="wallstreetcn-hot", name="WS Hot"),
                limit=3,
                progress_cb=progress_events.append,
            )

        assert [signal.rank for signal in signals] == [1, 2, 3]
        assert [signal.title for signal in signals] == ["Title 1", "Title 2", "Title 3"]
        assert all(signal.summary.startswith("summary-") for signal in signals)
        assert any(
            event == "Processing 3 hotlist items in batch for WS Hot" for event in progress_events
        )
        assert sum(event.startswith("Reading hotlist article") for event in progress_events) == 3
        assert max(starts) - min(starts) < 0.05


class TestArticleReader:
    """Test article reader progress output."""

    def test_read_reports_jina_result_with_duration(self) -> None:
        from aeloon.plugins.MarketResearch.enrichers.article_reader import ArticleReader

        reader = ArticleReader(timeout=1, content_limit=200, min_content_length=50)
        progress_events: list[str] = []

        with (
            patch.object(
                reader,
                "_read_local",
                return_value=ArticleReadResult(url="https://example.com/x", status="error:fail"),
            ),
            patch.object(
                reader,
                "_read_jina",
                return_value=ArticleReadResult(
                    url="https://example.com/x",
                    status="ok",
                    reader_used="jina",
                    content_text="usable content " * 10,
                    summary="usable summary",
                ),
            ),
        ):
            result = reader.read("https://example.com/x", progress_cb=progress_events.append)

        assert result.reader_used == "jina"
        assert any(
            event.startswith("Falling back to Jina reader: https://example.com/x")
            for event in progress_events
        )
        assert any(
            event.startswith("Jina reader result [ok] (")
            and event.endswith(": https://example.com/x")
            for event in progress_events
        )


class TestRuntimeConfigCleanup:
    """Test runtime_config.py plugin injection."""

    def test_inject_plugin_config_from_dict(self) -> None:
        """inject_plugin_config accepts a dict."""
        from aeloon.plugins.MarketResearch import runtime_config

        runtime_config.inject_plugin_config({"fast_news": {"poll_interval_seconds": 10}})
        section = runtime_config.runtime_section(Path("/nonexistent"), "fast_news")
        assert section["poll_interval_seconds"] == 10
        # Cleanup
        runtime_config._plugin_config = None

    def test_inject_plugin_config_from_model(self) -> None:
        """inject_plugin_config accepts a Pydantic model."""
        from aeloon.plugins.MarketResearch import runtime_config
        from aeloon.plugins.MarketResearch.config import MarketConfig

        cfg = MarketConfig(enabled=True)
        runtime_config.inject_plugin_config(cfg)
        assert runtime_config._plugin_config is not None
        assert runtime_config._plugin_config["enabled"] is True
        # Cleanup
        runtime_config._plugin_config = None

    def test_fallback_to_json_when_no_plugin_config(self, tmp_path: Path) -> None:
        """Without plugin config, runtime_section falls back to JSON file."""
        from aeloon.plugins.MarketResearch import runtime_config

        runtime_config._plugin_config = None
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "runtime.json").write_text(json.dumps({"test_section": {"key": "value"}}))
        section = runtime_config.runtime_section(tmp_path, "test_section")
        assert section["key"] == "value"


# ---------------------------------------------------------------------------
# TestLLMAdapter
# ---------------------------------------------------------------------------


class TestLLMAdapter:
    """Test PluginLLMProxy via PluginRuntime."""

    def test_runtime_exposes_llm_proxy(self, mock_agent_loop: MagicMock) -> None:
        """PluginRuntime exposes a PluginLLMProxy via .llm property."""
        from aeloon.plugins._sdk.runtime import PluginLLMProxy

        runtime = PluginRuntime(
            agent_loop=mock_agent_loop,
            plugin_id="aeloon.market",
            config={},
            storage_base=Path("/tmp"),
        )
        proxy = runtime.llm
        assert isinstance(proxy, PluginLLMProxy)


# ---------------------------------------------------------------------------
# TestMarketPluginIntegration (P3-3)
# ---------------------------------------------------------------------------


class TestMarketPluginIntegration:
    """End-to-end integration tests for Market plugin."""

    @pytest.mark.asyncio
    async def test_tool_execute_delegates_to_toolkit(self, mock_agent_loop: MagicMock) -> None:
        """MarketCollectNewsTool.execute() delegates to toolkit.collect_news()."""
        from aeloon.plugins.MarketResearch.plugin import MarketPlugin
        from aeloon.plugins.MarketResearch.tools import MarketCollectNewsTool

        plugin = MarketPlugin()
        tool = MarketCollectNewsTool(plugin=plugin)

        mock_toolkit = MagicMock()
        mock_toolkit.collect_news.return_value = {"signals": []}
        with patch.object(plugin, "_get_or_create_toolkit", return_value=mock_toolkit):
            result = await tool.execute(scope="fast_news", new_only=True, limit=5)

        mock_toolkit.collect_news.assert_called_once_with(scope="fast_news", new_only=True, limit=5)
        assert '"signals"' in result

    @pytest.mark.asyncio
    async def test_tool_execute_passes_progress_callback_when_turn_context_has_one(
        self, mock_agent_loop: MagicMock
    ) -> None:
        from aeloon.plugins.MarketResearch.plugin import MarketPlugin
        from aeloon.plugins.MarketResearch.tools import MarketCollectNewsTool

        plugin = MarketPlugin()
        tool = MarketCollectNewsTool(plugin=plugin)
        seen_progress: list[str] = []

        async def _progress(text: str, *, tool_hint: bool = False) -> None:
            seen_progress.append(text)

        tool.on_turn_start(
            TurnContext(
                channel="cli",
                chat_id="chat",
                session_key="cli:chat",
                metadata={"_on_progress_cb": _progress},
            )
        )

        def _collect_news(**kwargs: object) -> dict[str, object]:
            progress_cb = kwargs.get("progress_cb")
            assert callable(progress_cb)
            progress_cb("Collecting hotlists...")
            return {"signals": []}

        mock_toolkit = MagicMock()
        mock_toolkit.collect_news.side_effect = _collect_news
        with patch.object(plugin, "_get_or_create_toolkit", return_value=mock_toolkit):
            result = await tool.execute(scope="all", new_only=True, limit=0)

        assert seen_progress == ["Collecting hotlists..."]
        assert '"signals"' in result

    @pytest.mark.asyncio
    async def test_service_start_stop_lifecycle(self) -> None:
        """FastNewsService can be started and stopped."""
        from aeloon.plugins.MarketResearch.services import FastNewsService

        from aeloon.plugins._sdk.base import ServiceStatus
        from aeloon.plugins._sdk.manager import ServiceSupervisor
        from aeloon.plugins._sdk.types import ServicePolicy, ServiceRecord

        supervisor = ServiceSupervisor()
        record = ServiceRecord(
            plugin_id="aeloon.market",
            name="fast_news",
            full_id="aeloon.market.fast_news",
            service_cls=FastNewsService,
            policy=ServicePolicy(startup_timeout_seconds=5, shutdown_timeout_seconds=5),
        )
        runtime = MagicMock()
        config = {"poll_interval_seconds": 1}

        await supervisor.start_service(record, runtime, config)
        assert record.status == ServiceStatus.RUNNING

        await supervisor.stop_service("aeloon.market.fast_news")
        assert record.status == ServiceStatus.STOPPED

    @pytest.mark.asyncio
    async def test_llm_proxy_chat_json_delegates(self, mock_agent_loop: MagicMock) -> None:
        """PluginLLMProxy.chat_json() delegates to provider."""
        from aeloon.plugins._sdk.runtime import PluginLLMProxy

        runtime = PluginRuntime(
            agent_loop=mock_agent_loop,
            plugin_id="aeloon.market",
            config={},
            storage_base=Path("/tmp"),
        )
        proxy = runtime.llm
        assert isinstance(proxy, PluginLLMProxy)
        assert callable(proxy.chat_json)

    @pytest.mark.asyncio
    async def test_shutdown_stops_services_first(
        self, market_plugin_dir: Path, mock_agent_loop: MagicMock
    ) -> None:
        """Plugin shutdown stops services before deactivating."""
        from aeloon.plugins.MarketResearch.plugin import MarketPlugin

        with patch("aeloon.plugins._sdk.loader.importlib.import_module") as mock_import:
            mod = MagicMock()
            mod.MarketPlugin = MarketPlugin
            mock_import.return_value = mod

            registry = PluginRegistry()
            manager = PluginManager(
                registry=registry,
                discovery=PluginDiscovery(bundled_dir=market_plugin_dir.parent),
                loader=PluginLoader(),
                hook_dispatcher=HookDispatcher(),
                agent_loop=mock_agent_loop,
                plugin_config={"aeloon.market": {"enabled": True}},
                storage_base=Path("/tmp"),
            )
            await manager.boot()
            assert registry.get_plugin("aeloon.market").status == "active"

            await manager.shutdown()
            assert registry.get_plugin("aeloon.market").status == "discovered"
