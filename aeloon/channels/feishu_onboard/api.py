"""Official Feishu QR onboarding API helpers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

from aeloon.core.config.loader import get_aeloon_home

REGISTRATION_URL = "https://accounts.feishu.cn/oauth/v1/app/registration"


def get_qr_code_dir() -> Path:
    """Return directory for Feishu login QR images."""
    qr_dir = get_aeloon_home() / "media" / "feishu-login"
    qr_dir.mkdir(parents=True, exist_ok=True)
    return qr_dir


async def post_registration(
    action: str,
    payload: dict[str, str],
    *,
    tolerate_http_errors: bool = False,
) -> dict[str, Any]:
    """Call registration endpoint and return JSON."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            REGISTRATION_URL,
            data={"action": action, **payload},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30.0,
        )
        if not tolerate_http_errors:
            response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected Feishu registration response for {action}")
        return data


async def init_registration() -> dict[str, Any]:
    """Initialize onboarding and read supported auth methods."""
    return await post_registration("init", {})


async def begin_registration() -> dict[str, Any]:
    """Begin a QR onboarding session."""
    return await post_registration(
        "begin",
        {
            "archetype": "PersonalAgent",
            "auth_method": "client_secret",
            "request_user_info": "open_id",
        },
    )


async def poll_registration(device_code: str) -> dict[str, Any]:
    """Poll an existing QR onboarding session."""
    return await post_registration(
        "poll",
        {"device_code": device_code},
        tolerate_http_errors=True,
    )


async def render_qr_png(data: str, output_path: Path) -> None:
    """Render a QR payload to a PNG image."""
    if not data:
        raise RuntimeError("Missing QR content for Feishu onboarding")
    try:
        import qrcode
    except ImportError as exc:
        raise RuntimeError("qrcode package is required for Feishu QR rendering") from exc

    image = qrcode.make(data)
    image.save(output_path)
    os.chmod(output_path, 0o600)


def render_ascii_qrcode(data: str) -> str | None:
    """Render QR content as ASCII for text-only clients."""
    if not data:
        return None
    try:
        import qrcode
    except ImportError:
        return None

    from io import StringIO

    qr = qrcode.QRCode(border=1)
    qr.add_data(data)
    qr.make(fit=True)
    buf = StringIO()
    qr.print_ascii(out=buf, invert=True)
    return buf.getvalue().rstrip()
