"""Compatibility layer for the file-backed memory implementation."""

from __future__ import annotations

from aeloon.memory.backends.file import (
    FileMemoryBackend,
    FileMemoryConfig,
    MemoryStore,
)
from aeloon.memory.backends.file import (
    MemoryConsolidator as _MemoryConsolidator,
)
from aeloon.utils.helpers import estimate_message_tokens


class MemoryConsolidator(_MemoryConsolidator):
    """Temporary shim so existing callers/tests keep the old import path."""

    def pick_consolidation_boundary(self, session, tokens_to_remove: int):
        start = self._last_consolidated(session)
        if start >= len(session.messages) or tokens_to_remove <= 0:
            return None

        removed_tokens = 0
        last_boundary = None
        for idx in range(start, len(session.messages)):
            message = session.messages[idx]
            if idx > start and message.get("role") == "user":
                last_boundary = (idx, removed_tokens)
                if removed_tokens >= tokens_to_remove:
                    return last_boundary
            removed_tokens += estimate_message_tokens(message)

        return last_boundary


__all__ = ["FileMemoryBackend", "FileMemoryConfig", "MemoryConsolidator", "MemoryStore"]
