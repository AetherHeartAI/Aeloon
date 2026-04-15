"""Typed models for Feishu QR onboarding."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class FeishuQrLoginSession:
    """One Feishu QR onboarding session."""

    device_code: str
    verification_url: str
    qr_image_path: str
    poll_interval_s: float
    expires_at: float
    started_at: float
    status: str = "pending"
    app_id: str = ""
    app_secret: str = ""
    error: str = ""
    raw: dict[str, object] = field(default_factory=dict)
