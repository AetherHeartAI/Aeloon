"""Helpers for importing OpenViking config into Aeloon memory settings."""

from __future__ import annotations

import json
from pathlib import Path


DEFAULT_OPENVIKING_CONFIG_PATH = Path("~/.openviking/ov.conf").expanduser()


def resolve_openviking_config_path(raw_path: str | None) -> Path:
    text = (raw_path or "").strip()
    path = Path(text).expanduser() if text else DEFAULT_OPENVIKING_CONFIG_PATH
    return path.resolve()


def load_openviking_seed_config(path: Path) -> dict[str, object]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("OpenViking config root must be a JSON object")
    return sanitize_openviking_seed_config(data)


def sanitize_openviking_seed_config(data: dict[str, object]) -> dict[str, object]:
    sanitized = dict(data)
    sanitized.pop("server", None)
    sanitized.pop("bot", None)
    return sanitized
