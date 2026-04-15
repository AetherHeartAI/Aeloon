import json
from pathlib import Path

import httpx
import pytest

from aeloon.channels.wechat_ilink.client import (
    ILINK_APP_CLIENT_VERSION,
    ILINK_CHANNEL_VERSION,
    ILinkClient,
)
from aeloon.channels.wechat_ilink.media import (
    CDN_BASE_URL,
    UploadedFile,
    send_media_from_path,
    send_text_message,
    upload_file_to_cdn,
)
from aeloon.channels.wechat_ilink.types import CDNMediaTypeFile, Credentials, ItemTypeFile


class _FakeILinkClient:
    def __init__(self, upload_response: dict) -> None:
        self.upload_response = upload_response
        self.payload: dict | None = None

    async def get_upload_url(self, payload: dict) -> dict:
        self.payload = payload
        return self.upload_response


class _FakeSendClient:
    def __init__(self, response: dict | None = None) -> None:
        self.response = response or {"ret": 0}
        self.payload: dict | None = None
        self.bot_id = "bot-id"

    async def send_message(self, payload: dict) -> dict:
        self.payload = payload
        return self.response


class _FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text

    @property
    def is_error(self) -> bool:
        return self.status_code >= 400

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"ret": 0}


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse, calls: list[dict[str, object]]) -> None:
        self._response = response
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        content: bytes,
    ) -> _FakeResponse:
        self._calls.append({"url": url, "headers": headers, "content": content})
        return self._response


class _FakeHTTPClient:
    def __init__(self, response: _FakeResponse, calls: list[dict[str, object]]) -> None:
        self._response = response
        self._calls = calls

    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        content: bytes,
        timeout: float,
    ) -> _FakeResponse:
        self._calls.append(
            {
                "url": url,
                "headers": headers,
                "content": content,
                "timeout": timeout,
            }
        )
        return self._response


@pytest.mark.asyncio
async def test_upload_file_to_cdn_prefers_upload_full_url(monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    client = _FakeILinkClient(
        {
            "ret": 0,
            "upload_full_url": "https://cdn.example/upload?token=abc",
            "upload_param": "legacy-param",
        }
    )

    monkeypatch.setattr(
        "aeloon.channels.wechat_ilink.media.httpx.AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(
            _FakeResponse(headers={"X-Encrypted-Param": "download-token"}),
            calls,
        ),
    )

    uploaded = await upload_file_to_cdn(client, b"hello", "wx-user", CDNMediaTypeFile)

    assert client.payload is not None
    assert client.payload["to_user_id"] == "wx-user"
    assert client.payload["base_info"]["channel_version"] == ILINK_CHANNEL_VERSION
    assert calls[0]["url"] == "https://cdn.example/upload?token=abc"
    assert uploaded.download_param == "download-token"


@pytest.mark.asyncio
async def test_upload_file_to_cdn_falls_back_to_legacy_upload_param(monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    client = _FakeILinkClient({"ret": 0, "upload_param": "legacy-param"})

    monkeypatch.setattr(
        "aeloon.channels.wechat_ilink.media.httpx.AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(
            _FakeResponse(headers={"X-Encrypted-Param": "download-token"}),
            calls,
        ),
    )

    await upload_file_to_cdn(client, b"hello", "wx-user", CDNMediaTypeFile)

    assert str(calls[0]["url"]).startswith(
        f"{CDN_BASE_URL}/upload?encrypted_query_param=legacy-param&filekey="
    )


@pytest.mark.asyncio
async def test_upload_file_to_cdn_requires_upload_url(monkeypatch) -> None:
    client = _FakeILinkClient({"ret": 0})

    monkeypatch.setattr(
        "aeloon.channels.wechat_ilink.media.httpx.AsyncClient",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("CDN client should not be used")
        ),
    )

    with pytest.raises(RuntimeError, match="upload_full_url nor upload_param"):
        await upload_file_to_cdn(client, b"hello", "wx-user", CDNMediaTypeFile)


@pytest.mark.asyncio
async def test_upload_file_to_cdn_surfaces_cdn_status(monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    client = _FakeILinkClient({"ret": 0, "upload_full_url": "https://cdn.example/upload?token=abc"})

    monkeypatch.setattr(
        "aeloon.channels.wechat_ilink.media.httpx.AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(
            _FakeResponse(status_code=400, text="bad upload"),
            calls,
        ),
    )

    with pytest.raises(RuntimeError, match="status=400"):
        await upload_file_to_cdn(client, b"hello", "wx-user", CDNMediaTypeFile)

    assert calls[0]["url"] == "https://cdn.example/upload?token=abc"


