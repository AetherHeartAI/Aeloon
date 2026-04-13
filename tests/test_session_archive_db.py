from __future__ import annotations

from pathlib import Path

from aeloon.core.session.manager import Session


def _make_session(key: str, *messages: tuple[str, str]) -> Session:
    session = Session(key=key)
    for role, content in messages:
        session.add_message(role, content)
    return session


def test_archive_db_ingests_and_lists_recent_sessions(tmp_path: Path) -> None:
    from aeloon.memory.archive_db import SessionArchiveDB
    from aeloon.memory.archive_service import SessionArchiveService

    db = SessionArchiveDB(tmp_path / "archive.db")
    service = SessionArchiveService(db=db, workspace=tmp_path)
    session = _make_session(
        "cli:recent",
        ("user", "Discussed archive rollout."),
        ("assistant", "Suggested SQLite FTS."),
    )

    service.ingest_session_sync(session)
    recent = service.list_recent_sessions(limit=5)

    assert len(recent) == 1
    assert recent[0].session_key == "cli:recent"
    assert "Discussed archive rollout." in recent[0].preview
    db.close()


def test_archive_db_full_text_search_returns_matching_sessions(tmp_path: Path) -> None:
    from aeloon.memory.archive_db import SessionArchiveDB
    from aeloon.memory.archive_service import SessionArchiveService

    db = SessionArchiveDB(tmp_path / "archive.db")
    service = SessionArchiveService(db=db, workspace=tmp_path)
    service.ingest_session_sync(
        _make_session(
            "cli:alpha",
            ("user", "Need help with docker networking."),
            ("assistant", "Use a bridge network."),
        )
    )
    service.ingest_session_sync(
        _make_session(
            "cli:beta",
            ("user", "Need help with sqlite triggers."),
            ("assistant", "Use AFTER INSERT triggers."),
        )
    )

    hits = service.search(query="docker OR networking", limit=5)

    assert len(hits) == 1
    assert hits[0].session_key == "cli:alpha"
    assert "docker" in hits[0].conversation_text.lower()
    db.close()


def test_archive_db_excludes_current_lineage_from_search_results(tmp_path: Path) -> None:
    from aeloon.memory.archive_db import SessionArchiveDB
    from aeloon.memory.archive_service import SessionArchiveService

    db = SessionArchiveDB(tmp_path / "archive.db")
    service = SessionArchiveService(db=db, workspace=tmp_path)
    current = _make_session(
        "cli:current",
        ("user", "Current session about session_search."),
        ("assistant", "Still in progress."),
    )
    other = _make_session(
        "cli:other",
        ("user", "Previous session about session_search."),
        ("assistant", "Completed last week."),
    )

    service.ingest_session_sync(current)
    service.ingest_session_sync(other)
    hits = service.search(query="session_search", limit=5, current_session_key="cli:current")

    assert [hit.session_key for hit in hits] == ["cli:other"]
    db.close()
