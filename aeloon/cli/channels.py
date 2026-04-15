"""Channel-oriented CLI commands and helpers."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

import typer
from rich.table import Table

from aeloon import __logo__
from aeloon.cli.app import app, console
from aeloon.cli.runtime_helpers import load_runtime_config
from aeloon.core.config.schema import Config

channel_app = typer.Typer(help="Manage one channel")
app.add_typer(channel_app, name="channel")

wechat_channel_app = typer.Typer(help="Manage the WeChat channel")
channel_app.add_typer(wechat_channel_app, name="wechat")

feishu_channel_app = typer.Typer(help="Manage the Feishu channel")
channel_app.add_typer(feishu_channel_app, name="feishu")

whatsapp_channel_app = typer.Typer(help="Manage the WhatsApp channel")
channel_app.add_typer(whatsapp_channel_app, name="whatsapp")

app.add_typer(wechat_channel_app, name="wechat")
app.add_typer(feishu_channel_app, name="feishu")
app.add_typer(whatsapp_channel_app, name="whatsapp")

channels_app = typer.Typer(help="Manage channels")
app.add_typer(channels_app, name="channels")


def _channel_enabled(config: Config, name: str) -> bool:
    """Return whether one channel is enabled in config."""
    section = getattr(config.channels, name, None)
    if section is None:
        return False
    if isinstance(section, dict):
        return section.get("enabled", False)
    return getattr(section, "enabled", False)


def _render_channel_status_table(config: Config, *, only: str | None = None) -> Table:
    """Build a channel enabled-status table."""
    from aeloon.channels.registry import discover_all

    table = Table(title="Channel Status")
    table.add_column("Channel", style="cyan")
    table.add_column("Enabled", style="green")

    for name, cls in sorted(discover_all().items()):
        if only and name != only:
            continue
        table.add_row(
            cls.display_name,
            "[green]✓[/green]" if _channel_enabled(config, name) else "[dim]✗[/dim]",
        )
    return table


@channel_app.command("list")
def channel_list(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """List channels and whether they are enabled."""
    loaded_config = load_runtime_config(config)
    console.print(_render_channel_status_table(loaded_config))


@channel_app.command("status")
def channel_status(
    name: str | None = typer.Argument(None, help="Optional channel name"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Show channel status for all channels or one channel."""
    loaded_config = load_runtime_config(config)
    console.print(_render_channel_status_table(loaded_config, only=name))


@channels_app.command("status")
def channels_status() -> None:
    """Show channel status."""
    channel_list()


def _get_bridge_dir() -> Path:
    """Get the bridge directory, setting it up if needed."""
    from aeloon.core.config.paths import get_bridge_install_dir

    user_bridge = get_bridge_install_dir()
    if (user_bridge / "dist" / "index.js").exists():
        return user_bridge

    npm_path = shutil.which("npm")
    if not npm_path:
        console.print("[red]npm not found. Please install Node.js >= 18.[/red]")
        raise typer.Exit(1)

    pkg_bridge = Path(__file__).parents[1] / "bridge"
    src_bridge = Path(__file__).parents[2] / "bridge"

    source = None
    if (pkg_bridge / "package.json").exists():
        source = pkg_bridge
    elif (src_bridge / "package.json").exists():
        source = src_bridge

    if not source:
        console.print("[red]Bridge source not found.[/red]")
        console.print("Try reinstalling: pip install --force-reinstall aeloon")
        raise typer.Exit(1)

    console.print(f"{__logo__} Setting up bridge...")

    user_bridge.parent.mkdir(parents=True, exist_ok=True)
    if user_bridge.exists():
        shutil.rmtree(user_bridge)
    shutil.copytree(source, user_bridge, ignore=shutil.ignore_patterns("node_modules", "dist"))

    try:
        console.print("  Installing dependencies...")
        subprocess.run([npm_path, "install"], cwd=user_bridge, check=True, capture_output=True)

        console.print("  Building...")
        subprocess.run([npm_path, "run", "build"], cwd=user_bridge, check=True, capture_output=True)

        console.print("[green]✓[/green] Bridge ready\n")
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]Build failed: {exc}[/red]")
        if exc.stderr:
            console.print(f"[dim]{exc.stderr.decode()[:500]}[/dim]")
        raise typer.Exit(1)

    return user_bridge


