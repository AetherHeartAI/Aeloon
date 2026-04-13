"""Unified CommandContext for built-in command handlers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from aeloon.core.bus.events import InboundMessage

if TYPE_CHECKING:
    from aeloon.channels.manager import ChannelManager
    from aeloon.cli.registry import CommandCatalog
    from aeloon.core.agent.channel_auth import ChannelAuthHelper
    from aeloon.core.agent.loop import AgentLoop
    from aeloon.memory.runtime import MemoryRuntime


def _default_schedule_background(
    coroutine: Coroutine[Any, Any, Any],
) -> asyncio.Task[Any]:
    return asyncio.create_task(coroutine)


class CommandContext:
    """Unified context injected into built-in slash command handlers.

    Replaces the (CommandEnv, InboundMessage) two-argument pattern.
    Handlers receive a single ``ctx`` and return ``str | None``.
    The Dispatcher wraps the return value into an OutboundMessage.
    """

    __slots__ = (
        "_agent_loop",
        "_inbound_message",
        "_metadata",
        "_media",
        "_cancelled",
        "_restart_requested",
        "session_key",
        "channel",
        "chat_id",
        "sender_id",
        "inbound_metadata",
        "is_builtin",
        "plugin_id",
        "plugin_config",
        "reply",
        "send_progress",
        "channel_auth",
        "channel_manager",
        "builtin_catalog",
        "plugin_catalog_fn",
    )

    def __init__(
        self,
        *,
        agent_loop: AgentLoop | Any,
        inbound_message: InboundMessage,
        session_key: str,
        is_builtin: bool = True,
        plugin_id: str | None = None,
        plugin_config: Mapping[str, Any] | None = None,
        reply: Callable[[str], Awaitable[None]] | None = None,
        send_progress: Callable[..., Awaitable[None]] | None = None,
        channel_auth: ChannelAuthHelper | None = None,
        channel_manager: ChannelManager | None = None,
        builtin_catalog: CommandCatalog | None = None,
        plugin_catalog_fn: Callable[[], CommandCatalog] | None = None,
    ) -> None:
        from aeloon.cli.registry import CommandCatalog as _CatalogCls  # noqa: N814

        self._agent_loop = agent_loop
        self._inbound_message = inbound_message
        self._metadata: dict[str, Any] = dict(inbound_message.metadata or {})
        self._media: list[Any] = []
        self._cancelled = False
        self._restart_requested = False

        self.session_key = session_key
        self.channel = inbound_message.channel
        self.chat_id = inbound_message.chat_id
        self.sender_id = inbound_message.sender_id
        self.inbound_metadata: Mapping[str, Any] = dict(inbound_message.metadata or {})
        self.is_builtin = is_builtin
        self.plugin_id = plugin_id
        self.plugin_config: Mapping[str, Any] = plugin_config or {}
        self.reply = reply
        self.send_progress = send_progress
        self.channel_auth = channel_auth
        self.channel_manager = channel_manager
        self.builtin_catalog: CommandCatalog = builtin_catalog or _CatalogCls()
        self.plugin_catalog_fn: Callable[[], CommandCatalog] = plugin_catalog_fn or (
            lambda: _CatalogCls()
        )

    # ------------------------------------------------------------------
    # Proxied agent_loop properties (read-only, always fresh)
    # ------------------------------------------------------------------

    @property
    def sessions(self) -> Any:
        return getattr(self._agent_loop, "sessions", None)

    @property
    def memory(self) -> MemoryRuntime | None:
        return getattr(self._agent_loop, "memory", None)

    @property
    def profiler(self) -> Any:
        return getattr(self._agent_loop, "profiler", None)

    @property
    def runtime_settings(self) -> Any:
        return getattr(self._agent_loop, "runtime_settings", None)

    @property
    def model(self) -> str:
        return str(getattr(self._agent_loop, "model", "") or "")

    @property
    def context_window_tokens(self) -> int:
        return int(getattr(self._agent_loop, "context_window_tokens", 0) or 0)

    @property
    def provider(self) -> Any:
        return getattr(self._agent_loop, "provider", None)

    @property
    def channels_config(self) -> Any:
        return getattr(self._agent_loop, "channels_config", None)

    @property
    def plugin_manager(self) -> Any:
        return getattr(self._agent_loop, "plugin_manager", None)

    @property
    def output_manager(self) -> Any:
        return getattr(self._agent_loop, "output_manager", None)

    @property
    def bus(self) -> Any:
        return getattr(self._agent_loop, "bus", None)

    @property
    def schedule_background(self) -> Callable[[Coroutine[Any, Any, Any]], Any]:
        return getattr(self._agent_loop, "_schedule_background", _default_schedule_background)

    # ------------------------------------------------------------------
    # Side-effect methods
    # ------------------------------------------------------------------

    def set_metadata(self, key: str, value: Any) -> None:
        """Attach a metadata key to the outbound response."""
        self._metadata[key] = value

    def add_media(self, item: Any) -> None:
        """Attach a media item to the outbound response."""
        self._media.append(item)

    def cancel_session(self) -> None:
        """Signal the dispatcher to cancel the current session."""
        self._cancelled = True

    def restart(self) -> None:
        """Signal the dispatcher to restart Aeloon after the command."""
        self._restart_requested = True

    def as_bus_namespace(self) -> SimpleNamespace:
        """Return a lightweight stub exposing only ``.bus`` for channel auth."""
        return SimpleNamespace(bus=self.bus)

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_dispatch(
        cls,
        *,
        agent_loop: AgentLoop | Any,
        msg: InboundMessage,
        session_key: str,
        is_builtin: bool = True,
        plugin_id: str | None = None,
        plugin_config: Mapping[str, Any] | None = None,
        reply: Callable[[str], Awaitable[None]] | None = None,
        send_progress: Callable[..., Awaitable[None]] | None = None,
        channel_auth: ChannelAuthHelper | None = None,
        channel_manager: ChannelManager | None = None,
        builtin_catalog: CommandCatalog | None = None,
        plugin_catalog_fn: Callable[[], CommandCatalog] | None = None,
    ) -> CommandContext:
        """Construct a CommandContext from dispatcher arguments."""
        return cls(
            agent_loop=agent_loop,
            inbound_message=msg,
            session_key=session_key,
            is_builtin=is_builtin,
            plugin_id=plugin_id,
            plugin_config=plugin_config,
            reply=reply,
            send_progress=send_progress,
            channel_auth=channel_auth,
            channel_manager=channel_manager,
            builtin_catalog=builtin_catalog,
            plugin_catalog_fn=plugin_catalog_fn,
        )
