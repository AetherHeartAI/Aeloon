"""Async log tail utilities for interactive CLI views."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Callable


class AsyncLogTailer:
    """Tail a log file asynchronously and stream completed lines to a callback."""

    def __init__(self, path: Path, *, poll_interval: float = 0.25) -> None:
        self.path = path
        self.poll_interval = max(0.05, poll_interval)
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        """Request that the tail loop exit on the next poll."""
        self._stop_event.set()

    async def start(self, on_line: Callable[[str], None]) -> None:
        """Stream appended log lines until :meth:`stop` is called."""
        remainder = ""

        while not self._stop_event.is_set():
            if not self.path.exists():
                await asyncio.sleep(self.poll_interval)
                continue

            with self.path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(0, 2)
                while not self._stop_event.is_set():
                    chunk = await asyncio.to_thread(handle.read)
                    if not chunk:
                        await asyncio.sleep(self.poll_interval)
                        continue

                    remainder += chunk
                    parts = remainder.split("\n")
                    remainder = parts.pop()
                    for line in parts:
                        rendered = line.rstrip("\r")
                        if rendered:
                            on_line(rendered)
            await asyncio.sleep(self.poll_interval)
