"""Local archive memory persistence."""

from __future__ import annotations

from pathlib import Path

from aeloon.memory.types import MessagePayload
from aeloon.providers.base import LLMProvider
from aeloon.utils.helpers import ensure_dir


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
        del entry
        return None

    async def consolidate(
        self,
        messages: list[MessagePayload],
        provider: LLMProvider,
        model: str,
        output_summary: str = "",
    ) -> bool:
        del provider, model, output_summary
        if not messages:
            return True
        self._consecutive_failures = 0
        return True

    def _fail_or_raw_archive(self, messages: list[MessagePayload]) -> bool:
        del messages
        self._consecutive_failures = 0
        return True

    def _raw_archive(self, messages: list[MessagePayload]) -> None:
        del messages
        return None
