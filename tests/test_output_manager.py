"""Tests for OutputManager, OutputTracker, ChainedPolicy and /outputs integration."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from aeloon.core.agent.output_manager import OutputManager, OutputTracker, _infer_category, _slugify
from aeloon.core.agent.tools.policy import ChainedPolicy, set_file_policy
from aeloon.core.bus.events import InboundMessage


@dataclass
class FakeSession:
    key: str = "cli:test"
    metadata: dict[str, Any] = field(default_factory=dict)


def _read_manifest(workspace: Path) -> list[dict[str, Any]]:
    manifest = workspace / "outputs" / "manifest.jsonl"
    if not manifest.exists():
        return []
    entries = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            entries.append(json.loads(line))
    return entries


class TestSlugify:
    def test_basic(self) -> None:
        assert _slugify("Hello World") == "Hello_World"

    def test_unsafe_chars_stripped(self) -> None:
        assert _slugify('foo/bar:baz"qux') == "foo_bar_baz_qux"

    def test_truncation(self) -> None:
        result = _slugify("a" * 100, max_len=20)
        assert len(result) <= 20

    def test_empty_fallback(self) -> None:
        assert _slugify("   ") == "output"


class TestSaveOutput:
    def test_creates_file_and_manifest(self, tmp_path: Path) -> None:
        om = OutputManager(tmp_path)
        path = om.save_output("# Report\nBody", title="My Report", category="research")

        assert path.exists()
        assert path.read_text(encoding="utf-8") == "# Report\nBody"
        assert path.parent.name == "research"

        entries = _read_manifest(tmp_path)
        assert len(entries) == 1
        assert entries[0]["title"] == "My Report"
        assert entries[0]["category"] == "research"

    def test_session_metadata_tracking(self, tmp_path: Path) -> None:
        om = OutputManager(tmp_path)
        session = FakeSession()

        om.save_output("text", title="Test", session=session)

        assert len(session.metadata["outputs"]) == 1
        entry = session.metadata["outputs"][0]
        assert entry["title"] == "Test"
        assert entry["category"] == "general"

    def test_extra_meta_merged(self, tmp_path: Path) -> None:
        om = OutputManager(tmp_path)
        om.save_output("x", title="X", extra_meta={"plugin": "test", "score": 42})

        entries = _read_manifest(tmp_path)
        assert entries[0]["plugin"] == "test"
        assert entries[0]["score"] == 42


class TestBuildOutputSummary:
    def test_from_manifest(self, tmp_path: Path) -> None:
        om = OutputManager(tmp_path)
        om.save_output("a", title="Alpha", category="research")

        summary = om.build_output_summary()

        assert "Alpha" in summary
        assert "outputs/research/" in summary

    def test_from_session_metadata(self, tmp_path: Path) -> None:
        om = OutputManager(tmp_path)
        session = FakeSession()
        om.save_output("a", title="SessionDoc", session=session)

        summary = om.build_output_summary(session=session)

        assert "SessionDoc" in summary


class TestMemoryOutputSummaryIntegration:
    @pytest.mark.asyncio
    async def test_consolidate_with_output_summary(self, tmp_path: Path) -> None:
        from aeloon.core.agent.memory import MemoryStore

        store = MemoryStore(tmp_path)

        mock_provider = MagicMock()
        mock_response = MagicMock()
        mock_response.finish_reason = "stop"
        mock_response.has_tool_calls = True
        mock_response.content = None
        mock_tc = MagicMock()
        mock_tc.arguments = json.dumps(
            {
                "history_entry": "[2026-04-13 10:00] Test. Created outputs/research/test.md",
                "memory_update": "Test memory update.",
            }
        )
        mock_response.tool_calls = [mock_tc]
        mock_provider.chat_with_retry = AsyncMock(return_value=mock_response)

        result = await store.consolidate(
            messages=[{"role": "user", "content": "test", "timestamp": "2026-04-13T10:00:00"}],
            provider=mock_provider,
            model="test-model",
            output_summary='- outputs/research/test.md — "Test report"',
        )

        assert result is True
        user_content = mock_provider.chat_with_retry.call_args.kwargs["messages"][-1]["content"]
        assert "Files Created During This Conversation" in user_content
        assert "test.md" in user_content


class TestInferCategory:
    def test_outputs_subdir_preserved(self) -> None:
        assert _infer_category("outputs/research/paper.md") == "research"
        assert _infer_category("outputs/code/main.py") == "code"

    def test_report_and_doc_fallbacks(self) -> None:
        assert _infer_category("weekly_summary.txt") == "reports"
        assert _infer_category("README.md") == "docs"


class TestRecordToolWrite:
    def test_records_workspace_file(self, tmp_path: Path) -> None:
        om = OutputManager(tmp_path)
        target = tmp_path / "outputs" / "code" / "hello.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("print('hi')")

        om.record_tool_write(target, "write")

        entries = _read_manifest(tmp_path)
        assert len(entries) == 1
        assert entries[0]["source"] == "tool"
        assert entries[0]["op"] == "write"
        assert entries[0]["category"] == "code"

    def test_skips_system_path(self, tmp_path: Path) -> None:
        om = OutputManager(tmp_path)
        target = tmp_path / "memory" / "HISTORY.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("log")

        om.record_tool_write(target, "write")

        assert _read_manifest(tmp_path) == []


class TestOutputTracker:
    @pytest.mark.asyncio
    async def test_after_operation_records_write(self, tmp_path: Path) -> None:
        om = OutputManager(tmp_path)
        tracker = OutputTracker(om)
        target = tmp_path / "outputs" / "data" / "export.csv"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("a,b,c")

        result = await tracker.after_operation("write", str(target), "Wrote 5 bytes to export.csv")

        assert result == "Wrote 5 bytes to export.csv"
        entries = _read_manifest(tmp_path)
        assert len(entries) == 1
        assert entries[0]["category"] == "data"

    @pytest.mark.asyncio
    async def test_skips_error_results(self, tmp_path: Path) -> None:
        om = OutputManager(tmp_path)
        tracker = OutputTracker(om)

        result = await tracker.after_operation("write", "/some/path", "Error: permission denied")

        assert result == "Error: permission denied"
        assert _read_manifest(tmp_path) == []


class TestChainedPolicy:
    @pytest.mark.asyncio
    async def test_before_first_veto_wins(self) -> None:
        p1 = MagicMock()
        p1.before_operation = AsyncMock(return_value="blocked by p1")
        p2 = MagicMock()
        p2.before_operation = AsyncMock(return_value=None)

        chain = ChainedPolicy(p1, p2)

        result = await chain.before_operation("write", "/target")

        assert result == "blocked by p1"
        p2.before_operation.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_after_chains_results(self) -> None:
        p1 = MagicMock()
        p1.after_operation = AsyncMock(return_value="modified by p1")
        p2 = MagicMock()
        p2.after_operation = AsyncMock(return_value="modified by p2")

        chain = ChainedPolicy(p1, p2)

        result = await chain.after_operation("write", "/target", "original")

        assert result == "modified by p2"
        p2.after_operation.assert_awaited_once_with("write", "/target", "modified by p1")

    def test_set_file_policy_append(self) -> None:
        from aeloon.core.agent.tools.policy import get_file_policy

        p1 = MagicMock()
        p2 = MagicMock()

        try:
            set_file_policy(p1)
            set_file_policy(p2, append=True)
            policy = get_file_policy()
            assert isinstance(policy, ChainedPolicy)
            assert len(policy._policies) == 2
        finally:
            set_file_policy(None)


@pytest.mark.asyncio
async def test_outputs_command_lists_recent_outputs(tmp_path: Path) -> None:
    from aeloon.core.agent.loop import AgentLoop
    from aeloon.core.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path, model="test-model")
    loop.memory_consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=None)
    loop.output_manager.save_output("report body", title="Research Note", category="research")

    response = await loop._process_message(
        InboundMessage(channel="cli", sender_id="u", chat_id="c", content="/outputs")
    )

    assert response is not None
    assert "Recent Outputs" in response.content
    assert "Research Note" in response.content
    assert "outputs/research/" in response.content


@pytest.mark.asyncio
async def test_outputs_command_help(tmp_path: Path) -> None:
    from aeloon.core.agent.loop import AgentLoop
    from aeloon.core.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path, model="test-model")
    loop.memory_consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=None)

    response = await loop._process_message(
        InboundMessage(channel="cli", sender_id="u", chat_id="c", content="/outputs help")
    )

    assert response is not None
    assert "## /outputs" in response.content
