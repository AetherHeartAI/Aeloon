"""Feishu channel built on the lark-oapi SDK."""

import asyncio
import importlib.util
import json
import os
import re
import threading
import time
from collections import OrderedDict
from typing import Any, Literal

from loguru import logger
from pydantic import Field

from aeloon.channels.base import BaseChannel
from aeloon.core.bus.events import OutboundMessage
from aeloon.core.bus.queue import MessageBus
from aeloon.core.config.paths import get_media_dir
from aeloon.core.config.schema import Base

FEISHU_AVAILABLE = importlib.util.find_spec("lark_oapi") is not None
_THINKING_STEP_RE = re.compile(r"^thinking\s*\(\s*step\s*\d+\s*\)\s*\.\.\.$", re.IGNORECASE)

# Short labels for non-text message types.
MSG_TYPE_MAP = {
    "image": "[image]",
    "audio": "[audio]",
    "file": "[file]",
    "sticker": "[sticker]",
}


def _should_skip_progress_text(msg: OutboundMessage) -> bool:
    """Drop step-level thinking updates."""
    metadata = msg.metadata or {}
    if not metadata.get("_progress") or metadata.get("_tool_hint"):
        return False
    text = msg.content.strip() if msg.content else ""
    return bool(_THINKING_STEP_RE.search(text))


def _extract_share_card_content(content_json: dict, msg_type: str) -> str:
    """Convert share cards and interactive payloads to plain text."""
    parts = []

    if msg_type == "share_chat":
        parts.append(f"[shared chat: {content_json.get('chat_id', '')}]")
    elif msg_type == "share_user":
        parts.append(f"[shared user: {content_json.get('user_id', '')}]")
    elif msg_type == "interactive":
        parts.extend(_extract_interactive_content(content_json))
    elif msg_type == "share_calendar_event":
        parts.append(f"[shared calendar event: {content_json.get('event_key', '')}]")
    elif msg_type == "system":
        parts.append("[system message]")
    elif msg_type == "merge_forward":
        parts.append("[merged forward messages]")

    return "\n".join(parts) if parts else f"[{msg_type}]"


def _extract_interactive_content(content: dict) -> list[str]:
    """Recursively pull text and links from an interactive card."""
    parts = []

    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return [content] if content.strip() else []

    if not isinstance(content, dict):
        return parts

    if "title" in content:
        title = content["title"]
        if isinstance(title, dict):
            title_content = title.get("content", "") or title.get("text", "")
            if title_content:
                parts.append(f"title: {title_content}")
        elif isinstance(title, str):
            parts.append(f"title: {title}")

    for elements in (
        content.get("elements", []) if isinstance(content.get("elements"), list) else []
    ):
        for element in elements:
            parts.extend(_extract_element_content(element))

    card = content.get("card", {})
    if card:
        parts.extend(_extract_interactive_content(card))

    header = content.get("header", {})
    if header:
        header_title = header.get("title", {})
        if isinstance(header_title, dict):
            header_text = header_title.get("content", "") or header_title.get("text", "")
            if header_text:
                parts.append(f"title: {header_text}")

    return parts


def _extract_element_content(element: dict) -> list[str]:
    """Pull readable text from one card element."""
    parts = []

    if not isinstance(element, dict):
        return parts

    tag = element.get("tag", "")

    if tag in ("markdown", "lark_md"):
        content = element.get("content", "")
        if content:
            parts.append(content)

    elif tag == "div":
        text = element.get("text", {})
        if isinstance(text, dict):
            text_content = text.get("content", "") or text.get("text", "")
            if text_content:
                parts.append(text_content)
        elif isinstance(text, str):
            parts.append(text)
        for field in element.get("fields", []):
            if isinstance(field, dict):
                field_text = field.get("text", {})
                if isinstance(field_text, dict):
                    c = field_text.get("content", "")
                    if c:
                        parts.append(c)

    elif tag == "a":
        href = element.get("href", "")
        text = element.get("text", "")
        if href:
            parts.append(f"link: {href}")
        if text:
            parts.append(text)

    elif tag == "button":
        text = element.get("text", {})
        if isinstance(text, dict):
            c = text.get("content", "")
            if c:
                parts.append(c)
        url = element.get("url", "") or element.get("multi_url", {}).get("url", "")
        if url:
            parts.append(f"link: {url}")

    elif tag == "img":
        alt = element.get("alt", {})
        parts.append(alt.get("content", "[image]") if isinstance(alt, dict) else "[image]")

    elif tag == "note":
        for ne in element.get("elements", []):
            parts.extend(_extract_element_content(ne))

    elif tag == "column_set":
        for col in element.get("columns", []):
            for ce in col.get("elements", []):
                parts.extend(_extract_element_content(ce))

    elif tag == "plain_text":
        content = element.get("content", "")
        if content:
            parts.append(content)

    else:
        for ne in element.get("elements", []):
            parts.extend(_extract_element_content(ne))

    return parts


def _extract_post_content(content_json: dict) -> tuple[str, list[str]]:
    """Extract text and image keys from Feishu post (rich text) message.

    Handles three payload shapes:
    - Direct:    {"title": "...", "content": [[...]]}
    - Localized: {"zh_cn": {"title": "...", "content": [...]}}
    - Wrapped:   {"post": {"zh_cn": {"title": "...", "content": [...]}}}
    """

    def _parse_block(block: dict) -> tuple[str | None, list[str]]:
        if not isinstance(block, dict) or not isinstance(block.get("content"), list):
            return None, []
        texts, images = [], []
        if title := block.get("title"):
            texts.append(title)
        for row in block["content"]:
            if not isinstance(row, list):
                continue
            for el in row:
                if not isinstance(el, dict):
                    continue
                tag = el.get("tag")
                if tag in ("text", "a"):
                    texts.append(el.get("text", ""))
                elif tag == "at":
                    texts.append(f"@{el.get('user_name', 'user')}")
                elif tag == "img" and (key := el.get("image_key")):
                    images.append(key)
        return (" ".join(texts).strip() or None), images

    # Some payloads wrap the actual post under "post".
    root = content_json
    if isinstance(root, dict) and isinstance(root.get("post"), dict):
        root = root["post"]
    if not isinstance(root, dict):
        return "", []

    # Direct payload shape.
    if "content" in root:
        text, imgs = _parse_block(root)
        if text or imgs:
            return text or "", imgs

    # Localized payloads usually nest content by locale.
    for key in ("zh_cn", "en_us", "ja_jp"):
        if key in root:
            text, imgs = _parse_block(root[key])
            if text or imgs:
                return text or "", imgs
    for val in root.values():
        if isinstance(val, dict):
            text, imgs = _parse_block(val)
            if text or imgs:
                return text or "", imgs

    return "", []


