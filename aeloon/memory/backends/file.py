"""Markdown file-backed memory backend."""

from __future__ import annotations

import asyncio
import json
import weakref
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger
from pydantic import Field

from aeloon.core.session.manager import Session
from aeloon.memory.base import (
    MemoryBackend,
    MemoryBackendConfig,
    MemoryBackendDeps,
    PreparedMemoryContext,
)
from aeloon.memory.registry import register_backend
from aeloon.memory.types import MessagePayload
from aeloon.utils.helpers import ensure_dir, estimate_message_tokens, estimate_prompt_tokens_chain

if TYPE_CHECKING:
    from aeloon.providers.base import LLMProvider

_SAVE_MEMORY_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "Save the memory consolidation result to persistent storage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "history_entry": {
                        "type": "string",
                        "description": "A paragraph summarizing key events/decisions/topics. "
                        "Start with [YYYY-MM-DD HH:MM]. Include detail useful for grep search.",
                    },
                    "memory_update": {
                        "type": "string",
                        "description": "Full updated long-term memory as markdown. Include all existing "
                        "facts plus new ones. Return unchanged if nothing new.",
                    },
                },
                "required": ["history_entry", "memory_update"],
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
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _normalize_save_memory_args(args: object) -> dict[str, object] | None:
    if isinstance(args, str):
        args = json.loads(args)
    if isinstance(args, list):
        return args[0] if args and isinstance(args[0], dict) else None
    return args if isinstance(args, dict) else None


def _is_tool_choice_unsupported(content: str | None) -> bool:
    text = (content or "").lower()
    return any(marker in text for marker in _TOOL_CHOICE_ERROR_MARKERS)


