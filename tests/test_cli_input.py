import asyncio
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.keys import Keys

from aeloon.cli.flows import agent as agent_flow


@pytest.fixture
def mock_prompt_session():
    """Mock the global prompt session."""
    mock_session = MagicMock()
    mock_session.prompt_async = AsyncMock()
    with (
        patch("aeloon.cli.flows.agent._PROMPT_SESSION", mock_session),
        patch("aeloon.cli.flows.agent.patch_stdout"),
    ):
        yield mock_session


@pytest.mark.asyncio
async def test_read_interactive_input_async_returns_input(mock_prompt_session):
    """Test that _read_interactive_input_async returns the user input from prompt_session."""
    mock_prompt_session.prompt_async.return_value = "hello world"

    result = await agent_flow._read_interactive_input_async()

    assert result == "hello world"
    mock_prompt_session.prompt_async.assert_called_once()
    args, _ = mock_prompt_session.prompt_async.call_args
    assert isinstance(args[0], HTML)  # Verify HTML prompt is used


@pytest.mark.asyncio
async def test_read_interactive_input_async_handles_eof(mock_prompt_session):
    """Test that EOFError converts to KeyboardInterrupt."""
    mock_prompt_session.prompt_async.side_effect = EOFError()

    with pytest.raises(KeyboardInterrupt):
        await agent_flow._read_interactive_input_async()


def test_init_prompt_session_creates_session():
    """Test that _init_prompt_session initializes the global session."""
    # Ensure global is None before test
    agent_flow._PROMPT_SESSION = None

    with (
        patch("aeloon.cli.flows.agent.PromptSession") as mock_session,
        patch("aeloon.cli.flows.agent.FileHistory"),
        patch("pathlib.Path.home") as mock_home,
    ):
        mock_home.return_value = MagicMock()

        agent_flow._init_prompt_session()

        assert agent_flow._PROMPT_SESSION is not None
        mock_session.assert_called_once()
        _, kwargs = mock_session.call_args
        assert kwargs["multiline"] is False
        assert kwargs["enable_open_in_editor"] is False
        assert kwargs["key_bindings"] is not None


def test_build_interactive_key_bindings_ctrl_l_always_opens_logs() -> None:
    bindings = agent_flow._build_interactive_key_bindings()
    ctrl_l_binding = next(
        binding for binding in bindings.bindings if binding.keys == (Keys.ControlL,)
    )
    app = MagicMock()

    ctrl_l_binding.handler(MagicMock(app=app))

    app.exit.assert_called_once_with(result=agent_flow._OPEN_GATEWAY_LOGS_SENTINEL)
    assert all(binding.keys != (Keys.ControlI,) for binding in bindings.bindings)


def test_read_tty_bytes_returns_pending_input(monkeypatch) -> None:
    monkeypatch.setattr(agent_flow.select, "select", lambda *_args, **_kwargs: ([0], [], []))
    monkeypatch.setattr(agent_flow.os, "read", lambda *_args, **_kwargs: b"\x0c")

    assert agent_flow._read_tty_bytes(0, 0.1) == b"\x0c"


@pytest.mark.asyncio
async def test_watch_for_ctrl_l_sets_open_logs_event(monkeypatch) -> None:
    open_logs_requested = asyncio.Event()
    chunks = iter([b"\x0c"])

    monkeypatch.setattr(agent_flow.os, "isatty", lambda _fd: True)
    monkeypatch.setattr(agent_flow.sys.stdin, "fileno", lambda: 0)
    monkeypatch.setattr(
        agent_flow.asyncio, "to_thread", lambda func, *args: AsyncMock(return_value=func(*args))()
    )

    import termios

    monkeypatch.setattr(termios, "tcgetattr", lambda _fd: [1])
    monkeypatch.setattr(termios, "tcsetattr", lambda *_args: None)
    monkeypatch.setattr("tty.setraw", lambda _fd: None)
    monkeypatch.setattr(agent_flow, "_read_tty_bytes", lambda *_args, **_kwargs: next(chunks))

    await agent_flow._watch_for_ctrl_l(open_logs_requested, poll_interval=0.01)

    assert open_logs_requested.is_set()


