"""Agent CLI flow implementation."""

from __future__ import annotations

import asyncio
import os
import select
import shutil
import signal
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.widgets import Box, Frame, TextArea

from aeloon.cli.app import console
from aeloon.cli.flows.helpers import boot_plugins
from aeloon.cli.interactive.display import (
    ThinkingSpinner,
    print_agent_response,
    print_cli_progress_line,
    print_interactive_line,
    print_interactive_profile_report,
    print_interactive_progress_line,
    print_interactive_response,
    print_stderr_profile_report,
    try_render_inline_image,
)
from aeloon.cli.interactive.navigation import (
    PaletteInputProcessor,
    SlashCommandCompleter,
    auto_descend_query,
    interactive_slash_palette,
    rank_slash_commands,
    should_open_slash_palette,
)
from aeloon.cli.interactive.session import (
    build_bottom_toolbar,
    compose_welcome_banner,
    make_new_cli_session_id,
    print_replayed_history,
    resolve_agent_session_id,
    resolve_initial_cli_state,
    session_display_messages,
)
from aeloon.cli.plugins import register_plugin_cli
from aeloon.cli.runtime_helpers import (
    load_runtime_config,
    make_provider,
    print_deprecated_memory_window_notice,
)
from aeloon.utils.helpers import sync_workspace_templates

EXIT_COMMANDS = {"exit", "quit", "/exit", "/quit", ":q"}
_OPEN_GATEWAY_LOGS_SENTINEL = "\x00__logs__"

_PROMPT_SESSION: PromptSession | None = None
_SAVED_TERM_ATTRS: list[Any] | None = None

_SlashCommandCompleter = SlashCommandCompleter
_PaletteInputProcessor = PaletteInputProcessor
_session_display_messages = session_display_messages
_build_bottom_toolbar = build_bottom_toolbar
_resolve_initial_cli_state = resolve_initial_cli_state
_print_interactive_line = print_interactive_line
_print_stderr_profile_report = print_stderr_profile_report
_try_render_inline_image = try_render_inline_image
_rank_slash_commands = rank_slash_commands
_should_open_slash_palette = should_open_slash_palette
_auto_descend_query = auto_descend_query


def effective_profile_mode(
    agent_loop, cli_profile_flag: bool, cli_deep_flag: bool = False
) -> str | None:
    """Resolve the effective profile mode for the current CLI execution."""
    if cli_deep_flag:
        return "deep-profile"
    if cli_profile_flag:
        return "profile"
    output_mode = agent_loop.runtime_settings.output_mode
    if output_mode in {"profile", "deep-profile"}:
        return output_mode
    return None


def is_exit_command(command: str, *, exit_commands: set[str]) -> bool:
    """Return True when input should end interactive chat."""
    return command.lower() in exit_commands


def _flush_pending_tty_input() -> None:
    """Drop unread keypresses typed while the model was generating output."""
    try:
        fd = sys.stdin.fileno()
        if not os.isatty(fd):
            return
    except Exception:
        return

    try:
        import termios

        termios.tcflush(fd, termios.TCIFLUSH)
        return
    except Exception:
        pass

    try:
        while True:
            ready, _, _ = select.select([fd], [], [], 0)
            if not ready:
                break
            if not os.read(fd, 4096):
                break
    except Exception:
        return


def _restore_terminal() -> None:
    """Restore terminal to its original state."""
    if _SAVED_TERM_ATTRS is None:
        return
    try:
        import termios

        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, _SAVED_TERM_ATTRS)
    except Exception:
        pass


def _read_tty_bytes(fd: int, timeout: float) -> bytes:
    """Read pending stdin bytes from a TTY without blocking forever."""
    try:
        ready, _, _ = select.select([fd], [], [], timeout)
    except Exception:
        return b""
    if not ready:
        return b""
    try:
        return os.read(fd, 4096)
    except Exception:
        return b""


