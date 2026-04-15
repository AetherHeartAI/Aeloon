import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from typer.testing import CliRunner

from aeloon.cli.commands import app
from aeloon.core.agent.channel_auth import ChannelAuthHelper, GatewayManager
from aeloon.core.bus.events import InboundMessage, OutboundMessage
from aeloon.core.config.schema import Config

runner = CliRunner()


def test_gateway_manager_decodes_utf8_process_output() -> None:
    text = GatewayManager._decode_process_output("♥️ aeloon".encode("utf-8"))
    assert "aeloon" in text


@pytest.mark.asyncio
async def test_channel_auth_feishu_qr_login_saves_credentials_and_reloads(monkeypatch, tmp_path):
    helper = ChannelAuthHelper()
    helper.gateway = SimpleNamespace(is_running=lambda: True, start_background=lambda: True)
    channel_manager = SimpleNamespace(
        reload_channel=AsyncMock(return_value=True),
        _get_channel_config=lambda _name: {"enabled": False, "appId": ""},
        get_channel=lambda _name: None,
        config=SimpleNamespace(channels=SimpleNamespace(feishu=SimpleNamespace())),
    )
    helper.set_channel_manager(channel_manager)

    session = SimpleNamespace(
        verification_url="https://example.com/qr",
        qr_image_path=str(tmp_path / "feishu_login.png"),
        app_id="cli_x",
        app_secret="sec_y",
    )
    Path(session.qr_image_path).write_bytes(b"png")

    async def _fake_create_session():
        return session

    async def _fake_wait_for_login_confirmation(_session):
        return session

    saved: dict[str, str | bool] = {}
    helper.feishu.set_credentials = lambda app_id, app_secret, enabled=True: saved.update(
        {"app_id": app_id, "app_secret": app_secret, "enabled": enabled}
    )
    helper.feishu.sync_runtime_config = lambda *args, **kwargs: None

    published: list[str] = []

    class _Bus:
        async def publish_outbound(self, message: OutboundMessage) -> None:
            published.append(message.content)

    monkeypatch.setattr(
        "aeloon.channels.feishu_onboard.create_login_session",
        _fake_create_session,
    )
    monkeypatch.setattr(
        "aeloon.channels.feishu_onboard.wait_for_login_confirmation",
        _fake_wait_for_login_confirmation,
    )
    monkeypatch.setattr(
        "aeloon.channels.feishu_onboard.render_ascii_qrcode",
        lambda _data: "ASCII-QR",
    )

    response = await helper.handle_feishu_command(
        InboundMessage(channel="cli", sender_id="user", chat_id="chat-1", content="/feishu login"),
        ["login"],
        SimpleNamespace(bus=_Bus()),
    )

    assert "Please scan this QR code with Feishu" in response.content
    await helper.feishu._login_tasks[("cli", "chat-1")]
    assert saved == {"app_id": "cli_x", "app_secret": "sec_y", "enabled": True}
    channel_manager.reload_channel.assert_awaited_once_with("feishu")
    assert any("Feishu login successful!" in item for item in published)


@pytest.mark.asyncio
async def test_channel_auth_feishu_status_reports_pending_login():
    helper = ChannelAuthHelper()
    helper.feishu._login_status[("cli", "chat-1")] = {
        "status": "waiting",
        "verification_url": "https://example.com/qr",
    }
    helper.feishu._login_tasks[("cli", "chat-1")] = asyncio.create_task(asyncio.sleep(60))

    try:
        response = await helper.handle_feishu_command(
            InboundMessage(
                channel="cli", sender_id="user", chat_id="chat-1", content="/feishu status"
            ),
            ["status"],
        )
    finally:
        helper.feishu._login_tasks[("cli", "chat-1")].cancel()
        with pytest.raises(asyncio.CancelledError):
            await helper.feishu._login_tasks[("cli", "chat-1")]

    assert "Login in progress:" in response.content
    assert "Verification URL: https://example.com/qr" in response.content


