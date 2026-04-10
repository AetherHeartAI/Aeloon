"""ACP Bridge Plugin — entry point for the ``aeloon.acp_bridge`` plugin.

Registers the ``/acp`` command, connection service, status provider,
config schema, and lifecycle hooks.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from aeloon.plugins._sdk.base import Plugin
from aeloon.plugins._sdk.types import ServicePolicy

if TYPE_CHECKING:
    from aeloon.plugins._sdk.api import PluginAPI

    from .service import ACPConnectionService

logger = logging.getLogger(__name__)


def _merge_acp_config(api_config: dict[str, Any]) -> dict[str, Any]:
    """Merge external acp.json with main config.

    External config takes precedence over main config.
    """
    from .config import load_acp_config

    external = load_acp_config()
    if not external:
        return api_config

    # Merge external config into api_config
    # External values take precedence
    merged = dict(api_config)
    merged.update(external)
    return merged


class ACPBridgePlugin(Plugin):
    """Plugin that bridges Aeloon to external ACP agent servers."""

    def __init__(self) -> None:
        self._api: PluginAPI | None = None
        self._service: ACPConnectionService | None = None

    def register(self, api: PluginAPI) -> None:
        """Register commands, service, config schema, status provider, hooks."""
        self._api = api

        # Config schema
        from .config import ACPBridgeConfig

        api.register_config_schema(ACPBridgeConfig)

        # Lazy imports to avoid pulling in the service module at import time
        from .commands import make_command_handler
        from .service import ACPConnectionService
        from .status import make_acp_status_provider

        # Service (instantiated here so command handlers can share it)
        self._service = ACPConnectionService()

        # Command
        handler = make_command_handler(self._service)
        api.register_command("acp", handler, description="ACP bridge commands")

        # Service registration
        api.register_service(
            "acp_connection",
            type(self._service),
            policy=ServicePolicy(
                restart_policy="on-failure",
                max_restarts=3,
                restart_delay_seconds=5.0,
                startup_timeout_seconds=30.0,
                shutdown_timeout_seconds=10.0,
            ),
        )

        # Status provider
        status_fn = make_acp_status_provider(self._service)
        api.register_status_provider("acp_status", status_fn, priority=10)

        # Lifecycle hooks
        api.register_hook("AGENT_STOP", self._on_agent_stop)

    async def activate(self, api: PluginAPI) -> None:
        """Create storage dir and optionally auto-connect."""
        api.runtime.storage_path.mkdir(parents=True, exist_ok=True)

        # Merge external acp.json config with main config
        # External config (~/.aeloon/acp.json) takes precedence
        original_config = dict(api.config)
        merged_config = _merge_acp_config(original_config)

        # Update the runtime config reference
        if merged_config != original_config:
            logger.info("Loaded ACP Bridge configuration from external acp.json")
            # Note: We can't directly modify api.config, but we store merged
            # config for use by command handlers
            self._merged_config = merged_config
        else:
            self._merged_config = original_config

        # Start the connection service (does not auto-connect)
        try:
            await api.start_service("acp_connection")
        except Exception as exc:
            logger.warning("Could not start ACP connection service: %s", exc)

        # Auto-connect if configured (check merged config)
        if self._merged_config.get("auto_connect", False):
            profile_name = self._merged_config.get("default_profile", "claude_code")
            logger.info("Auto-connecting to ACP profile '%s'", profile_name)

    async def deactivate(self) -> None:
        """Ensure clean disconnect."""
        if self._service is not None and self._service.client.is_connected:
            await self._service.disconnect()
        self._service = None

    async def _on_agent_stop(self, **kwargs: Any) -> None:
        """AGENT_STOP hook — disconnect cleanly."""
        if self._service is not None and self._service.client.is_connected:
            logger.info("Agent stopping — disconnecting ACP bridge")
            await self._service.disconnect()
