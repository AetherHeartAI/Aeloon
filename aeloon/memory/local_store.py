"""Local archive memory persistence."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from loguru import logger

from aeloon.memory.types import MessagePayload
from aeloon.providers.base import LLMProvider
from aeloon.utils.helpers import ensure_dir

_SAVE_HISTORY_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "save_history",
            "description": "Save the archive consolidation result to persistent storage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "history_entry": {
                        "type": "string",
                        "description": "A paragraph summarizing key events or decisions.",
                    },
                },
                "required": ["history_entry"],
            },
        },
    }
]

_TOOL_CHOICE_ERROR_MARKERS = (
    "tool_choice",
    "toolchoice",
    "does not support",
    'should be ["none", "auto"]',
)


def _ensure_text(value: object) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)


def _normalize_save_memory_args(args: object) -> dict[str, object] | None:
    if isinstance(args, str):
        args = json.loads(args)
    if isinstance(args, list):
        if args and isinstance(args[0], dict):
            return args[0]
        return None
    if isinstance(args, dict):
        return args
    return None


def _is_tool_choice_unsupported(content: str | None) -> bool:
    text = (content or "").lower()
    return any(marker in text for marker in _TOOL_CHOICE_ERROR_MARKERS)


class LocalMemoryStore:
    def __init__(
        self,
        *,
        directory: Path,
        history_file_name: str,
        max_failures_before_raw_archive: int,
    ) -> None:
        self.directory = ensure_dir(directory)
        self.history_file = self.directory / history_file_name
        self._max_failures_before_raw_archive = max_failures_before_raw_archive
        self._consecutive_failures = 0

    def append_history(self, entry: str) -> None:
        with open(self.history_file, "a", encoding="utf-8") as file:
            file.write(entry.rstrip() + "\n\n")

    @staticmethod
    def _format_messages(messages: list[MessagePayload]) -> str:
        lines: list[str] = []
        for message in messages:
            if not message.get("content"):
                continue
            tools_used = message.get("tools_used")
            tools = f" [tools: {', '.join(tools_used)}]" if isinstance(tools_used, list) else ""
            timestamp = message.get("timestamp")
            role = str(message.get("role", "?")).upper()
            lines.append(f"[{str(timestamp or '?')[:16]}] {role}{tools}: {message['content']}")
        return "\n".join(lines)

    async def consolidate(
        self,
        messages: list[MessagePayload],
        provider: LLMProvider,
        model: str,
    ) -> bool:
        if not messages:
            return True

        prompt = f"""Summarize this conversation slice for archival history.

Do NOT rewrite prompt memory files (MEMORY.md / USER.md).
Prompt memory is maintained separately by the memory tool and flush pipeline.

## Conversation to Process
{self._format_messages(messages)}"""

        chat_messages = [
            {
                "role": "system",
                "content": (
                    "You are an archive consolidation agent. "
                    "Call the save_history tool with a grep-friendly archival summary."
                ),
            },
            {"role": "user", "content": prompt},
        ]

        try:
            forced = {"type": "function", "function": {"name": "save_history"}}
            response = await provider.chat_with_retry(
                messages=chat_messages,
                tools=_SAVE_HISTORY_TOOL,
                model=model,
                tool_choice=forced,
            )

            if response.finish_reason == "error" and _is_tool_choice_unsupported(response.content):
                logger.warning("Forced tool_choice unsupported, retrying with auto")
                response = await provider.chat_with_retry(
                    messages=chat_messages,
                    tools=_SAVE_HISTORY_TOOL,
                    model=model,
                    tool_choice="auto",
                )

            if not response.has_tool_calls:
                logger.warning(
                    "Memory consolidation: LLM did not call save_history "
                    "(finish_reason={}, content_len={}, content_preview={})",
                    response.finish_reason,
                    len(response.content or ""),
                    (response.content or "")[:200],
                )
                return self._fail_or_raw_archive(messages)

            args = _normalize_save_memory_args(response.tool_calls[0].arguments)
            if args is None:
                logger.warning("Memory consolidation: unexpected save_memory arguments")
                return self._fail_or_raw_archive(messages)

            if "history_entry" not in args:
                logger.warning("Memory consolidation: save_history payload missing required fields")
                return self._fail_or_raw_archive(messages)

            entry = args["history_entry"]
            if entry is None:
                logger.warning(
                    "Memory consolidation: save_memory payload contains null required fields"
                )
                return self._fail_or_raw_archive(messages)

            entry_text = _ensure_text(entry).strip()
            if not entry_text:
                logger.warning("Memory consolidation: history_entry is empty after normalization")
                return self._fail_or_raw_archive(messages)

            self.append_history(entry_text)
            self._consecutive_failures = 0
            logger.info("Memory consolidation done for {} messages", len(messages))
            return True
        except Exception:
            logger.exception("Memory consolidation failed")
            return self._fail_or_raw_archive(messages)

    def _fail_or_raw_archive(self, messages: list[MessagePayload]) -> bool:
        self._consecutive_failures += 1
        if self._consecutive_failures < self._max_failures_before_raw_archive:
            return False
        self._raw_archive(messages)
        self._consecutive_failures = 0
        return True

    def _raw_archive(self, messages: list[MessagePayload]) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.append_history(
            f"[{timestamp}] [RAW] {len(messages)} messages\n{self._format_messages(messages)}"
        )
        logger.warning("Memory consolidation degraded: raw-archived {} messages", len(messages))
