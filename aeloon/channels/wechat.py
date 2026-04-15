"""Native WeChat channel using the iLink HTTP API."""

from __future__ import annotations

import asyncio
import re
from collections import OrderedDict
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import Field

from aeloon.channels.base import BaseChannel
from aeloon.channels.wechat_ilink import (
    FileItem,
    ILinkClient,
    ILinkMonitor,
    ItemTypeFile,
    ItemTypeImage,
    ItemTypeText,
    ItemTypeVideo,
    MediaInfo,
    MessageStateFinish,
    MessageTypeUser,
    WeixinMessage,
    download_file_item,
    download_image_item,
    load_all_credentials,
    new_accounts_dir,
    send_media_from_path,
    send_text_message,
)
from aeloon.core.bus.events import OutboundMessage
from aeloon.core.bus.queue import MessageBus
from aeloon.core.config.paths import get_media_dir
from aeloon.core.config.schema import Base

# WeChat iLink has no official per-message limit, but very long messages
# often fail silently.  Split at this threshold to stay safe.
_MAX_TEXT_LENGTH = 4000
_THINKING_STEP_RE = re.compile(r"^Thinking \(step \d+\)\.\.\.$")
_MAX_PROCESSED_MESSAGE_IDS = 1000


class WeChatConfig(Base):
    """Native WeChat channel configuration."""

    enabled: bool = False
    accounts_dir: str = Field(default_factory=lambda: str(new_accounts_dir()))
    allow_from: list[str] = Field(default_factory=list)
    poll_interval_ms: int = 1000
    save_media_dir: str = ""
    download_media: bool = True