def _whatsapp_login(config: Config) -> None:
    """Link WhatsApp device via QR code."""
    from aeloon.core.config.paths import get_runtime_subdir

    bridge_dir = _get_bridge_dir()

    console.print(f"{__logo__} Starting bridge...")
    console.print("Scan the QR code to connect.\n")

    env = {**os.environ}
    whatsapp_config = getattr(config.channels, "whatsapp", None) or {}
    bridge_token = (
        whatsapp_config.get("bridgeToken", "")
        if isinstance(whatsapp_config, dict)
        else getattr(whatsapp_config, "bridge_token", "")
    )
    if bridge_token:
        env["BRIDGE_TOKEN"] = bridge_token
    env["AUTH_DIR"] = str(get_runtime_subdir("whatsapp-auth"))

    npm_path = shutil.which("npm")
    if not npm_path:
        console.print("[red]npm not found. Please install Node.js.[/red]")
        raise typer.Exit(1)

    try:
        subprocess.run([npm_path, "start"], cwd=bridge_dir, check=True, env=env)
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]Bridge failed: {exc}[/red]")


async def _run_channel_auth_cli(
    name: str,
    args: list[str],
    *,
    config: str | None = None,
    workspace: str | None = None,
) -> None:
    """Run one channel auth action through the shared helper."""
    from aeloon.channels.manager import ChannelManager
    from aeloon.core.agent.channel_auth import ChannelAuthHelper
    from aeloon.core.bus.events import InboundMessage, OutboundMessage
    from aeloon.core.bus.queue import MessageBus

    loaded_config = load_runtime_config(config, workspace)
    channel_bus = MessageBus()
    channel_manager = ChannelManager(loaded_config, channel_bus)
    helper = ChannelAuthHelper()
    helper.set_channel_manager(channel_manager)

    request = InboundMessage(
        channel="cli",
        sender_id="cli",
        chat_id=f"channel:{name}",
        content=f"/channel {name} {' '.join(args)}".strip(),
    )

    def _print_message(message: OutboundMessage) -> None:
        if message.content:
            console.print(message.content)
        if message.media:
            for media in message.media:
                console.print(f"[dim]Media: {media}[/dim]")

    class _ConsoleOutboundBus:
        async def publish_outbound(self, message: OutboundMessage) -> None:
            _print_message(message)

    agent_loop = type("_ChannelCLI", (), {"bus": _ConsoleOutboundBus()})()

    if name == "wechat":
        response = await helper.handle_wechat_command(request, args, agent_loop)
    elif name == "feishu":
        response = await helper.handle_feishu_command(request, args, agent_loop)
    else:
        raise typer.BadParameter(f"Unsupported channel action target: {name}")

    _print_message(response)

    if name == "wechat" and args and args[0] == "login":
        task = helper.wechat._login_tasks.get((request.channel, request.chat_id))
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass
    elif name == "feishu" and args and args[0] == "login":
        task = helper.feishu._login_tasks.get((request.channel, request.chat_id))
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass


@wechat_channel_app.command("login")
def channel_wechat_login(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
) -> None:
    """Login to WeChat."""
    asyncio.run(_run_channel_auth_cli("wechat", ["login"], config=config, workspace=workspace))


@wechat_channel_app.command("logout")
def channel_wechat_logout(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
) -> None:
    """Logout from WeChat."""
    asyncio.run(_run_channel_auth_cli("wechat", ["logout"], config=config, workspace=workspace))


@wechat_channel_app.command("status")
def channel_wechat_status(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
) -> None:
    """Show WeChat login status."""
    asyncio.run(_run_channel_auth_cli("wechat", ["status"], config=config, workspace=workspace))


@feishu_channel_app.command("login")
def channel_feishu_login(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
) -> None:
    """Login to Feishu."""
    asyncio.run(_run_channel_auth_cli("feishu", ["login"], config=config, workspace=workspace))


@feishu_channel_app.command("logout")
def channel_feishu_logout(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
) -> None:
    """Logout from Feishu."""
    asyncio.run(_run_channel_auth_cli("feishu", ["logout"], config=config, workspace=workspace))


@feishu_channel_app.command("status")
def channel_feishu_status(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
) -> None:
    """Show Feishu login status."""
    asyncio.run(_run_channel_auth_cli("feishu", ["status"], config=config, workspace=workspace))


@whatsapp_channel_app.command("login")
def channel_whatsapp_login(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Login to WhatsApp."""
    loaded_config = load_runtime_config(config)
    _whatsapp_login(loaded_config)
