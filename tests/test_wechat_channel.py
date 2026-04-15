import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from aeloon.channels.wechat import WeChatChannel, WeChatConfig
from aeloon.channels.wechat_ilink import (
    Credentials,
    ImageItem,
    ItemTypeImage,
    ItemTypeText,
    MediaInfo,
    MessageItem,
    MessageStateFinish,
    MessageTypeUser,
    TextItem,
    WeixinMessage,
)
from aeloon.core.bus.events import OutboundMessage
from aeloon.core.bus.queue import MessageBus


@pytest.mark.asyncio
async def test_on_message_publishes_text_inbound() -> None:
    bus = MessageBus()
    channel = WeChatChannel(WeChatConfig(allow_from=["*"]), bus)

    await channel._on_message(
        WeixinMessage(
            from_user_id="wx-user",
            to_user_id="wx-bot",
            message_type=MessageTypeUser,
            message_state=MessageStateFinish,
            context_token="ctx-1",
            item_list=[
                MessageItem(type=ItemTypeText, text_item=TextItem(text="hello from wx")),
            ],
        )
    )

    inbound = await bus.consume_inbound()
    assert inbound.channel == "wechat"
    assert inbound.sender_id == "wx-user"
    assert inbound.chat_id == "wx-user"
    assert inbound.content == "hello from wx"
    assert inbound.media == []
    assert inbound.metadata["context_token"] == "ctx-1"


@pytest.mark.asyncio
async def test_on_message_skips_duplicate_message_id() -> None:
    bus = MessageBus()
    channel = WeChatChannel(WeChatConfig(allow_from=["*"]), bus)

    message = WeixinMessage(
        from_user_id="wx-user",
        to_user_id="wx-bot",
        message_type=MessageTypeUser,
        message_state=MessageStateFinish,
        context_token="ctx-1",
        message_id="msg-1",
        item_list=[
            MessageItem(type=ItemTypeText, text_item=TextItem(text="hello from wx")),
        ],
    )

    await channel._on_message(message)
    await channel._on_message(message)

    inbound = await bus.consume_inbound()
    assert inbound.content == "hello from wx"
    assert inbound.metadata["message_id"] == "msg-1"

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(bus.consume_inbound(), timeout=0.05)


@pytest.mark.asyncio
async def test_on_message_downloads_image_into_media(monkeypatch, tmp_path: Path) -> None:
    bus = MessageBus()
    channel = WeChatChannel(WeChatConfig(allow_from=["*"], save_media_dir=str(tmp_path)), bus)

    async def _fake_download(_item, _save_dir: Path) -> str:
        path = tmp_path / "image.png"
        path.write_bytes(b"img")
        return str(path)

    monkeypatch.setattr("aeloon.channels.wechat.download_image_item", _fake_download)

    await channel._on_message(
        WeixinMessage(
            from_user_id="wx-user",
            to_user_id="wx-bot",
            message_type=MessageTypeUser,
            message_state=MessageStateFinish,
            context_token="ctx-2",
            item_list=[
                MessageItem(
                    type=ItemTypeImage,
                    image_item=ImageItem(
                        media=MediaInfo(
                            encrypt_query_param="enc",
                            aes_key="key",
                        )
                    ),
                ),
            ],
        )
    )

    inbound = await bus.consume_inbound()
    assert inbound.content == ""
    assert inbound.media == [str(tmp_path / "image.png")]
    assert inbound.metadata["item_types"] == [ItemTypeImage]


@pytest.mark.asyncio
async def test_on_message_ignores_non_http_image_url_and_falls_back_to_media(
    monkeypatch, tmp_path: Path
) -> None:
    bus = MessageBus()
    channel = WeChatChannel(WeChatConfig(allow_from=["*"], save_media_dir=str(tmp_path)), bus)
    calls: list[str] = []

    async def _fake_download(_item, _save_dir: Path) -> str:
        calls.append("downloaded")
        path = tmp_path / "fallback.png"
        path.write_bytes(b"img")
        return str(path)

    monkeypatch.setattr("aeloon.channels.wechat.download_image_item", _fake_download)

    await channel._on_message(
        WeixinMessage(
            from_user_id="wx-user",
            to_user_id="wx-bot",
            message_type=MessageTypeUser,
            message_state=MessageStateFinish,
            item_list=[
                MessageItem(
                    type=ItemTypeImage,
                    image_item=ImageItem(
                        url="/tmp/not-a-real-http-url",
                        media=MediaInfo(
                            encrypt_query_param="enc",
                            aes_key="key",
                        ),
                    ),
                ),
            ],
        )
    )

    inbound = await bus.consume_inbound()
    assert calls == ["downloaded"]
    assert inbound.media == [str(tmp_path / "fallback.png")]