class MemoryStore:
    """Two-layer memory: MEMORY.md plus append-only HISTORY.md."""

    _MAX_FAILURES_BEFORE_RAW_ARCHIVE = 3

    def __init__(
        self,
        workspace: Path,
        config: "FileMemoryConfig | None" = None,
    ):
        cfg = config or FileMemoryConfig()
        self.memory_dir = ensure_dir(workspace / cfg.memory_dir)
        self.memory_file = self.memory_dir / cfg.long_term_filename
        self.history_file = self.memory_dir / cfg.history_filename
        self._MAX_FAILURES_BEFORE_RAW_ARCHIVE = cfg.max_failures_before_raw_archive
        self._consecutive_failures = 0

    def read_long_term(self) -> str:
        if self.memory_file.exists():
            return self.memory_file.read_text(encoding="utf-8")
        return ""

    def write_long_term(self, content: str) -> None:
        self.memory_file.write_text(content, encoding="utf-8")

    def append_history(self, entry: str) -> None:
        with open(self.history_file, "a", encoding="utf-8") as file:
            file.write(entry.rstrip() + "\n\n")

    def get_memory_context(self) -> str:
        long_term = self.read_long_term()
        return f"## Long-term Memory\n{long_term}" if long_term else ""

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
            lines.append(
                f"[{str(timestamp or '?')[:16]}] {role}{tools}: {message['content']}"
            )
        return "\n".join(lines)

    async def consolidate(
        self,
        messages: list[MessagePayload],
        provider: "LLMProvider",
        model: str,
    ) -> bool:
        if not messages:
            return True

        current_memory = self.read_long_term()
        prompt = f"""Process this conversation and call the save_memory tool with your consolidation.

## Current Long-term Memory
{current_memory or "(empty)"}

## Conversation to Process
{self._format_messages(messages)}"""

        chat_messages = [
            {
                "role": "system",
                "content": "You are a memory consolidation agent. Call the save_memory tool with your consolidation of the conversation.",
            },
            {"role": "user", "content": prompt},
        ]

        try:
            forced = {"type": "function", "function": {"name": "save_memory"}}
            response = await provider.chat_with_retry(
                messages=chat_messages,
                tools=_SAVE_MEMORY_TOOL,
                model=model,
                tool_choice=forced,
            )

            if response.finish_reason == "error" and _is_tool_choice_unsupported(response.content):
                logger.warning("Forced tool_choice unsupported, retrying with auto")
                response = await provider.chat_with_retry(
                    messages=chat_messages,
                    tools=_SAVE_MEMORY_TOOL,
                    model=model,
                    tool_choice="auto",
                )

            if not response.has_tool_calls:
                logger.warning(
                    "Memory consolidation: LLM did not call save_memory "
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

            if "history_entry" not in args or "memory_update" not in args:
                logger.warning("Memory consolidation: save_memory payload missing required fields")
                return self._fail_or_raw_archive(messages)

            entry = args["history_entry"]
            update = args["memory_update"]
            if entry is None or update is None:
                logger.warning(
                    "Memory consolidation: save_memory payload contains null required fields"
                )
                return self._fail_or_raw_archive(messages)

            entry_text = _ensure_text(entry).strip()
            if not entry_text:
                logger.warning("Memory consolidation: history_entry is empty after normalization")
                return self._fail_or_raw_archive(messages)

            self.append_history(entry_text)
            update_text = _ensure_text(update)
            if update_text != current_memory:
                self.write_long_term(update_text)

            self._consecutive_failures = 0
            logger.info("Memory consolidation done for {} messages", len(messages))
            return True
        except Exception:
            logger.exception("Memory consolidation failed")
            return self._fail_or_raw_archive(messages)

    def _fail_or_raw_archive(self, messages: list[MessagePayload]) -> bool:
        self._consecutive_failures += 1
        if self._consecutive_failures < self._MAX_FAILURES_BEFORE_RAW_ARCHIVE:
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


class FileMemoryConfig(MemoryBackendConfig):
    """Configuration for the markdown file backend."""

    memory_dir: str = Field(default="memory", alias="memoryDir")
    long_term_filename: str = Field(default="MEMORY.md", alias="longTermFilename")
    history_filename: str = Field(default="HISTORY.md", alias="historyFilename")
    max_failures_before_raw_archive: int = Field(default=3, alias="maxFailuresBeforeRawArchive")
    trigger_ratio: float = Field(default=1.0, alias="triggerRatio")
    target_ratio: float = Field(default=0.5, alias="targetRatio")
    max_consolidation_rounds: int = Field(default=5, alias="maxConsolidationRounds")


@register_backend
class FileMemoryBackend(MemoryBackend):
    """Backend that preserves the existing MEMORY.md + HISTORY.md behavior."""

    backend_name = "file"
    config_model = FileMemoryConfig

    def __init__(self, config: FileMemoryConfig, deps: MemoryBackendDeps):
        super().__init__(config, deps)
        self.store = MemoryStore(deps.workspace, config)
        self.provider = deps.provider
        self.model = deps.model
        self.sessions = deps.sessions
        self.context_window_tokens = deps.context_window_tokens
        self._build_messages = deps.build_messages
        self._get_tool_definitions = deps.get_tool_definitions
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()

    @property
    def long_term_path(self) -> Path:
        return self.store.memory_file

    @property
    def history_path(self) -> Path:
        return self.store.history_file

    def _last_consolidated(self, session: object) -> int:
        memory_state = getattr(session, "memory_state", None)
        if isinstance(memory_state, dict):
            raw_file_state = memory_state.setdefault("file", {})
            if isinstance(raw_file_state, dict):
                raw_value = raw_file_state.get("last_consolidated", 0)
                if isinstance(raw_value, int):
                    return raw_value

        raw_value = getattr(session, "last_consolidated", 0)
        return int(raw_value) if isinstance(raw_value, int) else 0

    def _set_last_consolidated(self, session: object, value: int) -> None:
        memory_state = getattr(session, "memory_state", None)
        if isinstance(memory_state, dict):
            raw_file_state = memory_state.setdefault("file", {})
            if isinstance(raw_file_state, dict):
                raw_file_state["last_consolidated"] = value
        if hasattr(session, "last_consolidated"):
            setattr(session, "last_consolidated", value)

    def get_lock(self, session_key: str) -> asyncio.Lock:
        return self._locks.setdefault(session_key, asyncio.Lock())

    async def prepare_turn(
        self,
        *,
        session: object,
        query: str,
        channel: str | None,
        chat_id: str | None,
        current_role: str,
    ) -> PreparedMemoryContext:
        if isinstance(session, Session):
            await self.maybe_consolidate_by_tokens(session)

        memory_context = self.store.get_memory_context()
        system_sections = [f"# Memory\n\n{memory_context}"] if memory_context else []
        return PreparedMemoryContext(
            history_start_index=self._last_consolidated(session),
            system_sections=system_sections,
            runtime_lines=[
                f"Long-term memory: {self.long_term_path}",
                f"History log: {self.history_path}",
            ],
            always_skill_names=["memory"],
        )

    async def after_turn(
        self,
        *,
        session: object,
        raw_new_messages: list[MessagePayload],
        persisted_new_messages: list[MessagePayload],
        final_content: str | None,
    ) -> None:
        if isinstance(session, Session):
            await self.maybe_consolidate_by_tokens(session)

    def pending_start_index(self, session: object) -> int:
        return self._last_consolidated(session)

    async def on_new_session(
        self,
        *,
        session: object,
        pending_messages: list[MessagePayload],
    ) -> None:
        await self.archive_messages(pending_messages)

    async def consolidate_messages(self, messages: list[MessagePayload]) -> bool:
        return await self.store.consolidate(messages, self.provider, self.model)

    def pick_consolidation_boundary(
        self,
        session: "Session",
        tokens_to_remove: int,
    ) -> tuple[int, int] | None:
        start = self._last_consolidated(session)
        if start >= len(session.messages) or tokens_to_remove <= 0:
            return None

        removed_tokens = 0
        last_boundary: tuple[int, int] | None = None
        for idx in range(start, len(session.messages)):
            message = session.messages[idx]
            if idx > start and message.get("role") == "user":
                last_boundary = (idx, removed_tokens)
                if removed_tokens >= tokens_to_remove:
                    return last_boundary
            removed_tokens += estimate_message_tokens(message)

        return last_boundary

    def estimate_session_prompt_tokens(self, session: "Session") -> tuple[int, str]:
        history = session.get_history(max_messages=0)
        channel, chat_id = session.key.split(":", 1) if ":" in session.key else (None, None)
        probe_messages = self._build_messages(
            history=history,
            current_message="[token-probe]",
            channel=channel,
            chat_id=chat_id,
        )
        return estimate_prompt_tokens_chain(
            self.provider,
            self.model,
            probe_messages,
            self._get_tool_definitions(),
        )

    async def archive_messages(self, messages: list[MessagePayload]) -> bool:
        if not messages:
            return True
        for _ in range(self.store._MAX_FAILURES_BEFORE_RAW_ARCHIVE):
            if await self.consolidate_messages(messages):
                return True
        return True

    async def maybe_consolidate_by_tokens(self, session: "Session") -> None:
        if not session.messages or self.context_window_tokens <= 0:
            return

        lock = self.get_lock(session.key)
        async with lock:
            trigger = max(1, int(self.context_window_tokens * self.config.trigger_ratio))
            target = max(1, int(self.context_window_tokens * self.config.target_ratio))
            estimated, source = self.estimate_session_prompt_tokens(session)
            if estimated <= 0:
                return
            if estimated < trigger:
                logger.debug(
                    "Token consolidation idle {}: {}/{} via {}",
                    session.key,
                    estimated,
                    self.context_window_tokens,
                    source,
                )
                return

            for round_num in range(self.config.max_consolidation_rounds):
                if estimated <= target:
                    return

                boundary = self.pick_consolidation_boundary(session, max(1, estimated - target))
                if boundary is None:
                    logger.debug(
                        "Token consolidation: no safe boundary for {} (round {})",
                        session.key,
                        round_num,
                    )
                    return

                end_idx = boundary[0]
                chunk = session.messages[self._last_consolidated(session) : end_idx]
                if not chunk:
                    return

                logger.info(
                    "Token consolidation round {} for {}: {}/{} via {}, chunk={} msgs",
                    round_num,
                    session.key,
                    estimated,
                    self.context_window_tokens,
                    source,
                    len(chunk),
                )
                if not await self.consolidate_messages(chunk):
                    return
                self._set_last_consolidated(session, end_idx)
                self.sessions.save(session)

                estimated, source = self.estimate_session_prompt_tokens(session)
                if estimated <= 0:
                    return


class MemoryConsolidator(FileMemoryBackend):
    """Compatibility adapter for the pre-refactor runtime constructor shape."""

    def __init__(
        self,
        workspace: Path,
        provider: "LLMProvider",
        model: str,
        sessions,
        context_window_tokens: int,
        build_messages,
        get_tool_definitions,
    ):
        super().__init__(
            FileMemoryConfig(),
            MemoryBackendDeps(
                workspace=workspace,
                provider=provider,
                model=model,
                sessions=sessions,
                context_window_tokens=context_window_tokens,
                build_messages=build_messages,
                get_tool_definitions=get_tool_definitions,
            ),
        )