def _extract_post_text(content_json: dict) -> str:
    """Return only the text portion of a post payload."""
    text, _ = _extract_post_content(content_json)
    return text


class FeishuConfig(Base):
    """Config for the Feishu WebSocket channel."""

    enabled: bool = False
    app_id: str = ""
    app_secret: str = ""
    encrypt_key: str = ""
    verification_token: str = ""
    allow_from: list[str] = Field(default_factory=list)
    react_emoji: str = "THUMBSUP"
    group_policy: Literal["open", "mention"] = "mention"
    reply_to_message: bool = False  # Quote the original message on the first reply.


class FeishuChannel(BaseChannel):
    """Feishu channel that sends and receives messages over WebSocket."""

    name = "feishu"
    display_name = "Feishu"
    _PROGRESS_MIN_INTERVAL_S = 1.0
    _SEND_MIN_INTERVAL_S = 1.0
    _RECOVERY_WINDOW_S = 120
    _RECOVERY_PAGE_SIZE = 20

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return FeishuConfig().model_dump(by_alias=True)

    def __init__(self, config: Any, bus: MessageBus):
        if isinstance(config, dict):
            config = FeishuConfig.model_validate(config)
        super().__init__(config, bus)
        self.config: FeishuConfig = config
        self._client: Any = None
        self._ws_client: Any = None
        self._ws_thread: threading.Thread | None = None
        self._processed_message_ids: OrderedDict[str, None] = OrderedDict()  # FIFO dedup cache.
        self._last_progress_sent_at: dict[str, float] = {}
        self._last_send_started_at: dict[str, float] = {}
        self._send_locks: dict[str, asyncio.Lock] = {}
        self._recovery_locks: dict[str, asyncio.Lock] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws_loop: asyncio.AbstractEventLoop | None = None

    def _now_monotonic(self) -> float:
        """Wrap time.monotonic for easier tests."""
        return time.monotonic()

    def _should_send_progress(self, msg: OutboundMessage) -> bool:
        """Throttle plain progress messages per chat."""
        metadata = msg.metadata or {}
        if not metadata.get("_progress") or metadata.get("_tool_hint"):
            return True

        now = self._now_monotonic()
        last_sent_at = self._last_progress_sent_at.get(msg.chat_id)
        if last_sent_at is not None and now - last_sent_at < self._PROGRESS_MIN_INTERVAL_S:
            logger.debug(
                "Feishu progress throttled: chat_id={}, elapsed={:.3f}s, min_interval={:.3f}s",
                msg.chat_id,
                now - last_sent_at,
                self._PROGRESS_MIN_INTERVAL_S,
            )
            return False

        self._last_progress_sent_at[msg.chat_id] = now
        return True

    def _get_send_lock(self, chat_id: str) -> asyncio.Lock:
        """Return the send lock for one chat."""
        lock = self._send_locks.get(chat_id)
        if lock is None:
            lock = asyncio.Lock()
            self._send_locks[chat_id] = lock
        return lock

    def _get_recovery_lock(self, chat_id: str) -> asyncio.Lock:
        """Return the recovery lock for one chat."""
        lock = self._recovery_locks.get(chat_id)
        if lock is None:
            lock = asyncio.Lock()
            self._recovery_locks[chat_id] = lock
        return lock

    async def _send_with_rate_limit(
        self,
        chat_id: str,
        send_fn: Any,
        *args: Any,
    ) -> Any:
        """Serialize sends per chat and enforce a minimum gap."""
        async with self._get_send_lock(chat_id):
            now = self._now_monotonic()
            last_started_at = self._last_send_started_at.get(chat_id)
            if last_started_at is not None:
                delay = self._SEND_MIN_INTERVAL_S - (now - last_started_at)
                if delay > 0:
                    logger.debug(
                        "Feishu send rate-limited: chat_id={}, sleep={:.3f}s",
                        chat_id,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    now = self._now_monotonic()
            self._last_send_started_at[chat_id] = now
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, send_fn, *args)

    @staticmethod
    def _register_optional_event(builder: Any, method_name: str, handler: Any) -> Any:
        """Register an event only if the SDK exposes it."""
        method = getattr(builder, method_name, None)
        return method(handler) if callable(method) else builder

    async def start(self) -> None:
        """Start the Feishu channel and keep the socket alive."""
        if not FEISHU_AVAILABLE:
            logger.error("Feishu SDK not installed. Run: pip install lark-oapi")
            return

        if not self.config.app_id or not self.config.app_secret:
            logger.error("Feishu app_id and app_secret not configured")
            return

        import lark_oapi as lark

        self._running = True
        self._loop = asyncio.get_running_loop()

        # API client for outbound calls.
        self._client = (
            lark.Client.builder()
            .app_id(self.config.app_id)
            .app_secret(self.config.app_secret)
            .log_level(lark.LogLevel.INFO)
            .build()
        )
        builder = lark.EventDispatcherHandler.builder(
            self.config.encrypt_key or "",
            self.config.verification_token or "",
        ).register_p2_im_message_receive_v1(self._on_message_sync)
        builder = self._register_optional_event(
            builder, "register_p2_im_message_reaction_created_v1", self._on_reaction_created
        )
        builder = self._register_optional_event(
            builder, "register_p2_im_message_message_read_v1", self._on_message_read
        )
        builder = self._register_optional_event(
            builder,
            "register_p2_im_chat_access_event_bot_p2p_chat_entered_v1",
            self._on_bot_p2p_chat_entered,
        )
        event_handler = builder.build()

        # Socket client for inbound events.
        self._ws_client = lark.ws.Client(
            self.config.app_id,
            self.config.app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
        )

        # Run the Feishu socket client in its own event loop thread.
        def run_ws():
            import time

            import lark_oapi.ws.client as _lark_ws_client

            ws_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(ws_loop)
            self._ws_loop = ws_loop
            # The SDK reads a module-level loop when starting the socket.
            _lark_ws_client.loop = ws_loop
            try:
                while self._running:
                    try:
                        self._ws_client.start()
                    except Exception as e:
                        logger.warning("Feishu WebSocket error: {}", e)
                    if self._running:
                        time.sleep(5)
            finally:
                self._ws_loop = None
                ws_loop.close()

        self._ws_thread = threading.Thread(target=run_ws, daemon=True)
        self._ws_thread.start()

        logger.info("Feishu bot started with WebSocket long connection")
        logger.info("No public IP required - using WebSocket to receive events")

        # Keep the outer task alive until stop() flips the flag.
        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        """Stop the Feishu channel."""
        self._running = False
        ws_client = self._ws_client
        ws_loop = self._ws_loop

        if ws_client is not None:
            try:
                setattr(ws_client, "_auto_reconnect", False)
            except Exception:
                pass

        if ws_client is not None and ws_loop is not None and ws_loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(ws_client._disconnect(), ws_loop)
                future.result(timeout=5)
            except Exception:
                logger.opt(exception=True).debug("Feishu websocket disconnect raised during stop")
            try:
                ws_loop.call_soon_threadsafe(ws_loop.stop)
            except Exception:
                logger.opt(exception=True).debug("Feishu websocket loop stop raised during stop")

        if self._ws_thread is not None and self._ws_thread.is_alive():
            await asyncio.to_thread(self._ws_thread.join, 5)

        self._ws_thread = None
        self._ws_client = None
        self._client = None
        self._loop = None
        logger.info("Feishu bot stopped")

    def _is_bot_mentioned(self, message: Any) -> bool:
        """Check if the bot is @mentioned in the message."""
        raw_content = message.content or ""
        if "@_all" in raw_content:
            return True

        for mention in getattr(message, "mentions", None) or []:
            mid = getattr(mention, "id", None)
            if not mid:
                continue
            # Bot mentions usually have an open_id but no user_id.
            if not getattr(mid, "user_id", None) and (
                getattr(mid, "open_id", None) or ""
            ).startswith("ou_"):
                return True
        return False

    def _is_group_message_for_bot(self, message: Any) -> bool:
        """Allow group messages when policy is open or bot is @mentioned."""
        if self.config.group_policy == "open":
            return True
        return self._is_bot_mentioned(message)

    def _add_reaction_sync(self, message_id: str, emoji_type: str) -> None:
        """Sync helper for adding reaction (runs in thread pool)."""
        from lark_oapi.api.im.v1 import (
            CreateMessageReactionRequest,
            CreateMessageReactionRequestBody,
            Emoji,
        )

        try:
            request = (
                CreateMessageReactionRequest.builder()
                .message_id(message_id)
                .request_body(
                    CreateMessageReactionRequestBody.builder()
                    .reaction_type(Emoji.builder().emoji_type(emoji_type).build())
                    .build()
                )
                .build()
            )

            response = self._client.im.v1.message_reaction.create(request)

            if not response.success():
                logger.warning(
                    "Failed to add reaction: code={}, msg={}", response.code, response.msg
                )
            else:
                logger.debug("Added {} reaction to message {}", emoji_type, message_id)
        except Exception as e:
            logger.warning("Error adding reaction: {}", e)

    async def _add_reaction(self, message_id: str, emoji_type: str = "THUMBSUP") -> None:
        """
        Add a reaction emoji to a message (non-blocking).

        Common emoji types: THUMBSUP, OK, EYES, DONE, OnIt, HEART
        """
        if not self._client:
            return

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._add_reaction_sync, message_id, emoji_type)

    def _schedule_reaction(self, message_id: str, emoji_type: str = "THUMBSUP") -> None:
        """Schedule reaction work in the background so inbound delivery is not blocked."""
        if not self._client or not self._loop or not self._loop.is_running():
            return

        async def _run() -> None:
            try:
                await self._add_reaction(message_id, emoji_type)
            except Exception:
                logger.exception("Feishu background reaction failed: message_id={}", message_id)

        self._loop.call_soon_threadsafe(lambda: asyncio.create_task(_run()))

    def _remember_processed_message(self, message_id: str) -> bool:
        """Track a processed inbound message and return False on duplicate."""
        if message_id in self._processed_message_ids:
            return False
        self._processed_message_ids[message_id] = None
        while len(self._processed_message_ids) > 1000:
            self._processed_message_ids.popitem(last=False)
        return True

    async def _process_inbound_payload(
        self,
        *,
        message_id: str,
        sender_id: str,
        chat_id: str,
        chat_type: str,
        msg_type: str,
        raw_content: str | None,
        parent_id: str | None = None,
        root_id: str | None = None,
        recovered: bool = False,
    ) -> bool:
        """Parse an inbound Feishu payload and forward it to the bus."""
        content_parts: list[str] = []
        media_paths: list[str] = []

        try:
            content_json = json.loads(raw_content) if raw_content else {}
        except json.JSONDecodeError:
            content_json = {}

        if msg_type == "text":
            text = content_json.get("text", "")
            if text:
                content_parts.append(text)

        elif msg_type == "post":
            text, image_keys = _extract_post_content(content_json)
            if text:
                content_parts.append(text)
            for img_key in image_keys:
                file_path, content_text = await self._download_and_save_media(
                    "image", {"image_key": img_key}, message_id
                )
                if file_path:
                    media_paths.append(file_path)
                content_parts.append(content_text)

        elif msg_type in ("image", "audio", "file", "media"):
            file_path, content_text = await self._download_and_save_media(
                msg_type, content_json, message_id
            )
            if file_path:
                media_paths.append(file_path)

            if msg_type == "audio" and file_path:
                transcription = await self.transcribe_audio(file_path)
                if transcription:
                    content_text = f"[transcription: {transcription}]"

            content_parts.append(content_text)

        elif msg_type in (
            "share_chat",
            "share_user",
            "interactive",
            "share_calendar_event",
            "system",
            "merge_forward",
        ):
            text = _extract_share_card_content(content_json, msg_type)
            if text:
                content_parts.append(text)

        else:
            content_parts.append(MSG_TYPE_MAP.get(msg_type, f"[{msg_type}]"))

        if parent_id and self._client and not recovered:
            loop = asyncio.get_running_loop()
            reply_ctx = await loop.run_in_executor(None, self._get_message_content_sync, parent_id)
            if reply_ctx:
                content_parts.insert(0, reply_ctx)

        content = "\n".join(content_parts) if content_parts else ""

        if not content and not media_paths:
            logger.debug(
                "Feishu inbound skipped empty message: message_id={}, msg_type={}, recovered={}",
                message_id,
                msg_type,
                recovered,
            )
            return False

        reply_to = chat_id if chat_type == "group" else sender_id
        logger.debug(
            "Feishu inbound forwarding: message_id={}, sender_id={}, reply_to={}, content_len={}, media_count={}, recovered={}",
            message_id,
            sender_id,
            reply_to,
            len(content),
            len(media_paths),
            recovered,
        )
        await self._handle_message(
            sender_id=sender_id,
            chat_id=reply_to,
            content=content,
            media=media_paths,
            metadata={
                "message_id": message_id,
                "chat_type": chat_type,
                "msg_type": msg_type,
                "parent_id": parent_id,
                "root_id": root_id,
                "_recovered": recovered,
            },
        )
        logger.debug("Feishu inbound forwarded to bus: message_id={}", message_id)
        return True

    async def _recover_recent_messages(
        self,
        *,
        chat_id: str,
        chat_type: str,
        default_sender_id: str,
    ) -> None:
        """Backfill recent messages from the same chat to compensate for missed WS events."""
        if not self._client:
            return

        from lark_oapi.api.im.v1 import ListMessageRequest

        async with self._get_recovery_lock(chat_id):
            now_ms = int(time.time() * 1000)
            start_ms = now_ms - int(self._RECOVERY_WINDOW_S * 1000)
            request = (
                ListMessageRequest.builder()
                .container_id_type("chat")
                .container_id(chat_id)
                .start_time(str(start_ms))
                .end_time(str(now_ms))
                .sort_type("ByCreateTimeAsc")
                .page_size(self._RECOVERY_PAGE_SIZE)
                .build()
            )
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, self._client.im.v1.message.list, request)
            if not response.success():
                logger.warning(
                    "Feishu recovery list failed: chat_id={}, code={}, msg={}",
                    chat_id,
                    response.code,
                    response.msg,
                )
                return

            items = list((response.data.items or []) if response.data else [])
            if not items:
                return

            recovered_count = 0
            for item in items:
                message_id = getattr(item, "message_id", None)
                if not message_id or not self._remember_processed_message(message_id):
                    continue
                if getattr(item, "deleted", False):
                    continue
                sender = getattr(item, "sender", None)
                if getattr(sender, "sender_type", None) == "bot":
                    continue
                sender_id = getattr(sender, "id", None) or default_sender_id
                msg_type = getattr(item, "msg_type", None) or "text"
                body = getattr(item, "body", None)
                raw_content = getattr(body, "content", None)
                processed = await self._process_inbound_payload(
                    message_id=message_id,
                    sender_id=sender_id,
                    chat_id=chat_id,
                    chat_type=chat_type,
                    msg_type=msg_type,
                    raw_content=raw_content,
                    parent_id=getattr(item, "parent_id", None) or None,
                    root_id=getattr(item, "root_id", None) or None,
                    recovered=True,
                )
                if processed:
                    recovered_count += 1

            if recovered_count:
                logger.info(
                    "Feishu recovery replayed {} message(s) for chat {}",
                    recovered_count,
                    chat_id,
                )

    def _schedule_recovery(
        self,
        *,
        chat_id: str,
        chat_type: str,
        sender_id: str,
    ) -> None:
        """Schedule recent-message recovery in the background."""
        if not self._client or not self._loop or not self._loop.is_running():
            return

        async def _run() -> None:
            try:
                await self._recover_recent_messages(
                    chat_id=chat_id,
                    chat_type=chat_type,
                    default_sender_id=sender_id,
                )
            except Exception:
                logger.exception("Feishu recovery failed: chat_id={}", chat_id)

        self._loop.call_soon_threadsafe(lambda: asyncio.create_task(_run()))

    # Match a full markdown table block.
    _TABLE_RE = re.compile(
        r"((?:^[ \t]*\|.+\|[ \t]*\n)(?:^[ \t]*\|[-:\s|]+\|[ \t]*\n)(?:^[ \t]*\|.+\|[ \t]*\n?)+)",
        re.MULTILINE,
    )

    _HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)

    _CODE_BLOCK_RE = re.compile(r"(```[\s\S]*?```)", re.MULTILINE)

    # Remove markdown markers in plain-text surfaces like table cells.
    _MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
    _MD_BOLD_UNDERSCORE_RE = re.compile(r"__(.+?)__")
    _MD_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
    _MD_STRIKE_RE = re.compile(r"~~(.+?)~~")

    @classmethod
    def _strip_md_formatting(cls, text: str) -> str:
        """Remove markdown markers where Feishu cannot render them."""
        text = cls._MD_BOLD_RE.sub(r"\1", text)
        text = cls._MD_BOLD_UNDERSCORE_RE.sub(r"\1", text)
        text = cls._MD_ITALIC_RE.sub(r"\1", text)
        text = cls._MD_STRIKE_RE.sub(r"\1", text)
        return text

    @classmethod
    def _parse_md_table(cls, table_text: str) -> dict | None:
        """Parse a markdown table into a Feishu table element."""
        lines = [_line.strip() for _line in table_text.strip().split("\n") if _line.strip()]
        if len(lines) < 3:
            return None

        def split(_line: str) -> list[str]:
            return [c.strip() for c in _line.strip("|").split("|")]

        headers = [cls._strip_md_formatting(h) for h in split(lines[0])]
        rows = [[cls._strip_md_formatting(c) for c in split(_line)] for _line in lines[2:]]
        columns = [
            {"tag": "column", "name": f"c{i}", "display_name": h, "width": "auto"}
            for i, h in enumerate(headers)
        ]
        return {
            "tag": "table",
            "page_size": len(rows) + 1,
            "columns": columns,
            "rows": [
                {f"c{i}": r[i] if i < len(r) else "" for i in range(len(headers))} for r in rows
            ],
        }

    def _build_card_elements(self, content: str) -> list[dict]:
        """Split content into div/markdown + table elements for Feishu card."""
        elements, last_end = [], 0
        for m in self._TABLE_RE.finditer(content):
            before = content[last_end : m.start()]
            if before.strip():
                elements.extend(self._split_headings(before))
            elements.append(
                self._parse_md_table(m.group(1)) or {"tag": "markdown", "content": m.group(1)}
            )
            last_end = m.end()
        remaining = content[last_end:]
        if remaining.strip():
            elements.extend(self._split_headings(remaining))
        return elements or [{"tag": "markdown", "content": content}]

    @staticmethod
    def _split_elements_by_table_limit(
        elements: list[dict], max_tables: int = 1
    ) -> list[list[dict]]:
        """Split card elements so each chunk stays under the table limit."""
        if not elements:
            return [[]]
        groups: list[list[dict]] = []
        current: list[dict] = []
        table_count = 0
        for el in elements:
            if el.get("tag") == "table":
                if table_count >= max_tables:
                    if current:
                        groups.append(current)
                    current = []
                    table_count = 0
                current.append(el)
                table_count += 1
            else:
                current.append(el)
        if current:
            groups.append(current)
        return groups or [[]]

    def _split_headings(self, content: str) -> list[dict]:
        """Split content by headings, converting headings to div elements."""
        protected = content
        code_blocks = []
        for m in self._CODE_BLOCK_RE.finditer(content):
            code_blocks.append(m.group(1))
            protected = protected.replace(m.group(1), f"\x00CODE{len(code_blocks) - 1}\x00", 1)

        elements = []
        last_end = 0
        for m in self._HEADING_RE.finditer(protected):
            before = protected[last_end : m.start()].strip()
            if before:
                elements.append({"tag": "markdown", "content": before})
            text = self._strip_md_formatting(m.group(2).strip())
            display_text = f"**{text}**" if text else ""
            elements.append(
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": display_text,
                    },
                }
            )
            last_end = m.end()
        remaining = protected[last_end:].strip()
        if remaining:
            elements.append({"tag": "markdown", "content": remaining})

        for i, cb in enumerate(code_blocks):
            for el in elements:
                if el.get("tag") == "markdown":
                    el["content"] = el["content"].replace(f"\x00CODE{i}\x00", cb)

        return elements or [{"tag": "markdown", "content": content}]

    # Markdown patterns that need interactive cards.
    _COMPLEX_MD_RE = re.compile(
        r"```"  # fenced code block
        r"|^\|.+\|.*\n\s*\|[-:\s|]+\|"  # markdown table (header + separator)
        r"|^#{1,6}\s+",  # headings
        re.MULTILINE,
    )

    # Inline markdown that plain post messages cannot render well.
    _SIMPLE_MD_RE = re.compile(
        r"\*\*.+?\*\*"  # **bold**
        r"|__.+?__"  # __bold__
        r"|(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)"  # *italic* (single *)
        r"|~~.+?~~",  # ~~strikethrough~~
        re.DOTALL,
    )

    # Markdown link syntax.
    _MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\)]+)\)")

    # Unordered list items.
    _LIST_RE = re.compile(r"^[\s]*[-*+]\s+", re.MULTILINE)

    # Ordered list items.
    _OLIST_RE = re.compile(r"^[\s]*\d+\.\s+", re.MULTILINE)

    # Plain text threshold.
    _TEXT_MAX_LEN = 200

    # Post threshold. Longer messages become cards.
    _POST_MAX_LEN = 2000

    @classmethod
    def _detect_msg_format(cls, content: str) -> str:
        """Choose the best Feishu message format for a text payload."""
        stripped = content.strip()

        # Code blocks, tables, and headings need cards.
        if cls._COMPLEX_MD_RE.search(stripped):
            return "interactive"

        # Long messages read better as cards.
        if len(stripped) > cls._POST_MAX_LEN:
            return "interactive"

        # Rich inline markdown also needs cards.
        if cls._SIMPLE_MD_RE.search(stripped):
            return "interactive"

        # Lists do not render well in plain posts.
        if cls._LIST_RE.search(stripped) or cls._OLIST_RE.search(stripped):
            return "interactive"

        # Simple links fit in post format.
        if cls._MD_LINK_RE.search(stripped):
            return "post"

        # Short plain text can stay plain.
        if len(stripped) <= cls._TEXT_MAX_LEN:
            return "text"

        # Medium plain text uses rich post format.
        return "post"

    @classmethod
    def _markdown_to_post(cls, content: str) -> str:
        """Convert simple markdown into Feishu post JSON."""
        lines = content.strip().split("\n")
        paragraphs: list[list[dict]] = []

        for line in lines:
            elements: list[dict] = []
            last_end = 0

            for m in cls._MD_LINK_RE.finditer(line):
                # Keep plain text that appears before the link.
                before = line[last_end : m.start()]
                if before:
                    elements.append({"tag": "text", "text": before})
                elements.append(
                    {
                        "tag": "a",
                        "text": m.group(1),
                        "href": m.group(2),
                    }
                )
                last_end = m.end()

            # Keep trailing plain text after the last link.
            remaining = line[last_end:]
            if remaining:
                elements.append({"tag": "text", "text": remaining})

            # Preserve blank lines as empty rows.
            if not elements:
                elements.append({"tag": "text", "text": ""})

            paragraphs.append(elements)

        post_body = {
            "zh_cn": {
                "content": paragraphs,
            }
        }
        return json.dumps(post_body, ensure_ascii=False)

    _IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico", ".tiff", ".tif"}
    _AUDIO_EXTS = {".opus"}
    _VIDEO_EXTS = {".mp4", ".mov", ".avi"}
    _FILE_TYPE_MAP = {
        ".opus": "opus",
        ".mp4": "mp4",
        ".pdf": "pdf",
        ".doc": "doc",
        ".docx": "doc",
        ".xls": "xls",
        ".xlsx": "xls",
        ".ppt": "ppt",
        ".pptx": "ppt",
    }

    def _upload_image_sync(self, file_path: str) -> str | None:
        """Upload an image to Feishu and return the image_key."""
        from lark_oapi.api.im.v1 import CreateImageRequest, CreateImageRequestBody

        try:
            with open(file_path, "rb") as f:
                request = (
                    CreateImageRequest.builder()
                    .request_body(
                        CreateImageRequestBody.builder().image_type("message").image(f).build()
                    )
                    .build()
                )
                response = self._client.im.v1.image.create(request)
                if response.success():
                    image_key = response.data.image_key
                    logger.debug("Uploaded image {}: {}", os.path.basename(file_path), image_key)
                    return image_key
                else:
                    logger.error(
                        "Failed to upload image: code={}, msg={}", response.code, response.msg
                    )
                    return None
        except Exception as e:
            logger.error("Error uploading image {}: {}", file_path, e)
            return None

    def _upload_file_sync(self, file_path: str) -> str | None:
        """Upload a file to Feishu and return the file_key."""
        from lark_oapi.api.im.v1 import CreateFileRequest, CreateFileRequestBody

        ext = os.path.splitext(file_path)[1].lower()
        file_type = self._FILE_TYPE_MAP.get(ext, "stream")
        file_name = os.path.basename(file_path)
        try:
            with open(file_path, "rb") as f:
                request = (
                    CreateFileRequest.builder()
                    .request_body(
                        CreateFileRequestBody.builder()
                        .file_type(file_type)
                        .file_name(file_name)
                        .file(f)
                        .build()
                    )
                    .build()
                )
                response = self._client.im.v1.file.create(request)
                if response.success():
                    file_key = response.data.file_key
                    logger.debug("Uploaded file {}: {}", file_name, file_key)
                    return file_key
                else:
                    logger.error(
                        "Failed to upload file: code={}, msg={}", response.code, response.msg
                    )
                    return None
        except Exception as e:
            logger.error("Error uploading file {}: {}", file_path, e)
            return None

    def _download_image_sync(
        self, message_id: str, image_key: str
    ) -> tuple[bytes | None, str | None]:
        """Download an image from Feishu message by message_id and image_key."""
        from lark_oapi.api.im.v1 import GetMessageResourceRequest

        try:
            request = (
                GetMessageResourceRequest.builder()
                .message_id(message_id)
                .file_key(image_key)
                .type("image")
                .build()
            )
            response = self._client.im.v1.message_resource.get(request)
            if response.success():
                file_data = response.file
                # The SDK may return a BytesIO instead of raw bytes.
                if hasattr(file_data, "read"):
                    file_data = file_data.read()
                return file_data, response.file_name
            else:
                logger.error(
                    "Failed to download image: code={}, msg={}", response.code, response.msg
                )
                return None, None
        except Exception as e:
            logger.error("Error downloading image {}: {}", image_key, e)
            return None, None

    def _download_file_sync(
        self, message_id: str, file_key: str, resource_type: str = "file"
    ) -> tuple[bytes | None, str | None]:
        """Download a file/audio/media from a Feishu message by message_id and file_key."""
        from lark_oapi.api.im.v1 import GetMessageResourceRequest

        # The API only accepts "image" or "file" here.
        if resource_type == "audio":
            resource_type = "file"

        try:
            request = (
                GetMessageResourceRequest.builder()
                .message_id(message_id)
                .file_key(file_key)
                .type(resource_type)
                .build()
            )
            response = self._client.im.v1.message_resource.get(request)
            if response.success():
                file_data = response.file
                if hasattr(file_data, "read"):
                    file_data = file_data.read()
                return file_data, response.file_name
            else:
                logger.error(
                    "Failed to download {}: code={}, msg={}",
                    resource_type,
                    response.code,
                    response.msg,
                )
                return None, None
        except Exception:
            logger.exception("Error downloading {} {}", resource_type, file_key)
            return None, None

    async def _download_and_save_media(
        self, msg_type: str, content_json: dict, message_id: str | None = None
    ) -> tuple[str | None, str]:
        """Download Feishu media and save it locally."""
        loop = asyncio.get_running_loop()
        media_dir = get_media_dir("feishu")

        data, filename = None, None

        if msg_type == "image":
            image_key = content_json.get("image_key")
            if image_key and message_id:
                data, filename = await loop.run_in_executor(
                    None, self._download_image_sync, message_id, image_key
                )
                if not filename:
                    filename = f"{image_key[:16]}.jpg"

        elif msg_type in ("audio", "file", "media"):
            file_key = content_json.get("file_key")
            if file_key and message_id:
                data, filename = await loop.run_in_executor(
                    None, self._download_file_sync, message_id, file_key, msg_type
                )
                if not filename:
                    filename = file_key[:16]
                if msg_type == "audio" and not filename.endswith(".opus"):
                    filename = f"{filename}.opus"

        if data and filename:
            file_path = media_dir / filename
            file_path.write_bytes(data)
            logger.debug("Downloaded {} to {}", msg_type, file_path)
            return str(file_path), f"[{msg_type}: {filename}]"

        return None, f"[{msg_type}: download failed]"

    _REPLY_CONTEXT_MAX_LEN = 200

    def _get_message_content_sync(self, message_id: str) -> str | None:
        """Fetch a short reply-context string for a message id."""
        from lark_oapi.api.im.v1 import GetMessageRequest

        try:
            request = GetMessageRequest.builder().message_id(message_id).build()
            response = self._client.im.v1.message.get(request)
            if not response.success():
                logger.debug(
                    "Feishu: could not fetch parent message {}: code={}, msg={}",
                    message_id,
                    response.code,
                    response.msg,
                )
                return None
            items = getattr(response.data, "items", None)
            if not items:
                return None
            msg_obj = items[0]
            raw_content = getattr(msg_obj, "body", None)
            raw_content = getattr(raw_content, "content", None) if raw_content else None
            if not raw_content:
                return None
            try:
                content_json = json.loads(raw_content)
            except (json.JSONDecodeError, TypeError):
                return None
            msg_type = getattr(msg_obj, "msg_type", "")
            if msg_type == "text":
                text = content_json.get("text", "").strip()
            elif msg_type == "post":
                text, _ = _extract_post_content(content_json)
                text = text.strip()
            else:
                text = ""
            if not text:
                return None
            if len(text) > self._REPLY_CONTEXT_MAX_LEN:
                text = text[: self._REPLY_CONTEXT_MAX_LEN] + "..."
            return f"[Reply to: {text}]"
        except Exception as e:
            logger.debug("Feishu: error fetching parent message {}: {}", message_id, e)
            return None

    def _reply_message_sync(self, parent_message_id: str, msg_type: str, content: str) -> bool:
        """Reply to an existing Feishu message using the Reply API (synchronous)."""
        from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody

        try:
            request = (
                ReplyMessageRequest.builder()
                .message_id(parent_message_id)
                .request_body(
                    ReplyMessageRequestBody.builder().msg_type(msg_type).content(content).build()
                )
                .build()
            )
            response = self._client.im.v1.message.reply(request)
            if not response.success():
                logger.error(
                    "Failed to reply to Feishu message {}: code={}, msg={}, log_id={}",
                    parent_message_id,
                    response.code,
                    response.msg,
                    response.get_log_id(),
                )
                return False
            logger.debug("Feishu reply sent to message {}", parent_message_id)
            return True
        except Exception as e:
            logger.error("Error replying to Feishu message {}: {}", parent_message_id, e)
            return False

    def _send_message_sync(
        self, receive_id_type: str, receive_id: str, msg_type: str, content: str
    ) -> bool:
        """Send a single message (text/image/file/interactive) synchronously."""
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

        try:
            request = (
                CreateMessageRequest.builder()
                .receive_id_type(receive_id_type)
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(receive_id)
                    .msg_type(msg_type)
                    .content(content)
                    .build()
                )
                .build()
            )
            response = self._client.im.v1.message.create(request)
            if not response.success():
                logger.error(
                    "Failed to send Feishu {} message: code={}, msg={}, log_id={}",
                    msg_type,
                    response.code,
                    response.msg,
                    response.get_log_id(),
                )
                return False
            logger.debug("Feishu {} message sent to {}", msg_type, receive_id)
            return True
        except Exception as e:
            logger.error("Error sending Feishu {} message: {}", msg_type, e)
            return False

    async def send(self, msg: OutboundMessage) -> None:
        """Send text and media through Feishu."""
        if not self._client:
            logger.warning("Feishu client not initialized")
            return

        if _should_skip_progress_text(msg):
            return

        if not self._should_send_progress(msg):
            return

        try:
            receive_id_type = "chat_id" if msg.chat_id.startswith("oc_") else "open_id"

            # Tool hints always use card rendering and skip reply threading.
            if msg.metadata.get("_tool_hint"):
                if msg.content and msg.content.strip():
                    await self._send_tool_hint_card(
                        receive_id_type, msg.chat_id, msg.content.strip()
                    )
                return

            # Only the first outbound chunk should use reply threading.
            reply_message_id: str | None = None
            if self.config.reply_to_message and not msg.metadata.get("_progress", False):
                reply_message_id = msg.metadata.get("message_id") or None

            first_send = True  # True until the reply slot is used once.

            def _do_send(m_type: str, content: str) -> None:
                """Send via reply (first message) or create (subsequent)."""
                nonlocal first_send
                if reply_message_id and first_send:
                    first_send = False
                    ok = self._reply_message_sync(reply_message_id, m_type, content)
                    if ok:
                        return
                    # Fall back to a normal send when reply fails.
                self._send_message_sync(receive_id_type, msg.chat_id, m_type, content)

            for file_path in msg.media:
                if not os.path.isfile(file_path):
                    logger.warning("Media file not found: {}", file_path)
                    continue
                ext = os.path.splitext(file_path)[1].lower()
                if ext in self._IMAGE_EXTS:
                    loop = asyncio.get_running_loop()
                    key = await loop.run_in_executor(None, self._upload_image_sync, file_path)
                    if key:
                        await self._send_with_rate_limit(
                            msg.chat_id,
                            _do_send,
                            "image",
                            json.dumps({"image_key": key}, ensure_ascii=False),
                        )
                else:
                    loop = asyncio.get_running_loop()
                    key = await loop.run_in_executor(None, self._upload_file_sync, file_path)
                    if key:
                        # Pick the standalone Feishu msg_type for this file.
                        if ext in self._AUDIO_EXTS:
                            media_type = "audio"
                        elif ext in self._VIDEO_EXTS:
                            media_type = "video"
                        else:
                            media_type = "file"
                        await self._send_with_rate_limit(
                            msg.chat_id,
                            _do_send,
                            media_type,
                            json.dumps({"file_key": key}, ensure_ascii=False),
                        )

            if msg.content and msg.content.strip():
                fmt = self._detect_msg_format(msg.content)

                if fmt == "text":
                    # Short plain text uses the text message type.
                    text_body = json.dumps({"text": msg.content.strip()}, ensure_ascii=False)
                    await self._send_with_rate_limit(msg.chat_id, _do_send, "text", text_body)

                elif fmt == "post":
                    # Rich text with simple links uses post format.
                    post_body = self._markdown_to_post(msg.content)
                    await self._send_with_rate_limit(msg.chat_id, _do_send, "post", post_body)

                else:
                    # Complex or long content uses cards.
                    elements = self._build_card_elements(msg.content)
                    for chunk in self._split_elements_by_table_limit(elements):
                        card = {"config": {"wide_screen_mode": True}, "elements": chunk}
                        await self._send_with_rate_limit(
                            msg.chat_id,
                            _do_send,
                            "interactive",
                            json.dumps(card, ensure_ascii=False),
                        )

        except Exception as e:
            logger.error("Error sending Feishu message: {}", e)

    def _on_message_sync(self, data: Any) -> None:
        """
        Sync handler for incoming messages (called from WebSocket thread).
        Schedules async handling in the main event loop.
        """
        if not self._running:
            return
        if self._loop and self._loop.is_running():
            event = getattr(data, "event", None)
            message = getattr(event, "message", None)
            message_id = getattr(message, "message_id", "unknown")
            msg_type = getattr(message, "message_type", "unknown")
            chat_id = getattr(message, "chat_id", "unknown")
            future = asyncio.run_coroutine_threadsafe(self._on_message(data), self._loop)

            def _log_result(done_future: Any) -> None:
                try:
                    done_future.result()
                except Exception:
                    logger.exception(
                        "Feishu inbound future failed: message_id={}, msg_type={}, chat_id={}",
                        message_id,
                        msg_type,
                        chat_id,
                    )

            future.add_done_callback(_log_result)

    async def _on_message(self, data: Any) -> None:
        """Handle incoming message from Feishu."""
        if not self._running:
            return
        try:
            event = data.event
            message = event.message
            sender = event.sender

            # Ignore duplicate events.
            message_id = message.message_id
            if not self._remember_processed_message(message_id):
                return

            # Ignore bot-originated messages.
            if sender.sender_type == "bot":
                return

            sender_id = sender.sender_id.open_id if sender.sender_id else "unknown"
            chat_id = message.chat_id
            chat_type = message.chat_type
            msg_type = message.message_type

            if chat_type == "group" and not self._is_group_message_for_bot(message):
                return

            # Keep reply context ids for downstream handling.
            parent_id = getattr(message, "parent_id", None) or None
            root_id = getattr(message, "root_id", None) or None
            processed = await self._process_inbound_payload(
                message_id=message_id,
                sender_id=sender_id,
                chat_id=chat_id,
                chat_type=chat_type,
                msg_type=msg_type,
                raw_content=message.content,
                parent_id=parent_id,
                root_id=root_id,
            )
            if processed:
                self._schedule_reaction(message_id, self.config.react_emoji)
                self._schedule_recovery(chat_id=chat_id, chat_type=chat_type, sender_id=sender_id)

        except Exception as e:
            logger.error("Error processing Feishu message: {}", e)

    def _on_reaction_created(self, data: Any) -> None:
        """Ignore reaction events so they do not generate SDK noise."""
        pass

    def _on_message_read(self, data: Any) -> None:
        """Ignore read events so they do not generate SDK noise."""
        pass

    def _on_bot_p2p_chat_entered(self, data: Any) -> None:
        """Ignore p2p-enter events when a user opens a bot chat."""
        logger.debug("Bot entered p2p chat (user opened chat window)")
        pass

    @staticmethod
    def _format_tool_hint_lines(tool_hint: str) -> str:
        """Split tool hints across lines on top-level call separators only."""
        parts: list[str] = []
        buf: list[str] = []
        depth = 0
        in_string = False
        quote_char = ""
        escaped = False

        for i, ch in enumerate(tool_hint):
            buf.append(ch)

            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == quote_char:
                    in_string = False
                continue

            if ch in {'"', "'"}:
                in_string = True
                quote_char = ch
                continue

            if ch == "(":
                depth += 1
                continue

            if ch == ")" and depth > 0:
                depth -= 1
                continue

            if ch == "," and depth == 0:
                next_char = tool_hint[i + 1] if i + 1 < len(tool_hint) else ""
                if next_char == " ":
                    parts.append("".join(buf).rstrip())
                    buf = []

        if buf:
            parts.append("".join(buf).strip())

        return "\n".join(part for part in parts if part)

    async def _send_tool_hint_card(
        self, receive_id_type: str, receive_id: str, tool_hint: str
    ) -> None:
        """Send tool calls as a formatted interactive card."""
        # Split top-level calls without touching commas inside arguments.
        formatted_code = self._format_tool_hint_lines(tool_hint)

        card = {
            "config": {"wide_screen_mode": True},
            "elements": [
                {"tag": "markdown", "content": f"**Tool Calls**\n\n```text\n{formatted_code}\n```"}
            ],
        }

        await self._send_with_rate_limit(
            receive_id,
            self._send_message_sync,
            receive_id_type,
            receive_id,
            "interactive",
            json.dumps(card, ensure_ascii=False),
        )
