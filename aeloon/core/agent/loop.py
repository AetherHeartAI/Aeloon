"""High-level agent runtime loop."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from aeloon.core.agent.context import ContextBuilder
from aeloon.core.agent.dispatcher import Dispatcher
from aeloon.core.agent.kernel import run_agent_kernel
from aeloon.core.agent.middleware import ProfilerMiddleware
from aeloon.core.agent.output_manager import OutputManager, OutputTracker
from aeloon.core.agent.profiler import AgentProfiler, SpanCategory
from aeloon.core.agent.skill_runtime import SkillBuildContext, SkillRuntime
from aeloon.core.agent.subagent import SubagentManager
from aeloon.core.agent.tools.factory import register_core_tools
from aeloon.core.agent.tools.message import MessageTool
from aeloon.core.agent.tools.registry import ToolRegistry
from aeloon.core.agent.turn import TurnContext
from aeloon.core.bus.events import InboundMessage, OutboundMessage
from aeloon.core.bus.queue import MessageBus
from aeloon.core.session.manager import SessionManager
from aeloon.memory.base import MemoryBackendDeps, PreparedMemoryContext
from aeloon.memory.runtime import MemoryRuntime
from aeloon.plugins._sdk.runtime import PLUGIN_SESSION_PREFIX
from aeloon.providers.base import LLMProvider

if TYPE_CHECKING:
    from aeloon.core.config.schema import (
        ChannelsConfig,
        ExecToolConfig,
        MemoryConfig,
        WebSearchConfig,
    )
    from aeloon.services.cron.service import CronService


@dataclass
class RuntimeSettings:
    """Per-loop runtime settings adjustable via slash commands."""

    output_mode: str = "normal"
    fast: bool = False

    @property
    def show_detail(self) -> bool:
        return self.output_mode in {"profile", "deep-profile"}

    @property
    def show_debug(self) -> bool:
        return self.output_mode == "deep-profile"

    @property
    def show_profile(self) -> bool:
        return self.output_mode == "profile"

    @property
    def show_deep_profile(self) -> bool:
        return self.output_mode == "deep-profile"


class AgentLoop:
    """Wire sessions, tools, skills, and the reusable kernel together."""

    _SAVE_TOOL_RESULT_MAX_CHARS = 16_000

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int = 40,
        context_window_tokens: int = 65_536,
        web_search_config: WebSearchConfig | None = None,
        web_proxy: str | None = None,
        exec_config: ExecToolConfig | None = None,
        cron_service: CronService | None = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
        mcp_servers: dict | None = None,
        channels_config: ChannelsConfig | None = None,
        memory_config: MemoryConfig | None = None,
        output_mode: str = "normal",
        fast: bool = False,
    ):
        from aeloon.core.config.schema import ExecToolConfig, MemoryConfig, WebSearchConfig

        self.bus = bus
        self.channels_config = channels_config
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.context_window_tokens = context_window_tokens
        self.web_search_config = web_search_config or WebSearchConfig()
        self.web_proxy = web_proxy
        self.exec_config = exec_config or ExecToolConfig()
        self.cron_service = cron_service
        self.restrict_to_workspace = restrict_to_workspace
        self.memory_config = memory_config or MemoryConfig()

        self.context = ContextBuilder(workspace)
        self.output_manager = OutputManager(workspace)
        self.context.set_output_manager(self.output_manager)
        self.sessions = session_manager or SessionManager(workspace)
        self.tools = ToolRegistry()
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            web_search_config=self.web_search_config,
            web_proxy=web_proxy,
            exec_config=self.exec_config,
            restrict_to_workspace=restrict_to_workspace,
        )
        self.skill_context = SkillBuildContext(
            workspace=workspace,
            web_search_config=self.web_search_config,
            web_proxy=self.web_proxy,
            subagent_manager=self.subagents,
            cron_service=self.cron_service,
        )
        self.skill_runtime = SkillRuntime(
            registry=self.tools,
            context=self.skill_context,
        )

        self._mcp_servers = mcp_servers or {}
        self._mcp_stack: AsyncExitStack | None = None
        self._mcp_connected = False
        self._mcp_connecting = False
        self._background_tasks: list[asyncio.Task] = []

        self.dispatcher = Dispatcher(self)
        self.plugin_manager: Any = None  # Injected after boot.

        self.memory = MemoryRuntime(
            memory_config=self.memory_config,
            deps=MemoryBackendDeps(
                workspace=workspace,
                provider=provider,
                model=self.model,
                sessions=self.sessions,
                context_window_tokens=context_window_tokens,
                build_messages=self.context.build_messages,
                get_tool_definitions=self.tools.get_definitions,
            ),
        )
        self.memory_consolidator = self.memory.backend

        from aeloon.core.agent.tools.policy import set_file_policy

        set_file_policy(OutputTracker(self.output_manager), append=True)
        self.profiler = AgentProfiler()
        self.runtime_settings = RuntimeSettings(output_mode=output_mode, fast=fast)
        self.profiler.enabled = (
            self.runtime_settings.show_profile or self.runtime_settings.show_deep_profile
        )
        self._register_default_tools()

    @property
    def _running(self) -> bool:
        """Backward-compatible state alias to dispatcher."""
        return self.dispatcher.running

    @_running.setter
    def _running(self, value: bool) -> None:
        self.dispatcher.running = value

    @property
    def _active_tasks(self) -> dict[str, list[asyncio.Task]]:
        """Backward-compatible state alias to dispatcher."""
        return self.dispatcher.active_tasks

    @_active_tasks.setter
    def _active_tasks(self, value: dict[str, list[asyncio.Task]]) -> None:
        self.dispatcher.active_tasks = value

    @property
    def _processing_lock(self) -> asyncio.Lock:
        """Backward-compatible state alias to dispatcher."""
        return self.dispatcher.processing_lock

    @_processing_lock.setter
    def _processing_lock(self, value: asyncio.Lock) -> None:
        self.dispatcher.processing_lock = value

    def _register_default_tools(self) -> None:
        """Register built-in tools for the main loop."""
        register_core_tools(
            self.tools,
            workspace=self.workspace,
            restrict_to_workspace=self.restrict_to_workspace,
            exec_config=self.exec_config,
            web_search_config=self.web_search_config,
            web_proxy=self.web_proxy,
            prompt_memory_store=self.memory.prompt_memory,
        )
        self.tools.register(MessageTool(send_callback=self.bus.publish_outbound))
        self.skill_runtime.activate_defaults()

    async def _connect_mcp(self) -> None:
        """Lazily connect configured MCP servers once."""
        if self._mcp_connected or self._mcp_connecting or not self._mcp_servers:
            return

        self._mcp_connecting = True
        from aeloon.core.agent.tools.mcp import connect_mcp_servers

        try:
            self._mcp_stack = AsyncExitStack()
            await self._mcp_stack.__aenter__()
            await connect_mcp_servers(self._mcp_servers, self.tools, self._mcp_stack)
            self._mcp_connected = True
        except BaseException as e:
            logger.error("Failed to connect MCP servers (will retry next message): {}", e)
            if self._mcp_stack:
                try:
                    await self._mcp_stack.aclose()
                except Exception:
                    pass
                self._mcp_stack = None
        finally:
            self._mcp_connecting = False

    async def _publish_profile_report(self, msg: InboundMessage) -> None:
        """Publish the standard profile report as progress output."""
        meta = dict(msg.metadata or {})
        meta["_progress"] = True
        meta["_profile"] = True
        meta["_deep_profile"] = False
        await self.bus.publish_outbound(
            OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=self.profiler.report(),
                metadata=meta,
            )
        )

    async def _publish_hotspot_report(self, msg: InboundMessage) -> None:
        """Publish the hotspot profile report as progress output."""
        meta = dict(msg.metadata or {})
        meta["_progress"] = True
        meta["_profile"] = True
        meta["_deep_profile"] = False
        await self.bus.publish_outbound(
            OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=self.profiler.report_top_heavy(),
                metadata=meta,
            )
        )

    async def _publish_deep_profile_report(self, msg: InboundMessage) -> None:
        """Publish the deep profile report as progress output."""
        meta = dict(msg.metadata or {})
        meta["_progress"] = True
        meta["_profile"] = True
        meta["_deep_profile"] = True
        await self.bus.publish_outbound(
            OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=self.profiler.report_deep_profile(),
                metadata=meta,
            )
        )

    @asynccontextmanager
    async def _profiled_turn(self):
        """Profile one agent turn with guaranteed end-turn finalization."""
        self.profiler.start_turn()
        try:
            yield
        finally:
            self.profiler.end_turn()

    async def _run_agent_loop(
        self,
        initial_messages: list[dict],
        on_progress: Callable[..., Awaitable[None]] | None = None,
    ) -> tuple[str | None, list[str], list[dict]]:
        """Thin wrapper around the reusable agent kernel."""
        middlewares: list[Any] = [ProfilerMiddleware(self.profiler, self.model)]
        # Add plugin middlewares when available.
        if self.plugin_manager:
            middlewares.extend(rec.middleware for rec in self.plugin_manager.registry.middlewares)

        # Reuse the plugin hook dispatcher when available.
        hook_dispatcher = None
        if self.plugin_manager:
            hook_dispatcher = self.plugin_manager._hooks

        return await run_agent_kernel(
            provider=self.provider,
            model=self.model,
            tools=self.tools,
            messages=initial_messages,
            max_iterations=self.max_iterations,
            middlewares=middlewares,
            on_progress=on_progress,
            add_assistant_message=self.context.add_assistant_message,
            add_tool_result=self.context.add_tool_result,
            hook_dispatcher=hook_dispatcher,
        )

    async def process_turn(
        self,
        *,
        ctx: TurnContext,
        content: str,
        media: list[str] | None = None,
        current_role: str = "user",
        on_progress: Callable[..., Awaitable[None]] | None = None,
        default_empty_reply: bool = True,
        apply_message_suppress: bool = True,
    ) -> str | None:
        """Process one turn from context build through response handling."""
        async with self._profiled_turn():
            async with self.profiler.span(SpanCategory.SESSION_LOAD, "load"):
                session = self.sessions.get_or_create(ctx.session_key)

            self.tools.notify_turn_start(ctx)
            is_plugin_internal = ctx.session_key.startswith(PLUGIN_SESSION_PREFIX)
            if is_plugin_internal:
                prepared = PreparedMemoryContext(
                    history_start_index=self.memory.pending_start_index(session)
                )
            else:
                prepared = await self.memory.prepare_turn(
                    session=session,
                    query=content,
                    channel=ctx.channel,
                    chat_id=ctx.chat_id,
                    current_role=current_role,
                )

            history = session.get_history(start_index=prepared.history_start_index, max_messages=0)
            async with self.profiler.span(SpanCategory.CONTEXT, "build"):
                hidden_skill_names = list(self.memory.backend.hidden_skill_names)
                initial_messages = self.context.build_messages(
                    history=history,
                    current_message=content,
                    extra_system_sections=prepared.system_sections,
                runtime_lines=prepared.runtime_lines,
                extra_always_skills=prepared.always_skill_names,
                exclude_skill_names=hidden_skill_names,
                recalled_context_blocks=prepared.recalled_context_blocks,
                media=media if media else None,
                channel=ctx.channel,
                chat_id=ctx.chat_id,
                session_key=ctx.session_key,
                current_role=current_role,
                )

            final_content, _, all_msgs = await self._run_agent_loop(
                initial_messages,
                on_progress=on_progress,
            )
            if final_content is None and default_empty_reply:
                final_content = "I've completed processing but have no response to give."

            skip = 1 + len(history)
            raw_new_messages = [dict(message) for message in all_msgs[skip:]]
            pre_save_len = len(session.messages)
            self.sessions.save_turn(
                session,
                all_msgs,
                skip=skip,
                max_chars=self._SAVE_TOOL_RESULT_MAX_CHARS,
                runtime_context_tag=ContextBuilder._RUNTIME_CONTEXT_TAG,
            )
            persisted_new_messages = [dict(message) for message in session.messages[pre_save_len:]]
            async with self.profiler.span(SpanCategory.SESSION_SAVE, "save"):
                self.sessions.save(session)
            if not is_plugin_internal:
                await self.memory.after_turn(
                    session=session,
                    raw_new_messages=raw_new_messages,
                    persisted_new_messages=persisted_new_messages,
                    final_content=final_content,
                )

            if apply_message_suppress and self.tools.should_suppress_final_reply():
                return None

            return final_content

    async def run(self) -> None:
        """Run the loop using dispatcher task routing."""
        await self.dispatcher.run()

    async def _handle_stop(self, msg: InboundMessage) -> None:
        """Backward-compatible wrapper for stop handling."""
        await self.dispatcher._handle_stop(msg)

    async def _handle_restart(self, msg: InboundMessage) -> None:
        """Backward-compatible wrapper for restart handling."""
        await self.dispatcher._handle_restart(msg)

    async def _handle_profile_command(
        self,
        msg: InboundMessage,
        args: list[str],
    ) -> OutboundMessage:
        """Backward-compatible wrapper for profile command handling."""
        from aeloon.core.agent.commands.settings import handle_profile
        from aeloon.core.agent.commands._context import CommandContext

        self.dispatcher._ensure_builtin_dispatch_state()
        ctx = CommandContext.from_dispatch(
            agent_loop=self,
            msg=msg,
            session_key=f"{msg.channel}:{msg.chat_id}",
            is_builtin=True,
            send_progress=None,
        )
        content = await handle_profile(ctx, " ".join(args))
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=content or "",
            metadata=msg.metadata or {},
        )

    async def _dispatch(self, msg: InboundMessage) -> None:
        """Backward-compatible wrapper for dispatcher dispatch."""
        await self.dispatcher._dispatch(msg)

    async def close_mcp(self) -> None:
        """Drain background work, then close MCP connections."""
        await self.memory.close()

        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()

        if self._mcp_stack:
            try:
                await self._mcp_stack.aclose()
            except (RuntimeError, BaseExceptionGroup):
                pass
            self._mcp_stack = None

    def _schedule_background(self, coro) -> None:
        """Track a background task so it can be drained on shutdown."""
        task = asyncio.create_task(coro)
        self._background_tasks.append(task)
        task.add_done_callback(self._background_tasks.remove)

    def stop(self) -> None:
        """Stop the agent loop."""
        self.dispatcher.stop()
        logger.info("Agent loop stopping")

    async def _process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        on_progress: Callable[..., Awaitable[None]] | None = None,
    ) -> OutboundMessage | None:
        """Backward-compatible wrapper for dispatcher message processing."""
        return await self.dispatcher.process_message(
            msg,
            session_key=session_key,
            on_progress=on_progress,
        )

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress: Callable[..., Awaitable[None]] | None = None,
    ) -> str:
        """Process a message directly (for CLI or cron usage)."""
        response = await self.process_direct_full(
            content, session_key, channel, chat_id, on_progress
        )
        return response.content if response else ""

    async def process_direct_full(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress: Callable[..., Awaitable[None]] | None = None,
    ) -> OutboundMessage | None:
        """Process a message directly, returning the full OutboundMessage (including media)."""
        await self._connect_mcp()
        msg = InboundMessage(channel=channel, sender_id="user", chat_id=chat_id, content=content)
        return await self._process_message(
            msg,
            session_key=session_key,
            on_progress=on_progress,
        )
