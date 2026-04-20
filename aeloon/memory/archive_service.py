"""Archive service built on top of the SQLite sidecar."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from aeloon.core.config.paths import get_archive_db_path
from aeloon.core.session.manager import Session
from aeloon.memory.archive_db import (
    ArchivedMessageRecord,
    ArchivedSessionRecord,
    SessionArchiveDB,
)


@dataclass(slots=True)
class RecentArchivedSession:
    session_id: str
    session_key: str
    source: str
    started_at: float
    updated_at: float
    ended_at: float | None
    end_reason: str | None
    message_count: int
    preview: str
    title: str | None
    lineage_id: str
    parent_session_id: str | None


@dataclass(slots=True)
class SessionSearchHit:
    session_id: str
    session_key: str
    source: str
    started_at: float
    updated_at: float
    ended_at: float | None
    end_reason: str | None
    message_count: int
    preview: str
    title: str | None
    lineage_id: str
    parent_session_id: str | None
    snippet: str
    conversation: list[dict[str, object]]

    @property
    def conversation_text(self) -> str:
        parts: list[str] = []
        for message in self.conversation:
            parts.append(str(message.get("content") or ""))
        return "\n\n".join(parts)


@dataclass(slots=True)
class ArchivedSessionSnapshot:
    session_id: str
    session_key: str
    source: str
    started_at: float
    updated_at: float
    ended_at: float | None
    end_reason: str | None
    message_count: int
    title: str | None
    lineage_id: str
    parent_session_id: str | None
    metadata: dict[str, object]
    conversation: list[dict[str, object]]


class SessionArchiveService:
    """High-level archive ingestion and recall orchestration."""

    def __init__(
        self,
        *,
        workspace: Path,
        db: SessionArchiveDB | None = None,
        db_path: Path | None = None,
    ):
        self.workspace = workspace.resolve()
        self._workspace_id = str(self.workspace)
        self.db = db or SessionArchiveDB(db_path or get_archive_db_path())

    async def ingest_session(self, session: Session) -> None:
        self.ingest_session_sync(session)

    def ingest_session_sync(self, session: Session) -> None:
        record = self._build_session_record(session)
        messages = self._build_message_records(session)
        self.db.replace_session(record, messages)

    def list_recent_sessions(
        self,
        *,
        limit: int,
        current_session_id: str | None = None,
        current_lineage_id: str | None = None,
    ) -> list[RecentArchivedSession]:
        rows = self.db.list_recent_sessions(limit=limit + 5)
        sessions: list[RecentArchivedSession] = []
        for row in rows:
            session_id = str(row["id"])
            lineage_id = str(row["lineage_id"])
            if current_session_id and session_id == current_session_id:
                continue
            if current_lineage_id and lineage_id == current_lineage_id:
                continue
            if row.get("parent_session_id"):
                continue
            sessions.append(
                RecentArchivedSession(
                    session_id=session_id,
                    session_key=str(row["session_key"]),
                    source=str(row["source"]),
                    started_at=self._as_float(row["started_at"]),
                    updated_at=self._as_float(row["updated_at"]),
                    ended_at=self._as_optional_float(row.get("ended_at")),
                    end_reason=str(row["end_reason"]) if row.get("end_reason") else None,
                    message_count=self._as_int(row["message_count"]),
                    preview=str(row["preview"] or ""),
                    title=str(row["title"]) if row.get("title") else None,
                    lineage_id=lineage_id,
                    parent_session_id=(
                        str(row["parent_session_id"]) if row.get("parent_session_id") else None
                    ),
                )
            )
            if len(sessions) >= limit:
                break
        return sessions

    def search(
        self,
        *,
        query: str,
        limit: int,
        role_filter: list[str] | None = None,
        current_session_id: str | None = None,
        current_lineage_id: str | None = None,
    ) -> list[SessionSearchHit]:
        raw_results = self.db.search_messages(query=query, role_filter=role_filter, limit=50)
        seen_lineage_ids: set[str] = set()
        hits: list[SessionSearchHit] = []
        for row in raw_results:
            session_id = str(row["session_id"])
            lineage_id = str(row["lineage_id"])
            if current_session_id and session_id == current_session_id:
                continue
            if current_lineage_id and lineage_id == current_lineage_id:
                continue
            if lineage_id in seen_lineage_ids:
                continue
            seen_lineage_ids.add(lineage_id)
            conversation = self.db.get_messages_as_conversation(session_id)
            hits.append(
                SessionSearchHit(
                    session_id=session_id,
                    session_key=str(row["session_key"]),
                    source=str(row["source"]),
                    started_at=self._as_float(row["started_at"]),
                    updated_at=self._as_float(row["updated_at"]),
                    ended_at=self._as_optional_float(row.get("ended_at")),
                    end_reason=str(row["end_reason"]) if row.get("end_reason") else None,
                    message_count=self._as_int(row["message_count"]),
                    preview=str(row.get("preview") or ""),
                    title=str(row["title"]) if row.get("title") else None,
                    lineage_id=lineage_id,
                    parent_session_id=(
                        str(row["parent_session_id"]) if row.get("parent_session_id") else None
                    ),
                    snippet=str(row["snippet"] or ""),
                    conversation=conversation,
                )
            )
            if len(hits) >= limit:
                break
        return hits

    async def close(self) -> None:
        self.db.close()

    def load_session_snapshot(self, session_id: str) -> ArchivedSessionSnapshot | None:
        row = self.db.get_session(session_id)
        if row is None:
            return None
        metadata_raw = row.get("metadata_json")
        metadata: dict[str, object] = {}
        if isinstance(metadata_raw, str) and metadata_raw.strip():
            try:
                loaded = json.loads(metadata_raw)
                if isinstance(loaded, dict):
                    metadata = loaded
            except json.JSONDecodeError:
                metadata = {}
        return ArchivedSessionSnapshot(
            session_id=str(row["id"]),
            session_key=str(row["session_key"]),
            source=str(row["source"]),
            started_at=self._as_float(row["started_at"]),
            updated_at=self._as_float(row["updated_at"]),
            ended_at=self._as_optional_float(row.get("ended_at")),
            end_reason=str(row["end_reason"]) if row.get("end_reason") else None,
            message_count=self._as_int(row["message_count"]),
            title=str(row["title"]) if row.get("title") else None,
            lineage_id=str(row["lineage_id"]),
            parent_session_id=(
                str(row["parent_session_id"]) if row.get("parent_session_id") else None
            ),
            metadata=metadata,
            conversation=self.db.get_messages_as_conversation(session_id),
        )

    def _build_session_record(self, session: Session) -> ArchivedSessionRecord:
        source, chat_id = self._split_session_key(session.key)
        metadata = dict(session.metadata)
        title = metadata.get("title")
        metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
        return ArchivedSessionRecord(
            id=session.archive_session_id,
            session_key=session.key,
            workspace=self._workspace_id,
            source=source,
            chat_id=chat_id,
            lineage_id=session.lineage_id or session.archive_session_id,
            parent_session_id=session.parent_archive_session_id,
            title=str(title) if isinstance(title, str) else None,
            started_at=session.created_at.timestamp(),
            updated_at=session.updated_at.timestamp(),
            ended_at=session.ended_at.timestamp() if session.ended_at else None,
            end_reason=session.end_reason,
            message_count=len(session.messages),
            metadata_json=metadata_json,
        )

    def _build_message_records(self, session: Session) -> list[ArchivedMessageRecord]:
        records: list[ArchivedMessageRecord] = []
        for position, message in enumerate(session.messages):
            timestamp_raw = message.get("timestamp")
            timestamp = session.updated_at.timestamp()
            if isinstance(timestamp_raw, str):
                from datetime import datetime

                try:
                    timestamp = datetime.fromisoformat(timestamp_raw).timestamp()
                except ValueError:
                    timestamp = session.updated_at.timestamp()
            records.append(
                ArchivedMessageRecord(
                    position=position,
                    role=str(message.get("role") or "unknown"),
                    content=self._message_content_text(message.get("content")),
                    tool_name=str(message.get("name")) if message.get("name") else None,
                    tool_call_id=str(message.get("tool_call_id"))
                    if message.get("tool_call_id")
                    else None,
                    tool_calls_json=(
                        json.dumps(message.get("tool_calls"), ensure_ascii=False)
                        if message.get("tool_calls") is not None
                        else None
                    ),
                    timestamp=timestamp,
                )
            )
        return records

    @staticmethod
    def _split_session_key(session_key: str) -> tuple[str, str | None]:
        if ":" in session_key:
            source, chat_id = session_key.split(":", 1)
            return source, chat_id
        return session_key, None

    @staticmethod
    def _message_content_text(content: object) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text") or ""))
                else:
                    parts.append(json.dumps(cast(dict[str, object], item), ensure_ascii=False))
            return "\n".join(part for part in parts if part)
        if content is None:
            return ""
        return json.dumps(content, ensure_ascii=False)

    @staticmethod
    def _as_float(value: object) -> float:
        if isinstance(value, (int, float)):
            return float(value)
        return float(str(value))

    @staticmethod
    def _as_int(value: object) -> int:
        if isinstance(value, int):
            return value
        return int(str(value))

    @staticmethod
    def _as_optional_float(value: object) -> float | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip()
        return float(text) if text else None
