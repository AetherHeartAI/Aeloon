"""Path helpers for Aeloon runtime data."""

from __future__ import annotations

from pathlib import Path

from aeloon.core.config.loader import get_aeloon_home, get_config_path
from aeloon.utils.helpers import ensure_dir


def get_data_dir() -> Path:
    """Return the runtime data directory."""
    return ensure_dir(get_config_path().parent)


def get_runtime_subdir(name: str) -> Path:
    """Return a named runtime subdirectory."""
    return ensure_dir(get_data_dir() / name)


def get_media_dir(channel: str | None = None) -> Path:
    """Return the media directory."""
    base = get_runtime_subdir("media")
    return ensure_dir(base / channel) if channel else base


def get_cron_dir() -> Path:
    """Return the cron data directory."""
    return get_runtime_subdir("cron")


def get_logs_dir() -> Path:
    """Return the logs directory."""
    return get_runtime_subdir("logs")


def get_gateway_log_path() -> Path:
    """Return the gateway log file path."""
    return get_logs_dir() / "gateway.log"


def get_workspace_path(workspace: str | None = None) -> Path:
    """Return the workspace path."""
    return Path(workspace).expanduser() if workspace else get_aeloon_home() / "workspace"


def get_cli_history_path() -> Path:
    """Return the CLI history path."""
    return get_aeloon_home() / "history" / "cli_history"


def get_bridge_install_dir() -> Path:
    """Return the WhatsApp bridge directory."""
    return get_aeloon_home() / "bridge"


def get_legacy_sessions_dir() -> Path:
    """Return the legacy sessions directory."""
    return get_aeloon_home() / "sessions"


def get_wechat_accounts_dir() -> Path:
    """Return the WeChat accounts directory."""
    return ensure_dir(get_aeloon_home() / "accounts" / "wechat")


def get_wechat_login_qr_dir() -> Path:
    """Return the WeChat login QR directory."""
    return ensure_dir(get_aeloon_home() / "media" / "wechat-login")