async def _watch_for_ctrl_l(
    open_logs_requested: asyncio.Event,
    *,
    poll_interval: float = 0.1,
) -> None:
    """Watch raw stdin for Ctrl+L during the thinking phase."""
    try:
        fd = sys.stdin.fileno()
        if not os.isatty(fd):
            return
    except Exception:
        return

    saved_attrs = None
    try:
        import termios
        import tty

        saved_attrs = termios.tcgetattr(fd)
        tty.setraw(fd)
        while not open_logs_requested.is_set():
            chunk = await asyncio.to_thread(_read_tty_bytes, fd, poll_interval)
            if not chunk:
                continue
            if chunk[:1] == b"\x0c":
                open_logs_requested.set()
                return
    except asyncio.CancelledError:
        raise
    except Exception:
        return
    finally:
        if saved_attrs is not None:
            try:
                import termios

                termios.tcsetattr(fd, termios.TCSADRAIN, saved_attrs)
            except Exception:
                pass


def _build_interactive_key_bindings() -> KeyBindings:
    """Build prompt key bindings for interactive mode."""
    kb = KeyBindings()

    def _open_logs(event) -> None:
        event.app.exit(result=_OPEN_GATEWAY_LOGS_SENTINEL)

    kb.add("c-l")(_open_logs)
    return kb


def _init_prompt_session(bottom_toolbar=None) -> None:
    """Create the prompt_toolkit session with persistent file history."""
    global _PROMPT_SESSION, _SAVED_TERM_ATTRS

    try:
        import termios

        _SAVED_TERM_ATTRS = termios.tcgetattr(sys.stdin.fileno())
    except Exception:
        pass

    from aeloon.core.config.paths import get_cli_history_path

    history_file = get_cli_history_path()
    history_file.parent.mkdir(parents=True, exist_ok=True)
    _PROMPT_SESSION = PromptSession(
        history=FileHistory(str(history_file)),
        enable_open_in_editor=False,
        multiline=False,
        bottom_toolbar=bottom_toolbar,
        completer=_SlashCommandCompleter(),
        complete_while_typing=True,
        key_bindings=_build_interactive_key_bindings(),
    )


def _get_prompt_session() -> PromptSession | None:
    return _PROMPT_SESSION


async def _read_interactive_input_async() -> str:
    """Read one interactive input line from prompt_toolkit."""
    if _PROMPT_SESSION is None:
        raise RuntimeError("Call _init_prompt_session() first")
    try:
        with patch_stdout():
            return await _PROMPT_SESSION.prompt_async(HTML("<b fg='ansiblue'>You:</b> "))
    except EOFError as exc:
        raise KeyboardInterrupt from exc


async def _interactive_setting_menu() -> str | None:
    return await interactive_slash_palette("/setting")


async def _wait_for_turn_completion(
    turn_done: asyncio.Event,
    *,
    logs: bool,
    status_mgr: Any | None = None,
    cancel_requested: asyncio.Event | None = None,
    cancel_current_turn: Any | None = None,
    open_logs_requested: asyncio.Event | None = None,
    open_log_viewer: Any | None = None,
) -> None:
    """Wait for one interactive turn without re-entering prompt_toolkit input mode."""
    thinking = _ThinkingSpinner(enabled=not logs)
    ctrl_l_task: asyncio.Task[None] | None = None
    if status_mgr is not None:
        status_mgr.thinking = True
    try:
        with thinking:
            if cancel_requested is None or cancel_current_turn is None:
                await turn_done.wait()
                return

            if open_logs_requested is not None:
                ctrl_l_task = asyncio.create_task(_watch_for_ctrl_l(open_logs_requested))
            while not turn_done.is_set():
                turn_wait = asyncio.create_task(turn_done.wait())
                cancel_wait = asyncio.create_task(cancel_requested.wait())
                waiters: set[asyncio.Task[bool]] = {turn_wait, cancel_wait}
                logs_wait: asyncio.Task[bool] | None = None
                if open_logs_requested is not None and open_log_viewer is not None:
                    logs_wait = asyncio.create_task(open_logs_requested.wait())
                    waiters.add(logs_wait)
                done, pending = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)

                if turn_wait in done:
                    await turn_wait
                    break

                if logs_wait is not None and logs_wait in done:
                    open_logs_requested.clear()
                    if ctrl_l_task is not None:
                        ctrl_l_task.cancel()
                        await asyncio.gather(ctrl_l_task, return_exceptions=True)
                        ctrl_l_task = None
                    viewer_result = await open_log_viewer()
                    if viewer_result == "exit_process":
                        raise KeyboardInterrupt
                    if turn_done.is_set():
                        break
                    if open_logs_requested is not None:
                        ctrl_l_task = asyncio.create_task(_watch_for_ctrl_l(open_logs_requested))
                    continue

                cancel_requested.clear()
                await cancel_current_turn()
    finally:
        if ctrl_l_task is not None:
            ctrl_l_task.cancel()
            await asyncio.gather(ctrl_l_task, return_exceptions=True)
        if status_mgr is not None:
            status_mgr.thinking = False


