"""Profile-aware .env loading helpers."""

from __future__ import annotations

import os
from pathlib import Path

from aeloon.core.config.paths import get_env_path


def load_profile_env(config_path: Path | None = None, *, override: bool = True) -> Path | None:
    """Load the active profile .env file into os.environ if it exists."""
    env_path = get_env_path(config_path=config_path)
    if not env_path.exists():
        return None
    for key, value in parse_env_file(env_path).items():
        if override or key not in os.environ:
            os.environ[key] = value
    return env_path


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse a minimal dotenv file."""
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = _strip_quotes(value.strip())
    return values


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
