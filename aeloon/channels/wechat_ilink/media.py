"""Media helpers for native WeChat/iLink transport."""

from __future__ import annotations

import base64
import hashlib
import mimetypes
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from aeloon.utils.helpers import detect_image_mime

from .client import ILinkClient, build_base_info
from .types import (
    CDNMediaTypeFile,
    CDNMediaTypeImage,
    CDNMediaTypeVideo,
    ItemTypeFile,
    ItemTypeImage,
    ItemTypeVideo,
)

CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"
_STRICT_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


@dataclass(slots=True)
class UploadedFile:
    download_param: str
    aes_key_hex: str
    file_size: int
    cipher_size: int


def _format_ilink_error(action: str, response: dict[str, Any]) -> str:
    ret = response.get("ret")
    errcode = response.get("errcode")
    errmsg = str(response.get("errmsg") or "").strip() or "<empty>"

    details: list[str] = []
    for key in ("retmsg", "message", "detail"):
        value = str(response.get(key) or "").strip()
        if value:
            details.append(f"{key}={value}")

    detail_suffix = f" {' '.join(details)}" if details else ""
    return (
        f"{action} failed"
        f" ret={ret if ret is not None else '<missing>'}"
        f" errcode={errcode if errcode is not None else '<missing>'}"
        f" errmsg={errmsg}{detail_suffix}"
    )


def _resolve_upload_url(upload_response: dict[str, Any], file_key: str) -> str:
    upload_full_url = str(upload_response.get("upload_full_url") or "").strip()
    if upload_full_url:
        return upload_full_url

    upload_param = str(upload_response.get("upload_param") or "").strip()
    if upload_param:
        return (
            f"{CDN_BASE_URL}/upload"
            f"?encrypted_query_param={quote(upload_param)}&filekey={quote(file_key)}"
        )

    raise RuntimeError(
        "CDN upload URL missing: getuploadurl returned neither upload_full_url nor upload_param"
    )


async def _upload_ciphertext(upload_url: str, encrypted: bytes) -> str:
    async with httpx.AsyncClient(timeout=60.0) as http:
        response = await http.post(
            upload_url,
            headers={"Content-Type": "application/octet-stream"},
            content=encrypted,
        )
    if response.is_error:
        body = response.text.strip().replace("\n", " ")[:200]
        raise RuntimeError(
            f"CDN upload failed status={response.status_code} body={body or '<empty>'}"
        )

    download_param = response.headers.get("X-Encrypted-Param", "")
    if not download_param:
        raise RuntimeError("CDN upload missing X-Encrypted-Param")
    return download_param


def _pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len]) * pad_len


def _pkcs7_unpad(data: bytes, block_size: int = 16) -> bytes:
    if not data:
        return data
    pad_len = data[-1]
    if pad_len <= 0 or pad_len > block_size:
        raise ValueError("invalid PKCS7 padding")
    return data[:-pad_len]


def encrypt_aes_ecb(data: bytes, key: bytes) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    encryptor = cipher.encryptor()
    return encryptor.update(_pkcs7_pad(data)) + encryptor.finalize()


def decrypt_aes_ecb(data: bytes, key: bytes) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    decryptor = cipher.decryptor()
    plain = decryptor.update(data) + decryptor.finalize()
    return _pkcs7_unpad(plain)


def aes_key_to_base64(hex_key: str) -> str:
    return base64.b64encode(hex_key.encode()).decode()


def classify_media(path: str, mime: str | None = None) -> tuple[int, int]:
    mime = (mime or mimetypes.guess_type(path)[0] or "").lower()
    ext = Path(path).suffix.lower()
    if mime.startswith("image/") or ext in {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".bmp",
    }:
        return CDNMediaTypeImage, ItemTypeImage
    if mime.startswith("video/") or ext in {".mp4", ".mov", ".webm", ".mkv", ".avi"}:
        return CDNMediaTypeVideo, ItemTypeVideo
    return CDNMediaTypeFile, ItemTypeFile


def _classify_outbound_media(path: str, data: bytes) -> tuple[int, int]:
    image_mime = detect_image_mime(data)
    ext = Path(path).suffix.lower()
    if ext in _STRICT_IMAGE_EXTENSIONS and not image_mime:
        raise RuntimeError(
            f"outbound image validation failed: {path} is not a supported image file"
        )
    return classify_media(path, image_mime)


