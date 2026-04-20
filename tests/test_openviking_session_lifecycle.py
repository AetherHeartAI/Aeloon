from __future__ import annotations

from pathlib import Path

import pytest
from test_openviking_provider import _install_fake_runtime, _make_deps

from aeloon.core.session.manager import Session
from aeloon.memory.providers.openviking import OpenVikingProvider
from aeloon.memory.providers.openviking_service import OpenVikingState


def _state(provider: OpenVikingProvider, session: Session) -> OpenVikingState:
    return provider.service._read_state(session)


@pytest.mark.asyncio
async def test_openviking_live_session_id_is_unique_per_session_instance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_fake_runtime(monkeypatch)
    provider = OpenVikingProvider({"ovConfig": {"storage": {}}}, _make_deps(tmp_path))

    first = Session(key="cli:test")
    second = Session(key="cli:test")

    assert _state(provider, first)["liveSessionId"] != _state(provider, second)["liveSessionId"]


@pytest.mark.asyncio
async def test_openviking_pre_compress_rotates_live_session_and_tracks_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    factory, _ = _install_fake_runtime(monkeypatch)
    provider = OpenVikingProvider(
        {
            "ovConfig": {"storage": {}},
            "waitProcessedTimeoutS": 9.0,
        },
        _make_deps(tmp_path),
    )
    session = Session(key="cli:test")
    session.messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ]

    await provider.sync_turn(
        session=session,
        raw_new_messages=[],
        persisted_new_messages=list(session.messages),
        final_content="world",
    )
    before = _state(provider, session)

    await provider.on_pre_compress(
        session=session,
        pending_messages=session.messages[:1],
        reason="compression",
    )

    client = factory.clients[0]
    after = _state(provider, session)

    assert after["archiveRound"] == 1
    assert after["archivedThrough"] == 1
    assert after["liveGeneration"] == 1
    assert after["liveSessionId"] != before["liveSessionId"]
    assert after["staleSessionIds"] == [before["liveSessionId"]]
    assert client.commit_session_calls == [
        f"aeloon-archive-cli_test-{session.archive_session_id.removeprefix('session-')[:12]}-r001"
    ]
    assert client.delete_session_calls == []
    assert client.sessions[after["liveSessionId"]].messages == [("assistant", "world")]


@pytest.mark.asyncio
async def test_openviking_session_end_archives_pending_messages_without_deleting_live(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    factory, _ = _install_fake_runtime(monkeypatch)
    provider = OpenVikingProvider(
        {
            "ovConfig": {"storage": {}},
            "waitProcessedTimeoutS": 11.0,
        },
        _make_deps(tmp_path),
    )
    session = Session(key="cli:test")
    session.messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ]

    await provider.sync_turn(
        session=session,
        raw_new_messages=[],
        persisted_new_messages=list(session.messages),
        final_content="world",
    )
    live_session_id = _state(provider, session)["liveSessionId"]

    await provider.on_session_end(
        session=session,
        pending_messages=list(session.messages),
        reason="shutdown",
    )

    after = _state(provider, session)

    assert factory.clients[0].commit_session_calls == [
        f"aeloon-archive-cli_test-{session.archive_session_id.removeprefix('session-')[:12]}-r001"
    ]
    assert factory.clients[0].wait_processed_calls == [11.0]
    assert factory.clients[0].delete_session_calls == []
    assert after["archivedThrough"] == len(session.messages)
    assert after["archiveRound"] == 1
    assert after["staleSessionIds"] == [live_session_id]
    assert provider.service._active_search_session_id(session) is None


@pytest.mark.asyncio
async def test_openviking_repairs_broken_live_session_shell_before_mirroring(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    factory, _ = _install_fake_runtime(monkeypatch)
    provider = OpenVikingProvider({"ovConfig": {"storage": {}}}, _make_deps(tmp_path))
    session = Session(key="cli:test")
    session.messages = [{"role": "user", "content": "hello"}]
    state = _state(provider, session)
    live_session_id = state["liveSessionId"]

    factory.shared_existing_session_ids.add(live_session_id)
    factory.shared_broken_session_ids.add(live_session_id)

    await provider.sync_turn(
        session=session,
        raw_new_messages=[],
        persisted_new_messages=list(session.messages),
        final_content=None,
    )

    client = factory.clients[0]
    assert live_session_id not in client.broken_session_ids
    assert client.delete_session_calls == [live_session_id]
    assert client.sessions[live_session_id].messages == [("user", "hello")]
