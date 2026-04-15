from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from aeloon.core.agent.dispatcher import Dispatcher
from aeloon.core.bus.events import InboundMessage
from aeloon.core.bus.queue import MessageBus


def _make_agent_loop():
    bus = MessageBus()
    return SimpleNamespace(
        bus=bus,
        profiler=SimpleNamespace(enabled=False),
        sessions=SimpleNamespace(get_or_create=lambda _key: None),
        process_turn=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_wechat_login_returns_single_qr_message(monkeypatch, tmp_path) -> None:
    loop = _make_agent_loop()
    dispatcher = Dispatcher(loop)

    monkeypatch.setattr(
        "aeloon.core.agent.channel_auth.ChannelAuthHelper._wechat_accounts_dir",
        lambda self: str(tmp_path),
    )
    monkeypatch.setattr("aeloon.channels.wechat_ilink.auth.has_valid_credentials", lambda *_: False)
    monkeypatch.setattr(
        "aeloon.channels.wechat_ilink.auth.fetch_qrcode",
        AsyncMock(return_value={"qrcode": "qr-1", "qrcode_img_content": "aGVsbG8="}),
    )
    monkeypatch.setattr(
        "aeloon.channels.wechat_ilink.auth.get_qr_code_dir",
        lambda: tmp_path,
    )
    monkeypatch.setattr("aeloon.channels.wechat_ilink.auth.download_qr_image", AsyncMock())

    def _fake_create_task(coro):
        coro.close()
        task = MagicMock()
        task.done.return_value = False
        task.add_done_callback = lambda cb: None
        return task

    monkeypatch.setattr("aeloon.core.agent.channel_auth.asyncio.create_task", _fake_create_task)

    response = await dispatcher._channel_auth._handle_wechat_login(
        InboundMessage(channel="feishu", sender_id="u", chat_id="c", content="/wechat login"),
        loop,
    )

    assert response.content.startswith("Please scan this QR code with WeChat within 5 minutes.")
    assert len(response.media) == 0  # Media is no longer included


@pytest.mark.asyncio
async def test_wechat_login_checks_reload_result_before_success_message(
    monkeypatch, tmp_path
) -> None:
    loop = _make_agent_loop()
    dispatcher = Dispatcher(loop)
    dispatcher.channel_manager = SimpleNamespace(reload_channel=AsyncMock(return_value=False))

    monkeypatch.setattr(
        "aeloon.core.agent.channel_auth.ChannelAuthHelper._wechat_accounts_dir",
        lambda self: str(tmp_path),
    )

    async def _fake_poll(_qrcode):
        return SimpleNamespace(
            ilink_bot_id="bot-id", bot_token="bot-token", base_url="", ilink_user_id=""
        )

    monkeypatch.setattr("aeloon.channels.wechat_ilink.auth.has_valid_credentials", lambda *_: False)
    monkeypatch.setattr(
        "aeloon.channels.wechat_ilink.auth.fetch_qrcode",
        AsyncMock(return_value={"qrcode": "qr-1", "qrcode_img_content": "aGVsbG8="}),
    )
    monkeypatch.setattr("aeloon.channels.wechat_ilink.auth.get_qr_code_dir", lambda: tmp_path)
    monkeypatch.setattr("aeloon.channels.wechat_ilink.auth.download_qr_image", AsyncMock())
    monkeypatch.setattr("aeloon.channels.wechat_ilink.auth.poll_qrcode_until_confirmed", _fake_poll)
    monkeypatch.setattr("aeloon.channels.wechat_ilink.auth.save_credentials", MagicMock())

    created = {}

    def _capture_task(coro):
        task = MagicMock()
        task.done.return_value = False
        task.add_done_callback = lambda cb: None
        created["coro"] = coro
        return task

    monkeypatch.setattr("aeloon.core.agent.channel_auth.asyncio.create_task", _capture_task)

    await dispatcher._channel_auth._handle_wechat_login(
        InboundMessage(channel="feishu", sender_id="u", chat_id="c", content="/wechat login"),
        loop,
    )
    await created["coro"]

    outbound = await loop.bus.consume_outbound()
    assert "login successful" in outbound.content.lower()
    outbound = await loop.bus.consume_outbound()
    assert "credentials saved" in outbound.content.lower()


@pytest.mark.asyncio
async def test_wechat_logout_uses_configured_accounts_dir(monkeypatch, tmp_path) -> None:
    loop = _make_agent_loop()
    dispatcher = Dispatcher(loop)
    dispatcher._channel_auth.gateway = SimpleNamespace(
        stop=MagicMock(return_value=True),
        is_current_process_gateway=lambda: False,
    )

    monkeypatch.setattr(
        "aeloon.core.agent.channel_auth.ChannelAuthHelper._wechat_accounts_dir",
        lambda self: str(tmp_path),
    )
    has_valid = MagicMock(return_value=True)
    remove_all = MagicMock(return_value=1)
    monkeypatch.setattr("aeloon.channels.wechat_ilink.auth.has_valid_credentials", has_valid)
    monkeypatch.setattr("aeloon.channels.wechat_ilink.auth.remove_all_credentials", remove_all)

    response = await dispatcher._channel_auth._handle_wechat_logout(
        InboundMessage(channel="feishu", sender_id="u", chat_id="c", content="/wechat logout"),
    )

    has_valid.assert_called_once_with(str(tmp_path))
    remove_all.assert_called_once_with(str(tmp_path))
    dispatcher._channel_auth.gateway.stop.assert_called_once_with(exclude_current=False)
    assert "Removed 1 credential" in response.content