async def upload_file_to_cdn(
    client: ILinkClient,
    data: bytes,
    to_user_id: str,
    media_type: int,
) -> UploadedFile:
    file_key = os.urandom(16).hex()
    aes_key = os.urandom(16)
    aes_key_hex = aes_key.hex()
    encrypted = encrypt_aes_ecb(data, aes_key)
    raw_md5 = hashlib.md5(data).hexdigest()

    upload_response = await client.get_upload_url(
        {
            "filekey": file_key,
            "media_type": media_type,
            "to_user_id": to_user_id,
            "rawsize": len(data),
            "rawfilemd5": raw_md5,
            "filesize": len(encrypted),
            "no_need_thumb": True,
            "aeskey": aes_key_hex,
            "base_info": build_base_info(),
        }
    )
    if upload_response.get("ret", 0) != 0:
        raise RuntimeError(_format_ilink_error("get upload URL", upload_response))

    upload_url = _resolve_upload_url(upload_response, file_key)
    download_param = await _upload_ciphertext(upload_url, encrypted)

    return UploadedFile(
        download_param=download_param,
        aes_key_hex=aes_key_hex,
        file_size=len(data),
        cipher_size=len(encrypted),
    )


async def download_file_from_cdn(encrypt_query_param: str, aes_key_b64: str) -> bytes:
    aes_key_hex = base64.b64decode(aes_key_b64).decode()
    aes_key = bytes.fromhex(aes_key_hex)
    download_url = f"{CDN_BASE_URL}/download?encrypted_query_param={quote(encrypt_query_param)}"
    async with httpx.AsyncClient(timeout=60.0) as http:
        response = await http.get(download_url)
        response.raise_for_status()
    return decrypt_aes_ecb(response.content, aes_key)


async def download_image_item(image_item, save_dir: Path) -> str | None:
    raw: bytes | None = None
    if image_item.media and image_item.media.encrypt_query_param and image_item.media.aes_key:
        raw = await download_file_from_cdn(
            image_item.media.encrypt_query_param,
            image_item.media.aes_key,
        )
    elif image_item.url and (
        image_item.url.startswith("http://") or image_item.url.startswith("https://")
    ):
        async with httpx.AsyncClient(timeout=60.0) as http:
            response = await http.get(image_item.url)
            response.raise_for_status()
            raw = response.content
    if not raw:
        return None

    save_dir.mkdir(parents=True, exist_ok=True)
    mime = detect_image_mime(raw) or "image/jpeg"
    ext = mimetypes.guess_extension(mime) or ".jpg"
    path = save_dir / f"{uuid.uuid4().hex}{ext}"
    path.write_bytes(raw)
    return str(path)


async def download_file_item(file_item, save_dir: Path) -> str | None:
    if not (file_item.media and file_item.media.encrypt_query_param and file_item.media.aes_key):
        return None
    raw = await download_file_from_cdn(
        file_item.media.encrypt_query_param,
        file_item.media.aes_key,
    )
    save_dir.mkdir(parents=True, exist_ok=True)
    file_name = file_item.file_name or f"{uuid.uuid4().hex}.bin"
    path = save_dir / Path(file_name).name
    path.write_bytes(raw)
    return str(path)


async def send_text_message(
    client: ILinkClient,
    to_user_id: str,
    text: str,
    context_token: str = "",
) -> None:
    response = await client.send_message(
        {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": str(uuid.uuid4()),
                "message_type": 2,
                "message_state": 2,
                "item_list": [{"type": 1, "text_item": {"text": text}}],
                "context_token": context_token,
            },
            "base_info": build_base_info(),
        }
    )
    if response.get("ret", 0) != 0:
        raise RuntimeError(_format_ilink_error("send message", response))


async def send_media_from_path(
    client: ILinkClient,
    to_user_id: str,
    path: str,
    context_token: str = "",
) -> None:
    data = Path(path).read_bytes()
    media_type, item_type = _classify_outbound_media(path, data)
    uploaded = await upload_file_to_cdn(client, data, to_user_id, media_type)
    media = {
        "encrypt_query_param": uploaded.download_param,
        "aes_key": aes_key_to_base64(uploaded.aes_key_hex),
        "encrypt_type": 1,
    }

    if item_type == ItemTypeImage:
        item = {
            "type": item_type,
            "image_item": {"media": media, "mid_size": uploaded.cipher_size},
        }
    elif item_type == ItemTypeVideo:
        item = {
            "type": item_type,
            "video_item": {"media": media, "video_size": uploaded.cipher_size},
        }
    else:
        item = {
            "type": item_type,
            "file_item": {
                "media": media,
                "file_name": Path(path).name,
                "len": str(uploaded.file_size),
            },
        }

    response = await client.send_message(
        {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": str(uuid.uuid4()),
                "message_type": 2,
                "message_state": 2,
                "item_list": [item],
                "context_token": context_token,
            },
            "base_info": build_base_info(),
        }
    )
    if response.get("ret", 0) != 0:
        raise RuntimeError(_format_ilink_error("send media", response))