@pytest.mark.asyncio
async def test_wait_for_turn_completion_exits_process_from_log_viewer_ctrl_c() -> None:
    turn_done = asyncio.Event()
    cancel_requested = asyncio.Event()
    open_logs_requested = asyncio.Event()
    open_logs_requested.set()

    with patch("aeloon.cli.flows.agent._watch_for_ctrl_l", AsyncMock()):
        with pytest.raises(KeyboardInterrupt):
            await agent_flow._wait_for_turn_completion(
                turn_done,
                logs=False,
                cancel_requested=cancel_requested,
                cancel_current_turn=AsyncMock(),
                open_logs_requested=open_logs_requested,
                open_log_viewer=AsyncMock(return_value="exit_process"),
            )


def test_thinking_spinner_pause_stops_and_restarts():
    """Pause should stop the active spinner and restart it afterward."""
    spinner = MagicMock()

    with patch.object(agent_flow.console, "status", return_value=spinner):
        thinking = agent_flow._ThinkingSpinner(enabled=True)
        with thinking:
            with thinking.pause():
                pass

    assert spinner.method_calls == [
        call.start(),
        call.stop(),
        call.start(),
        call.stop(),
    ]


def test_print_cli_progress_line_pauses_spinner_before_printing():
    """CLI progress output should pause spinner to avoid garbled lines."""
    order: list[str] = []
    spinner = MagicMock()
    spinner.start.side_effect = lambda: order.append("start")
    spinner.stop.side_effect = lambda: order.append("stop")

    with (
        patch.object(agent_flow.console, "status", return_value=spinner),
        patch.object(
            agent_flow.console, "print", side_effect=lambda *_args, **_kwargs: order.append("print")
        ),
    ):
        thinking = agent_flow._ThinkingSpinner(enabled=True)
        with thinking:
            agent_flow._print_cli_progress_line("tool running", thinking)

    assert order == ["start", "stop", "print", "start", "stop"]


@pytest.mark.asyncio
async def test_print_interactive_progress_line_pauses_spinner_before_printing():
    """Interactive progress output should also pause spinner cleanly."""
    order: list[str] = []
    spinner = MagicMock()
    spinner.start.side_effect = lambda: order.append("start")
    spinner.stop.side_effect = lambda: order.append("stop")

    async def fake_print(_text: str) -> None:
        order.append("print")

    with (
        patch.object(agent_flow.console, "status", return_value=spinner),
        patch("aeloon.cli.flows.agent._print_interactive_line", side_effect=fake_print),
    ):
        thinking = agent_flow._ThinkingSpinner(enabled=True)
        with thinking:
            await agent_flow._print_interactive_progress_line("tool running", thinking)

    assert order == ["start", "stop", "print", "start", "stop"]


def test_try_open_media_file_uses_mac_open(monkeypatch, tmp_path):
    """macOS should use the `open` command for local media."""
    media_path = tmp_path / "qr.png"
    media_path.write_text("x", encoding="utf-8")
    popen = MagicMock()

    monkeypatch.setattr(agent_flow.sys, "platform", "darwin")
    monkeypatch.setattr(
        agent_flow.shutil, "which", lambda cmd: "/usr/bin/open" if cmd == "open" else None
    )
    monkeypatch.setattr(agent_flow.subprocess, "Popen", popen)

    assert agent_flow._try_open_media_file(media_path) is True
    popen.assert_called_once_with(
        ["open", str(media_path)],
        stdout=agent_flow.subprocess.DEVNULL,
        stderr=agent_flow.subprocess.DEVNULL,
        start_new_session=True,
    )


def test_try_open_media_file_uses_windows_startfile(monkeypatch, tmp_path):
    """Windows should prefer os.startfile when it is available."""
    media_path = tmp_path / "qr.png"
    media_path.write_text("x", encoding="utf-8")
    startfile = MagicMock()

    monkeypatch.setattr(agent_flow.sys, "platform", "win32")
    monkeypatch.setattr(agent_flow.os, "startfile", startfile, raising=False)

    assert agent_flow._try_open_media_file(media_path) is True
    startfile.assert_called_once_with(str(media_path))


@pytest.mark.asyncio
async def test_handle_interactive_media_prints_path_when_auto_open_fails(monkeypatch):
    """CLI should surface the saved file path when it cannot open the media."""
    messages: list[str] = []

    monkeypatch.setattr(agent_flow, "_try_render_inline_image", lambda _path: False)

    async def _fake_print(text: str, _thinking) -> None:
        messages.append(text)

    monkeypatch.setattr(agent_flow, "_print_interactive_progress_line", _fake_print)

    await agent_flow._handle_interactive_media(["/tmp/wechat_login.png"], None)

    assert messages == ["Unable to display image. File: /tmp/wechat_login.png"]
