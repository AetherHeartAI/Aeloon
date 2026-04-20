"""Onboarding flow implementation."""

from __future__ import annotations

from pathlib import Path

from aeloon import __logo__
from aeloon.cli.app import console
from aeloon.cli.flows.helpers import onboard_plugins
from aeloon.core.config.paths import get_workspace_path
from aeloon.core.config.schema import Config
from aeloon.utils.helpers import sync_workspace_templates


def _print_memory_backend_guidance(config_path: Path) -> None:
    console.print("\nMemory layers:")
    console.print("  - [cyan]prompt memory[/cyan]: local `memory/MEMORY.md` + `memory/USER.md`")
    console.print("  - [cyan]archive[/cyan]: searchable transcript sidecar")
    console.print("  - [cyan]provider[/cyan]: optional additive provider such as OpenViking")
    console.print(
        "    Configure provider details with "
        f"[cyan]aeloon memory setup openviking --config {config_path}[/cyan]"
    )
    console.print("    Prompt memory and archive remain active when a provider is enabled.")


def run_onboard(*, workspace: str | None, config: str | None) -> None:
    """Initialize aeloon configuration and workspace."""
    from aeloon.core.config.loader import get_config_path, load_config, save_config, set_config_path

    if config:
        config_path = Path(config).expanduser().resolve()
        set_config_path(config_path)
        console.print(f"[dim]Using config: {config_path}[/dim]")
    else:
        config_path = get_config_path()

    def _apply_workspace_override(loaded: Config) -> Config:
        if workspace:
            loaded.agents.defaults.workspace = workspace
        return loaded

    if config_path.exists():
        console.print(f"[yellow]Config already exists at {config_path}[/yellow]")
        console.print("  [bold]y[/bold] = overwrite with defaults (existing values will be lost)")
        console.print(
            "  [bold]N[/bold] = refresh config, keeping existing values and adding new fields"
        )
        if __import__("typer").confirm("Overwrite?"):
            loaded = _apply_workspace_override(Config())
            save_config(loaded, config_path)
            console.print(f"[green]✓[/green] Config reset to defaults at {config_path}")
        else:
            loaded = _apply_workspace_override(load_config(config_path))
            save_config(loaded, config_path)
            console.print(
                f"[green]✓[/green] Config refreshed at {config_path} (existing values preserved)"
            )
    else:
        loaded = _apply_workspace_override(Config())
        save_config(loaded, config_path)
        console.print(f"[green]✓[/green] Created config at {config_path}")

    console.print(
        "[dim]Config template now uses `maxTokens` + `contextWindowTokens`; `memoryWindow` is no longer a runtime setting.[/dim]"
    )
    _print_memory_backend_guidance(config_path)
    onboard_plugins(config_path)
    workspace_path = get_workspace_path(loaded.workspace_path)
    if not workspace_path.exists():
        workspace_path.mkdir(parents=True, exist_ok=True)
        console.print(f"[green]✓[/green] Created workspace at {workspace_path}")
    sync_workspace_templates(
        workspace_path,
        include_file_memory=loaded.memory.prompt.enabled,
    )

    agent_cmd = 'aeloon agent -m "Hello."'
    if config:
        agent_cmd += f" --config {config_path}"
    console.print(f"\n{__logo__} aeloon is ready!")
    console.print("\nNext steps:")
    console.print(f"  1. Add your API key to [cyan]{config_path}[/cyan]")
    console.print("     Get one at: https://openrouter.ai/keys")
    console.print(f"  2. Chat: [cyan]{agent_cmd}[/cyan]")
