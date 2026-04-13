"""Session and banner helpers for the interactive CLI."""

from __future__ import annotations

import getpass
import re
import shutil
import socket
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from prompt_toolkit.formatted_text import FormattedText
from rich.console import Group
from rich.padding import Padding
from rich.panel import Panel
from rich.text import Text

_WELCOME_BANNER_PATH = Path(__file__).resolve().parents[2] / "resources" / "long8_render.ansi"
_ANSI_TRUECOLOR_RE = re.compile(r"\x1b\[38;2;(\d{1,3});(\d{1,3});(\d{1,3})m")
_FALLBACK_LOGO_LINES = [
    " █████╗ ███████╗██╗      ██████╗  ██████╗ ███╗   ██╗",
    "██╔══██╗██╔════╝██║     ██╔═══██╗██╔═══██╗████╗  ██║",
    "███████║█████╗  ██║     ██║   ██║██║   ██║██╔██╗ ██║",
    "██╔══██║██╔══╝  ██║     ██║   ██║██║   ██║██║╚██╗██║",
    "██║  ██║███████╗███████╗╚██████╔╝╚██████╔╝██║ ╚████║",
    "╚═╝  ╚═╝╚══════╝╚══════╝ ╚═════╝  ╚═════╝ ╚═╝  ╚═══╝",
]
_FALLBACK_PET_LINES = [
    "       ╭────╮  ╭────╮      ",
    "       │    │  │    │      ",
    "   ╭───╯    ╰──╯    ╰───╮  ",
    "   │   ▌▌          ▌▌   │  ",
    "   │        ▄▄▄▄        │  ",
    "   │      ╭──────╮      │  ",
    "   ╰──╮   │▐▐▐▐▐▐│   ╭──╯  ",
    "      ╰───╯      ╰───╯     ",
]


def _load_ansi_banner_lines(max_width: int) -> tuple[list[Text], int] | None:
    """Load the ANSI startup banner if it fits in the current terminal width."""
    if not _WELCOME_BANNER_PATH.exists():
        return None

    try:
        raw_lines = _WELCOME_BANNER_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    banner_lines = [Text.from_ansi(line) for line in raw_lines if line]
    if not banner_lines:
        return None

    banner_width = max(line.cell_len for line in banner_lines)
    if banner_width > max_width:
        return None

    return banner_lines, banner_width


def _load_ansi_banner_theme_style() -> str | None:
    """Extract a theme color from ANSI foreground codes for the ASCII logo."""
    if not _WELCOME_BANNER_PATH.exists():
        return None

    try:
        raw_text = _WELCOME_BANNER_PATH.read_text(encoding="utf-8")
    except OSError:
        return None

    weighted_colors: list[tuple[float, tuple[int, int, int]]] = []
    for match in _ANSI_TRUECOLOR_RE.finditer(raw_text):
        r, g, b = (int(group) for group in match.groups())
        brightness = 0.299 * r + 0.587 * g + 0.114 * b
        spread = max(r, g, b) - min(r, g, b)
        if brightness < 55 or brightness > 245 or spread < 30:
            continue
        score = spread + brightness / 8
        weighted_colors.append((score, (r, g, b)))

    if not weighted_colors:
        return None

    top_colors = sorted(weighted_colors, key=lambda item: item[0], reverse=True)[:24]
    total_weight = sum(weight for weight, _color in top_colors)
    if total_weight <= 0:
        return None

    red = round(sum(weight * color[0] for weight, color in top_colors) / total_weight)
    green = round(sum(weight * color[1] for weight, color in top_colors) / total_weight)
    blue = round(sum(weight * color[2] for weight, color in top_colors) / total_weight)
    return f"rgb({red},{green},{blue})"


def session_display_messages(session) -> list[tuple[str, str]]:
    """Extract visible user/assistant messages from a stored session."""
    visible: list[tuple[str, str]] = []
    for message in session.messages:
        role = message.get("role")
        if role not in {"user", "assistant"}:
            continue
        content = message.get("content", "")
        if not isinstance(content, str) or not content.strip():
            continue
        visible.append((role, content))
    return visible


def print_replayed_history(
    messages: list[tuple[str, str]], render_markdown: bool, *, console, print_agent_response
) -> None:
    """Replay stored session messages into the interactive terminal."""
    if not messages:
        return
    for role, content in messages:
        if role == "user":
            console.print(f"[bold blue]You:[/bold blue] {content}")
            console.print()
        else:
            print_agent_response(content, render_markdown=render_markdown)


def make_new_cli_session_id() -> str:
    """Create a fresh CLI session key."""
    return f"cli:{datetime.now().strftime('%Y%m%d%H%M%S')}"


def resolve_agent_session_id(session_id: str | None, resume: bool, session_manager: Any) -> str:
    """Resolve the effective session key for `aeloon agent`."""
    if session_id:
        return session_id if ":" in session_id else f"cli:{session_id}"

    if resume:
        for item in session_manager.list_sessions():
            key = str(item.get("key") or "")
            if key.startswith("cli:"):
                return key
        return "cli:direct"

    return make_new_cli_session_id()


