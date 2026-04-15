"""CLI application bootstrap."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Mapping

import typer
from loguru import logger
from rich.console import Console

from aeloon import __logo__
from aeloon.cli.registry import CommandCatalog, CommandSpec
from aeloon.core.agent.commands import all_specs as all_command_specs

if TYPE_CHECKING:
    from aeloon.cli.registry import CommandHandler

_STATIC_COMMAND_SPECS: tuple[CommandSpec, ...] = (
    CommandSpec(
        name="onboard",
        help="Initialize aeloon configuration and workspace.",
        cli_path=("onboard",),
    ),
    CommandSpec(
        name="gateway",
        help="Start the aeloon gateway.",
        cli_path=("gateway",),
    ),
    CommandSpec(
        name="agent",
        help="Interact with the agent directly.",
        cli_path=("agent",),
    ),
    CommandSpec(
        name="benchmark",
        help="Run profiling benchmarks across predefined scenarios.",
        cli_path=("benchmark",),
    ),
    CommandSpec(
        name="status_cli",
        help="Show aeloon status.",
        cli_path=("status",),
    ),
    CommandSpec(
        name="channels",
        help="Manage channels.",
        cli_path=("channels",),
    ),
    CommandSpec(
        name="channel_plugins",
        help="Manage channel plugins.",
        cli_path=("plugins",),
    ),
    CommandSpec(
        name="provider",
        help="Manage providers.",
        cli_path=("provider",),
        slash_path=("provider",),
        slash_paths=(
            ("provider", "login"),
            ("provider", "login", "openai-codex"),
            ("provider", "login", "github-copilot"),
        ),
    ),
    CommandSpec(
        name="ext",
        help="Run extension commands.",
        cli_path=("ext",),
    ),
)

_STATIC_SLASH_SPECS: tuple[CommandSpec, ...] = (
    CommandSpec(name="stop", help="Stop the current task", slash_path=("stop",)),
    CommandSpec(name="restart", help="Restart the bot", slash_path=("restart",)),
)

BUILTIN_COMMAND_SPECS: tuple[CommandSpec, ...] = (
    *_STATIC_COMMAND_SPECS,
    *_STATIC_SLASH_SPECS,
    *all_command_specs(),
)


def create_builtin_catalog(
    handlers: Mapping[str, "CommandHandler"] | None = None,
) -> CommandCatalog:
    """Return a catalog preloaded with built-in command specs."""
    catalog = CommandCatalog()
    if handlers:
        catalog.extend(
            tuple(replace(spec, handler=handlers.get(spec.name)) for spec in BUILTIN_COMMAND_SPECS)
        )
    else:
        catalog.extend(BUILTIN_COMMAND_SPECS)
    return catalog


def _apply_boot_defaults() -> None:
    """Apply lightweight environment defaults before runtime startup."""
    if sys.platform == "win32":
        # Set Windows console code page to UTF-8 so Chinese / CJK output stays readable.
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
        os.environ["PYTHONIOENCODING"] = "utf-8"
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    try:
        config_path = Path.home() / ".aeloon" / "config.json"
        if not config_path.exists():
            return
        with open(config_path, encoding="utf-8") as handle:
            boot_config = json.load(handle)
        fast_default = boot_config.get("agents", {}).get("defaults", {}).get("fast", False) is True
        if fast_default:
            os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "true"
    except Exception:
        pass


_apply_boot_defaults()

app = typer.Typer(
    name="aeloon",
    help=f"{__logo__} aeloon - Personal AI Assistant",
    no_args_is_help=True,
)
console = Console()
command_catalog = create_builtin_catalog()
ext_app = typer.Typer(help="Run extension commands")
app.add_typer(ext_app, name="ext")


def _should_import_commands_module() -> bool:
    """Return True when app bootstrap should import the command module."""
    if "aeloon.cli.commands" in sys.modules:
        return False
    if any(
        name in sys.modules
        for name in {
            "aeloon.cli.channels",
            "aeloon.cli.plugins",
            "aeloon.cli.providers",
            "aeloon.cli.flows.agent",
            "aeloon.cli.flows.benchmark",
            "aeloon.cli.flows.gateway",
            "aeloon.cli.flows.onboard",
        }
    ):
        return False

    main_module = sys.modules.get("__main__")
    main_file = getattr(main_module, "__file__", None)
    commands_file = Path(__file__).with_name("commands.py")
    return Path(main_file).resolve() != commands_file.resolve() if main_file else True


if _should_import_commands_module():
    from aeloon.cli import commands as _commands  # noqa: F401,E402

try:
    from aeloon.cli import plugins as _plugins

    plugin_registry = _plugins.build_lightweight_plugin_registry()
    _plugins.register_plugin_cli(plugin_registry)
except Exception as exc:
    plugin_registry = None
    logger.debug("Skipping lightweight CLI plugin bootstrap: {}", exc)
