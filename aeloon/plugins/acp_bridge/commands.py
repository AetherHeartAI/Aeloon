"""/acp command handlers for the ACP Bridge plugin."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aeloon.plugins._sdk.acp.types import BackendProfile
from aeloon.plugins._sdk.types import CommandContext

if TYPE_CHECKING:
    from aeloon.plugins.acp_bridge.service import ACPConnectionService


def _get_merged_plugin_config(ctx: CommandContext) -> dict[str, Any]:
    """Get plugin config merged with external acp.json if available.

    External config takes precedence over main config.
    """
    from .config import load_acp_config

    main_config = dict(ctx.plugin_config)
    external_config = load_acp_config()

    if external_config:
        # External values take precedence
        main_config.update(external_config)

    return main_config

HELP_TEXT = """\
ACP Bridge — connect to external ACP agent servers

Usage:
  /acp connect [profile]   Connect to an ACP backend (default: claude_code)
  /acp list                List available ACP backend profiles
  /acp chat <message>      Send a message to the connected agent
  /acp disconnect          Disconnect from the current backend
  /acp status              Show connection state, profile, and sessions
  /acp help                Show this help message

Prerequisites:
  1. Install the Python ACP SDK: pip install agent-client-protocol
     (this provides the `acp` Python module used by Aeloon)
  2. Install the agent: npm install -g @agentclientprotocol/claude-agent-acp
     (or just use npx — it auto-downloads)
  3. Authenticate: run 'claude login' or set ANTHROPIC_API_KEY

Configuration (in aeloon config):
  [plugins.aeloon_acp_bridge]
  enabled = true
  default_profile = "claude_code"

  [plugins.aeloon_acp_bridge.profiles.claude_code]
  command = ["npx", "@agentclientprotocol/claude-agent-acp"]
  env = {ACP_PERMISSION_MODE = "acceptEdits"}
