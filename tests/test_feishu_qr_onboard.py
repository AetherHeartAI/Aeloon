import pytest

from aeloon.channels.feishu_onboard.service import (
    create_login_session,
    wait_for_login_confirmation,
)
from aeloon.channels.feishu_onboard.types import FeishuQrLoginSession


@pytest.mark.asyncio
async def test_create_login_session_parses_init_and_begin(monkeypatch, tmp_path):
    async def _fake_init():
        return {"supported_auth_methods": ["client_secret"]}

    async def _fake_begin():
        return {
            "verification_uri_complete": "https://example.com/qr",
            "device_code": "device-1",
            "interval": 3,
            "expire_in": 600,
        }

    async def _fake_render(_data: str, output_path):
        output_path.write_bytes(b"png")

    monkeypatch.setattr("aeloon.channels.feishu_onboard.service.init_registration", _fake_init)
    monkeypatch.setattr("aeloon.channels.feishu_onboard.service.begin_registration", _fake_begin)
    monkeypatch.setattr("aeloon.channels.feishu_onboard.service.render_qr_png", _fake_render)
    monkeypatch.setattr(
        "aeloon.channels.feishu_onboard.service.get_qr_code_dir",
        lambda: tmp_path,
    )

    session = await create_login_session()

    assert session.device_code == "device-1"
    assert session.verification_url == "https://example.com/qr"
    assert session.qr_image_path.endswith("feishu_login.png")
    assert session.poll_interval_s == 3.0


@pytest.mark.asyncio
async def test_wait_for_login_confirmation_handles_pending_slow_down_and_confirmed(monkeypatch):
    results = iter(
        [
            {"error": "authorization_pending"},
            {"error": "slow_down"},
            {"client_id": "cli_x", "client_secret": "sec_y"},
        ]
    )

    async def _fake_poll(_device_code: str):
        return next(results)

    sleep_calls: list[float] = []

    async def _fake_sleep(delay: float):
        sleep_calls.append(delay)

    session = FeishuQrLoginSession(
        device_code="device-1",
        verification_url="https://example.com/qr",
        qr_image_path="qr.png",
        poll_interval_s=1.0,
        expires_at=10_000.0,
        started_at=0.0,
    )

    monkeypatch.setattr("aeloon.channels.feishu_onboard.service.poll_registration", _fake_poll)
    monkeypatch.setattr("aeloon.channels.feishu_onboard.service.asyncio.sleep", _fake_sleep)
    monkeypatch.setattr("aeloon.channels.feishu_onboard.service.time.monotonic", lambda: 0.0)

    confirmed = await wait_for_login_confirmation(session)

    assert confirmed.status == "confirmed"
    assert confirmed.app_id == "cli_x"
    assert confirmed.app_secret == "sec_y"
    assert sleep_calls == [1.0, 6.0]


@pytest.mark.asyncio
async def test_wait_for_login_confirmation_handles_access_denied(monkeypatch):
    async def _fake_poll(_device_code: str):
        return {"error": "access_denied"}

    session = FeishuQrLoginSession(
        device_code="device-1",
        verification_url="https://example.com/qr",
        qr_image_path="qr.png",
        poll_interval_s=1.0,
        expires_at=10_000.0,
        started_at=0.0,
    )

    monkeypatch.setattr("aeloon.channels.feishu_onboard.service.poll_registration", _fake_poll)
    monkeypatch.setattr("aeloon.channels.feishu_onboard.service.time.monotonic", lambda: 0.0)

    with pytest.raises(RuntimeError, match="denied"):
        await wait_for_login_confirmation(session)


@pytest.mark.asyncio
async def test_wait_for_login_confirmation_handles_expired(monkeypatch):
    session = FeishuQrLoginSession(
        device_code="device-1",
        verification_url="https://example.com/qr",
        qr_image_path="qr.png",
        poll_interval_s=1.0,
        expires_at=1.0,
        started_at=0.0,
    )

    monkeypatch.setattr("aeloon.channels.feishu_onboard.service.time.monotonic", lambda: 2.0)

    with pytest.raises(TimeoutError, match="expired"):
        await wait_for_login_confirmation(session)
