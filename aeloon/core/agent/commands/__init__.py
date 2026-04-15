"""Built-in slash command specs and handlers."""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Awaitable, Callable, Coroutine
from importlib import import_module
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, TypeAlias

from aeloon.cli.registry import CommandCatalog, CommandSpec
from aeloon.core.agent.channel_auth import ChannelAuthHelper
from aeloon.core.bus.events import InboundMessage, OutboundMessage

if TYPE_CHECKING:
    from aeloon.core.agent.loop import AgentLoop


class CommandEnv:
    """Runtime environment injected into built-in command handlers.

    Instead of copying every field from AgentLoop, this holds a reference
    and reads attributes on demand.  Only ``channel_auth``, ``channel_manager``,
    ``builtin_catalog``, and ``plugin_catalog_fn`` are owned directly because
    they live on the Dispatcher, not the AgentLoop.
    """

    __slots__ = (
        "_agent_loop",
        "channel_auth",
        "channel_manager",
        "builtin_catalog",
        "plugin_catalog_fn",
    )

    def __init__(
        self,
        agent_loop: AgentLoop | Any,
        *,
        channel_auth: ChannelAuthHelper,
        channel_manager: Any | None = None,
        builtin_catalog: CommandCatalog | None = None,
        plugin_catalog_fn: Callable[[], CommandCatalog] | None = None,
    ) -> None:
        self._agent_loop = agent_loop
        self.channel_auth = channel_auth
        self.channel_manager = channel_manager
        self.builtin_catalog = builtin_catalog or CommandCatalog()
        self.plugin_catalog_fn = plugin_catalog_fn or (lambda: CommandCatalog())

    # -- Proxied from agent_loop (read on demand, always fresh) --

    @property
    def sessions(self) -> Any:
        return getattr(self._agent_loop, "sessions", None)

    @property
    def memory_consolidator(self) -> Any:
        return getattr(self._agent_loop, "memory_consolidator", None)

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
    def bus(self) -> Any:
        return getattr(self._agent_loop, "bus", None)

    @property
    def output_manager(self) -> Any:
        return getattr(self._agent_loop, "output_manager", None)

    @property
    def schedule_background(self) -> Callable[[Coroutine[Any, Any, Any]], Any]:
        return getattr(self._agent_loop, "_schedule_background", _default_schedule_background)

    def as_bus_namespace(self) -> SimpleNamespace:
        """Return a lightweight stub exposing only ``.bus`` for channel auth."""
        return SimpleNamespace(bus=self.bus)


BuiltinCommandHandler: TypeAlias = Callable[
    [CommandEnv, InboundMessage, str],
    Awaitable[OutboundMessage | None] | OutboundMessage | None,
]
BuiltinHandlerMap: TypeAlias = dict[str, BuiltinCommandHandler]


def _default_schedule_background(
    coroutine: Coroutine[Any, Any, Any],
) -> asyncio.Task[Any]:
    """Schedule a background coroutine when the loop lacks a helper."""
    return asyncio.create_task(coroutine)


@functools.cache
def _modules() -> tuple[Any, ...]:
    """Load built-in command modules in registration order."""
    return tuple(
        import_module(f"{__name__}.{module_name}")
        for module_name in ("info", "session", "settings", "channel", "plugin_admin", "outputs")
    )


def all_specs() -> tuple[CommandSpec, ...]:
    """Return all built-in command specs owned by command modules."""
    return tuple(spec for module in _modules() for spec in module.SPECS)


def all_handlers() -> BuiltinHandlerMap:
    """Return all built-in command handlers keyed by spec name."""
    handlers: BuiltinHandlerMap = {}
    for module in _modules():
        handlers.update(module.HANDLERS)
    return handlers


__all__ = [
    "BuiltinCommandHandler",
    "BuiltinHandlerMap",
    "CommandEnv",
    "_default_schedule_background",
    "all_handlers",
    "all_specs",
]