"""


async def handle_acp_command(
    ctx: CommandContext,
    args: str,
) -> str | None:
    """Main dispatcher for the ``/acp`` command namespace."""
    # Get the service from plugin — passed through closure
    # This function is wrapped by the plugin to inject the service reference.
    raise NotImplementedError("Must be called via make_command_handler")


def make_command_handler(service: ACPConnectionService) -> Any:
    """Create a bound command handler with access to the service."""

    async def _handler(ctx: CommandContext, args: str) -> str | None:
        parts = args.strip().split(maxsplit=1)
        subcmd = parts[0] if parts else "help"
        subcmd_args = parts[1] if len(parts) > 1 else ""

        if subcmd == "connect":
            return await _cmd_connect(ctx, subcmd_args, service)
        elif subcmd == "list":
            return await _cmd_list(ctx)
        elif subcmd in ("chat", "ask", "delegate"):
            return await _cmd_chat(ctx, subcmd_args, service)
        elif subcmd == "disconnect":
            return await _cmd_disconnect(ctx, service)
        elif subcmd == "status":
            return await _cmd_status(ctx, service)
        elif subcmd == "help":
            return HELP_TEXT
        else:
            return f"Unknown /acp subcommand: {subcmd}\n\n{HELP_TEXT}"

    return _handler


async def _cmd_connect(
    ctx: CommandContext,
    args: str,
    service: ACPConnectionService,
) -> str:
    """Handle ``/acp connect [profile]``."""
    # Merge external acp.json with main config
    config = _get_merged_plugin_config(ctx)

    profile_name = args.strip() or config.get("default_profile", "claude_code")

    # Resolve profile from merged config (includes external acp.json)
    profiles = config.get("profiles", {})
    profile_data = profiles.get(profile_name, {})

    if not profile_data and profile_name == "claude_code":
        # Use built-in default
        profile = BackendProfile(
            name="claude_code",
            command=["npx", "@agentclientprotocol/claude-agent-acp"],
            cwd="~",
            timeout_seconds=30,
            env={"ACP_PERMISSION_MODE": "acceptEdits"},
        )
    elif profile_data:
        profile = BackendProfile(
            name=profile_name,
            command=profile_data.get("command", ["npx", "@zed-industries/claude-agent-acp"]),
            cwd=profile_data.get("cwd", "~"),
            timeout_seconds=profile_data.get("timeout_seconds", 30),
            env=profile_data.get("env", {}),
        )
    else:
        return f"Profile '{profile_name}' not found in configuration."

    await ctx.send_progress(f"Connecting to ACP backend '{profile_name}'...")

    try:
        await service.connect(profile)
        return f"Connected to ACP backend '{profile_name}'."
    except ModuleNotFoundError as exc:
        if exc.name == "acp":
            return (
                "Failed to connect: missing Python module `acp`. "
                "Install the ACP Python SDK with `pip install agent-client-protocol`, "
                "then retry `/acp connect`."
            )
        return f"Failed to connect: {exc}"
    except Exception as exc:
        return f"Failed to connect: {exc}"


async def _cmd_list(ctx: CommandContext) -> str:
    """Handle ``/acp list``."""
    config = _get_merged_plugin_config(ctx)
    default_profile = config.get("default_profile", "claude_code")
    profiles = dict(config.get("profiles", {}))

    if "claude_code" not in profiles:
        profiles["claude_code"] = {
            "command": ["npx", "@agentclientprotocol/claude-agent-acp"],
            "cwd": "~",
            "timeout_seconds": 30,
        }

    lines = ["Available ACP backends:"]
    for name in sorted(profiles):
        profile = profiles[name] or {}
        command = profile.get("command", [])
        command_text = " ".join(command) if command else "(no command)"
        suffix = " (default)" if name == default_profile else ""
        lines.append(f"- {name}{suffix} — {command_text}")

    return "\n".join(lines)


async def _cmd_chat(
    ctx: CommandContext,
    args: str,
    service: ACPConnectionService,
) -> str:
    """Handle ``/acp chat <message>``."""
    task_text = args.strip()
    if not task_text:
        return "Usage: /acp chat <message>"

    if not service.client.is_connected:
        return "Not connected. Use /acp connect first."

    await ctx.send_progress("Delegating to ACP agent...")

    try:
        result = await service.client.prompt(
            aeloon_session_key=ctx.session_key,
            text=task_text,
        )

        if result.content:
            await ctx.reply(result.content)
        else:
            stop_reason = result.execution_meta.get("stop_reason") or "unknown"
            update_types = result.execution_meta.get("update_types") or []
            unknown_update_types = result.execution_meta.get("unknown_update_types") or []
            await ctx.reply(
                "(agent returned empty response)\n"
                f"stop_reason: {stop_reason}\n"
                f"update_types: {', '.join(update_types) if update_types else 'none'}\n"
                f"unparsed_update_types: {', '.join(unknown_update_types) if unknown_update_types else 'none'}"
            )
        return None  # response already sent via ctx.reply

    except RuntimeError as exc:
        return f"Delegation failed: {exc}"
    except Exception as exc:
        return f"Unexpected error: {exc}"
    finally:
        service.client.set_update_callback(None)


async def _cmd_disconnect(
    ctx: CommandContext,
    service: ACPConnectionService,
) -> str:
    """Handle ``/acp disconnect``."""
    if not service.client.is_connected:
        return "Not connected."

    try:
        profile = service.active_profile
        await service.disconnect()
        name = profile.name if profile else "unknown"
        return f"Disconnected from ACP backend '{name}'."
    except Exception as exc:
        return f"Error during disconnect: {exc}"


async def _cmd_status(
    ctx: CommandContext,
    service: ACPConnectionService,
) -> str:
    """Handle ``/acp status``."""
    health = service.health_check()
    state = health.get("state", "disconnected")

    lines = [f"State: {state}"]

    if "profile" in health:
        lines.append(f"Profile: {health['profile']}")

    sessions = health.get("sessions", 0)
    if sessions > 0:
        lines.append(f"Sessions: {sessions}")

    last_error = health.get("last_error")
    if last_error:
        lines.append(f"Last error: {last_error}")

    return "\n".join(lines)
