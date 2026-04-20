"""Inbound message router and task controller."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from aeloon.cli.app import create_builtin_catalog
from aeloon.cli.plugins import extend_catalog_with_plugin_commands
from aeloon.cli.registry import CommandCatalog
from aeloon.core.agent.channel_auth import ChannelAuthHelper
from aeloon.core.agent.commands import all_specs_and_handlers
from aeloon.core.agent.commands._context import CommandContext as BuiltinCommandContext
from aeloon.core.agent.turn import TurnContext
from aeloon.core.bus.events import InboundMessage, OutboundMessage
from aeloon.plugins._sdk.types import CommandContext, CommandExecutionContext, CommandMiddleware

if TYPE_CHECKING:
    from aeloon.channels.manager import ChannelManager
    from aeloon.core.agent.loop import AgentLoop


class Dispatcher:
    """Route messages, slash commands, and per-session tasks."""

    def __init__(self, agent_loop: "AgentLoop"):
        self._agent_loop = agent_loop
        self._running = False
        self._active_tasks: dict[str, list[asyncio.Task]] = {}
        self._pending_latest: dict[str, InboundMessage] = {}
        self._processing_lock = asyncio.Lock()
        self._channel_manager: ChannelManager | None = None
        self._command_middlewares: list[CommandMiddleware] = []
        # Shared auth helper for channel login flows.
        self._channel_auth = ChannelAuthHelper()
        self._initialize_builtin_dispatch_state()

    @property
    def channel_manager(self) -> ChannelManager | None:
        return self._channel_manager

    @channel_manager.setter
    def channel_manager(self, value: ChannelManager | None) -> None:
        self._channel_manager = value
        self._channel_auth.set_channel_manager(value)

    @property
    def running(self) -> bool:
        return self._running

    @running.setter
    def running(self, value: bool) -> None:
        self._running = value

    @property
    def active_tasks(self) -> dict[str, list[asyncio.Task]]:
        return self._active_tasks

    @active_tasks.setter
    def active_tasks(self, value: dict[str, list[asyncio.Task]]) -> None:
        self._active_tasks = value

    @property
    def processing_lock(self) -> asyncio.Lock:
        return self._processing_lock

    @processing_lock.setter
    def processing_lock(self, value: asyncio.Lock) -> None:
        self._processing_lock = value

    async def run(self) -> None:
        """Run the inbound dispatch loop."""
        self._running = True
        await self._agent_loop._connect_mcp()
        logger.info("Agent loop started")

        # Fire the startup hook when plugins are loaded.
        pm = getattr(self._agent_loop, "plugin_manager", None)
        if pm:
            try:
                from aeloon.plugins._sdk.hooks import HookEvent

                await pm._hooks.dispatch_notify(
                    HookEvent.AGENT_START,
                    model=str(self._agent_loop.model),
                )
            except Exception:
                logger.opt(exception=True).debug("AGENT_START hook dispatch failed")

        while self._running:
            try:
                msg = await asyncio.wait_for(self._agent_loop.bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.warning("Error consuming inbound message: {}, continuing...", e)
                continue

            cmd = msg.content.strip().lower()
            if cmd == "/stop":
                await self._handle_stop(msg)
            elif cmd == "/restart":
                await self._handle_restart(msg)
            else:
                if self._should_replace_pending_message(msg):
                    replaced = self._pending_latest.get(msg.session_key)
                    self._pending_latest[msg.session_key] = msg
                    logger.info(
                        "Queued latest pending message for session {} (replaced_pending={})",
                        msg.session_key,
                        "yes" if replaced is not None else "no",
                    )
                    continue
                self._start_dispatch_task(msg)

    def _start_dispatch_task(self, msg: InboundMessage) -> None:
        """Start one dispatch task and track it under the session."""
        task = asyncio.create_task(self._dispatch(msg))
        self._active_tasks.setdefault(msg.session_key, []).append(task)

        def _remove_for_session(done_task: asyncio.Task[None]) -> None:
            self._remove_task(msg.session_key, done_task)

        task.add_done_callback(_remove_for_session)

    @staticmethod
    def _is_control_command(msg: InboundMessage) -> bool:
        """Return True when the inbound message is a slash command."""
        return msg.content.strip().startswith("/")

    def _should_replace_pending_message(self, msg: InboundMessage) -> bool:
        """Keep only the latest queued Feishu message per active session."""
        if msg.channel != "feishu":
            return False
        if self._is_control_command(msg):
            return False
        return bool(self._active_tasks.get(msg.session_key))

    def _remove_task(self, session_key: str, task: asyncio.Task) -> None:
        tasks = self._active_tasks.get(session_key)
        if not tasks:
            return
        if task in tasks:
            tasks.remove(task)
        if not tasks:
            self._active_tasks.pop(session_key, None)
            pending = self._pending_latest.pop(session_key, None)
            if pending is not None and self._running:
                logger.info("Dispatching latest pending message for session {}", session_key)
                self._start_dispatch_task(pending)

    def stop(self) -> None:
        """Stop the dispatch loop."""
        self._running = False

    def add_middleware(self, middleware: CommandMiddleware) -> None:
        """Register one dispatcher-level command middleware."""
        self._ensure_builtin_dispatch_state()
        self._command_middlewares.append(middleware)

    def _plugin_command_catalog(self) -> CommandCatalog:
        """Return the plugin-only slash command catalog."""
        catalog = CommandCatalog()
        pm = getattr(self._agent_loop, "plugin_manager", None)
        if pm:
            extend_catalog_with_plugin_commands(catalog, pm.registry)
        return catalog

    def _slash_command_catalog(self) -> CommandCatalog:
        """Return the combined built-in and plugin slash command catalog."""
        self._ensure_builtin_dispatch_state()
        catalog = CommandCatalog()
        catalog.extend(tuple(self._builtin_command_catalog.all()))
        pm = getattr(self._agent_loop, "plugin_manager", None)
        extend_catalog_with_plugin_commands(catalog, pm.registry if pm else None)
        return catalog

    def _initialize_builtin_dispatch_state(self) -> None:
        """Construct shared built-in command catalog and register handlers."""
        self._builtin_command_catalog = create_builtin_catalog()
        # Register new-style handlers keyed by slash label.
        for spec, handler in all_specs_and_handlers():
            for path in spec.iter_slash_paths():
                label = "/" + " ".join(path)
                self._builtin_command_catalog.register_handler(label, handler)

    def _ensure_builtin_dispatch_state(self) -> None:
        """Initialize built-in dispatch state for tests that bypass __init__."""
        if not hasattr(self, "_command_middlewares"):
            self._command_middlewares = []
        if not hasattr(self, "_active_tasks"):
            self._active_tasks = {}
        if not hasattr(self, "_pending_latest"):
            self._pending_latest = {}
        if not hasattr(self, "_processing_lock"):
            self._processing_lock = asyncio.Lock()
        if not hasattr(self, "_channel_manager"):
            self._channel_manager = None
        if not hasattr(self, "_channel_auth"):
            self._channel_auth = ChannelAuthHelper()
        self._channel_auth.set_channel_manager(self._channel_manager)
        if not hasattr(self, "_builtin_command_catalog"):
            self._initialize_builtin_dispatch_state()

    def _collect_command_middlewares(self) -> list[CommandMiddleware]:
        """Return dispatcher and plugin command middlewares in execution order."""
        self._ensure_builtin_dispatch_state()
        middlewares = list(self._command_middlewares)
        pm = getattr(self._agent_loop, "plugin_manager", None)
        registry = getattr(pm, "registry", None)
        try:
            plugin_records = list(registry.command_middlewares) if registry is not None else []
        except Exception:
            plugin_records = []
        middlewares.extend(record.middleware for record in plugin_records)
        return middlewares

    def _build_command_execution_context(
        self,
        *,
        msg: InboundMessage,
        session_key: str,
        is_builtin: bool,
        plugin_id: str | None = None,
        plugin_config: Mapping[str, Any] | None = None,
        reply: Callable[[str], Awaitable[None]] | None = None,
        send_progress: Callable[..., Awaitable[None]] | None = None,
    ) -> CommandExecutionContext:
        """Build immutable context for command middleware hooks."""
        return CommandExecutionContext(
            session_key=session_key,
            channel=msg.channel,
            chat_id=msg.chat_id,
            sender_id=msg.sender_id,
            metadata=dict(msg.metadata or {}),
            is_builtin=is_builtin,
            plugin_id=plugin_id,
            plugin_config=dict(plugin_config or {}),
            reply=reply,
            send_progress=send_progress,
        )

    async def _run_command_with_middlewares(
        self,
        cmd: str,
        args: str,
        ctx: CommandExecutionContext,
        execute: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Run one command through the dispatcher middleware chain."""
        middlewares = self._collect_command_middlewares()
        for middleware in middlewares:
            await middleware.before(cmd, args, ctx)
        result = await execute()
        for middleware in reversed(middlewares):
            await middleware.after(cmd, result, ctx)
        return result

    async def _publish_command_reply(
        self,
        msg: InboundMessage,
        text: str,
        *,
        media: list[str] | None = None,
    ) -> None:
        """Publish one plugin-originated command reply."""
        await self._agent_loop.bus.publish_outbound(
            OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=text,
                media=media or [],
            )
        )

    async def _publish_command_progress(
        self,
        msg: InboundMessage,
        text: str,
        *,
        tool_hint: bool = False,
    ) -> None:
        """Publish one plugin-originated command progress update."""
        settings = self._agent_loop.runtime_settings
        if tool_hint and not settings.show_detail:
            return
        meta = dict(msg.metadata or {})
        meta["_progress"] = True
        meta["_tool_hint"] = tool_hint
        await self._agent_loop.bus.publish_outbound(
            OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=text,
                metadata=meta,
            )
        )

    async def _execute_plugin_command(
        self,
        *,
        record: Any,
        msg: InboundMessage,
        key: str,
        args_str: str,
        on_progress: Callable[..., Awaitable[None]] | None,
        cmd: str,
    ) -> OutboundMessage | None:
        """Execute one plugin command under dispatcher middleware and profiling."""

        async def _plugin_reply(text: str = "", *, media: list[str] | None = None) -> None:
            await self._publish_command_reply(msg, text, media=media)

        async def _plugin_progress(text: str, *, tool_hint: bool = False) -> None:
            await self._publish_command_progress(msg, text, tool_hint=tool_hint)

        plugin_config = getattr(self._agent_loop.plugin_manager, "_plugin_config", {}).get(
            record.plugin_id, {}
        )
        handler_ctx = CommandContext(
            session_key=key,
            channel=msg.channel,
            reply=_plugin_reply,
            send_progress=_plugin_progress,
            plugin_config=plugin_config,
        )
        middleware_ctx = self._build_command_execution_context(
            msg=msg,
            session_key=key,
            is_builtin=False,
            plugin_id=record.plugin_id,
            plugin_config=plugin_config,
            reply=_plugin_reply,
            send_progress=_plugin_progress,
        )

        async def _execute() -> str | None:
            async with self._agent_loop._profiled_turn():
                return await record.handler(handler_ctx, args_str)

        result = await self._run_command_with_middlewares(cmd, args_str, middleware_ctx, _execute)

        self._inject_plugin_summary_into_session(
            session_key=key,
            cmd=cmd,
            args_str=args_str,
            result=result,
            plugin_id=record.plugin_id,
        )

        if self._agent_loop.runtime_settings.show_deep_profile and on_progress is None:
            await self._agent_loop._publish_deep_profile_report(msg)
        elif self._agent_loop.runtime_settings.show_profile and on_progress is None:
            await self._agent_loop._publish_hotspot_report(msg)
        elif self._agent_loop.profiler.enabled and on_progress is None:
            await self._agent_loop._publish_profile_report(msg)

        if result is not None:
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=result,
            )
        return None

    def _inject_plugin_summary_into_session(
        self,
        *,
        session_key: str,
        cmd: str,
        args_str: str,
        result: str | None,
        plugin_id: str,
    ) -> None:
        """Append a compact user/assistant message pair to the main session.

        This lets the main agent see what the plugin did on subsequent turns
        (e.g. the user can ask follow-up questions about a ``/sr`` report).
        """
        try:
            session = self._agent_loop.sessions.get_or_create(session_key)
            user_text = f"{cmd} {args_str}".strip()
            if result:
                # Keep only the first 2000 chars to avoid bloating the session.
                truncated = result[:2000] + ("..." if len(result) > 2000 else "")
                assistant_text = f"[Plugin {plugin_id} executed `{cmd}`]\n\n{truncated}"
            else:
                assistant_text = (
                    f"[Plugin {plugin_id} executed `{cmd}` — completed with no text output]"
                )
            session.add_message("user", user_text)
            session.add_message("assistant", assistant_text)
            self._agent_loop.sessions.save(session)
        except Exception:
            logger.opt(exception=True).debug(
                "Failed to inject plugin summary into session {}", session_key
            )

    async def _execute_builtin_command(
        self,
        *,
        msg: InboundMessage,
        session_key: str,
        cmd: str,
        args_str: str,
    ) -> OutboundMessage | None:
        """Build a CommandContext and invoke a new-style built-in handler."""
        handler = self._builtin_command_catalog.find_handler(cmd)
        if handler is None:
            return self._unknown_command_response(msg, cmd)

        async def _builtin_progress(text: str, *, tool_hint: bool = False) -> None:
            await self._publish_command_progress(msg, text, tool_hint=tool_hint)

        ctx = BuiltinCommandContext.from_dispatch(
            agent_loop=self._agent_loop,
            msg=msg,
            session_key=session_key,
            is_builtin=True,
            send_progress=_builtin_progress,
            channel_auth=self._channel_auth,
            channel_manager=self._channel_manager,
            builtin_catalog=self._builtin_command_catalog,
            plugin_catalog_fn=self._plugin_command_catalog,
        )
        middleware_ctx = self._build_command_execution_context(
            msg=msg,
            session_key=session_key,
            is_builtin=True,
            send_progress=_builtin_progress,
        )

        async def _execute() -> OutboundMessage | None:
            result = await handler(ctx, args_str)
            # Handle /restart side-effect
            if ctx._restart_requested:
                if msg.channel == "cli":
                    metadata = dict(msg.metadata or {})
                    metadata["_restart_requested"] = True
                    return OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content="Restarting...",
                        metadata=metadata,
                    )
                await self._do_restart(msg)
                return None
            content = result if result is not None else ""
            metadata = dict(msg.metadata or {})
            metadata.update(ctx._metadata)
            if not content and not metadata:
                return None
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=content,
                metadata=metadata if metadata != dict(msg.metadata or {}) else (msg.metadata or {}),
            )

        return await self._run_command_with_middlewares(cmd, args_str, middleware_ctx, _execute)

    def _known_slash_commands(self) -> list[str]:
        return self._slash_command_catalog().slash_labels()

    def _unknown_command_response(self, msg: InboundMessage, cmd: str) -> OutboundMessage:
        content = f"Unknown command: {cmd}. Use /help to see available commands."
        return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=content)

    async def _handle_stop(self, msg: InboundMessage) -> None:
        """Cancel all active tasks and subagents for the session."""
        tasks = self._active_tasks.pop(msg.session_key, [])
        self._pending_latest.pop(msg.session_key, None)
        cancelled = sum(1 for task in tasks if not task.done() and task.cancel())
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        sub_cancelled = await self._agent_loop.subagents.cancel_by_session(msg.session_key)
        total = cancelled + sub_cancelled
        content = f"Stopped {total} task(s)." if total else "No active task to stop."
        await self._agent_loop.bus.publish_outbound(
            OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=content,
            )
        )

    async def _handle_restart(self, msg: InboundMessage) -> None:
        """Restart the process in-place via os.execv."""
        await self._agent_loop.bus.publish_outbound(
            OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="Restarting...",
            )
        )
        await self._do_restart(msg)

    async def _do_restart(self, _msg: InboundMessage) -> None:
        """Schedule an in-place process restart."""

        async def _restart() -> None:
            await asyncio.sleep(1)
            os.execv(sys.executable, [sys.executable, "-m", "aeloon"] + sys.argv[1:])

        asyncio.create_task(_restart())

    def _extract_debug_error(
        self, content: str, metadata: Mapping[str, object] | None
    ) -> str | None:
        if not content.startswith("Error:"):
            return None
        if metadata and metadata.get("_tool_hint"):
            return content
        return content

    async def _dispatch(self, msg: InboundMessage) -> None:
        """Process a message under the global lock."""
        async with self._processing_lock:
            try:
                response = await self.process_message(msg)
                if response is not None:
                    await self._agent_loop.bus.publish_outbound(response)
                elif msg.channel == "cli":
                    await self._agent_loop.bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content="",
                            metadata=msg.metadata or {},
                        )
                    )
            except asyncio.CancelledError:
                logger.info("Task cancelled for session {}", msg.session_key)
                raise
            except Exception as exc:
                logger.exception("Error processing message for session {}", msg.session_key)
                await self._agent_loop.bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=f"Sorry, I encountered an error: {type(exc).__name__}: {exc}",
                    )
                )

    async def process_message(
        self,
        msg: InboundMessage,
        *,
        session_key: str | None = None,
        on_progress: Callable[..., Awaitable[None]] | None = None,
    ) -> OutboundMessage | None:
        """Route one inbound message and return outbound response if any."""
        if msg.channel == "system":
            channel, chat_id = (
                msg.chat_id.split(":", 1) if ":" in msg.chat_id else ("cli", msg.chat_id)
            )
            logger.info("Processing system message from {}", msg.sender_id)
            ctx_metadata = dict(msg.metadata or {})
            ctx_metadata["_on_progress_cb"] = on_progress
            ctx = TurnContext(
                channel=channel,
                chat_id=chat_id,
                message_id=msg.metadata.get("message_id"),
                session_key=f"{channel}:{chat_id}",
                sender_id=msg.sender_id,
                metadata=ctx_metadata,
            )
            current_role = "assistant" if msg.sender_id == "subagent" else "user"
            final_content = await self._agent_loop.process_turn(
                ctx=ctx,
                content=msg.content,
                current_role=current_role,
                default_empty_reply=False,
                apply_message_suppress=False,
            )
            return OutboundMessage(
                channel=channel,
                chat_id=chat_id,
                content=final_content or "Background task completed.",
            )

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info("Processing message from {}:{}: {}", msg.channel, msg.sender_id, preview)
        self._ensure_builtin_dispatch_state()

        # Dispatch MESSAGE_RECEIVED hook (fire-and-forget)
        pm = getattr(self._agent_loop, "plugin_manager", None)
        if pm:
            try:
                from aeloon.plugins._sdk.hooks import HookEvent

                await pm._hooks.dispatch_notify(
                    HookEvent.MESSAGE_RECEIVED,
                    channel=msg.channel,
                    sender_id=msg.sender_id,
                    chat_id=msg.chat_id,
                    content=msg.content,
                    media=list(msg.media or []),
                    content_preview=preview,
                    session_key=session_key or msg.session_key,
                )
            except Exception:
                logger.opt(exception=True).debug("MESSAGE_RECEIVED hook dispatch failed")

        key = session_key or msg.session_key
        normalized_content = msg.content.replace("│", " ")
        cmd_text = " ".join(normalized_content.strip().split())
        cmd_parts = cmd_text.split()
        cmd = cmd_parts[0].lower() if cmd_parts else ""
        if cmd.startswith("/"):
            dispatch_msg = msg if key == msg.session_key else replace(msg, session_key_override=key)
            args_str = " ".join(cmd_parts[1:])
            handler = self._builtin_command_catalog.find_handler(cmd)
            if handler is not None:
                return await self._execute_builtin_command(
                    msg=dispatch_msg,
                    session_key=key,
                    cmd=cmd,
                    args_str=args_str,
                )

            pm = getattr(self._agent_loop, "plugin_manager", None)
            if pm:
                plugin_commands = pm.registry.commands
                command_name = cmd.lstrip("/")
                if command_name in plugin_commands:
                    record = plugin_commands[command_name]
                    return await self._execute_plugin_command(
                        record=record,
                        msg=msg,
                        key=key,
                        args_str=args_str,
                        on_progress=on_progress,
                        cmd=cmd,
                    )

            return self._unknown_command_response(msg, cmd)

        async def _bus_progress(content: str, *, tool_hint: bool = False) -> None:
            settings = self._agent_loop.runtime_settings
            if tool_hint and not settings.show_detail:
                return
            if not tool_hint and content.startswith("Error:") and not settings.show_debug:
                return
            meta = dict(msg.metadata or {})
            meta["_progress"] = True
            meta["_tool_hint"] = tool_hint
            debug_error = self._extract_debug_error(content, meta)
            if debug_error is not None:
                meta["_debug"] = True
            await self._agent_loop.bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=debug_error or content,
                    metadata=meta,
                )
            )

        ctx_metadata = dict(msg.metadata or {})
        ctx_metadata["_on_progress_cb"] = on_progress or _bus_progress
        ctx = TurnContext(
            channel=msg.channel,
            chat_id=msg.chat_id,
            message_id=msg.metadata.get("message_id"),
            session_key=key,
            sender_id=msg.sender_id,
            metadata=ctx_metadata,
        )
        final_content = await self._agent_loop.process_turn(
            ctx=ctx,
            content=msg.content,
            media=msg.media if msg.media else None,
            on_progress=on_progress or _bus_progress,
        )

        if self._agent_loop.runtime_settings.show_deep_profile and on_progress is None:
            await self._agent_loop._publish_deep_profile_report(msg)
        elif self._agent_loop.runtime_settings.show_profile and on_progress is None:
            await self._agent_loop._publish_hotspot_report(msg)
        elif self._agent_loop.profiler.enabled and on_progress is None:
            await self._agent_loop._publish_profile_report(msg)

        if final_content is None:
            return None

        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)

        # Fire the sent-message hook.
        pm = getattr(self._agent_loop, "plugin_manager", None)
        if pm:
            try:
                from aeloon.plugins._sdk.hooks import HookEvent

                await pm._hooks.dispatch_notify(
                    HookEvent.MESSAGE_SENT,
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content_preview=preview,
                    session_key=session_key or msg.session_key,
                )
            except Exception:
                logger.opt(exception=True).debug("MESSAGE_SENT hook dispatch failed")

        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content,
            metadata=msg.metadata or {},
        )