async def _interactive_menu(title: str, options: list[tuple[str, str, str]]) -> str | None:
    """Shared arrow-key menu helper used by local interactive command menus."""
    index = 0
    result: dict[str, str | None] = {"value": None}
    body = TextArea(focusable=False, scrollbar=False)

    def _render() -> None:
        lines = []
        label_width = max((len(label) for _value, label, _desc in options), default=10)
        for idx, (_value, label, desc) in enumerate(options):
            prefix = "❯ " if idx == index else "  "
            lines.append(f"{prefix}{label:<{label_width}}  {desc}")
        body.text = "\n".join(lines)

    kb = KeyBindings()

    @kb.add("up")
    def _up(_event) -> None:
        nonlocal index
        index = (index - 1) % len(options)
        _render()

    @kb.add("down")
    def _down(_event) -> None:
        nonlocal index
        index = (index + 1) % len(options)
        _render()

    @kb.add("enter")
    def _enter(event) -> None:
        result["value"] = options[index][0]
        event.app.exit()

    @kb.add("escape")
    @kb.add("c-c")
    def _cancel(event) -> None:
        result["value"] = None
        event.app.exit()

    _render()
    prompt_app = Application(
        layout=Layout(Box(Frame(body, title=title), padding=1)),
        key_bindings=kb,
        full_screen=False,
    )
    await prompt_app.run_async()
    return result["value"]


async def _interactive_sessions_menu(
    agent_loop: Any,
    current_session_key: str,
    *,
    base_command: str = "/sessions",
) -> str | None:
    return await interactive_slash_palette(
        base_command,
        agent_loop=agent_loop,
        current_session_key=current_session_key,
    )


class _ThinkingSpinner(ThinkingSpinner):
    """Compatibility spinner wrapper bound to the shared console."""

    def __init__(self, enabled: bool):
        super().__init__(enabled, console=console)


def _print_agent_response(response: str, render_markdown: bool) -> None:
    print_agent_response(response, render_markdown, console=console)


def _print_cli_progress_line(text: str, thinking: _ThinkingSpinner | None) -> None:
    print_cli_progress_line(text, thinking, console=console)


async def _print_interactive_progress_line(
    text: str,
    thinking: _ThinkingSpinner | None,
) -> None:
    await print_interactive_progress_line(
        text,
        thinking,
        print_interactive_line=_print_interactive_line,
    )


def _try_open_media_file(path: str | Path) -> bool:
    """Try to open a local media file with a platform-native viewer."""
    target = Path(path).expanduser()
    if not target.exists():
        return False

    target_str = str(target)
    if sys.platform == "win32":
        startfile = getattr(os, "startfile", None)
        if startfile is not None:
            try:
                startfile(target_str)
                return True
            except OSError:
                pass
        candidates = [
            ["powershell", "-NoProfile", "-Command", "Start-Process", "-FilePath", target_str],
            ["explorer", target_str],
        ]
    elif sys.platform == "darwin":
        candidates = [["open", target_str]]
    else:
        candidates = [
            ["xdg-open", target_str],
            ["gio", "open", target_str],
            ["gnome-open", target_str],
            ["kde-open", target_str],
            ["kde-open5", target_str],
            ["see", target_str],
        ]

    for command in candidates:
        if shutil.which(command[0]) is None:
            continue
        try:
            subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True
        except OSError:
            continue

    return False


