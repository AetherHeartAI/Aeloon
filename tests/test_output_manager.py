"""Tests for OutputManager, OutputTracker, ChainedPolicy and memory integration."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from aeloon.core.agent.output_manager import (
    OutputManager,
    OutputTracker,
    _infer_category,
    _slugify,
)
from aeloon.core.agent.tools.policy import ChainedPolicy, set_file_policy
from aeloon.core.session.manager import Session

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class FakeSession(Session):
    key: str = "cli:test"
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# _slugify
# ---------------------------------------------------------------------------


class TestSlugify:
    def test_basic(self) -> None:
        assert _slugify("Hello World") == "Hello_World"

    def test_unsafe_chars_stripped(self) -> None:
        assert _slugify('foo/bar:baz"qux') == "foo_bar_baz_qux"

    def test_truncation(self) -> None:
        long = "a" * 100
        result = _slugify(long, max_len=20)
        assert len(result) <= 20

    def test_empty_fallback(self) -> None:
        assert _slugify("   ") == "output"

    def test_unicode_preserved(self) -> None:
        assert "钙钛矿" in _slugify("钙钛矿太阳能电池")


# ---------------------------------------------------------------------------
# OutputManager.save_output
# ---------------------------------------------------------------------------


class TestSaveOutput:
    def test_creates_file_and_manifest(self, tmp_path: Path) -> None:
        om = OutputManager(tmp_path)
        path = om.save_output("# Report\nBody", title="My Report", category="research")
        assert path.exists()
        assert path.read_text(encoding="utf-8") == "# Report\nBody"
        assert path.parent.name == "research"
        assert (tmp_path / "outputs" / "manifest.jsonl").exists()

        entries = _read_manifest(tmp_path)
        assert len(entries) == 1
        assert entries[0]["title"] == "My Report"
        assert entries[0]["category"] == "research"
        assert "path" in entries[0]

    def test_explicit_filename(self, tmp_path: Path) -> None:
        om = OutputManager(tmp_path)
        path = om.save_output("data", title="Data", filename="report.txt", category="code")
        assert path.name == "report.txt"
        assert path.parent.name == "code"

    def test_session_metadata_tracking(self, tmp_path: Path) -> None:
        om = OutputManager(tmp_path)
        session = FakeSession()
        om.save_output("text", title="Test", session=session)
        assert "outputs" in session.metadata
        assert len(session.metadata["outputs"]) == 1
        entry = session.metadata["outputs"][0]
        assert entry["title"] == "Test"
        assert entry["category"] == "general"
        assert "path" in entry
        assert "ts" in entry

    def test_multiple_saves_accumulate_manifest(self, tmp_path: Path) -> None:
        om = OutputManager(tmp_path)
        om.save_output("a", title="First")
        om.save_output("b", title="Second")
        om.save_output("c", title="Third")
        entries = _read_manifest(tmp_path)
        assert len(entries) == 3

    def test_extra_meta_merged(self, tmp_path: Path) -> None:
        om = OutputManager(tmp_path)
        om.save_output("x", title="X", extra_meta={"plugin": "test", "score": 42})
        entries = _read_manifest(tmp_path)
        assert entries[0]["plugin"] == "test"
        assert entries[0]["score"] == 42


# ---------------------------------------------------------------------------
# OutputManager.list_recent
# ---------------------------------------------------------------------------


class TestListRecent:
    def test_empty(self, tmp_path: Path) -> None:
        om = OutputManager(tmp_path)
        assert om.list_recent() == []

    def test_returns_newest_first(self, tmp_path: Path) -> None:
        om = OutputManager(tmp_path)
        om.save_output("a", title="First")
        om.save_output("b", title="Second")
        om.save_output("c", title="Third")
        recent = om.list_recent(limit=2)
        assert len(recent) == 2
        assert recent[0]["title"] == "Third"
        assert recent[1]["title"] == "Second"

    def test_limit_respected(self, tmp_path: Path) -> None:
        om = OutputManager(tmp_path)
        for i in range(10):
            om.save_output(str(i), title=f"Item {i}")
        assert len(om.list_recent(limit=3)) == 3


# ---------------------------------------------------------------------------
# OutputManager.build_output_summary
# ---------------------------------------------------------------------------


class TestBuildOutputSummary:
    def test_empty_returns_empty(self, tmp_path: Path) -> None:
        om = OutputManager(tmp_path)
        assert om.build_output_summary() == ""

    def test_from_manifest(self, tmp_path: Path) -> None:
        om = OutputManager(tmp_path)
        om.save_output("a", title="Alpha", category="research")
        summary = om.build_output_summary()
        assert "Alpha" in summary
        assert "research" in summary

    def test_from_session_metadata(self, tmp_path: Path) -> None:
        om = OutputManager(tmp_path)
        session = FakeSession()
        om.save_output("a", title="SessionDoc", session=session)
        summary = om.build_output_summary(session=session)
        assert "SessionDoc" in summary


# ---------------------------------------------------------------------------
# OutputManager.promote_to_wiki
# ---------------------------------------------------------------------------


class TestPromoteToWiki:
    @pytest.mark.asyncio
    async def test_promote_success(self, tmp_path: Path) -> None:
        om = OutputManager(tmp_path)
        path = om.save_output("wiki content", title="ToWiki", category="research")

        wiki_plugin = MagicMock()
        wiki_plugin._ingest_service = MagicMock()
        wiki_plugin._ingest_service.ingest_file = AsyncMock()

        result = await om.promote_to_wiki(path, wiki_plugin)
        assert "Promoted" in result
        wiki_plugin._ingest_service.ingest_file.assert_awaited_once_with(path)

    @pytest.mark.asyncio
    async def test_promote_missing_file(self, tmp_path: Path) -> None:
        om = OutputManager(tmp_path)
        wiki_plugin = MagicMock()
        wiki_plugin._ingest_service = MagicMock()
        result = await om.promote_to_wiki(tmp_path / "nonexistent.md", wiki_plugin)
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_promote_no_ingest_service(self, tmp_path: Path) -> None:
        om = OutputManager(tmp_path)
        path = om.save_output("x", title="X")
        wiki_plugin = MagicMock(spec=[])
        result = await om.promote_to_wiki(path, wiki_plugin)
        assert "not available" in result


# ---------------------------------------------------------------------------
# Memory consolidation integration
# ---------------------------------------------------------------------------


class TestMemoryOutputSummaryIntegration:
    """Verify that consolidate() accepts output_summary."""

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
        mock_provider.chat_with_retry.assert_not_awaited()
        assert not store.history_file.exists()


# ---------------------------------------------------------------------------
# _infer_category
# ---------------------------------------------------------------------------


class TestInferCategory:
    def test_code_suffix(self) -> None:
        assert _infer_category("scripts/etl.py") == "code"
        assert _infer_category("build.sh") == "code"
        assert _infer_category("app.tsx") == "code"

    def test_data_suffix(self) -> None:
        assert _infer_category("dump.csv") == "data"
        assert _infer_category("config.yaml") == "data"

    def test_media_suffix(self) -> None:
        assert _infer_category("logo.png") == "media"
        assert _infer_category("intro.mp4") == "media"

    def test_markdown_report(self) -> None:
        assert _infer_category("q1_report.md") == "reports"
        assert _infer_category("weekly_summary.txt") == "reports"

    def test_markdown_doc(self) -> None:
        assert _infer_category("README.md") == "docs"
        assert _infer_category("guide.txt") == "docs"

    def test_misc_fallback(self) -> None:
        assert _infer_category("archive.tar.gz") == "misc"
        assert _infer_category("data.unknown") == "misc"

    def test_outputs_subdir_preserved(self) -> None:
        assert _infer_category("outputs/research/paper.md") == "research"
        assert _infer_category("outputs/code/main.py") == "code"


# ---------------------------------------------------------------------------
# OutputManager.record_tool_write
# ---------------------------------------------------------------------------


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

        entries = _read_manifest(tmp_path)
        assert len(entries) == 0

    def test_skips_outside_workspace(self, tmp_path: Path) -> None:
        om = OutputManager(tmp_path / "workspace")
        outside = tmp_path / "other" / "file.txt"
        outside.parent.mkdir(parents=True, exist_ok=True)
        outside.write_text("x")
        om.record_tool_write(outside, "write")

        manifest = tmp_path / "workspace" / "outputs" / "manifest.jsonl"
        if manifest.exists():
            assert manifest.read_text().strip() == ""


# ---------------------------------------------------------------------------
# OutputTracker (file_policy callback)
# ---------------------------------------------------------------------------


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
        assert entries[0]["op"] == "write"
        assert entries[0]["category"] == "data"

    @pytest.mark.asyncio
    async def test_after_operation_records_edit(self, tmp_path: Path) -> None:
        om = OutputManager(tmp_path)
        tracker = OutputTracker(om)
        target = tmp_path / "outputs" / "code" / "main.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("pass")

        result = await tracker.after_operation("edit", str(target), "Edited main.py")
        assert result == "Edited main.py"

        entries = _read_manifest(tmp_path)
        assert len(entries) == 1
        assert entries[0]["op"] == "edit"

    @pytest.mark.asyncio
    async def test_skips_error_results(self, tmp_path: Path) -> None:
        om = OutputManager(tmp_path)
        tracker = OutputTracker(om)
        result = await tracker.after_operation("write", "/some/path", "Error: permission denied")
        assert result == "Error: permission denied"
        assert _read_manifest(tmp_path) == []

    @pytest.mark.asyncio
    async def test_skips_read_operations(self, tmp_path: Path) -> None:
        om = OutputManager(tmp_path)
        tracker = OutputTracker(om)
        result = await tracker.after_operation("read", "/some/path", "contents...")
        assert result == "contents..."
        assert _read_manifest(tmp_path) == []

    @pytest.mark.asyncio
    async def test_before_operation_always_none(self, tmp_path: Path) -> None:
        om = OutputManager(tmp_path)
        tracker = OutputTracker(om)
        assert await tracker.before_operation("write", "/path") is None


# ---------------------------------------------------------------------------
# ChainedPolicy
# ---------------------------------------------------------------------------


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
    async def test_before_none_when_all_pass(self) -> None:
        p1 = MagicMock()
        p1.before_operation = AsyncMock(return_value=None)
        p2 = MagicMock()
        p2.before_operation = AsyncMock(return_value=None)

        chain = ChainedPolicy(p1, p2)
        assert await chain.before_operation("write", "/target") is None

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

    @pytest.mark.asyncio
    async def test_set_file_policy_append(self) -> None:
        """Verify that set_file_policy(append=True) chains policies."""
        from aeloon.core.agent.tools.policy import get_file_policy

        p1 = MagicMock()
        p1.before_operation = AsyncMock(return_value=None)
        p1.after_operation = AsyncMock(return_value="result1")

        p2 = MagicMock()
        p2.before_operation = AsyncMock(return_value=None)
        p2.after_operation = AsyncMock(return_value="result2")

        try:
            set_file_policy(p1)
            set_file_policy(p2, append=True)
            policy = get_file_policy()
            assert isinstance(policy, ChainedPolicy)
            assert len(policy._policies) == 2
        finally:
            set_file_policy(None)

    @pytest.mark.asyncio
    async def test_set_file_policy_append_to_chain(self) -> None:
        """Appending to an existing ChainedPolicy extends the list."""
        from aeloon.core.agent.tools.policy import get_file_policy

        p1 = MagicMock()
        p1.before_operation = AsyncMock(return_value=None)
        p1.after_operation = AsyncMock(return_value="r1")
        p2 = MagicMock()
        p2.before_operation = AsyncMock(return_value=None)
        p2.after_operation = AsyncMock(return_value="r2")
        p3 = MagicMock()
        p3.before_operation = AsyncMock(return_value=None)
        p3.after_operation = AsyncMock(return_value="r3")

        try:
            set_file_policy(p1)
            set_file_policy(p2, append=True)
            set_file_policy(p3, append=True)
            policy = get_file_policy()
            assert isinstance(policy, ChainedPolicy)
            assert len(policy._policies) == 3
        finally:
            set_file_policy(None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
