"""Compatibility facade for the layered memory runtime."""

from __future__ import annotations

import asyncio

from loguru import logger

from aeloon.memory.runtime import MemoryRuntime


class MemoryManager(MemoryRuntime):
    """Backwards-compatible alias over MemoryRuntime."""

    def _remove_task(self, task: asyncio.Task[None]) -> None:
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            exc = None
        if exc is not None:
            logger.opt(exception=exc).error("Memory backend background task failed")
        if task in self._background_tasks:
            self._background_tasks.remove(task)