@pytest.mark.asyncio
async def test_ilink_client_post_adds_protocol_headers_and_base_info() -> None:
    calls: list[dict[str, object]] = []
    client = ILinkClient(Credentials(bot_token="bot-token", ilink_bot_id="bot-id"))
    await client.http.aclose()
    client.http = _FakeHTTPClient(_FakeResponse(), calls)  # type: ignore[assignment]

    await client._post("/ilink/bot/sendmessage", {"msg": {"text": "你好"}}, timeout=12.0)

    assert len(calls) == 1
    headers = calls[0]["headers"]
    assert headers["Authorization"] == "Bearer bot-token"
    assert headers["AuthorizationType"] == "ilink_bot_token"
    assert headers["iLink-App-ClientVersion"] == ILINK_APP_CLIENT_VERSION

    body = calls[0]["content"]
    payload = json.loads(body.decode("utf-8"))
    assert payload["base_info"]["channel_version"] == ILINK_CHANNEL_VERSION
    assert headers["Content-Length"] == str(len(body))


@pytest.mark.asyncio
async def test_ilink_client_post_surfaces_timeout_context() -> None:
    class _TimeoutHTTPClient:
        async def post(self, *args, **kwargs):
            raise httpx.ReadTimeout("")

    client = ILinkClient(Credentials(bot_token="bot-token", ilink_bot_id="bot-id"))
    await client.http.aclose()
    client.http = _TimeoutHTTPClient()  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match=r"path=/ilink/bot/sendmessage timeout=12.0s"):
        await client._post("/ilink/bot/sendmessage", {"msg": {"text": "你好"}}, timeout=12.0)


@pytest.mark.asyncio
async def test_send_text_message_uses_protocol_payload() -> None:
    client = _FakeSendClient()

    await send_text_message(client, "wx-user", "hello", "ctx-1")

    assert client.payload is not None
    assert client.payload["msg"]["from_user_id"] == ""
    assert client.payload["msg"]["context_token"] == "ctx-1"
    assert client.payload["base_info"]["channel_version"] == ILINK_CHANNEL_VERSION


@pytest.mark.asyncio
async def test_send_text_message_surfaces_ret_and_errcode() -> None:
    client = _FakeSendClient({"ret": 3, "errcode": 401, "errmsg": ""})

    with pytest.raises(RuntimeError, match=r"ret=3 errcode=401 errmsg=<empty>"):
        await send_text_message(client, "wx-user", "hello", "ctx-1")


@pytest.mark.asyncio
async def test_send_media_from_path_rejects_fake_image(tmp_path: Path) -> None:
    client = _FakeSendClient()
    fake_image = tmp_path / "rabbit.jpg"
    fake_image.write_text("<!DOCTYPE html><title>nope</title>", encoding="utf-8")

    with pytest.raises(RuntimeError, match="outbound image validation failed"):
        await send_media_from_path(client, "wx-user", str(fake_image), "ctx-1")

    assert client.payload is None


@pytest.mark.asyncio
async def test_send_media_from_path_sends_html_as_file_item(monkeypatch, tmp_path: Path) -> None:
    client = _FakeSendClient()
    html = tmp_path / "slides.html"
    html.write_text("<!DOCTYPE html><title>deck</title>", encoding="utf-8")

    async def _fake_upload(*args, **kwargs) -> UploadedFile:
        return UploadedFile(
            download_param="download-token",
            aes_key_hex="00112233445566778899aabbccddeeff",
            file_size=html.stat().st_size,
            cipher_size=64,
        )

    monkeypatch.setattr("aeloon.channels.wechat_ilink.media.upload_file_to_cdn", _fake_upload)

    await send_media_from_path(client, "wx-user", str(html), "ctx-1")

    assert client.payload is not None
    item = client.payload["msg"]["item_list"][0]
    assert item["type"] == ItemTypeFile
    assert item["file_item"]["file_name"] == "slides.html"
    assert item["file_item"]["len"] == str(html.stat().st_size)


@pytest.mark.asyncio
async def test_send_media_from_path_sends_pptx_as_file_item(monkeypatch, tmp_path: Path) -> None:
    client = _FakeSendClient()
    pptx = tmp_path / "deck.pptx"
    pptx.write_bytes(b"PK\x03\x04fake-pptx")

    async def _fake_upload(*args, **kwargs) -> UploadedFile:
        return UploadedFile(
            download_param="download-token",
            aes_key_hex="00112233445566778899aabbccddeeff",
            file_size=pptx.stat().st_size,
            cipher_size=64,
        )

    monkeypatch.setattr("aeloon.channels.wechat_ilink.media.upload_file_to_cdn", _fake_upload)

    await send_media_from_path(client, "wx-user", str(pptx), "ctx-1")

    assert client.payload is not None
    item = client.payload["msg"]["item_list"][0]
    assert item["type"] == ItemTypeFile
    assert item["file_item"]["file_name"] == "deck.pptx"
    assert item["file_item"]["len"] == str(pptx.stat().st_size)
