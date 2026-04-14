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
        self.load_from_disk()

    def load_from_disk(self) -> None:
        """Refresh live entries and capture a new prompt snapshot."""
        self.memory_entries = self._dedupe(self._read_file(self.memory_path))
        self.user_entries = self._dedupe(self._read_file(self.user_path))
        self._system_prompt_snapshot = {
            "memory": self._render_block("memory", self.memory_entries),
            "user": self._render_block("user", self.user_entries),
        }

    def refresh_snapshot(self) -> None:
        self.load_from_disk()

    def snapshot_payload(self) -> dict[str, str]:
        return {
            "memory": self._system_prompt_snapshot.get("memory", ""),
            "user": self._system_prompt_snapshot.get("user", ""),
        }

    def load_snapshot_payload(self, payload: dict[str, str]) -> None:
        self._system_prompt_snapshot = {
            "memory": str(payload.get("memory", "")),
            "user": str(payload.get("user", "")),
        }

    def format_for_system_prompt(self, target: MemoryTarget) -> str | None:
        block = self._system_prompt_snapshot.get(target, "")
        return block or None

    def system_prompt_sections(self) -> list[str]:
        """Render frozen prompt sections for the current turn."""
        sections: list[str] = []
        memory_block = self.format_for_system_prompt("memory")
        if memory_block:
            sections.append("# Memory\n\n" + memory_block)
        user_block = self.format_for_system_prompt("user")
        if user_block:
            sections.append("# User Memory\n\n" + user_block)
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
                current = self._char_count(target)
                usage = f"{current:,}/{limit:,}"
                return {
                    "success": False,
                    "error": (
                        f"Memory at {usage} chars. "
                        f"Adding this entry ({len(normalized)} chars) would exceed the limit."
                    ),
                    "current_entries": list(entries),
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
            matches = [(index, entry) for index, entry in enumerate(entries) if match_text in entry]
            if not matches:
                return {"success": False, "error": f"No entry matched '{match_text}'."}
            if len(matches) > 1:
                unique_texts = {entry for _, entry in matches}
                if len(unique_texts) > 1:
                    previews = [
                        entry[:80] + ("..." if len(entry) > 80 else "")
                        for _, entry in matches
                    ]
                    return {
                        "success": False,
                        "error": f"Multiple entries matched '{match_text}'. Be more specific.",
                        "matches": previews,
                    }

            updated = list(entries)
            updated[matches[0][0]] = replacement
            limit = self._char_limit(target)
            new_total = len(ENTRY_DELIMITER.join(updated))
            if new_total > limit:
                return {
                    "success": False,
                    "error": (
                        f"Replacement would put memory at {new_total:,}/{limit:,} chars. "
                        "Shorten the new content or remove other entries first."
                    ),
                }

            self._set_entries(target, updated)
            self._write_file(self._path_for(target), updated)

        return self._success_response(target, "Entry replaced.")

    def remove(self, target: MemoryTarget, old_text: str) -> dict[str, object]:
        """Remove a single entry matched by substring."""
        match_text = old_text.strip()
        if not match_text:
            return {"success": False, "error": "old_text cannot be empty."}

        with self._file_lock(self._path_for(target)):
            self._reload_target(target)
            entries = self._entries_for(target)
            matches = [(index, entry) for index, entry in enumerate(entries) if match_text in entry]
            if not matches:
                return {"success": False, "error": f"No entry matched '{match_text}'."}
            if len(matches) > 1:
                unique_texts = {entry for _, entry in matches}
                if len(unique_texts) > 1:
                    previews = [
                        entry[:80] + ("..." if len(entry) > 80 else "")
                        for _, entry in matches
                    ]
                    return {
                        "success": False,
                        "error": f"Multiple entries matched '{match_text}'. Be more specific.",
                        "matches": previews,
                    }

            updated = [entry for index, entry in enumerate(entries) if index != matches[0][0]]
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
        current = self._char_count(target)
        limit = self._char_limit(target)
        pct = min(100, int((current / limit) * 100)) if limit > 0 else 0
        return {
            "success": True,
            "target": target,
            "message": message,
            "entries": entries,
            "usage": f"{pct}% — {current:,}/{limit:,} chars",
            "entry_count": len(entries),
        }

    @staticmethod
    def _dedupe(entries: list[str]) -> list[str]:
        return list(dict.fromkeys(entries))

    def _render_block(self, target: MemoryTarget, entries: list[str]) -> str:
        if not entries:
            return ""
        limit = self._char_limit(target)
        content = ENTRY_DELIMITER.join(entries)
        current = len(content)
        pct = min(100, int((current / limit) * 100)) if limit > 0 else 0
        if target == "user":
            header = f"USER PROFILE (who the user is) [{pct}% — {current:,}/{limit:,} chars]"
        else:
            header = f"MEMORY (your personal notes) [{pct}% — {current:,}/{limit:,} chars]"
        separator = "═" * 46
        return f"{separator}\n{header}\n{separator}\n{content}"

    def over_limit_status(self) -> dict[str, tuple[int, int]]:
        status: dict[str, tuple[int, int]] = {}
        for target in ("memory", "user"):
            current = self._char_count(target)
            limit = self._char_limit(target)
            if current > limit:
                status[target] = (current, limit)
        return status

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
