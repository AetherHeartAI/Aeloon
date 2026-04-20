from __future__ import annotations

from pathlib import Path

from aeloon.core.session.manager import SessionManager
from aeloon.memory.archive_db import SessionArchiveDB
from aeloon.memory.archive_service import SessionArchiveService


def test_archive_keeps_prior_session_after_rollover(tmp_path: Path) -> None:
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:test")
    session.add_message("user", "first conversation")
    session.add_message("assistant", "first answer")

    first_archive_id = session.archive_session_id

    previous, successor = sessions.rollover("cli:test", reason="new_session")

    assert previous.archive_session_id == first_archive_id
    assert successor.archive_session_id != first_archive_id
    assert successor.lineage_id != first_archive_id
    assert successor.key == previous.key


def test_recent_sessions_skip_current_lineage_but_keep_prior_lineages(tmp_path: Path) -> None:
    sessions = SessionManager(tmp_path)
    db = SessionArchiveDB(tmp_path / "archive.db")
    service = SessionArchiveService(db=db, workspace=tmp_path)

    old_session = sessions.get_or_create("cli:test")
    old_session.add_message("user", "previous discussion about docker")
    old_session.add_message("assistant", "old answer")
    service.ingest_session_sync(old_session)

    _, current_session = sessions.rollover("cli:test", reason="new_session")
    current_session.add_message("user", "current discussion")
    current_session.add_message("assistant", "current answer")
    service.ingest_session_sync(current_session)

    recent = service.list_recent_sessions(
        limit=5,
        current_session_id=current_session.archive_session_id,
        current_lineage_id=current_session.lineage_id,
    )

    assert [item.session_id for item in recent] == [old_session.archive_session_id]
    db.close()
