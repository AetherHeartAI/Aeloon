"""Backward-compatible memory imports for the layered memory runtime."""

from __future__ import annotations

from pathlib import Path

from aeloon.memory.local_store import LocalMemoryStore


class MemoryStore(LocalMemoryStore):
    """Compatibility wrapper for legacy imports from aeloon.core.agent.memory."""

    def __init__(self, workspace: Path):
        super().__init__(
            directory=workspace / "memory",
            history_file_name="HISTORY.md",
            max_failures_before_raw_archive=3,
        )
