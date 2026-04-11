"""Tests for the built-in slash command decorator."""

from __future__ import annotations

import types

from aeloon.core.agent.commands._decorator import module_commands, slash_command


async def _sample_handler(_ctx, _args: str) -> str | None:
    return "ok"


def test_slash_command_registers_module_command() -> None:
    module = types.ModuleType("tests.fake_command_module")

    async def handler(_ctx, _args: str) -> str | None:
        return "hello"

    handler.__module__ = module.__name__

    import sys

    sys.modules[module.__name__] = module
    try:
        decorated = slash_command(
            name="greet",
            help="Say hi",
            slash_path=("greet",),
        )(handler)

        assert decorated is handler
        commands = module_commands(module)
        assert len(commands) == 1
        spec, registered_handler = commands[0]
        assert spec.name == "greet"
        assert spec.help == "Say hi"
        assert spec.slash_path == ("greet",)
        assert registered_handler is handler
    finally:
        sys.modules.pop(module.__name__, None)


def test_module_commands_returns_empty_list_when_unset() -> None:
    module = types.ModuleType("tests.empty_command_module")
    assert module_commands(module) == []
