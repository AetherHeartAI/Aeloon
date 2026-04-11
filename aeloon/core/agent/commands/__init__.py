"""Built-in slash command specs and handlers."""

from __future__ import annotations

import functools
from importlib import import_module
from typing import Any, TypeAlias

from aeloon.cli.registry import CommandSpec
from aeloon.core.agent.commands._decorator import module_commands, slash_command
from aeloon.plugins._sdk.types import CommandHandler

BuiltinHandlerMap: TypeAlias = dict[str, CommandHandler]


@functools.cache
def _modules() -> tuple[Any, ...]:
    """Load built-in command modules in registration order."""
    return tuple(
        import_module(f"{__name__}.{module_name}")
        for module_name in ("info", "session", "settings", "channel", "plugin_admin", "control")
    )


def all_specs() -> tuple[CommandSpec, ...]:
    """Return all built-in command specs owned by command modules."""
    return all_specs_and_handlers()[0]


def all_handlers() -> BuiltinHandlerMap:
    """Return all built-in command handlers keyed by spec name."""
    return all_specs_and_handlers()[1]


def all_specs_and_handlers() -> tuple[tuple[CommandSpec, ...], BuiltinHandlerMap]:
    """Return all built-in command specs and handlers.

    Supports both legacy `SPECS`/`HANDLERS` modules and decorator-based modules
    during the migration.
    """

    specs: list[CommandSpec] = []
    handlers: BuiltinHandlerMap = {}
    for module in _modules():
        module_specs = getattr(module, "SPECS", ())
        if module_specs:
            specs.extend(module_specs)
        module_handlers = getattr(module, "HANDLERS", None)
        if module_handlers:
            handlers.update(module_handlers)
        for spec, handler in module_commands(module):
            specs.append(spec)
            handlers[spec.name] = handler
    return tuple(specs), handlers


__all__ = [
    "BuiltinHandlerMap",
    "CommandHandler",
    "all_handlers",
    "all_specs",
    "all_specs_and_handlers",
    "slash_command",
]
