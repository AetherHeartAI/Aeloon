"""Typed message models for the iLink HTTP API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MessageTypeUser = 1
MessageTypeBot = 2

MessageStateFinish = 2

ItemTypeText = 1
ItemTypeImage = 2
ItemTypeVoice = 3
ItemTypeFile = 4
ItemTypeVideo = 5

CDNMediaTypeImage = 1
CDNMediaTypeVideo = 2
CDNMediaTypeFile = 3

TypingStatusTyping = 1


@dataclass(slots=True)
class Credentials:
    bot_token: str
    ilink_bot_id: str
    base_url: str = ""
    ilink_user_id: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Credentials":
        return cls(
            bot_token=str(data.get("bot_token") or ""),
            ilink_bot_id=str(data.get("ilink_bot_id") or ""),
            base_url=str(data.get("baseurl") or data.get("base_url") or ""),
            ilink_user_id=str(data.get("ilink_user_id") or ""),
        )


@dataclass(slots=True)
class MediaInfo:
    encrypt_query_param: str = ""
    aes_key: str = ""
    encrypt_type: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "MediaInfo | None":
        if not isinstance(data, dict):
            return None
        return cls(
            encrypt_query_param=str(data.get("encrypt_query_param") or ""),
            aes_key=str(data.get("aes_key") or ""),
            encrypt_type=int(data.get("encrypt_type") or 0),
        )


@dataclass(slots=True)
class TextItem:
    text: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "TextItem | None":
        if not isinstance(data, dict):
            return None
        return cls(text=str(data.get("text") or ""))


@dataclass(slots=True)
class ImageItem:
    url: str = ""
    media: MediaInfo | None = None
    mid_size: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ImageItem | None":
        if not isinstance(data, dict):
            return None
        return cls(
            url=str(data.get("url") or ""),
            media=MediaInfo.from_dict(data.get("media")),
            mid_size=int(data.get("mid_size") or 0),
        )


@dataclass(slots=True)
class VideoItem:
    media: MediaInfo | None = None
    video_size: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "VideoItem | None":
        if not isinstance(data, dict):
            return None
        return cls(
            media=MediaInfo.from_dict(data.get("media")),
            video_size=int(data.get("video_size") or 0),
        )


@dataclass(slots=True)
class FileItem:
    media: MediaInfo | None = None
    file_name: str = ""
    length: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "FileItem | None":
        if not isinstance(data, dict):
            return None
        return cls(
            media=MediaInfo.from_dict(data.get("media")),
            file_name=str(data.get("file_name") or ""),
            length=str(data.get("len") or ""),
        )


@dataclass(slots=True)
class MessageItem:
    type: int
    text_item: TextItem | None = None
    image_item: ImageItem | None = None
    video_item: VideoItem | None = None
    file_item: FileItem | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MessageItem":
        return cls(
            type=int(data.get("type") or 0),
            text_item=TextItem.from_dict(data.get("text_item")),
            image_item=ImageItem.from_dict(data.get("image_item")),
            video_item=VideoItem.from_dict(data.get("video_item")),
            file_item=FileItem.from_dict(data.get("file_item")),
        )


@dataclass(slots=True)
class WeixinMessage:
    from_user_id: str
    to_user_id: str
    message_type: int
    message_state: int
    item_list: list[MessageItem]
    context_token: str = ""
    message_id: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WeixinMessage":
        items = data.get("item_list") or []
        return cls(
            from_user_id=str(data.get("from_user_id") or ""),
            to_user_id=str(data.get("to_user_id") or ""),
            message_type=int(data.get("message_type") or 0),
            message_state=int(data.get("message_state") or 0),
            item_list=[MessageItem.from_dict(item) for item in items if isinstance(item, dict)],
            context_token=str(data.get("context_token") or ""),
            message_id=str(
                data.get("message_id")
                or data.get("msg_id")
                or data.get("client_msg_id")
                or data.get("client_id")
                or data.get("id")
                or ""
            ),
        )
