"""SQLite/FTS archive sidecar for transcript recall."""

from __future__ import annotations

import json
import random
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    session_key TEXT NOT NULL,
    workspace TEXT NOT NULL,
    source TEXT NOT NULL,
    chat_id TEXT,
    lineage_id TEXT NOT NULL,
    parent_session_id TEXT,
    title TEXT,
    started_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    ended_at REAL,
    end_reason TEXT,
    message_count INTEGER DEFAULT 0,
    metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_updated_at ON sessions(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_lineage_id ON sessions(lineage_id);
CREATE INDEX IF NOT EXISTS idx_sessions_parent_session_id ON sessions(parent_session_id);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    tool_name TEXT,
    tool_call_id TEXT,
    tool_calls_json TEXT,
    timestamp REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_session_position ON messages(session_id, position);
"""

FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    content=messages,
    content_rowid=id
);

CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_delete AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_update AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
"""


@dataclass(slots=True)
class ArchivedMessageRecord:
    """One archived message row."""

    position: int
    role: str
    content: str
    tool_name: str | None
    tool_call_id: str | None
    tool_calls_json: str | None
    timestamp: float


@dataclass(slots=True)
class ArchivedSessionRecord:
    """One archived session row."""

    id: str
    session_key: str
    workspace: str
    source: str
    chat_id: str | None
    lineage_id: str
    parent_session_id: str | None
    title: str | None
    started_at: float
    updated_at: float
    ended_at: float | None
    end_reason: str | None
    message_count: int
    metadata_json: str | None


class SessionArchiveDB:
    """SQLite-backed archive index with FTS5 search."""

    _WRITE_MAX_RETRIES = 15
    _WRITE_RETRY_MIN_S = 0.02
    _WRITE_RETRY_MAX_S = 0.15
    _CHECKPOINT_EVERY_N_WRITES = 50

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._write_count = 0
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            timeout=1.0,
            isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        cursor = self._conn.cursor()
        cursor.executescript(SCHEMA_SQL)
        columns = {
            str(row["name"]) for row in cursor.execute("PRAGMA table_info(sessions)").fetchall()
        }
        if "ended_at" not in columns:
            cursor.execute("ALTER TABLE sessions ADD COLUMN ended_at REAL")
        if "end_reason" not in columns:
            cursor.execute("ALTER TABLE sessions ADD COLUMN end_reason TEXT")
        try:
            cursor.execute("SELECT * FROM messages_fts LIMIT 0")
        except sqlite3.OperationalError:
            cursor.executescript(FTS_SQL)
        self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            self._conn.close()

    def replace_session(
        self,
        session: ArchivedSessionRecord,
        messages: list[ArchivedMessageRecord],
    ) -> None:
        """Replace a session snapshot and all archived messages."""

        def _do(conn: sqlite3.Connection) -> None:
            conn.execute(
                """
                INSERT INTO sessions (
                    id, session_key, workspace, source, chat_id, lineage_id,
                    parent_session_id, title, started_at, updated_at, ended_at, end_reason,
                    message_count, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    session_key=excluded.session_key,
                    workspace=excluded.workspace,
                    source=excluded.source,
                    chat_id=excluded.chat_id,
                    lineage_id=excluded.lineage_id,
                    parent_session_id=excluded.parent_session_id,
                    title=excluded.title,
                    started_at=excluded.started_at,
                    updated_at=excluded.updated_at,
                    ended_at=excluded.ended_at,
                    end_reason=excluded.end_reason,
                    message_count=excluded.message_count,
                    metadata_json=excluded.metadata_json
                """,
                (
                    session.id,
                    session.session_key,
                    session.workspace,
                    session.source,
                    session.chat_id,
                    session.lineage_id,
                    session.parent_session_id,
                    session.title,
                    session.started_at,
                    session.updated_at,
                    session.ended_at,
                    session.end_reason,
                    session.message_count,
                    session.metadata_json,
                ),
            )
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session.id,))
            for message in messages:
                conn.execute(
                    """
                    INSERT INTO messages (
                        session_id, position, role, content, tool_name, tool_call_id,
                        tool_calls_json, timestamp
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session.id,
                        message.position,
                        message.role,
                        message.content,
                        message.tool_name,
                        message.tool_call_id,
                        message.tool_calls_json,
                        message.timestamp,
                    ),
                )

        self._execute_write(_do)

    def list_recent_sessions(self, limit: int) -> list[dict[str, object]]:
        """Return recent archived sessions with previews."""
        query = """
            SELECT s.*,
                COALESCE(
                    (
                        SELECT SUBSTR(REPLACE(REPLACE(m.content, X'0A', ' '), X'0D', ' '), 1, 120)
                        FROM messages m
                        WHERE m.session_id = s.id AND m.role = 'user' AND m.content IS NOT NULL
                        ORDER BY m.position
                        LIMIT 1
                    ),
                    ''
                ) AS preview
            FROM sessions s
            ORDER BY s.updated_at DESC
            LIMIT ?
        """
        with self._lock:
            rows = self._conn.execute(query, (limit,)).fetchall()
        return [dict(row) for row in rows]

    def search_messages(
        self,
        query: str,
        *,
        role_filter: list[str] | None = None,
        limit: int,
    ) -> list[dict[str, object]]:
        """Run an FTS search across archived messages."""
        sanitized = self._sanitize_fts5_query(query)
        if not sanitized:
            return []

        where_clauses = ["messages_fts MATCH ?"]
        params: list[object] = [sanitized]
        if role_filter:
            placeholders = ",".join("?" for _ in role_filter)
            where_clauses.append(f"m.role IN ({placeholders})")
            params.extend(role_filter)
        params.append(limit)
        where_sql = " AND ".join(where_clauses)
        query_sql = f"""
            SELECT
                s.id AS session_id,
                s.session_key,
                s.source,
                s.chat_id,
                s.lineage_id,
                s.parent_session_id,
                s.title,
                s.started_at,
                s.updated_at,
                s.ended_at,
                s.end_reason,
                s.message_count,
                snippet(messages_fts, 0, '>>>', '<<<', '...', 40) AS snippet
            FROM messages_fts
            JOIN messages m ON m.id = messages_fts.rowid
            JOIN sessions s ON s.id = m.session_id
            WHERE {where_sql}
            ORDER BY rank
            LIMIT ?
        """
        with self._lock:
            try:
                rows = self._conn.execute(query_sql, params).fetchall()
            except sqlite3.OperationalError:
                return []
        return [dict(row) for row in rows]

    def get_session(self, session_id: str) -> dict[str, object] | None:
        """Return one archived session row."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_messages_as_conversation(self, session_id: str) -> list[dict[str, object]]:
        """Return archived messages in conversation order."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT role, content, tool_name, tool_call_id, tool_calls_json
                FROM messages
                WHERE session_id = ?
                ORDER BY position
                """,
                (session_id,),
            ).fetchall()
        messages: list[dict[str, object]] = []
        for row in rows:
            message: dict[str, object] = {"role": row["role"], "content": row["content"] or ""}
            if row["tool_name"]:
                message["tool_name"] = row["tool_name"]
            if row["tool_call_id"]:
                message["tool_call_id"] = row["tool_call_id"]
            if row["tool_calls_json"]:
                try:
                    message["tool_calls"] = json.loads(row["tool_calls_json"])
                except json.JSONDecodeError:
                    message["tool_calls"] = []
            messages.append(message)
        return messages

    def _execute_write(self, fn) -> None:
        last_error: Exception | None = None
        for attempt in range(self._WRITE_MAX_RETRIES):
            try:
                with self._lock:
                    self._conn.execute("BEGIN IMMEDIATE")
                    try:
                        fn(self._conn)
                        self._conn.commit()
                    except BaseException:
                        self._conn.rollback()
                        raise
                self._write_count += 1
                if self._write_count % self._CHECKPOINT_EVERY_N_WRITES == 0:
                    self._try_wal_checkpoint()
                return
            except sqlite3.OperationalError as exc:
                error_text = str(exc).lower()
                if "locked" in error_text or "busy" in error_text:
                    last_error = exc
                    if attempt < self._WRITE_MAX_RETRIES - 1:
                        time.sleep(random.uniform(self._WRITE_RETRY_MIN_S, self._WRITE_RETRY_MAX_S))
                        continue
                raise
        if last_error is not None:
            raise last_error

    def _try_wal_checkpoint(self) -> None:
        try:
            with self._lock:
                self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except Exception:
            return

    @staticmethod
    def _sanitize_fts5_query(query: str) -> str:
        quoted_parts: list[str] = []

        def _preserve_quoted(match: re.Match[str]) -> str:
            quoted_parts.append(match.group(0))
            return f"\x00Q{len(quoted_parts) - 1}\x00"

        import re

        sanitized = re.sub(r'"[^"]*"', _preserve_quoted, query)
        sanitized = re.sub(r"[+{}()\"^]", " ", sanitized)
        sanitized = re.sub(r"\*+", "*", sanitized)
        sanitized = re.sub(r"(^|\s)\*", r"\1", sanitized)
        sanitized = re.sub(r"(?i)^(AND|OR|NOT)\b\s*", "", sanitized.strip())
        sanitized = re.sub(r"(?i)\s+(AND|OR|NOT)\s*$", "", sanitized.strip())
        sanitized = re.sub(r"\b(\w+(?:[.-]\w+)+)\b", r'"\1"', sanitized)
        for index, quoted in enumerate(quoted_parts):
            sanitized = sanitized.replace(f"\x00Q{index}\x00", quoted)
        return sanitized.strip()