async def _handle_interactive_media(
    media_paths: list[str],
    thinking: Any,
) -> None:
    for media_path in media_paths:
        if _try_render_inline_image(media_path):
            continue
        await _print_interactive_progress_line(
            f"Unable to display image. File: {media_path}",
            thinking,
        )


def run_agent(
    *,
    message: str | None,
    session_id: str | None,
    resume: bool,
    workspace: str | None,
    config: str | None,
    profile: bool,
    deep_profile: bool,
    markdown: bool,
    logs: bool,
) -> None:
    """Interact with the agent directly."""
    from loguru import logger

    from aeloon.core.agent.loop import AgentLoop
    from aeloon.core.bus.queue import MessageBus
    from aeloon.core.config.paths import get_cron_dir
    from aeloon.services.cron.service import CronService

    loaded_config = load_runtime_config(config, workspace)
    print_deprecated_memory_window_notice(loaded_config)
    sync_workspace_templates(loaded_config.workspace_path)

    bus = MessageBus()
    provider = make_provider(loaded_config)
    cron = CronService(get_cron_dir() / "jobs.json")

    if logs:
        logger.enable("aeloon")
    else:
        logger.disable("aeloon")

    agent_loop = AgentLoop(
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
        mcp_servers=loaded_config.tools.mcp_servers,
        channels_config=loaded_config.channels,
        output_mode=loaded_config.agents.defaults.output_mode,
        fast=loaded_config.agents.defaults.fast,
    )
    if session_id or resume:
        session_id = resolve_agent_session_id(session_id, resume, agent_loop.sessions)
    else:
        session_id = make_new_cli_session_id()
    profile_mode = effective_profile_mode(agent_loop, profile, deep_profile)
    agent_loop.profiler.enabled = profile_mode is not None
    thinking = None

    async def _cli_progress(content: str, *, tool_hint: bool = False) -> None:
        ch = agent_loop.channels_config
        if ch and tool_hint and not ch.send_tool_hints:
            return
        if ch and not tool_hint and not ch.send_progress:
            return
        _print_cli_progress_line(content, thinking)

    if message:

        def _print_profile_if_available() -> None:
            if not profile_mode:
                return
            profiler = agent_loop.profiler
            if profiler._current_report is not None:
                profiler.end_turn()
            if profiler.last_report:
                report = (
                    profiler.report_deep_profile()
                    if profile_mode == "deep-profile"
                    else profiler.report()
                )
                _print_stderr_profile_report(report)

        async def _run_once() -> None:
            nonlocal thinking
            agent_loop.plugin_manager = await boot_plugins(agent_loop, loaded_config)
            if agent_loop.plugin_manager:
                register_plugin_cli(agent_loop.plugin_manager.registry)
            thinking = _ThinkingSpinner(enabled=not logs)
            sigterm_handler = signal.getsignal(signal.SIGTERM)

            def _sigterm_to_kbi(signum: int, frame: Any) -> None:
                raise KeyboardInterrupt

            try:
                signal.signal(signal.SIGTERM, _sigterm_to_kbi)
            except (ValueError, OSError):
                pass

            async def _run_channel_auth_once(channel_name: str) -> None:
                from aeloon.core.bus.events import InboundMessage

                effective_session_key = session_id
                chat_id = session_id.split(":", 1)[1] if ":" in session_id else session_id
                runner = asyncio.create_task(agent_loop.run())
                saw_initial = False
                try:
                    await agent_loop.bus.publish_inbound(
                        InboundMessage(
                            channel="cli",
                            sender_id="user",
                            chat_id=chat_id,
                            content=message,
                            session_key_override=effective_session_key,
                        )
                    )
                    while True:
                        outbound = await asyncio.wait_for(
                            agent_loop.bus.consume_outbound(), timeout=310.0
                        )
                        with thinking.pause() if thinking else nullcontext():
                            if outbound.media:
                                for media_path in outbound.media:
                                    _try_render_inline_image(media_path)
                            if outbound.content:
                                _print_agent_response(outbound.content, render_markdown=markdown)
                        text = (outbound.content or "").lower()
                        if (
                            channel_name == "wechat"
                            and "please scan this qr code with wechat" in text
                        ):
                            saw_initial = True
                            continue
                        if (
                            channel_name == "feishu"
                            and "please scan this qr code with feishu" in text
                        ):
                            saw_initial = True
                            continue
                        if channel_name == "wechat" and (
                            "already logged in to wechat" in text
                            or "failed to initiate wechat login" in text
                            or "wechat login successful" in text
                            or "wechat login timed out" in text
                            or "wechat login failed" in text
                            or "wechat login was cancelled" in text
                            or "wechat logged out" in text
                            or "not currently logged in to wechat" in text
                            or (saw_initial and "wechat channel is starting" in text)
                            or (saw_initial and "wechat credentials saved" in text)
                        ):
                            break
                        if channel_name == "feishu" and (
                            "already logged in to feishu" in text
                            or "failed to initiate feishu login" in text
                            or "feishu login successful" in text
                            or "feishu login timed out" in text
                            or "feishu login failed" in text
                            or "feishu login was cancelled" in text
                            or "feishu logged out" in text
                            or "not currently logged in to feishu" in text
                            or (saw_initial and "feishu channel is starting" in text)
                            or (saw_initial and "feishu credentials saved" in text)
                        ):
                            break
                finally:
                    agent_loop.dispatcher.stop()
                    runner.cancel()
                    try:
                        await runner
                    except asyncio.CancelledError:
                        pass

            try:
                with thinking:
                    if message.strip() in {"/wechat login", "/wechat logout"}:
                        await _run_channel_auth_once("wechat")
                    elif (
                        message.strip().startswith("/feishu login")
                        or message.strip() == "/feishu logout"
                    ):
                        await _run_channel_auth_once("feishu")
                    else:
                        response = await agent_loop.process_direct_full(
                            message, session_id, on_progress=_cli_progress
                        )
                        thinking = None
                        if response and response.media:
                            for media_path in response.media:
                                _try_render_inline_image(media_path)
                        _print_agent_response(
                            response.content if response else "", render_markdown=markdown
                        )
                thinking = None
            except (KeyboardInterrupt, asyncio.CancelledError):
                thinking = None
                print("\n\n— interrupted —\n", file=sys.stderr)
            finally:
                _print_profile_if_available()
                try:
                    signal.signal(signal.SIGTERM, sigterm_handler)
                except (ValueError, OSError):
                    pass
                if agent_loop.plugin_manager:
                    await agent_loop.plugin_manager.shutdown()
                await agent_loop.close_mcp()

        asyncio.run(_run_once())
        return

    from aeloon.core.bus.events import InboundMessage
    from aeloon.plugins._sdk.status_line import StatusLineManager

    cli_state, _start_fresh = resolve_initial_cli_state(session_id)
    status_mgr = StatusLineManager(agent_loop)

    def _toolbar():
        return status_mgr.build_toolbar(cli_state["channel"], cli_state["chat_id"])

    _init_prompt_session(_toolbar)
    startup_workspace = Path(loaded_config.workspace_path).name or "workspace"
    console.print(compose_welcome_banner(startup_workspace, loaded_config.agents.defaults.model))
    console.print()

    existing_session = agent_loop.sessions.get_or_create(
        f"{cli_state['channel']}:{cli_state['chat_id']}"
    )
    replay_messages = session_display_messages(existing_session)
    if replay_messages:
        console.print("[dim]Restored conversation:[/dim]\n")
        print_replayed_history(
            replay_messages,
            markdown,
            console=console,
            print_agent_response=_print_agent_response,
        )

    signal_state: dict[str, Any] = {
        "cancel_turn": None,
        "turn_active": False,
        "in_log_viewer": False,
    }

    def _handle_signal(signum, frame):
        sig_name = signal.Signals(signum).name
        cancel_turn = signal_state.get("cancel_turn")
        if (
            signum == signal.SIGINT
            and signal_state.get("turn_active")
            and cancel_turn is not None
            and not signal_state.get("in_log_viewer")
        ):
            cancel_turn()
            return
        _restore_terminal()
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, _handle_signal)
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_IGN)

    async def _run_interactive():
        nonlocal thinking
        agent_loop.plugin_manager = await boot_plugins(agent_loop, loaded_config)
        if agent_loop.plugin_manager:
            register_plugin_cli(agent_loop.plugin_manager.registry)
            status_mgr.set_registry(agent_loop.plugin_manager.registry)
        bus_task = asyncio.create_task(agent_loop.run())
        turn_done = asyncio.Event()
        turn_done.set()
        turn_response: list[str] = []
        current_turn_task: asyncio.Task[None] | None = None
        active_turn_id: int | None = None
        turn_counter = 0
        cancel_requested = asyncio.Event()
        open_logs_requested = asyncio.Event()
        loop = asyncio.get_running_loop()
        abandoned_turn_tasks: set[asyncio.Task[None]] = set()

        def _mark_turn_cancelled() -> None:
            nonlocal active_turn_id
            signal_state["turn_active"] = False
            active_turn_id = None
            if turn_done.is_set():
                return
            turn_response.clear()
            turn_response.append("Interrupted current task.")
            turn_done.set()

        def _on_turn_task_done(task: asyncio.Task[None]) -> None:
            nonlocal current_turn_task
            if current_turn_task is task:
                current_turn_task = None
            abandoned_turn_tasks.discard(task)
            if task.cancelled():
                loop.call_soon_threadsafe(_mark_turn_cancelled)

        async def _cancel_current_turn() -> bool:
            nonlocal current_turn_task
            task = current_turn_task
            if task is None or task.done():
                return False
            await _print_interactive_progress_line(
                "Interrupt requested. Stopping current task...",
                thinking,
            )
            task.cancel()
            abandoned_turn_tasks.add(task)
            current_turn_task = None
            agent_loop.dispatcher.processing_lock = asyncio.Lock()
            _mark_turn_cancelled()
            return True

        signal_state["cancel_turn"] = lambda: loop.call_soon_threadsafe(cancel_requested.set)

        async def _run_gateway_log_viewer_for_turn() -> str:
            from aeloon.cli.interactive.log_viewer import (
                LOG_VIEWER_RESULT_EXIT_PROCESS,
                run_gateway_log_viewer,
            )
            from aeloon.core.config.paths import get_gateway_log_path

            signal_state["in_log_viewer"] = True
            try:
                result = await run_gateway_log_viewer(get_gateway_log_path())
            finally:
                signal_state["in_log_viewer"] = False
            if result == LOG_VIEWER_RESULT_EXIT_PROCESS:
                return "exit_process"
            return "close"

        async def _consume_outbound():
            while True:
                try:
                    msg = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
                    msg_turn_id = msg.metadata.get("_interactive_turn_id")
                    is_stale_turn_msg = msg_turn_id is not None and msg_turn_id != active_turn_id
                    if is_stale_turn_msg:
                        continue
                    if msg.media:
                        await _handle_interactive_media(msg.media, thinking)
                    if msg.metadata.get("_profile"):
                        await print_interactive_profile_report(msg.content, thinking)
                    elif msg.metadata.get("_progress"):
                        is_tool_hint = msg.metadata.get("_tool_hint", False)
                        ch = agent_loop.channels_config
                        if ch and is_tool_hint and not ch.send_tool_hints:
                            pass
                        elif ch and not is_tool_hint and not ch.send_progress:
                            pass
                        else:
                            await _print_interactive_progress_line(msg.content, thinking)
                    elif not turn_done.is_set():
                        if msg.metadata.get("_session_switch"):
                            target_key = str(msg.metadata.get("session_key") or "")
                            if ":" in target_key:
                                next_channel, next_chat_id = target_key.split(":", 1)
                                cli_state["channel"] = next_channel
                                cli_state["chat_id"] = next_chat_id
                                session = agent_loop.sessions.get_or_create(target_key)
                                replay_messages = session_display_messages(session)
                                if replay_messages:
                                    from prompt_toolkit.application import run_in_terminal

                                    await run_in_terminal(
                                        lambda: (
                                            console.print("[dim]Switched session history:[/dim]\n"),
                                            print_replayed_history(
                                                replay_messages,
                                                markdown,
                                                console=console,
                                                print_agent_response=_print_agent_response,
                                            ),
                                        )
                                    )
                        if msg.content:
                            turn_response.append(msg.content)
                        turn_done.set()
                    elif msg.content:
                        await print_interactive_response(msg.content, render_markdown=markdown)
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break

        outbound_task = asyncio.create_task(_consume_outbound())
        try:
            while True:
                try:
                    _flush_pending_tty_input()
                    user_input = await _read_interactive_input_async()
                    if user_input == _OPEN_GATEWAY_LOGS_SENTINEL:
                        viewer_result = await _run_gateway_log_viewer_for_turn()
                        if viewer_result == "exit_process":
                            raise KeyboardInterrupt
                        continue
                    command = user_input.strip()
                    if not command:
                        continue
                    current_session_key = f"{cli_state['channel']}:{cli_state['chat_id']}"
                    if should_open_slash_palette(
                        command,
                        agent_loop=agent_loop,
                        current_session_key=current_session_key,
                    ):
                        replacement = await interactive_slash_palette(
                            command,
                            agent_loop=agent_loop,
                            current_session_key=current_session_key,
                        )
                        if not replacement:
                            if command != "/":
                                replacement = command
                            else:
                                continue
                        user_input = replacement
                        command = replacement.strip()

                    if is_exit_command(command, exit_commands=EXIT_COMMANDS):
                        _restore_terminal()
                        console.print("\nGoodbye!")
                        break

                    turn_done.clear()
                    turn_response.clear()
                    signal_state["turn_active"] = True
                    turn_counter += 1
                    active_turn_id = turn_counter
                    current_turn_task = asyncio.create_task(
                        agent_loop.dispatcher._dispatch(
                            InboundMessage(
                                channel=cli_state["channel"],
                                sender_id="user",
                                chat_id=cli_state["chat_id"],
                                content=user_input,
                                metadata={"_interactive_turn_id": active_turn_id},
                                session_key_override=current_session_key,
                            )
                        )
                    )
                    current_turn_task.add_done_callback(_on_turn_task_done)

                    await _wait_for_turn_completion(
                        turn_done,
                        logs=logs,
                        status_mgr=status_mgr,
                        cancel_requested=cancel_requested,
                        cancel_current_turn=_cancel_current_turn,
                        open_logs_requested=open_logs_requested,
                        open_log_viewer=_run_gateway_log_viewer_for_turn,
                    )
                    signal_state["turn_active"] = False
                    active_turn_id = None

                    if turn_response:
                        _print_agent_response(turn_response[0], render_markdown=markdown)
                except (KeyboardInterrupt, EOFError):
                    _restore_terminal()
                    console.print("\nGoodbye!")
                    break
        finally:
            signal_state["cancel_turn"] = None
            signal_state["turn_active"] = False
            active_turn_id = None
            if current_turn_task is not None and not current_turn_task.done():
                current_turn_task.cancel()
                try:
                    await asyncio.wait_for(
                        asyncio.gather(current_turn_task, return_exceptions=True),
                        timeout=0.2,
                    )
                except asyncio.TimeoutError:
                    pass
            agent_loop.stop()
            outbound_task.cancel()
            await asyncio.gather(bus_task, outbound_task, return_exceptions=True)
            if agent_loop.plugin_manager:
                await agent_loop.plugin_manager.shutdown()
            pm = getattr(agent_loop, "plugin_manager", None)
            if pm:
                try:
                    from aeloon.plugins._sdk.hooks import HookEvent

                    await pm._hooks.dispatch_notify(HookEvent.AGENT_STOP)
                except Exception:
                    pass
            await agent_loop.close_mcp()

    asyncio.run(_run_interactive())
