"""ACP Bridge plugin configuration schema."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


def load_acp_config() -> dict[str, Any]:
    """Load ACP Bridge configuration from external acp.json file.

    Checks for ~/.aeloon/acp.json first, then falls back to main config.

    Returns:
        Configuration dictionary loaded from file or empty dict if not found.
    """
    config_paths = [
        Path.home() / ".aeloon" / "acp.json",
        Path.home() / ".config" / "aeloon" / "acp.json",
    ]

    for config_path in config_paths:
        if config_path.exists():
            try:
                with open(config_path, encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                # Log warning but continue to fallback
                import logging
                logging.getLogger(__name__).warning(
                    f"Failed to load ACP config from {config_path}: {e}"
                )

    return {}


class ProfileConfig(BaseModel):
    """Configuration for a single ACP backend profile."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    transport: str = "stdio"
    command: list[str] = ["npx", "@agentclientprotocol/claude-agent-acp"]
    cwd: str = "~"
    timeout_seconds: int = 30
    env: dict[str, str] = {"ACP_PERMISSION_MODE": "acceptEdits"}


class PolicyConfig(BaseModel):
    """Permission policy for ACP requests — deny-by-default."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    auto_approve_safe_requests: bool = False
    allow_file_read: bool = False
    allow_file_write: bool = False
    allow_shell: bool = False


class ACPBridgeConfig(BaseModel):
    """Top-level configuration for the ACP Bridge plugin."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    enabled: bool = True
    default_profile: str = "claude_code"
    auto_connect: bool = False
    profiles: dict[str, ProfileConfig] = {}
    policy: PolicyConfig = PolicyConfig()
