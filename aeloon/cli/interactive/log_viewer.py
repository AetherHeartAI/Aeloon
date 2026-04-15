"""Gateway log viewer for the interactive CLI."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.widgets import Box, Frame, TextArea

from aeloon.cli.interactive.log_tail import AsyncLogTailer

WAITING_FOR_GATEWAY_MESSAGE = "Waiting for gateway..."
LOG_VIEWER_RESULT_CLOSE = "close"
LOG_VIEWER_RESULT_EXIT_PROCESS = "exit_process"


@dataclass
class _GatewayLogViewerState:
    """Mutable UI state for the gateway log viewer."""

    app: Application[None] | None
    body: TextArea
    max_lines: int = 500
    lines: deque[str] = field(default_factory=deque)

    def append_line(self, line: str) -> None:
        """Append one rendered line while preserving scroll intent."""
        current_text = self.body.text
        current_cursor = self.body.buffer.cursor_position
        should_stick_to_bottom = (
            not current_text
            or current_text == WAITING_FOR_GATEWAY_MESSAGE
            or self.body.buffer.document.is_cursor_at_the_end
        )

        self.lines.append(line)
        while len(self.lines) > self.max_lines:
            self.lines.popleft()

        self.body.text = "\n".join(self.lines)
        if should_stick_to_bottom:
            self.body.buffer.cursor_position = len(self.body.text)
        else:
            self.body.buffer.cursor_position = min(current_cursor, len(self.body.text))
        if self.app is not None:
            self.app.invalidate()


def _build_gateway_log_viewer(
    log_path: Path,
    *,
    input=None,
    output=None,
    poll_interval: float = 0.25,
    max_lines: int = 500,
) -> tuple[Application[None], _GatewayLogViewerState, AsyncLogTailer]:
    initial_text = "" if log_path.exists() else WAITING_FOR_GATEWAY_MESSAGE
    body = TextArea(
        text=initial_text,
        read_only=True,
        scrollbar=True,
        focusable=True,
    )
    state = _GatewayLogViewerState(app=None, body=body, max_lines=max_lines)
    if initial_text:
        body.buffer.cursor_position = len(initial_text)

    kb = KeyBindings()

    @kb.add("c-l")
    def _close(event) -> None:
        event.app.exit(result=LOG_VIEWER_RESULT_CLOSE)

    @kb.add("c-c")
    def _exit_process(event) -> None:
        event.app.exit(result=LOG_VIEWER_RESULT_EXIT_PROCESS)

    footer = Window(
        content=FormattedTextControl(
            lambda: FormattedText([("", "Ctrl+L - exit viewer | Ctrl+C - exit | Up/Down scroll")])
        ),
        height=1,
    )
    app = Application(
        layout=Layout(
            Box(
                HSplit(
                    [
                        Frame(body=body, title="Gateway Logs"),
                        footer,
                    ]
                ),
                padding=1,
            )
        ),
        key_bindings=kb,
        full_screen=False,
        input=input,
        output=output,
    )
    state.app = app
    tailer = AsyncLogTailer(log_path, poll_interval=poll_interval)
    return app, state, tailer


async def run_gateway_log_viewer(
    log_path: Path,
    *,
    input=None,
    output=None,
    poll_interval: float = 0.25,
    max_lines: int = 500,
    on_ready: Callable[[_GatewayLogViewerState], None] | None = None,
) -> str:
    """Run the gateway log viewer until the user exits back to chat."""
    app, state, tailer = _build_gateway_log_viewer(
        log_path,
        input=input,
        output=output,
        poll_interval=poll_interval,
        max_lines=max_lines,
    )
    tail_task = asyncio.create_task(tailer.start(state.append_line))
    try:
        if on_ready is not None:
            on_ready(state)
        result = await app.run_async()
    finally:
        tailer.stop()
        await asyncio.gather(tail_task, return_exceptions=True)
    return result or LOG_VIEWER_RESULT_CLOSE
