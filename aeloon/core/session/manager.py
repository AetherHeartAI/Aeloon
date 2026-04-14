"""Session management for conversation history."""

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from aeloon.core.config.paths import get_legacy_sessions_dir
from aeloon.utils.helpers import ensure_dir, safe_filename


@dataclass
class Session:
    """
    A conversation session.

    Stores messages in JSONL format for easy reading and persistence.

    Important: Messages are append-only for LLM cache efficiency.
    Memory backends may archive or summarize old turns without modifying
    the messages list or get_history() output.
    """

    key: str  # channel:chat_id
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    memory_state: dict[str, Any] = field(default_factory=dict)

    def _local_memory_state(self, *, create: bool = True) -> dict[str, Any]:
        raw_local_state = self.memory_state.get("local")
        if isinstance(raw_local_state, dict):
            local_state = raw_local_state
        else:
            local_state = {}
            if create:
                self.memory_state["local"] = local_state

        legacy_file_state = self.memory_state.get("file")
        if "last_compacted" not in local_state and isinstance(legacy_file_state, dict):
            raw_value = legacy_file_state.get("last_consolidated", 0)
            if isinstance(raw_value, int):
                local_state = dict(local_state)
                local_state["last_compacted"] = raw_value
                if create:
                    self.memory_state["local"] = local_state
        return local_state

    @property
    def last_compacted(self) -> int:
        raw_value = self._local_memory_state(create=False).get("last_compacted", 0)
        return raw_value if isinstance(raw_value, int) else 0

    @last_compacted.setter
    def last_compacted(self, value: int) -> None:
        self._local_memory_state()["last_compacted"] = value

    @property
    def last_consolidated(self) -> int:
        return self.last_compacted

    @last_consolidated.setter
    def last_consolidated(self, value: int) -> None:
        self.last_compacted = value

    def normalize_memory_state(self) -> None:
        self._local_memory_state()
        legacy_file_state = self.memory_state.get("file")
        if isinstance(legacy_file_state, dict) and "last_consolidated" in legacy_file_state:
            trimmed = dict(legacy_file_state)
            trimmed.pop("last_consolidated", None)
            if trimmed:
                self.memory_state["file"] = trimmed
            else:
                self.memory_state.pop("file", None)
        local_state = self.memory_state.get("local")
        if isinstance(local_state, dict) and not local_state:
            self.memory_state.pop("local", None)

    def get_prompt_memory_snapshot(self) -> dict[str, str] | None:
        raw = self.memory_state.get("prompt_memory")
        if not isinstance(raw, dict):
            return None
        memory = raw.get("memory")
        user = raw.get("user")
        if isinstance(memory, str) and isinstance(user, str):
            return {"memory": memory, "user": user}
        return None

    def set_prompt_memory_snapshot(self, snapshot: dict[str, str]) -> None:
        self.memory_state["prompt_memory"] = {
            "memory": snapshot.get("memory", ""),
            "user": snapshot.get("user", ""),
        }

    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        """Add a message to the session."""
        msg = {"role": role, "content": content, "timestamp": datetime.now().isoformat(), **kwargs}
        self.messages.append(msg)
        self.updated_at = datetime.now()

    @staticmethod
    def _find_legal_start(messages: list[dict[str, Any]]) -> int:
        """Find first index where every tool result has a matching assistant tool_call."""
        declared: set[str] = set()
        start = 0
        for i, msg in enumerate(messages):
            role = msg.get("role")
            if role == "assistant":
                for tc in msg.get("tool_calls") or []:
                    if isinstance(tc, dict) and tc.get("id"):
                        declared.add(str(tc["id"]))
            elif role == "tool":
                tid = msg.get("tool_call_id")
                if tid and str(tid) not in declared:
                    start = i + 1
                    declared.clear()
                    for prev in messages[start : i + 1]:
                        if prev.get("role") == "assistant":
                            for tc in prev.get("tool_calls") or []:
                                if isinstance(tc, dict) and tc.get("id"):
                                    declared.add(str(tc["id"]))
        return start

    def get_history(
        self,
        *,
        start_index: int | None = None,
        max_messages: int = 500,
    ) -> list[dict[str, Any]]:
        """Return unconsolidated messages for LLM input, aligned to a legal tool-call boundary."""
        history_start = self.last_compacted if start_index is None else start_index
        unconsolidated = self.messages[history_start:]
        sliced = unconsolidated[-max_messages:] if max_messages else list(unconsolidated)

        # Drop leading non-user messages to avoid starting mid-turn when possible.
        for i, message in enumerate(sliced):
            if message.get("role") == "user":
                sliced = sliced[i:]
                break

        # Some providers reject orphan tool results if the matching assistant
        # tool_calls message fell outside the fixed-size history window.
        start = self._find_legal_start(sliced)
        if start:
            sliced = sliced[start:]

        out: list[dict[str, Any]] = []
        for message in sliced:
            entry: dict[str, Any] = {"role": message["role"], "content": message.get("content", "")}
            for key in ("tool_calls", "tool_call_id", "name"):
                if key in message:
                    entry[key] = message[key]
            out.append(entry)
        return out

    def clear(self) -> None:
        """Clear all messages and reset session to initial state."""
        self.messages = []
        self.memory_state = {}
        self.updated_at = datetime.now()


