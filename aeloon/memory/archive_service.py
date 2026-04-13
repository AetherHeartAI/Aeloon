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
    message_count: int
    preview: str
    title: str | None
    lineage_id: str


@dataclass(slots=True)
class SessionSearchHit:
    session_id: str
    session_key: str
    source: str
    started_at: float
    updated_at: float
    message_count: int
    preview: str
    title: str | None
    snippet: str
    conversation: list[dict[str, object]]

    @property
    def conversation_text(self) -> str:
        parts: list[str] = []
        for message in self.conversation:
            parts.append(str(message.get("content") or ""))
        return "\n\n".join(parts)


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
        current_session_key: str | None = None,
    ) -> list[RecentArchivedSession]:
        exclude_lineage_id = (
            self._lineage_id_for_key(current_session_key) if current_session_key else None
        )
        rows = self.db.list_recent_sessions(limit=limit, exclude_lineage_id=exclude_lineage_id)
        return [
            RecentArchivedSession(
                session_id=str(row["id"]),
                session_key=str(row["session_key"]),
                source=str(row["source"]),
                started_at=self._as_float(row["started_at"]),
                updated_at=self._as_float(row["updated_at"]),
                message_count=self._as_int(row["message_count"]),
                preview=str(row["preview"] or ""),
                title=str(row["title"]) if row.get("title") else None,
                lineage_id=str(row["lineage_id"]),
            )
            for row in rows
        ]

    def search(
        self,
        *,
        query: str,
        limit: int,
        role_filter: list[str] | None = None,
        current_session_key: str | None = None,
    ) -> list[SessionSearchHit]:
        current_lineage_id = (
            self._lineage_id_for_key(current_session_key) if current_session_key else None
        )
        raw_results = self.db.search_messages(query=query, role_filter=role_filter, limit=50)
        seen_session_ids: set[str] = set()
        hits: list[SessionSearchHit] = []
        for row in raw_results:
            session_id = str(row["session_id"])
            if session_id in seen_session_ids:
                continue
            if current_lineage_id and str(row["lineage_id"]) == current_lineage_id:
                continue
            seen_session_ids.add(session_id)
            conversation = self.db.get_messages_as_conversation(session_id)
            hits.append(
                SessionSearchHit(
                    session_id=session_id,
                    session_key=str(row["session_key"]),
                    source=str(row["source"]),
                    started_at=self._as_float(row["started_at"]),
                    updated_at=self._as_float(row["updated_at"]),
                    message_count=self._as_int(row["message_count"]),
                    preview=str(row.get("preview") or ""),
                    title=str(row["title"]) if row.get("title") else None,
                    snippet=str(row["snippet"] or ""),
                    conversation=conversation,
                )
            )
            if len(hits) >= limit:
                break
        return hits

    async def close(self) -> None:
        self.db.close()

    def _build_session_record(self, session: Session) -> ArchivedSessionRecord:
        source, chat_id = self._split_session_key(session.key)
        metadata = dict(session.metadata)
        lineage_key = str(metadata.get("lineage_id") or session.key)
        parent_key = metadata.get("parent_session_key")
        title = metadata.get("title")
        metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
        return ArchivedSessionRecord(
            id=self._session_id_for_key(session.key),
            session_key=session.key,
            workspace=self._workspace_id,
            source=source,
            chat_id=chat_id,
            lineage_id=self._lineage_id_for_key(lineage_key),
            parent_session_id=self._session_id_for_key(str(parent_key)) if parent_key else None,
            title=str(title) if isinstance(title, str) else None,
            started_at=session.created_at.timestamp(),
            updated_at=session.updated_at.timestamp(),
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

    def _session_id_for_key(self, session_key: str) -> str:
        return f"{self._workspace_id}::{session_key}"

    def _lineage_id_for_key(self, session_key: str | None) -> str:
        return self._session_id_for_key(session_key or "")

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
