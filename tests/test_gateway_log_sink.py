from __future__ import annotations

import asyncio
from types import SimpleNamespace

from loguru import logger

from aeloon.cli.flows import gateway as gateway_flow


def test_configure_gateway_log_sink_returns_stable_path(monkeypatch, tmp_path) -> None:
    log_path = tmp_path / "logs" / "gateway.log"
    monkeypatch.setattr(
        "aeloon.core.config.paths.get_gateway_log_path",
        lambda: log_path,
    )

    first = gateway_flow._configure_gateway_log_sink()
    second = gateway_flow._configure_gateway_log_sink()

    assert first == log_path
    assert second == log_path
    logger.remove(gateway_flow._GATEWAY_LOG_SINK_ID)
    gateway_flow._GATEWAY_LOG_SINK_ID = None


def test_run_gateway_writes_startup_line_to_log(monkeypatch, tmp_path) -> None:
    original_asyncio_run = asyncio.run
    log_path = tmp_path / "logs" / "gateway.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    config = SimpleNamespace(
        workspace_path=str(tmp_path / "workspace"),
        gateway=SimpleNamespace(
            port=8765,
            heartbeat=SimpleNamespace(interval_s=30, enabled=True),
        ),
        agents=SimpleNamespace(
            defaults=SimpleNamespace(
                model="test-model",
                max_tool_iterations=4,
                context_window_tokens=16000,
                output_mode="normal",
                fast=False,
            )
        ),
        tools=SimpleNamespace(
            web=SimpleNamespace(search=None, proxy=None),
            exec=SimpleNamespace(),
            restrict_to_workspace=True,
            mcp_servers=[],
        ),
        channels=SimpleNamespace(),
    )

    class FakeChannelManager:
        def __init__(self, *_args, **_kwargs) -> None:
            self.enabled_channels: list[str] = []

    class FakeAgentLoop:
        def __init__(self, **kwargs) -> None:
            self.model = kwargs["model"]
            self.dispatcher = SimpleNamespace(channel_manager=None)
            self.tools: dict[str, object] = {}

    class FakeCronService:
        def __init__(self, *_args, **_kwargs) -> None:
            self.on_job = None

        def status(self) -> dict[str, int]:
            return {"jobs": 0}

    class FakeHeartbeatService:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

    monkeypatch.setattr(gateway_flow, "load_runtime_config", lambda *_args, **_kwargs: config)
    monkeypatch.setattr(gateway_flow, "make_provider", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(gateway_flow, "compose_welcome_banner", lambda *_args, **_kwargs: "banner")
    monkeypatch.setattr(gateway_flow, "print_deprecated_memory_window_notice", lambda *_args: None)
    monkeypatch.setattr(gateway_flow, "sync_workspace_templates", lambda *_args: None)
    monkeypatch.setattr(
        "aeloon.core.config.paths.get_gateway_log_path",
        lambda: log_path,
    )
    monkeypatch.setattr("aeloon.channels.manager.ChannelManager", FakeChannelManager)
    monkeypatch.setattr("aeloon.core.agent.loop.AgentLoop", FakeAgentLoop)
    monkeypatch.setattr("aeloon.core.bus.queue.MessageBus", lambda: object())
    monkeypatch.setattr("aeloon.core.session.manager.SessionManager", lambda *_args: object())
    monkeypatch.setattr("aeloon.services.cron.service.CronService", FakeCronService)
    monkeypatch.setattr("aeloon.services.heartbeat.HeartbeatService", FakeHeartbeatService)
    monkeypatch.setattr(
        gateway_flow.asyncio,
        "run",
        lambda coro: coro.close(),
    )

    gateway_flow.run_gateway(port=None, workspace=None, verbose=False, config=None)

    async def _flush_logs() -> None:
        await logger.complete()

    original_asyncio_run(_flush_logs())

    content = log_path.read_text(encoding="utf-8")
    assert "Starting aeloon gateway version" in content
    assert "port 8765" in content

    if gateway_flow._GATEWAY_LOG_SINK_ID is not None:
        logger.remove(gateway_flow._GATEWAY_LOG_SINK_ID)
        gateway_flow._GATEWAY_LOG_SINK_ID = None
