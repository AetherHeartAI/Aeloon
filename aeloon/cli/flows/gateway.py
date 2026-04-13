"""Gateway flow implementation."""

from __future__ import annotations

import asyncio
from pathlib import Path

from loguru import logger

from aeloon import __logo__, __version__
from aeloon.cli.app import console
from aeloon.cli.flows.helpers import boot_plugins
from aeloon.cli.interactive.session import compose_welcome_banner
from aeloon.cli.plugins import register_plugin_cli
from aeloon.cli.runtime_helpers import (
    load_runtime_config,
    make_provider,
    print_deprecated_memory_window_notice,
)
from aeloon.utils.helpers import sync_workspace_templates

_GATEWAY_LOG_FORMAT = "{time:HH:mm:ss} | {level:<8} | {message}"
_GATEWAY_LOG_SINK_ID: int | None = None


def _configure_gateway_log_sink() -> Path:
    """Configure the persistent gateway log sink."""
    from aeloon.core.config.paths import get_gateway_log_path

    global _GATEWAY_LOG_SINK_ID

    logger.enable("aeloon")
    if _GATEWAY_LOG_SINK_ID is not None:
        logger.remove(_GATEWAY_LOG_SINK_ID)
    log_path = get_gateway_log_path()
    _GATEWAY_LOG_SINK_ID = logger.add(
        str(log_path),
        rotation="10 MB",
        retention=3,
        enqueue=True,
        format=_GATEWAY_LOG_FORMAT,
    )
    return log_path


