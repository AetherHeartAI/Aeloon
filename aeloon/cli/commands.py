"""CLI command definitions."""

from __future__ import annotations

import typer

from aeloon import __logo__, __version__
from aeloon.cli.app import app, console
from aeloon.cli.channels import (  # noqa: F401
    channel_app,
    channels_app,
    feishu_channel_app,
    wechat_channel_app,
    whatsapp_channel_app,
)
from aeloon.cli.flows.agent import run_agent
from aeloon.cli.flows.benchmark import run_benchmark
from aeloon.cli.flows.gateway import run_gateway
from aeloon.cli.flows.onboard import run_onboard
from aeloon.cli.memory import memory_app  # noqa: F401
from aeloon.cli.plugins import plugin_cli_app, plugins_app  # noqa: F401
from aeloon.cli.providers import provider_app  # noqa: F401

__all__ = ["app"]

app.add_typer(memory_app, name="memory")


def version_callback(value: bool) -> None:
    """Print the CLI version and exit."""
    if value:
        console.print(f"{__logo__} aeloon v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(None, "--version", "-v", callback=version_callback, is_eager=True),
) -> None:
    """aeloon - Personal AI Assistant."""


@app.command()
def status() -> None:
    """Show aeloon status."""
    from aeloon.core.config.loader import get_config_path, load_config
    from aeloon.providers.registry import PROVIDERS

    config_path = get_config_path()
    config = load_config()
    workspace = config.workspace_path

    console.print(f"{__logo__} aeloon Status\n")
    console.print(
        f"Config: {config_path} {'[green]✓[/green]' if config_path.exists() else '[red]✗[/red]'}"
    )
    console.print(
        f"Workspace: {workspace} {'[green]✓[/green]' if workspace.exists() else '[red]✗[/red]'}"
    )

    if not config_path.exists():
        return

    console.print(f"Model: {config.agents.defaults.model}")

    for spec in PROVIDERS:
        provider_config = getattr(config.providers, spec.name, None)
        if provider_config is None:
            continue
        if spec.is_oauth:
            console.print(f"{spec.label}: [green]✓ (OAuth)[/green]")
        elif spec.is_local:
            if provider_config.api_base:
                console.print(f"{spec.label}: [green]✓ {provider_config.api_base}[/green]")
            else:
                console.print(f"{spec.label}: [dim]not set[/dim]")
        else:
            has_key = bool(provider_config.api_key)
            console.print(
                f"{spec.label}: {'[green]✓[/green]' if has_key else '[dim]not set[/dim]'}"
            )


@app.command()
def onboard(
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Initialize aeloon configuration and workspace."""
    run_onboard(workspace=workspace, config=config)


@app.command()
def gateway(
    port: int | None = typer.Option(None, "--port", "-p", help="Gateway port"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Start the aeloon gateway."""
    run_gateway(port=port, workspace=workspace, verbose=verbose, config=config)


@app.command()
def agent(
    message: str = typer.Option(None, "--message", "-m", help="Message to send to the agent"),
    session_id: str | None = typer.Option(None, "--session", "-s", help="Session ID"),
    resume: bool = typer.Option(False, "--resume", "-r", help="Resume the last CLI session"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
    profile: bool = typer.Option(
        False,
        "--profile",
        "-P",
        help="Enable profiling and print timing report for each turn",
    ),
    deep_profile: bool = typer.Option(
        False,
        "--deep-profile",
        "-D",
        help="Enable deep-profile (workflow stages + timing) for each turn",
    ),
    markdown: bool = typer.Option(
        True,
        "--markdown/--no-markdown",
        help="Render assistant output as Markdown",
    ),
    logs: bool = typer.Option(
        False,
        "--logs/--no-logs",
        help="Show aeloon runtime logs during chat",
    ),
) -> None:
    """Interact with the agent directly."""
    run_agent(
        message=message,
        session_id=session_id,
        resume=resume,
        workspace=workspace,
        config=config,
        profile=profile,
        deep_profile=deep_profile,
        markdown=markdown,
        logs=logs,
    )


@app.command()
def benchmark(
    scenario: str | None = typer.Option(
        None, "--scenario", "-s", help="Scenario name or group prefix"
    ),
    repeat: int = typer.Option(3, "--repeat", "-r", min=1, help="Runs per scenario"),
    output: str = typer.Option("table", "--output", "-o", help="Output: table or json"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
) -> None:
    """Run profiling benchmarks across predefined scenarios."""
    run_benchmark(
        scenario=scenario,
        repeat=repeat,
        output=output,
        workspace=workspace,
        config=config,
    )


if __name__ == "__main__":
    app()
