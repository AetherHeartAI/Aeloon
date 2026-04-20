from __future__ import annotations

import json
from pathlib import Path

import pytest

from aeloon.core.agent.turn import TurnContext
from aeloon.core.session.manager import Session
from aeloon.providers.base import LLMProvider, LLMResponse


def _make_session(key: str, *messages: tuple[str, str]) -> Session:
    session = Session(key=key)
    for role, content in messages:
        session.add_message(role, content)
    return session


class _SummaryProvider(LLMProvider):
    def __init__(self, summary: str | None, *, fail: bool = False):
        super().__init__()
        self.summary = summary
        self.fail = fail
        self.calls: list[str] = []

    async def chat(
        self,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, object] | None = None,
    ) -> LLMResponse:
        self.calls.append(str(messages[-1]["content"]))
        if self.fail:
            raise RuntimeError("summary offline")
        return LLMResponse(content=self.summary)

    def get_default_model(self) -> str:
        return "test-model"


def _tool(tmp_path: Path, provider: _SummaryProvider):
    from aeloon.core.agent.tools.session_search import SessionSearchTool
    from aeloon.memory.archive_db import SessionArchiveDB
    from aeloon.memory.archive_service import SessionArchiveService

    db = SessionArchiveDB(tmp_path / "archive.db")
    service = SessionArchiveService(db=db, workspace=tmp_path)
    service.ingest_session_sync(
        _make_session(
            "cli:alpha",
            ("user", "We fixed docker networking."),
            ("assistant", "Created a bridge network and updated compose."),
        )
    )
    service.ingest_session_sync(
        _make_session(
            "cli:beta",
            ("user", "We discussed prompt memory."),
            ("assistant", "Added MEMORY.md and USER.md."),
        )
    )
    tool = SessionSearchTool(service=service, provider=provider, model="test-model")
    tool.on_turn_start(
        TurnContext(
            channel="cli",
            chat_id="alpha",
            session_key="cli:current",
            metadata={
                "archive_session_id": "current-session",
                "lineage_id": "current-lineage",
            },
        )
    )
    return tool


@pytest.mark.asyncio
async def test_session_search_tool_recent_mode_lists_recent_sessions(tmp_path: Path) -> None:
    tool = _tool(tmp_path, _SummaryProvider("unused"))

    payload = json.loads(await tool.execute(limit=2))

    assert payload["mode"] == "recent"
    assert payload["count"] == 2
    assert payload["results"][0]["session_key"] == "cli:beta"
    assert "session_id" in payload["results"][0]
    assert "lineage_id" in payload["results"][0]


@pytest.mark.asyncio
async def test_session_search_tool_search_mode_returns_summaries(tmp_path: Path) -> None:
    provider = _SummaryProvider("Summary: docker networking fix.")
    tool = _tool(tmp_path, provider)

    payload = json.loads(await tool.execute(query="docker OR networking", limit=2))

    assert payload["count"] == 1
    assert payload["results"][0]["session_key"] == "cli:alpha"
    assert payload["results"][0]["session_id"]
    assert payload["results"][0]["lineage_id"]
    assert "docker networking fix" in payload["results"][0]["summary"].lower()
    assert provider.calls


@pytest.mark.asyncio
async def test_session_search_tool_falls_back_to_raw_preview_when_summary_fails(
    tmp_path: Path,
) -> None:
    tool = _tool(tmp_path, _SummaryProvider(None, fail=True))

    payload = json.loads(await tool.execute(query="prompt memory", limit=2))

    assert payload["count"] == 1
    assert payload["results"][0]["session_key"] == "cli:beta"
    assert "Raw preview" in payload["results"][0]["summary"]
