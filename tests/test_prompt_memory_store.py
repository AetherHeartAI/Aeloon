from __future__ import annotations

import asyncio
import json
from pathlib import Path

from aeloon.core.config.schema import PromptMemoryConfig


def _write_memory_files(workspace: Path, memory_text: str = "", user_text: str = "") -> None:
    memory_dir = workspace / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "MEMORY.md").write_text(memory_text, encoding="utf-8")
    (memory_dir / "USER.md").write_text(user_text, encoding="utf-8")


def test_prompt_memory_store_loads_dual_targets_and_renders_sections(tmp_path: Path) -> None:
    from aeloon.memory.prompt_store import PromptMemoryStore

    _write_memory_files(
        tmp_path,
        memory_text="Project uses uv.\n§\nKeep patches minimal.",
        user_text="Prefers concise answers.",
    )

    store = PromptMemoryStore(tmp_path, PromptMemoryConfig())

    sections = store.system_prompt_sections()

    assert len(sections) == 2
    assert "Project uses uv." in sections[0]
    assert "Keep patches minimal." in sections[0]
    assert "Prefers concise answers." in sections[1]


def test_prompt_memory_store_snapshot_stays_stable_until_refresh(tmp_path: Path) -> None:
    from aeloon.memory.prompt_store import PromptMemoryStore

    _write_memory_files(tmp_path, memory_text="Initial fact.")
    store = PromptMemoryStore(tmp_path, PromptMemoryConfig())

    before = store.system_prompt_sections()
    result = store.add("memory", "New fact.")

    assert result["success"] is True
    assert "New fact." not in "\n".join(before)
    assert "New fact." not in "\n".join(store.system_prompt_sections())

    store.refresh_snapshot()

    assert "New fact." in "\n".join(store.system_prompt_sections())


def test_prompt_memory_store_enforces_char_budget_and_deduplicates(tmp_path: Path) -> None:
    from aeloon.memory.prompt_store import PromptMemoryStore

    _write_memory_files(tmp_path, memory_text="Alpha")
    store = PromptMemoryStore(
        tmp_path,
        PromptMemoryConfig(memoryCharLimit=11, userCharLimit=20),
    )

    duplicate = store.add("memory", "Alpha")
    overflow = store.add("memory", "Longer entry")

    assert duplicate["success"] is True
    assert "duplicate" in str(duplicate["message"]).lower()
    assert overflow["success"] is False
    assert "exceed" in str(overflow["error"]).lower()


def test_memory_tool_updates_user_memory(tmp_path: Path) -> None:
    from aeloon.core.agent.tools.memory import MemoryTool
    from aeloon.memory.prompt_store import PromptMemoryStore

    _write_memory_files(tmp_path)
    tool = MemoryTool(PromptMemoryStore(tmp_path, PromptMemoryConfig()))

    payload = json.loads(
        asyncio.run(tool.execute(action="add", target="user", content="Likes terse summaries."))
    )

    user_text = (tmp_path / "memory" / "USER.md").read_text(encoding="utf-8")
    assert payload["success"] is True
    assert "Likes terse summaries." in user_text
