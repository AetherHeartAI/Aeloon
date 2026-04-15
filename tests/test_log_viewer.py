from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.widgets import TextArea

from aeloon.cli.interactive.log_viewer import (
    LOG_VIEWER_RESULT_CLOSE,
    LOG_VIEWER_RESULT_EXIT_PROCESS,
    WAITING_FOR_GATEWAY_MESSAGE,
    _GatewayLogViewerState,
    run_gateway_log_viewer,
)


async def _wait_for(predicate, *, timeout: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Timed out waiting for predicate")


def test_gateway_log_viewer_caps_rendered_lines() -> None:
    app = MagicMock()
    body = TextArea(text="", read_only=True)
    state = _GatewayLogViewerState(app=app, body=body, max_lines=3)

    for index in range(5):
        state.append_line(f"line {index}")

    assert body.text.splitlines() == ["line 2", "line 3", "line 4"]
    assert app.invalidate.call_count == 5


@pytest.mark.asyncio
async def test_gateway_log_viewer_returns_close_on_ctrl_l(tmp_path) -> None:
    log_path = tmp_path / "gateway.log"
    log_path.write_text("", encoding="utf-8")
    ready = asyncio.Event()

    with create_pipe_input() as pipe_input:
        state_holder = {}

        task = asyncio.create_task(
            run_gateway_log_viewer(
                log_path,
                input=pipe_input,
                output=DummyOutput(),
                poll_interval=0.05,
                on_ready=lambda state: (state_holder.setdefault("state", state), ready.set()),
            )
        )
        await ready.wait()
        pipe_input.send_text("\x0c")
        result = await asyncio.wait_for(task, timeout=1.0)

    assert state_holder["state"].body.text == ""
    assert result == LOG_VIEWER_RESULT_CLOSE


@pytest.mark.asyncio
async def test_gateway_log_viewer_returns_exit_process_on_ctrl_c(tmp_path) -> None:
    log_path = tmp_path / "gateway.log"
    log_path.write_text("", encoding="utf-8")

    with create_pipe_input() as pipe_input:
        task = asyncio.create_task(
            run_gateway_log_viewer(
                log_path,
                input=pipe_input,
                output=DummyOutput(),
                poll_interval=0.05,
            )
        )
        await asyncio.sleep(0.1)
        pipe_input.send_text("\x03")
        result = await asyncio.wait_for(task, timeout=1.0)

    assert result == LOG_VIEWER_RESULT_EXIT_PROCESS


@pytest.mark.asyncio
async def test_gateway_log_viewer_streams_new_lines(tmp_path) -> None:
    log_path = tmp_path / "gateway.log"
    log_path.write_text("", encoding="utf-8")
    ready = asyncio.Event()
    state_holder = {}

    with create_pipe_input() as pipe_input:
        task = asyncio.create_task(
            run_gateway_log_viewer(
                log_path,
                input=pipe_input,
                output=DummyOutput(),
                poll_interval=0.05,
                on_ready=lambda state: (state_holder.setdefault("state", state), ready.set()),
            )
        )
        try:
            await ready.wait()
            await asyncio.sleep(0.1)
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write("gateway connected\n")
                handle.flush()

            await _wait_for(
                lambda: "gateway connected" in state_holder["state"].body.text,
            )
            pipe_input.send_text("\x0c")
            await asyncio.wait_for(task, timeout=1.0)
        finally:
            if not task.done():
                pipe_input.send_text("\x0c")
                await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_gateway_log_viewer_shows_waiting_placeholder_for_missing_file(tmp_path) -> None:
    log_path = tmp_path / "gateway.log"
    ready = asyncio.Event()
    state_holder = {}

    with create_pipe_input() as pipe_input:
        task = asyncio.create_task(
            run_gateway_log_viewer(
                log_path,
                input=pipe_input,
                output=DummyOutput(),
                poll_interval=0.05,
                on_ready=lambda state: (state_holder.setdefault("state", state), ready.set()),
            )
        )
        try:
            await ready.wait()
            assert state_holder["state"].body.text == WAITING_FOR_GATEWAY_MESSAGE
            pipe_input.send_text("\x0c")
            await asyncio.wait_for(task, timeout=1.0)
        finally:
            if not task.done():
                pipe_input.send_text("\x0c")
                await asyncio.wait_for(task, timeout=1.0)