def resolve_initial_cli_state(session_id: str) -> tuple[dict[str, str], bool]:
    """Resolve interactive CLI state and whether it should start fresh."""
    if ":" in session_id:
        channel, chat_id = session_id.split(":", 1)
    else:
        channel, chat_id = "cli", session_id

    start_fresh = channel == "cli" and chat_id == "direct"
    if start_fresh:
        fresh_session_id = make_new_cli_session_id()
        channel, chat_id = fresh_session_id.split(":", 1)

    return {"channel": channel, "chat_id": chat_id}, start_fresh


def build_bottom_toolbar(
    agent_loop,
    cli_channel: str,
    cli_chat_id: str,
) -> Callable[[], FormattedText]:
    """Build a prompt_toolkit bottom toolbar showing model/context usage."""

    from aeloon.core.agent.channel_auth import GatewayManager

    def _toolbar() -> FormattedText:
        session_key = f"{cli_channel}:{cli_chat_id}"
        session = agent_loop.sessions.get_or_create(session_key)
        estimated, _source = agent_loop.memory.estimate_session_prompt_tokens(session)
        context_window = max(0, int(agent_loop.context_window_tokens))
        ratio = (estimated / context_window * 100) if context_window > 0 else 0.0
        model_value = str(agent_loop.model)
        gateway_hint = " | Gateway: running | Ctrl+L: logs" if GatewayManager.is_running() else ""
        context_text = f"Context: {estimated}/{context_window} ({ratio:.0f}%){gateway_hint}"
        width = shutil.get_terminal_size((80, 20)).columns
        min_spacing = 3
        reserved = len("Model: ") + len(context_text) + min_spacing
        available_model = max(8, width - reserved)
        if len(model_value) > available_model:
            model_value = f"{model_value[: max(1, available_model - 1)]}…"
        spacing = max(3, width - len("Model: ") - len(model_value) - len(context_text))
        context_style = "bold ansired" if ratio >= 90 else "ansiyellow" if ratio >= 75 else ""
        return FormattedText(
            [
                ("bold", "Model:"),
                ("", f" {model_value}{' ' * spacing}"),
                ("bold", "Context:"),
                (context_style, f" {estimated}/{context_window} ({ratio:.0f}%)"),
                ("dim", gateway_hint),
            ]
        )

    return _toolbar


def compose_welcome_banner(workspace_name: str, model_name: str) -> Panel:
    """Build the startup banner shown before interactive chat begins."""
    cyan = "bright_cyan"
    purple = "bright_magenta"
    green = "bright_green"
    dim = "grey70"
    terminal_width = shutil.get_terminal_size((120, 20)).columns
    panel_content_width = max(0, terminal_width - 6)
    ansi_logo = _load_ansi_banner_lines(max(0, panel_content_width - 2))
    logo_style = _load_ansi_banner_theme_style() or cyan

    def _centered_text(content: str, style: str = "") -> Text:
        text = Text(justify="center", no_wrap=True)
        text.append(content, style=style)
        return text

    def _padded_rich_text(content: Text, left_padding: int) -> Text:
        text = Text(no_wrap=True)
        if left_padding > 0:
            text.append(" " * left_padding)
        text.append_text(content)
        return text

    command_line = Text()
    command_line.append("○ ", style=dim)
    command_line.append(
        f"{getpass.getuser()}@{socket.gethostname().split('.')[0]}", style="bold white"
    )
    command_line.append(f"  {workspace_name}  $ ", style=dim)
    command_line.append("aeloon", style=logo_style)

    body = [command_line, Text("")]
    body.append(_centered_text("Welcome to Aeloon CLI Version", dim))
    body.append(Text(""))
    for logo_line in _FALLBACK_LOGO_LINES:
        body.append(_centered_text(logo_line, logo_style))
    body.append(Text(""))
    if ansi_logo is not None:
        logo_lines, logo_width = ansi_logo
        logo_padding = max(0, (panel_content_width - logo_width) // 2)
        for rendered_line in logo_lines:
            body.append(_padded_rich_text(rendered_line, logo_padding))
    else:
        for pet_line in _FALLBACK_PET_LINES:
            line = Text(justify="center", no_wrap=True)
            if "▌▌" in pet_line:
                left, _, right = pet_line.partition("▌▌")
                line.append(left, style=purple)
                line.append("▌▌", style=green)
                mid_left, _, tail = right.partition("▌▌")
                line.append(mid_left, style=purple)
                line.append("▌▌", style=green)
                line.append(tail, style=purple)
            elif "▐▐▐▐▐▐" in pet_line:
                head, _, tail = pet_line.partition("▐▐▐▐▐▐")
                line.append(head, style=purple)
                line.append("▐▐▐▐▐▐", style=green)
                line.append(tail, style=purple)
            else:
                line.append(pet_line, style=purple)
            body.append(line)
    body.append(Text(""))
    subtitle = Text(justify="center")
    subtitle.append("Aeloon Agent", style=f"bold {purple}")
    subtitle.append("  ·  ", style=dim)
    subtitle.append("Neon terminal mode", style=green)
    body.append(subtitle)
    hint = Text(justify="center")
    hint.append("New session ready. Type ", style=dim)
    hint.append("/resume", style=cyan)
    hint.append(" to reopen history.", style=dim)
    body.append(hint)
    footer = Text()
    footer.append(f"~/{workspace_name}", style="bold white")
    footer.append(" " * max(2, 72 - len(workspace_name) - len(model_name)))
    footer.append(model_name, style=dim)
    body.extend([Text(""), footer])
    return Panel(Padding(Group(*body), (1, 2)), border_style=purple, padding=0)