async def _flush_cached_sessions_before_shutdown(agent_loop, *, reason: str) -> None:
    seen: set[str] = set()
    for item in agent_loop.sessions.list_sessions():
        key = str(item.get("key") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        session = agent_loop.sessions.get_or_create(key)
        start_index = agent_loop.memory.pending_start_index(session)
        pending_messages = list(session.messages[start_index:])
        if pending_messages:
            await agent_loop.memory.flush(
                session=session,
                pending_messages=pending_messages,
                reason=reason,
            )


def run_gateway(
    *, port: int | None, workspace: str | None, verbose: bool, config: str | None
) -> None:
    """Start the aeloon gateway."""
    from aeloon.channels.manager import ChannelManager
    from aeloon.core.agent.loop import AgentLoop
    from aeloon.core.bus.queue import MessageBus
    from aeloon.core.config.paths import get_cron_dir
    from aeloon.core.session.manager import SessionManager
    from aeloon.services.cron.service import CronService
    from aeloon.services.cron.types import CronJob
    from aeloon.services.heartbeat import HeartbeatService

    if verbose:
        import logging

        logging.basicConfig(level=logging.DEBUG)

    loaded_config = load_runtime_config(config, workspace)
    print_deprecated_memory_window_notice(loaded_config)
    actual_port = port if port is not None else loaded_config.gateway.port
    log_path = _configure_gateway_log_sink()

    startup_workspace = Path(loaded_config.workspace_path).name or "workspace"
    console.print(compose_welcome_banner(startup_workspace, loaded_config.agents.defaults.model))
    console.print()
    console.print(
        f"{__logo__} Starting aeloon gateway version {__version__} on port {actual_port}..."
    )
    logger.info(
        "Starting aeloon gateway version {} on port {} (log: {})",
        __version__,
        actual_port,
        log_path,
    )
    sync_workspace_templates(
        loaded_config.workspace_path,
        include_file_memory=loaded_config.memory.prompt.enabled,
    )

    bus = MessageBus()
    provider = make_provider(loaded_config)
    session_manager = SessionManager(loaded_config.workspace_path)
    cron = CronService(get_cron_dir() / "jobs.json")
    agent = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=loaded_config.workspace_path,
        model=loaded_config.agents.defaults.model,
        max_iterations=loaded_config.agents.defaults.max_tool_iterations,
        context_window_tokens=loaded_config.agents.defaults.context_window_tokens,
        web_search_config=loaded_config.tools.web.search,
        web_proxy=loaded_config.tools.web.proxy or None,
        exec_config=loaded_config.tools.exec,
        cron_service=cron,
        restrict_to_workspace=loaded_config.tools.restrict_to_workspace,
        session_manager=session_manager,
        mcp_servers=loaded_config.tools.mcp_servers,
        output_mode=loaded_config.agents.defaults.output_mode,
        fast=loaded_config.agents.defaults.fast,
        channels_config=loaded_config.channels,
        memory_config=loaded_config.memory,
    )

    async def on_cron_job(job: CronJob) -> str | None:
        from aeloon.core.agent.tools.cron import CronTool
        from aeloon.core.agent.tools.message import MessageTool
        from aeloon.services.evaluator import evaluate_response

        reminder_note = (
            "[Scheduled Task] Timer finished.\n\n"
            f"Task '{job.name}' has been triggered.\n"
            f"Scheduled instruction: {job.payload.message}"
        )
        cron_tool = agent.tools.get("cron")
        cron_token = None
        if isinstance(cron_tool, CronTool):
            cron_token = cron_tool.set_cron_context(True)
        try:
            response = await agent.process_direct(
                reminder_note,
                session_key=f"cron:{job.id}",
                channel=job.payload.channel or "cli",
                chat_id=job.payload.to or "direct",
            )
        finally:
            if isinstance(cron_tool, CronTool) and cron_token is not None:
                cron_tool.reset_cron_context(cron_token)

        message_tool = agent.tools.get("message")
        if isinstance(message_tool, MessageTool) and message_tool.sent_in_turn:
            return response

        if job.payload.deliver and job.payload.to and response:
            should_notify = await evaluate_response(
                response, job.payload.message, provider, agent.model
            )
            if should_notify:
                from aeloon.core.bus.events import OutboundMessage

                await bus.publish_outbound(
                    OutboundMessage(
                        channel=job.payload.channel or "cli",
                        chat_id=job.payload.to,
                        content=response,
                    )
                )
        return response

    cron.on_job = on_cron_job
    channels = ChannelManager(loaded_config, bus)
    agent.dispatcher.channel_manager = channels

    def _pick_heartbeat_target() -> tuple[str, str]:
        enabled = set(channels.enabled_channels)
        for item in session_manager.list_sessions():
            key = item.get("key") or ""
            if ":" not in key:
                continue
            channel, chat_id = key.split(":", 1)
            if channel in {"cli", "system"}:
                continue
            if channel in enabled and chat_id:
                return channel, chat_id
        return "cli", "direct"

    async def on_heartbeat_execute(tasks: str) -> str:
        channel, chat_id = _pick_heartbeat_target()

        async def _silent(*_args, **_kwargs):
            pass

        return await agent.process_direct(
            tasks,
            session_key="heartbeat",
            channel=channel,
            chat_id=chat_id,
            on_progress=_silent,
        )

    async def on_heartbeat_notify(response: str) -> None:
        from aeloon.core.bus.events import OutboundMessage

        channel, chat_id = _pick_heartbeat_target()
        if channel == "cli":
            return
        await bus.publish_outbound(
            OutboundMessage(channel=channel, chat_id=chat_id, content=response)
        )

    hb_cfg = loaded_config.gateway.heartbeat
    heartbeat = HeartbeatService(
        workspace=loaded_config.workspace_path,
        provider=provider,
        model=agent.model,
        on_execute=on_heartbeat_execute,
        on_notify=on_heartbeat_notify,
        interval_s=hb_cfg.interval_s,
        enabled=hb_cfg.enabled,
    )

    if channels.enabled_channels:
        console.print(f"[green]✓[/green] Channels enabled: {', '.join(channels.enabled_channels)}")
    else:
        console.print("[yellow]Warning: No channels enabled[/yellow]")

    cron_status = cron.status()
    if cron_status["jobs"] > 0:
        console.print(f"[green]✓[/green] Cron: {cron_status['jobs']} scheduled jobs")
    console.print(f"[green]✓[/green] Heartbeat: every {hb_cfg.interval_s}s")

    async def _run() -> None:
        try:
            agent.plugin_manager = await boot_plugins(agent, loaded_config)
            if agent.plugin_manager:
                register_plugin_cli(agent.plugin_manager.registry)
            await cron.start()
            await heartbeat.start()
            await asyncio.gather(agent.run(), channels.start_all())
        except KeyboardInterrupt:
            logger.info("Gateway interrupted by keyboard signal")
            console.print("\nShutting down...")
        except Exception:
            import traceback

            logger.exception("Gateway crashed unexpectedly")
            console.print("\n[red]Error: Gateway crashed unexpectedly[/red]")
            console.print(traceback.format_exc())
        finally:
            await _flush_cached_sessions_before_shutdown(agent, reason="gateway-shutdown")
            if agent.plugin_manager:
                await agent.plugin_manager.shutdown()
            await agent.close_mcp()
            heartbeat.stop()
            cron.stop()
            agent.stop()
            pm = getattr(agent, "plugin_manager", None)
            if pm:
                try:
                    from aeloon.plugins._sdk.hooks import HookEvent

                    await pm._hooks.dispatch_notify(HookEvent.AGENT_STOP)
                except Exception:
                    pass
            await channels.stop_all()

    asyncio.run(_run())