@pytest.mark.asyncio
async def test_send_sends_text_and_existing_media(monkeypatch, tmp_path: Path) -> None:
    bus = MessageBus()
    channel = WeChatChannel(WeChatConfig(allow_from=["*"]), bus)
    channel._client = SimpleNamespace()
    channel._context_tokens["wx-user"] = "ctx-send"
    channel._running = True

    calls: list[tuple[str, str, str]] = []

    async def _fake_send_text(_client, to_user_id: str, text: str, context_token: str) -> None:
        calls.append(("text", to_user_id, f"{text}|{context_token}"))

    async def _fake_send_media(_client, to_user_id: str, path: str, context_token: str) -> None:
        calls.append(("media", to_user_id, f"{path}|{context_token}"))

    monkeypatch.setattr("aeloon.channels.wechat.send_text_message", _fake_send_text)
    monkeypatch.setattr("aeloon.channels.wechat.send_media_from_path", _fake_send_media)

    image_path = tmp_path / "reply.png"
    image_path.write_bytes(b"img")

    await channel.send(
        OutboundMessage(
            channel="wechat",
            chat_id="wx-user",
            content="reply text",
            media=[str(image_path), str(tmp_path / "missing.png")],
        )
    )

    assert calls == [
        ("text", "wx-user", "reply text|ctx-send"),
        ("media", "wx-user", f"{image_path}|ctx-send"),
    ]


@pytest.mark.asyncio
async def test_send_filters_step_thinking_progress(monkeypatch) -> None:
    bus = MessageBus()
    channel = WeChatChannel(WeChatConfig(allow_from=["*"]), bus)
    channel._client = SimpleNamespace()
    channel._context_tokens["wx-user"] = "ctx-send"
    channel._running = True

    calls: list[tuple[str, str, str]] = []

    async def _fake_send_text(_client, to_user_id: str, text: str, context_token: str) -> None:
        calls.append(("text", to_user_id, f"{text}|{context_token}"))

    monkeypatch.setattr("aeloon.channels.wechat.send_text_message", _fake_send_text)

    await channel.send(
        OutboundMessage(
            channel="wechat",
            chat_id="wx-user",
            content="Thinking (step 2)...",
            metadata={"_progress": True},
        )
    )
    await channel.send(
        OutboundMessage(
            channel="wechat",
            chat_id="wx-user",
            content="Thinking...",
            metadata={"_progress": True},
        )
    )

    assert calls == [("text", "wx-user", "Thinking...|ctx-send")]


@pytest.mark.asyncio
async def test_start_uses_first_loaded_credential(monkeypatch) -> None:
    bus = MessageBus()
    channel = WeChatChannel(WeChatConfig(allow_from=["*"]), bus)
    creds = [
        Credentials(bot_token="bot-1", ilink_bot_id="ilink-1"),
        Credentials(bot_token="bot-2", ilink_bot_id="ilink-2"),
    ]
    created: dict[str, object] = {}

    class _FakeClient:
        def __init__(self, cred: Credentials) -> None:
            created["credential"] = cred
            created["client"] = self

        async def aclose(self) -> None:
            created["closed"] = True

    class _FakeMonitor:
        def __init__(self, client, handler, poll_interval_ms: int) -> None:
            created["monitor_client"] = client
            created["poll_interval_ms"] = poll_interval_ms
            created["handler"] = handler

        async def run(self, stop_event) -> None:
            stop_event.set()
            channel._running = False

    monkeypatch.setattr("aeloon.channels.wechat.load_all_credentials", lambda _path: creds)
    monkeypatch.setattr("aeloon.channels.wechat.ILinkClient", _FakeClient)
    monkeypatch.setattr("aeloon.channels.wechat.ILinkMonitor", _FakeMonitor)

    await channel.start()
    await channel.stop()

    assert created["credential"] is creds[0]
    assert created["monitor_client"] is created["client"]
    assert created["poll_interval_ms"] == channel.config.poll_interval_ms
    assert created["closed"] is True
