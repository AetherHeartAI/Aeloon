"""@slash_command decorator for co-locating command spec and handler."""

from __future__ import annotations

import sys
from collections.abc import Awaitable, Callable
from types import ModuleType
from typing import Any

from aeloon.cli.registry import CommandSpec

# Type alias for decorated handler functions (new-style)
_Handler = Callable[[Any, str], Awaitable[str | None]]

# Each entry: (CommandSpec, handler)
_CommandEntry = tuple[CommandSpec, _Handler]


def slash_command(
    name: str,
    help: str,  # noqa: A002
    *,
    slash_path: tuple[str, ...] | None = None,
    slash_paths: tuple[tuple[str, ...], ...] = (),
    slash_aliases: tuple[tuple[str, ...], ...] = (),
    cli_path: tuple[str, ...] | None = None,
    cli_aliases: tuple[tuple[str, ...], ...] = (),
) -> Callable[[_Handler], _Handler]:
    """Decorate a command handler, registering its spec alongside it.

    The decorated function is unchanged.  Its (spec, handler) pair is appended
    to the calling module's ``_COMMANDS`` list so :func:`module_commands` can
    collect them later.

    Usage::

        @slash_command(name="help", help="Show available commands", slash_path=("help",))
        async def handle_help(ctx: CommandContext, args: str) -> str | None:
            ...
    """

    def decorator(fn: _Handler) -> _Handler:
        spec = CommandSpec(
            name=name,
            help=help,
            cli_path=cli_path,
            slash_path=slash_path,
            slash_paths=slash_paths,
            cli_aliases=cli_aliases,
            slash_aliases=slash_aliases,
        )
        # Append to the defining module's _COMMANDS list.
        module = sys.modules.get(fn.__module__)
        if module is not None:
            if not hasattr(module, "_COMMANDS"):
                module._COMMANDS = []  # type: ignore[attr-defined]
            module._COMMANDS.append((spec, fn))
        return fn

    return decorator


def module_commands(module: ModuleType) -> list[_CommandEntry]:
    """Return all (spec, handler) pairs registered in *module* via @slash_command."""
    return list(getattr(module, "_COMMANDS", []))
