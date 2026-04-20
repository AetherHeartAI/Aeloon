"""Status line manager — aggregates status segments from plugin providers.

:class:`StatusLineManager` collects :class:`StatusSegment` entries from
all registered :class:`StatusProviderRecord` providers and assembles them
into a :class:`prompt_toolkit.formatted_text.FormattedText` suitable for
the CLI bottom toolbar.

When no providers are registered (i.e. before plugin boot), the manager
falls back to a built-in default showing Model + Context usage.

The module also provides :func:`flatten_toolbar` and
:func:`render_bottom_toolbar_loop` for rendering the toolbar via ANSI
escape codes when prompt_toolkit's session is not active (e.g. while the
agent is thinking).
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from typing import TYPE_CHECKING, Any

from loguru import logger

from aeloon.plugins._sdk.types import StatusContext, StatusSegment

if TYPE_CHECKING:
    from aeloon.plugins._sdk.registry import PluginRegistry


# ---------------------------------------------------------------------------
# Module-level rendering helpers
# ---------------------------------------------------------------------------


def flatten_toolbar(formatted_text: Any, *, exclude_thinking: bool = True) -> str:
    """Flatten a :class:`FormattedText` into a plain string.

    If *exclude_thinking* is True, tuples whose text contains
    ``"thinking"`` are dropped (the ``● thinking…`` indicator is only
    relevant inside prompt_toolkit).
    """
    if exclude_thinking:
        return "".join(p[1] for p in formatted_text if "thinking" not in p[1])
    return "".join(p[1] for p in formatted_text)


async def render_bottom_toolbar_loop(cached_text: str) -> None:
    """Periodically render a cached toolbar string at the terminal bottom.

    This keeps the status bar visible while the agent is working (when
    prompt_toolkit's PromptSession is not active).  On cancellation the
    toolbar line is cleared.
    """
    try:
        while True:
            rows, cols = shutil.get_terminal_size((80, 24))
            text = cached_text[:cols].ljust(cols)
            # ESC 7 = save cursor, ESC[rows;1H = go to last row,
            # ESC[7m = reverse video, ESC[0m = reset, ESC 8 = restore cursor
            sys.stdout.write(f"\0337\033[{rows};1H\033[7m{text}\033[0m\0338")
            sys.stdout.flush()
            await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        rows, cols = shutil.get_terminal_size((80, 24))
        sys.stdout.write(f"\0337\033[{rows};1H\033[K\0338")
        sys.stdout.flush()


# ---------------------------------------------------------------------------
# StatusLineManager
# ---------------------------------------------------------------------------


class StatusLineManager:
    """Aggregates status segments from all registered status providers."""

    def __init__(self, agent_loop: Any) -> None:
        self._agent_loop = agent_loop
        self._registry: PluginRegistry | None = None
        self.thinking: bool = False

    def set_registry(self, registry: PluginRegistry) -> None:
        """Wire the registry after plugin boot."""
        self._registry = registry

    # ------------------------------------------------------------------
    # Toolbar builder (sync — called by prompt_toolkit on each render)
    # ------------------------------------------------------------------

    def build_toolbar(self, channel: str, chat_id: str) -> Any:
        """Return a :class:`FormattedText` for the bottom toolbar.

        This is a **synchronous** callable compatible with
        ``prompt_toolkit``'s ``bottom_toolbar`` parameter.

        When :attr:`thinking` is True a ``● thinking…`` indicator is
        appended so the user can see the agent is working.
        """
        from prompt_toolkit.formatted_text import FormattedText

        ctx = self._build_context(channel, chat_id)

        # Collect segments from all registered providers
        segments: list[StatusSegment] = []
        if self._registry is not None:
            for rec in self._registry.status_providers:
                try:
                    provider_result = rec.provider(ctx)
                    segments.extend(self._normalise_result(provider_result))
                except Exception:
                    logger.opt(exception=True).warning(
                        "Status provider '{}' from plugin '{}' failed",
                        rec.name,
                        rec.plugin_id,
                    )

        # If no providers contributed anything, fall back to the default
        toolbar: Any
        if not segments:
            toolbar = self._default_toolbar(ctx)
        else:
            # Sort by priority (highest first = leftmost) and assemble
            segments.sort(key=lambda s: s.priority, reverse=True)
            toolbar = self._segments_to_formatted(segments, ctx.terminal_width)

        # Append thinking indicator when the agent is working
        if self.thinking:
            thinking_part = [("bold ansiyellow", " ● thinking...")]
            toolbar = FormattedText(list(toolbar) + thinking_part)

        return toolbar

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_context(self, channel: str, chat_id: str) -> StatusContext:
        """Build a :class:`StatusContext` from the current agent state."""
        loop = self._agent_loop
        session_key = f"{channel}:{chat_id}"
        session = loop.sessions.get_or_create(session_key)
        estimated, _source = loop.memory.estimate_session_prompt_tokens(session)
        context_total = max(0, int(loop.context_window_tokens))
        width = shutil.get_terminal_size((80, 20)).columns
        return StatusContext(
            session_key=session_key,
            channel=channel,
            model=str(loop.model),
            context_tokens_used=estimated,
            context_tokens_total=context_total,
            terminal_width=width,
        )

    @staticmethod
    def _normalise_result(
        result: str | StatusSegment | list[StatusSegment],
    ) -> list[StatusSegment]:
        """Convert any valid provider return into a list of segments."""
        if isinstance(result, list):
            return result
        if isinstance(result, StatusSegment):
            return [result]
        # Bare string
        return [StatusSegment(text=result)]

    def _default_toolbar(self, ctx: StatusContext) -> Any:
        """Fallback toolbar reproducing the original Model + Context display."""
        from prompt_toolkit.formatted_text import FormattedText

        ratio = (
            ctx.context_tokens_used / ctx.context_tokens_total * 100
            if ctx.context_tokens_total > 0
            else 0.0
        )
        context_value = f"{ctx.context_tokens_used}/{ctx.context_tokens_total} ({ratio:.0f}%)"
        # Full context text (with label) used only for spacing calculation.
        context_text = f"Context: {context_value}"
        model_value = ctx.model
        width = ctx.terminal_width
        min_spacing = 3
        reserved = len("Model: ") + len(context_text) + min_spacing
        available_model = max(8, width - reserved)
        if len(model_value) > available_model:
            model_value = f"{model_value[: max(1, available_model - 1)]}\u2026"
        spacing = max(3, width - len("Model: ") - len(model_value) - len(context_text))
        if ratio >= 90:
            context_style = "bold ansired"
        elif ratio >= 75:
            context_style = "ansiyellow"
        else:
            context_style = ""

        return FormattedText(
            [
                ("bold", "Model:"),
                ("", f" {model_value}{' ' * spacing}"),
                ("bold", "Context:"),
                (context_style, f" {context_value}"),
            ]
        )

    @staticmethod
    def _segments_to_formatted(segments: list[StatusSegment], width: int) -> Any:
        """Assemble a list of :class:`StatusSegment` into :class:`FormattedText`.

        The ``Context:`` segment (if present) is always right-aligned to the
        terminal edge; all other segments stay left-aligned.  If no Context
        segment exists, the last segment is right-aligned instead.
        """
        from prompt_toolkit.formatted_text import FormattedText

        if not segments:
            return FormattedText([("", " " * width)])

        # Find the Context segment and pin it to the right side
        right_seg = None
        left_segs: list[StatusSegment] = []
        for seg in segments:
            if right_seg is None and "Context:" in seg.text:
                right_seg = seg
            else:
                left_segs.append(seg)

        # Fallback: right-align the last segment if no Context found
        if right_seg is None:
            right_seg = left_segs.pop()

        parts: list[tuple[str, str]] = []
        left_len = 0

        for seg in left_segs:
            if parts:
                parts.append(("", "  "))  # separator
                left_len += 2
            parts.append((seg.style, seg.text))
            left_len += len(seg.text)

        # Right-aligned segment
        right_len = len(right_seg.text)
        gap = max(3, width - left_len - right_len)
        parts.append(("", " " * gap))
        parts.append((right_seg.style, right_seg.text))

        return FormattedText(parts)