class WeChatChannel(BaseChannel):
    """WeChat channel backed by the native iLink transport."""

    name = "wechat"
    display_name = "WeChat"

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return WeChatConfig().model_dump(by_alias=True)

    def __init__(self, config: Any, bus: MessageBus):
        if isinstance(config, dict):
            config = WeChatConfig.model_validate(config)
        super().__init__(config, bus)
        self.config: WeChatConfig = config
        self._client: ILinkClient | None = None
        self._monitor: ILinkMonitor | None = None
        self._monitor_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._context_tokens: dict[str, str] = {}
        self._processed_message_ids: OrderedDict[str, None] = OrderedDict()

    async def start(self) -> None:
        """Start the native WeChat monitor loop."""
        creds = load_all_credentials(self.config.accounts_dir)
        if not creds:
            logger.error("WeChat credentials not found in {}", self.config.accounts_dir)
            return

        self._client = ILinkClient(creds[0])
        self._monitor = ILinkMonitor(
            client=self._client,
            handler=self._on_message,
            poll_interval_ms=self.config.poll_interval_ms,
        )
        self._stop_event.clear()
        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor.run(self._stop_event))
        logger.info("WeChat native channel started with account {}", creds[0].ilink_bot_id)

        while self._running:
            if self._monitor_task and self._monitor_task.done():
                await self._monitor_task
                break
            await asyncio.sleep(1)

    async def stop(self) -> None:
        """Stop the WeChat monitor and client."""
        self._running = False
        self._stop_event.set()
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None
        if self._client:
            await self._client.aclose()
            self._client = None
        self._monitor = None

    async def send(self, msg: OutboundMessage) -> None:
        """Send text and local media back to the WeChat user."""
        if not self._running:
            return
        if not self._client:
            logger.warning("WeChat client not initialized")
            return
        if _should_skip_progress_text(msg):
            return

        context_token = self._context_tokens.get(msg.chat_id, "")
        if msg.content and msg.content.strip():
            text = msg.content.strip()
            chunks = _split_text(text, _MAX_TEXT_LENGTH)
            for chunk in chunks:
                await send_text_message(self._client, msg.chat_id, chunk, context_token)

        for path in msg.media or []:
            if not Path(path).is_file():
                logger.warning("WeChat outbound media missing: {}", path)
                continue
            await send_media_from_path(self._client, msg.chat_id, path, context_token)

    async def _on_message(self, msg: WeixinMessage) -> None:
        """Handle one inbound WeChat message."""
        if msg.message_type != MessageTypeUser or msg.message_state != MessageStateFinish:
            return

        if msg.message_id and not self._remember_processed_message(msg.message_id):
            logger.debug("WeChat duplicate inbound skipped: message_id={}", msg.message_id)
            return

        sender_id = msg.from_user_id
        if not sender_id:
            return

        self._context_tokens[sender_id] = msg.context_token

        text_parts: list[str] = []
        media_paths: list[str] = []
        item_types: list[int] = []

        for item in msg.item_list:
            item_types.append(item.type)
            if item.type == ItemTypeText and item.text_item and item.text_item.text:
                text_parts.append(item.text_item.text)
            elif item.type == ItemTypeImage and item.image_item:
                if self.config.download_media:
                    if path := await download_image_item(
                        item.image_item, self._inbound_media_dir()
                    ):
                        media_paths.append(path)
                else:
                    text_parts.append("[image]")
            elif item.type in {ItemTypeFile, ItemTypeVideo}:
                file_item = item.file_item
                if item.type == ItemTypeVideo and item.video_item and item.video_item.media:
                    file_item = FileItem(
                        media=MediaInfo(
                            encrypt_query_param=item.video_item.media.encrypt_query_param,
                            aes_key=item.video_item.media.aes_key,
                            encrypt_type=item.video_item.media.encrypt_type,
                        ),
                        file_name="video.mp4",
                        length=str(item.video_item.video_size or ""),
                    )
                if file_item and self.config.download_media:
                    if path := await download_file_item(file_item, self._inbound_media_dir()):
                        media_paths.append(path)
                else:
                    text_parts.append("[file]")

        content = "\n".join(part for part in text_parts if part).strip()
        if not content and not media_paths:
            return

        await self._handle_message(
            sender_id=sender_id,
            chat_id=sender_id,
            content=content,
            media=media_paths,
            metadata={
                "message_id": msg.message_id,
                "context_token": msg.context_token,
                "item_types": item_types,
                "from_user_id": msg.from_user_id,
                "to_user_id": msg.to_user_id,
            },
        )

    def _inbound_media_dir(self) -> Path:
        """Return the directory for inbound WeChat media."""
        if self.config.save_media_dir:
            path = Path(self.config.save_media_dir).expanduser()
            path.mkdir(parents=True, exist_ok=True)
            return path
        return get_media_dir("wechat")

    def _remember_processed_message(self, message_id: str) -> bool:
        """Track processed inbound message ids and reject duplicates."""
        if message_id in self._processed_message_ids:
            return False
        self._processed_message_ids[message_id] = None
        while len(self._processed_message_ids) > _MAX_PROCESSED_MESSAGE_IDS:
            self._processed_message_ids.popitem(last=False)
        return True


def _split_text(text: str, max_len: int) -> list[str]:
    """Split *text* into chunks of at most *max_len* characters.

    Prefers splitting on paragraph breaks (``\\n\\n``), then on newlines,
    then hard-cuts at *max_len* as a last resort.
    """
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break

        # Try paragraph boundary first
        cut = remaining.rfind("\n\n", 0, max_len)
        if cut > 0:
            chunks.append(remaining[:cut])
            remaining = remaining[cut:].lstrip("\n")
            continue

        # Try newline boundary
        cut = remaining.rfind("\n", 0, max_len)
        if cut > 0:
            chunks.append(remaining[:cut])
            remaining = remaining[cut + 1 :]
            continue

        # Hard cut
        chunks.append(remaining[:max_len])
        remaining = remaining[max_len:]

    return chunks


def _should_skip_progress_text(msg: OutboundMessage) -> bool:
    """Filter redundant step-level thinking progress for WeChat users."""
    metadata = msg.metadata or {}
    if not metadata.get("_progress") or metadata.get("_tool_hint"):
        return False
    text = msg.content.strip() if msg.content else ""
    return bool(_THINKING_STEP_RE.fullmatch(text))