class SessionManager:
    """
    Manages conversation sessions.

    Sessions are stored as JSONL files in the sessions directory.
    """

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.sessions_dir = ensure_dir(self.workspace / "sessions")
        self.legacy_sessions_dir = get_legacy_sessions_dir()
        self._cache: dict[str, Session] = {}

    def _get_session_path(self, key: str) -> Path:
        """Get the file path for a session."""
        safe_key = safe_filename(key.replace(":", "_"))
        return self.sessions_dir / f"{safe_key}.jsonl"

    def _get_legacy_session_path(self, key: str) -> Path:
        """Legacy global session path (~/.aeloon/sessions/)."""
        safe_key = safe_filename(key.replace(":", "_"))
        return self.legacy_sessions_dir / f"{safe_key}.jsonl"

    def get_or_create(self, key: str) -> Session:
        """
        Get an existing session or create a new one.

        Args:
            key: Session key (usually channel:chat_id).

        Returns:
            The session.
        """
        if key in self._cache:
            return self._cache[key]

        session = self._load(key)
        if session is None:
            session = Session(key=key)

        self._cache[key] = session
        return session

    def _load(self, key: str) -> Session | None:
        """Load a session from disk."""
        path = self._get_session_path(key)
        if not path.exists():
            legacy_path = self._get_legacy_session_path(key)
            if legacy_path.exists():
                try:
                    shutil.move(str(legacy_path), str(path))
                    logger.info("Migrated session {} from legacy path", key)
                except Exception:
                    logger.exception("Failed to migrate session {}", key)

        if not path.exists():
            return None

        try:
            messages = []
            metadata = {}
            created_at = None
            memory_state: dict[str, Any] = {}

            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    data = json.loads(line)

                    if data.get("_type") == "metadata":
                        metadata = data.get("metadata", {})
                        created_at = (
                            datetime.fromisoformat(data["created_at"])
                            if data.get("created_at")
                            else None
                        )
                        raw_memory_state = data.get("memory_state", {})
                        if isinstance(raw_memory_state, dict):
                            memory_state = raw_memory_state
                        else:
                            memory_state = {}

                        if "memory_state" not in data:
                            memory_state = {
                                "local": {"last_compacted": data.get("last_consolidated", 0)}
                            }
                    else:
                        messages.append(data)

            return Session(
                key=key,
                messages=messages,
                created_at=created_at or datetime.now(),
                metadata=metadata,
                memory_state=memory_state,
            )
        except Exception as e:
            logger.warning("Failed to load session {}: {}", key, e)
            return None

    def save(self, session: Session) -> None:
        """Save a session to disk."""
        path = self._get_session_path(session.key)
        session.normalize_memory_state()

        with open(path, "w", encoding="utf-8") as f:
            metadata_line = {
                "_type": "metadata",
                "key": session.key,
                "created_at": session.created_at.isoformat(),
                "updated_at": session.updated_at.isoformat(),
                "metadata": session.metadata,
                "memory_state": session.memory_state,
            }
            f.write(json.dumps(metadata_line, ensure_ascii=False) + "\n")
            for msg in session.messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")

        self._cache[session.key] = session

    def save_turn(
        self,
        session: Session,
        messages: list[dict[str, Any]],
        skip: int,
        max_chars: int = 16_000,
        runtime_context_tag: str = "",
    ) -> None:
        """Persist one new turn into session with cleanup transforms."""
        now = datetime.now
        for message in messages[skip:]:
            entry = dict(message)
            role, content = entry.get("role"), entry.get("content")

            if role == "assistant" and not content and not entry.get("tool_calls"):
                continue

            if role == "tool" and isinstance(content, str) and len(content) > max_chars:
                entry["content"] = content[:max_chars] + "\n... (truncated)"
            elif role == "user":
                if (
                    runtime_context_tag
                    and isinstance(content, str)
                    and content.startswith(runtime_context_tag)
                ):
                    parts = content.split("\n\n", 1)
                    if len(parts) > 1 and parts[1].strip():
                        entry["content"] = parts[1]
                    else:
                        continue

                if isinstance(content, list):
                    filtered = []
                    for item in content:
                        if (
                            runtime_context_tag
                            and item.get("type") == "text"
                            and isinstance(item.get("text"), str)
                            and item["text"].startswith(runtime_context_tag)
                        ):
                            continue
                        if item.get("type") == "image_url" and item.get("image_url", {}).get(
                            "url", ""
                        ).startswith("data:image/"):
                            path = (item.get("_meta") or {}).get("path", "")
                            placeholder = f"[image: {path}]" if path else "[image]"
                            filtered.append({"type": "text", "text": placeholder})
                        else:
                            filtered.append(item)
                    if not filtered:
                        continue
                    entry["content"] = filtered

            entry.setdefault("timestamp", now().isoformat())
            session.messages.append(entry)

        session.updated_at = now()

    def invalidate(self, key: str) -> None:
        """Remove a session from the in-memory cache."""
        self._cache.pop(key, None)

    def archive_metadata(self, session: Session) -> dict[str, object]:
        """Return archive-friendly metadata for a session snapshot."""
        source, chat_id = session.key.split(":", 1) if ":" in session.key else (session.key, None)
        metadata = dict(session.metadata)
        metadata.setdefault("source", source)
        metadata.setdefault("chat_id", chat_id)
        metadata.setdefault("lineage_id", session.key)
        return metadata

    def list_sessions(self) -> list[dict[str, Any]]:
        """
        List all sessions.

        Returns:
            List of session info dicts.
        """
        sessions = []

        for path in self.sessions_dir.glob("*.jsonl"):
            try:
                # Read just the metadata line
                with open(path, encoding="utf-8") as f:
                    first_line = f.readline().strip()
                    if first_line:
                        data = json.loads(first_line)
                        if data.get("_type") == "metadata":
                            key = data.get("key") or path.stem.replace("_", ":", 1)
                            sessions.append(
                                {
                                    "key": key,
                                    "created_at": data.get("created_at"),
                                    "updated_at": data.get("updated_at"),
                                    "path": str(path),
                                }
                            )
            except Exception:
                continue

        return sorted(sessions, key=lambda x: x.get("updated_at", ""), reverse=True)
