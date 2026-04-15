"""Feishu QR onboarding business flow."""

from __future__ import annotations

import asyncio
import time

from .api import (
    begin_registration,
    get_qr_code_dir,
    init_registration,
    poll_registration,
    render_ascii_qrcode,
    render_qr_png,
)
from .types import FeishuQrLoginSession

MAX_POLL_INTERVAL_S = 60.0


async def create_login_session() -> FeishuQrLoginSession:
    """Create a Feishu QR onboarding session and render its PNG."""
    init_data = await init_registration()
    methods = init_data.get("supported_auth_methods") or []
    if "client_secret" not in methods:
        raise RuntimeError("Current Feishu environment does not support client_secret onboarding")

    begin_data = await begin_registration()
    verification_url = (
        str(begin_data.get("verification_uri_complete") or begin_data.get("verification_uri") or "")
    ).strip()
    device_code = str(begin_data.get("device_code") or "").strip()
    if not verification_url or not device_code:
        error = begin_data.get("error_description") or begin_data.get("error") or "missing fields"
        raise RuntimeError(f"Feishu onboarding did not return a usable QR session ({error})")

    qr_dir = get_qr_code_dir()
    qr_image_path = qr_dir / "feishu_login.png"
    await render_qr_png(verification_url, qr_image_path)

    now = time.monotonic()
    poll_interval_s = max(0.0, float(begin_data.get("interval") or 5))
    expire_in_s = max(1.0, float(begin_data.get("expire_in") or 600))
    return FeishuQrLoginSession(
        device_code=device_code,
        verification_url=verification_url,
        qr_image_path=str(qr_image_path),
        poll_interval_s=poll_interval_s,
        expires_at=now + expire_in_s,
        started_at=now,
        raw=begin_data,
    )


async def wait_for_login_confirmation(session: FeishuQrLoginSession) -> FeishuQrLoginSession:
    """Wait until a QR session yields app credentials or fails."""
    while True:
        if time.monotonic() >= session.expires_at:
            session.status = "expired"
            session.error = "Feishu onboarding session expired. Start a new login."
            raise TimeoutError(session.error)

        result = await poll_registration(session.device_code)
        app_id = str(result.get("client_id") or "").strip()
        app_secret = str(result.get("client_secret") or "").strip()
        if app_id and app_secret:
            session.status = "confirmed"
            session.app_id = app_id
            session.app_secret = app_secret
            return session

        error = str(result.get("error") or "").strip()
        if not error or error == "authorization_pending":
            session.status = "waiting"
        elif error == "slow_down":
            session.status = "waiting"
            session.poll_interval_s = min(session.poll_interval_s + 5.0, MAX_POLL_INTERVAL_S)
        elif error == "access_denied":
            session.status = "failed"
            session.error = "Feishu onboarding was denied by the user."
            raise RuntimeError(session.error)
        elif error == "expired_token":
            session.status = "expired"
            session.error = "Feishu onboarding session expired. Start a new login."
            raise TimeoutError(session.error)
        else:
            session.status = "failed"
            detail = str(result.get("error_description") or error)
            session.error = f"Feishu onboarding failed: {detail}"
            raise RuntimeError(session.error)

        sleep_for = min(session.poll_interval_s, max(0.0, session.expires_at - time.monotonic()))
        await asyncio.sleep(sleep_for)


__all__ = [
    "FeishuQrLoginSession",
    "create_login_session",
    "get_qr_code_dir",
    "render_ascii_qrcode",
    "wait_for_login_confirmation",
]
