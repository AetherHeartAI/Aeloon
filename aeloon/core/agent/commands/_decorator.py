"""Decorator-based registration for built-in slash commands."""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from aeloon.cli.registry import CommandSpec

if TYPE_CHECKING:
    from aeloon.plugins._sdk.types import CommandHandler


def slash_command(
    name: str,
    help: str,
    *,
    slash_path: tuple[str, ...] | None = None,
    slash_paths: tuple[tuple[str, ...], ...] = (),
    slash_aliases: tuple[tuple[str, ...], ...] = (),
) -> Callable[["CommandHandler"], "CommandHandler"]:
    """Register one built-in slash command handler on its defining module."""

    def _decorator(handler: "CommandHandler") -> "CommandHandler":
        spec = CommandSpec(
            name=name,
            help=help,
            slash_path=slash_path,
            slash_paths=slash_paths,
            slash_aliases=slash_aliases,
        )
        module = sys.modules[handler.__module__]
        commands = getattr(module, "_COMMANDS", None)
        if commands is None:
            commands = []
            setattr(module, "_COMMANDS", commands)
        commands.append((spec, handler))
        return handler

    return _decorator


def module_commands(module: Any) -> list[tuple[CommandSpec, "CommandHandler"]]:
    """Return decorator-registered commands owned by one module."""

    commands = getattr(module, "_COMMANDS", ())
    return list(cast(list[tuple[CommandSpec, "CommandHandler"]], commands))
