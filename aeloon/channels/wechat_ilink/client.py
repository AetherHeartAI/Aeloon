"""Async iLink HTTP client."""

from __future__ import annotations

import base64
import json
import os
from typing import Any

import httpx

from .types import Credentials

DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
ILINK_CHANNEL_VERSION = "1.0.0"
ILINK_APP_CLIENT_VERSION = "1"


def build_base_info(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build default iLink base_info."""
    base_info: dict[str, Any] = {"channel_version": ILINK_CHANNEL_VERSION}
    if extra:
        base_info.update(extra)
    return base_info


class ILinkClient:
    """Minimal async client for the iLink bot HTTP API."""

    def __init__(self, credentials: Credentials):
        self.credentials = credentials
        self.base_url = credentials.base_url or DEFAULT_BASE_URL
        self.bot_id = credentials.ilink_bot_id
        self.http = httpx.AsyncClient(timeout=40.0, trust_env=False)
        self._wechat_uin = base64.b64encode(str(int.from_bytes(os.urandom(4), "little")).encode())
        self._wechat_uin = self._wechat_uin.decode()

    async def aclose(self) -> None:
        await self.http.aclose()

    async def get_updates(self, cursor: str) -> dict[str, Any]:
        return await self._post(
            "/ilink/bot/getupdates",
            {
                "get_updates_buf": cursor,
            },
            timeout=40.0,
        )

    async def send_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/ilink/bot/sendmessage", payload, timeout=15.0)

    async def get_config(self, user_id: str, context_token: str = "") -> dict[str, Any]:
        return await self._post(
            "/ilink/bot/getconfig",
            {
                "ilink_user_id": user_id,
                "context_token": context_token,
            },
            timeout=10.0,
        )

    async def send_typing(self, user_id: str, typing_ticket: str, status: int) -> dict[str, Any]:
        return await self._post(
            "/ilink/bot/sendtyping",
            {
                "ilink_user_id": user_id,
                "typing_ticket": typing_ticket,
                "status": status,
            },
            timeout=10.0,
        )

    async def get_upload_url(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/ilink/bot/getuploadurl", payload, timeout=15.0)

    def headers(self, content_length: int | None = None) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "Authorization": f"Bearer {self.credentials.bot_token}",
            "X-WECHAT-UIN": self._wechat_uin,
            "iLink-App-ClientVersion": ILINK_APP_CLIENT_VERSION,
        }
        if content_length is not None:
            headers["Content-Length"] = str(content_length)
        return headers

    @staticmethod
    def _merge_base_info(payload: dict[str, Any]) -> dict[str, Any]:
        merged = dict(payload)
        extra = merged.get("base_info")
        merged["base_info"] = build_base_info(extra if isinstance(extra, dict) else None)
        return merged

    async def _post(self, path: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        payload = self._merge_base_info(payload)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            response = await self.http.post(
                f"{self.base_url}{path}",
                headers=self.headers(content_length=len(body)),
                content=body,
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise RuntimeError(f"iLink request timed out path={path} timeout={timeout}s") from exc
        except httpx.RequestError as exc:
            raise RuntimeError(
                f"iLink request failed path={path} error={type(exc).__name__}"
            ) from exc
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError(f"Unexpected iLink response type: {type(data).__name__}")
        return data