@pytest.mark.asyncio
async def test_channel_auth_feishu_logout_syncs_runtime_config_and_stops_channel():
    helper = ChannelAuthHelper()
    helper.gateway = SimpleNamespace(
        stop=MagicMock(return_value=True),
        is_current_process_gateway=lambda: False,
    )
    feishu_config = SimpleNamespace(enabled=True, app_id="cli_x", app_secret="sec_y")
    config = SimpleNamespace(channels=SimpleNamespace(feishu=feishu_config))
    channel_manager = SimpleNamespace(
        config=config,
        stop_channel=AsyncMock(return_value=True),
        _get_channel_config=lambda _name: feishu_config if feishu_config.enabled else None,
        get_channel=lambda _name: None,
    )
    helper.set_channel_manager(channel_manager)

    response = await helper.handle_feishu_command(
        InboundMessage(channel="cli", sender_id="user", chat_id="chat-1", content="/feishu logout"),
        ["logout"],
    )

    assert "Feishu logged out" in response.content
    assert "Background gateway process stopped." in response.content
    assert feishu_config.enabled is False
    assert feishu_config.app_id == ""
    assert feishu_config.app_secret == ""
    assert helper.feishu.has_credentials() is False
    channel_manager.stop_channel.assert_awaited_once_with("feishu")
    helper.gateway.stop.assert_called_once_with(exclude_current=False)


def test_agent_feishu_login_message_uses_bus_backed_one_shot(monkeypatch, tmp_path: Path) -> None:
    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "workspace")
    cron_dir = tmp_path / "cron"
    printed: list[str] = []
    rendered_media: list[str] = []

    class _FakeDispatcher:
        def stop(self) -> None:
            return None

    class _FakeAgentLoop:
        def __init__(self, *args, **kwargs) -> None:
            from aeloon.core.bus.queue import MessageBus

            self.bus = MessageBus()
            self.dispatcher = _FakeDispatcher()
            self.plugin_manager = None
            self.profiler = SimpleNamespace(enabled=False, last_report=None)
            self.runtime_settings = SimpleNamespace(output_mode="normal")
            self.channels_config = None
            self.process_direct_full = AsyncMock()

        async def run(self) -> None:
            msg = await self.bus.consume_inbound()
            assert msg.content == "/feishu login"
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel="cli",
                    chat_id=msg.chat_id,
                    content="Please scan this QR code with Feishu within 10 minutes.",
                    media=["/tmp/feishu_qr.png"],
                )
            )
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel="cli",
                    chat_id=msg.chat_id,
                    content="Feishu login successful!\nApp ID: cli_x",
                )
            )
            await asyncio.sleep(60)

        async def close_mcp(self) -> None:
            return None

    fake_loop: _FakeAgentLoop | None = None

    def _make_fake_loop(*args, **kwargs):
        nonlocal fake_loop
        fake_loop = _FakeAgentLoop(*args, **kwargs)
        return fake_loop

    monkeypatch.setattr("aeloon.core.config.loader.load_config", lambda _path=None: config)
    monkeypatch.setattr("aeloon.core.config.paths.get_cron_dir", lambda: cron_dir)
    monkeypatch.setattr("aeloon.cli.flows.agent.sync_workspace_templates", lambda _path: None)
    monkeypatch.setattr("aeloon.cli.flows.agent.make_provider", lambda _config: object())
    monkeypatch.setattr("aeloon.cli.flows.agent.boot_plugins", AsyncMock(return_value=None))
    monkeypatch.setattr("aeloon.core.agent.loop.AgentLoop", _make_fake_loop)
    monkeypatch.setattr(
        "aeloon.cli.flows.agent._print_agent_response",
        lambda response, **_kwargs: printed.append(response),
    )
    monkeypatch.setattr(
        "aeloon.cli.flows.agent._try_render_inline_image",
        lambda path: rendered_media.append(path) or True,
    )

    result = runner.invoke(app, ["agent", "-m", "/feishu login"])

    assert result.exit_code == 0
    assert any("Please scan" in item for item in printed)
    assert any("login successful" in item.lower() for item in printed)
    assert rendered_media == ["/tmp/feishu_qr.png"]
    assert fake_loop is not None
    fake_loop.process_direct_full.assert_not_called()
