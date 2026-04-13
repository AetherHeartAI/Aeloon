"""Always-on prompt memory store."""

from __future__ import annotations

import fcntl
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from aeloon.core.config.schema import PromptMemoryConfig
from aeloon.memory.security import scan_memory_content
from aeloon.utils.helpers import ensure_dir

MemoryTarget = Literal["memory", "user"]
ENTRY_DELIMITER = "\n§\n"


class PromptMemoryStore:
    """Bounded prompt-memory store for MEMORY.md and USER.md."""

    def __init__(self, workspace: Path, config: PromptMemoryConfig):
        self.directory = ensure_dir(workspace / config.directory)
        self.memory_path = self.directory / config.memory_file
        self.user_path = self.directory / config.user_file
        self.memory_char_limit = config.memory_char_limit
        self.user_char_limit = config.user_char_limit
        self.memory_entries: list[str] = []
        self.user_entries: list[str] = []
        self._system_prompt_snapshot = {"memory": "", "user": ""}
        self.refresh_snapshot()

    def refresh_snapshot(self) -> None:
        """Refresh live entries and capture a new prompt snapshot."""
        self.memory_entries = self._dedupe(self._read_file(self.memory_path))
        self.user_entries = self._dedupe(self._read_file(self.user_path))
        self._system_prompt_snapshot = {
            "memory": self._render_block("memory", self.memory_entries),
            "user": self._render_block("user", self.user_entries),
        }

    def system_prompt_sections(self) -> list[str]:
        """Render frozen prompt sections for the current turn."""
        sections: list[str] = []
        if self._system_prompt_snapshot["memory"]:
            sections.append("# Memory\n\n" + self._system_prompt_snapshot["memory"])
        if self._system_prompt_snapshot["user"]:
            sections.append("# User Memory\n\n" + self._system_prompt_snapshot["user"])
        return sections

    def add(self, target: MemoryTarget, content: str) -> dict[str, object]:
        """Add a new durable prompt-memory entry."""
        normalized = content.strip()
        if not normalized:
            return {"success": False, "error": "Content cannot be empty."}

        scan_error = scan_memory_content(normalized)
        if scan_error:
            return {"success": False, "error": scan_error}

        with self._file_lock(self._path_for(target)):
            self._reload_target(target)
            entries = self._entries_for(target)
            if normalized in entries:
                return self._success_response(target, "Entry already exists (no duplicate added).")

            new_total = len(ENTRY_DELIMITER.join([*entries, normalized]))
            limit = self._char_limit(target)
            if new_total > limit:
                usage = f"{self._char_count(target):,}/{limit:,}"
                return {
                    "success": False,
                    "error": (
                        f"Memory at {usage} chars. "
                        f"Adding this entry ({len(normalized)} chars) would exceed the limit."
                    ),
                    "usage": usage,
                }

            entries.append(normalized)
            self._set_entries(target, entries)
            self._write_file(self._path_for(target), entries)

        return self._success_response(target, "Entry added.")

    def replace(self, target: MemoryTarget, old_text: str, new_content: str) -> dict[str, object]:
        """Replace a single entry matched by substring."""
        match_text = old_text.strip()
        replacement = new_content.strip()
        if not match_text:
            return {"success": False, "error": "old_text cannot be empty."}
        if not replacement:
            return {"success": False, "error": "new_content cannot be empty."}

        scan_error = scan_memory_content(replacement)
        if scan_error:
            return {"success": False, "error": scan_error}

        with self._file_lock(self._path_for(target)):
            self._reload_target(target)
            entries = self._entries_for(target)
            matches = [index for index, entry in enumerate(entries) if match_text in entry]
            if not matches:
                return {"success": False, "error": "No matching entry found."}
            if len(matches) > 1:
                return {"success": False, "error": "old_text matched multiple entries."}

            updated = list(entries)
            updated[matches[0]] = replacement
            if len(ENTRY_DELIMITER.join(updated)) > self._char_limit(target):
                return {
                    "success": False,
                    "error": "Replacement would exceed the configured character limit.",
                }

            self._set_entries(target, updated)
            self._write_file(self._path_for(target), updated)

        return self._success_response(target, "Entry replaced.")

    def remove(self, target: MemoryTarget, content: str) -> dict[str, object]:
        """Remove a single entry matched by substring."""
        match_text = content.strip()
        if not match_text:
            return {"success": False, "error": "content cannot be empty."}

        with self._file_lock(self._path_for(target)):
            self._reload_target(target)
            entries = self._entries_for(target)
            matches = [index for index, entry in enumerate(entries) if match_text in entry]
            if not matches:
                return {"success": False, "error": "No matching entry found."}
            if len(matches) > 1:
                return {"success": False, "error": "content matched multiple entries."}

            updated = [entry for index, entry in enumerate(entries) if index != matches[0]]
            self._set_entries(target, updated)
            self._write_file(self._path_for(target), updated)

        return self._success_response(target, "Entry removed.")

    @staticmethod
    @contextmanager
    def _file_lock(path: Path):
        lock_path = path.with_suffix(path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock_path, "w", encoding="utf-8")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
            handle.close()

    def _reload_target(self, target: MemoryTarget) -> None:
        self._set_entries(target, self._dedupe(self._read_file(self._path_for(target))))

    def _entries_for(self, target: MemoryTarget) -> list[str]:
        return self.user_entries if target == "user" else self.memory_entries

    def _set_entries(self, target: MemoryTarget, entries: list[str]) -> None:
        if target == "user":
            self.user_entries = entries
        else:
            self.memory_entries = entries

    def _char_count(self, target: MemoryTarget) -> int:
        entries = self._entries_for(target)
        return len(ENTRY_DELIMITER.join(entries)) if entries else 0

    def _char_limit(self, target: MemoryTarget) -> int:
        return self.user_char_limit if target == "user" else self.memory_char_limit

    def _path_for(self, target: MemoryTarget) -> Path:
        return self.user_path if target == "user" else self.memory_path

    def _success_response(self, target: MemoryTarget, message: str) -> dict[str, object]:
        entries = list(self._entries_for(target))
        return {
            "success": True,
            "target": target,
            "message": message,
            "entries": entries,
            "usage": f"{self._char_count(target):,}/{self._char_limit(target):,}",
        }

    @staticmethod
    def _dedupe(entries: list[str]) -> list[str]:
        return list(dict.fromkeys(entries))

    @staticmethod
    def _render_block(target: MemoryTarget, entries: list[str]) -> str:
        if not entries:
            return ""
        title = "Stable facts" if target == "memory" else "Stable user preferences"
        return title + ":\n\n" + "\n\n".join(entries)

    @staticmethod
    def _read_file(path: Path) -> list[str]:
        if not path.exists():
            return []
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            return []
        if ENTRY_DELIMITER not in content:
            return [content]
        return [entry.strip() for entry in content.split(ENTRY_DELIMITER) if entry.strip()]

    @staticmethod
    def _write_file(path: Path, entries: list[str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_path_str = tempfile.mkstemp(prefix=path.stem.lower() + ".", dir=path.parent)
        temp_path = Path(temp_path_str)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                payload = ENTRY_DELIMITER.join(entries)
                if payload:
                    handle.write(payload + "\n")
            os.replace(temp_path, path)
        finally:
            if temp_path.exists():
                temp_path.unlink()
