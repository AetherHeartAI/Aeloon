from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import cast

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
    assert "MEMORY (your personal notes)" in sections[0]
    assert "USER PROFILE (who the user is)" in sections[1]
    assert "Project uses uv." in sections[0]
    assert "Keep patches minimal." in sections[0]
    assert "Prefers concise answers." in sections[1]
    assert "chars" in sections[0]
    assert "chars" in sections[1]


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
    assert "current_entries" in overflow
    assert "usage" in overflow


def test_prompt_memory_store_replace_rejects_multiple_distinct_matches(tmp_path: Path) -> None:
    from aeloon.memory.prompt_store import PromptMemoryStore

    _write_memory_files(
        tmp_path,
        memory_text="Project uses uv.\n§\nProject uses pytest.",
    )
    store = PromptMemoryStore(tmp_path, PromptMemoryConfig())

    payload = store.replace("memory", "Project uses", "Single merged entry")

    assert payload["success"] is False
    assert "multiple" in str(payload["error"]).lower()
    assert "matches" in payload


def test_prompt_memory_store_replace_allows_multiple_identical_duplicates(tmp_path: Path) -> None:
    from aeloon.memory.prompt_store import PromptMemoryStore

    _write_memory_files(
        tmp_path,
        memory_text="Same text.\n§\nSame text.",
    )
    store = PromptMemoryStore(tmp_path, PromptMemoryConfig())

    payload = store.replace("memory", "Same text", "Updated text.")

    assert payload["success"] is True
    assert payload["entry_count"] == 1
    entries = cast(list[str], payload["entries"])
    assert "Updated text." in entries


def test_prompt_memory_store_remove_rejects_multiple_distinct_matches(tmp_path: Path) -> None:
    from aeloon.memory.prompt_store import PromptMemoryStore

    _write_memory_files(
        tmp_path,
        user_text="Prefers concise answers.\n§\nPrefers direct answers.",
    )
    store = PromptMemoryStore(tmp_path, PromptMemoryConfig())

    payload = store.remove("user", "Prefers")

    assert payload["success"] is False
    assert "multiple" in str(payload["error"]).lower()
    assert "matches" in payload


def test_prompt_memory_store_success_response_includes_count_and_percent_usage(tmp_path: Path) -> None:
    from aeloon.memory.prompt_store import PromptMemoryStore

    _write_memory_files(tmp_path)
    store = PromptMemoryStore(tmp_path, PromptMemoryConfig())

    payload = store.add("user", "Likes terse summaries.")

    assert payload["success"] is True
    assert payload["entry_count"] == 1
    assert "%" in str(payload["usage"])
    assert "chars" in str(payload["usage"])


def test_prompt_memory_store_loads_existing_oversized_file_without_truncation(tmp_path: Path) -> None:
    from aeloon.memory.prompt_store import PromptMemoryStore

    oversized = "very long existing memory entry"
    _write_memory_files(tmp_path, memory_text=oversized)
    store = PromptMemoryStore(tmp_path, PromptMemoryConfig(memoryCharLimit=10))

    sections = "\n".join(store.system_prompt_sections())

    assert oversized in sections
    assert store.over_limit_status()["memory"] == (len(oversized), 10)


def test_prompt_memory_store_blocks_new_writes_when_file_is_already_oversized(tmp_path: Path) -> None:
    from aeloon.memory.prompt_store import PromptMemoryStore

    oversized = "very long existing memory entry"
    _write_memory_files(tmp_path, memory_text=oversized)
    store = PromptMemoryStore(tmp_path, PromptMemoryConfig(memoryCharLimit=10))

    payload = store.add("memory", "new fact")

    assert payload["success"] is False
    assert "exceed" in str(payload["error"]).lower()


def test_prompt_memory_store_loads_existing_oversized_user_file_without_truncation(
    tmp_path: Path,
) -> None:
    from aeloon.memory.prompt_store import PromptMemoryStore

    oversized = "very long existing user preference entry"
    _write_memory_files(tmp_path, user_text=oversized)
    store = PromptMemoryStore(tmp_path, PromptMemoryConfig(userCharLimit=10))

    sections = "\n".join(store.system_prompt_sections())

    assert oversized in sections
    assert store.over_limit_status()["user"] == (len(oversized), 10)


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


def test_memory_tool_replace_uses_content_field(tmp_path: Path) -> None:
    from aeloon.core.agent.tools.memory import MemoryTool
    from aeloon.memory.prompt_store import PromptMemoryStore

    _write_memory_files(tmp_path, memory_text="Project uses uv.")
    tool = MemoryTool(PromptMemoryStore(tmp_path, PromptMemoryConfig()))

    payload = json.loads(
        asyncio.run(
            tool.execute(
                action="replace",
                target="memory",
                old_text="uses uv",
                content="Project uses uv and pytest.",
            )
        )
    )

    memory_text = (tmp_path / "memory" / "MEMORY.md").read_text(encoding="utf-8")
    assert payload["success"] is True
    assert "pytest" in memory_text


def test_memory_tool_remove_uses_old_text_field(tmp_path: Path) -> None:
    from aeloon.core.agent.tools.memory import MemoryTool
    from aeloon.memory.prompt_store import PromptMemoryStore

    _write_memory_files(tmp_path, user_text="Prefers concise answers.")
    tool = MemoryTool(PromptMemoryStore(tmp_path, PromptMemoryConfig()))

    payload = json.loads(
        asyncio.run(
            tool.execute(
                action="remove",
                target="user",
                old_text="concise",
            )
        )
    )

    user_text = (tmp_path / "memory" / "USER.md").read_text(encoding="utf-8")
    assert payload["success"] is True
    assert "concise" not in user_text


def test_memory_tool_remove_no_longer_uses_content_field(tmp_path: Path) -> None:
    from aeloon.core.agent.tools.memory import MemoryTool
    from aeloon.memory.prompt_store import PromptMemoryStore

    _write_memory_files(tmp_path, user_text="Prefers concise answers.")
    tool = MemoryTool(PromptMemoryStore(tmp_path, PromptMemoryConfig()))

    payload = json.loads(
        asyncio.run(
            tool.execute(
                action="remove",
                target="user",
                content="concise",
            )
        )
    )

    user_text = (tmp_path / "memory" / "USER.md").read_text(encoding="utf-8")
    assert payload["success"] is False
    assert "old_text" in str(payload["error"]).lower()
    assert "concise" in user_text


def test_memory_tool_mirrors_only_add_and_replace(tmp_path: Path) -> None:
    from aeloon.core.agent.tools.memory import MemoryTool
    from aeloon.memory.prompt_store import PromptMemoryStore

    _write_memory_files(tmp_path, memory_text="Project uses uv.")
    mirrored: list[tuple[str, str, str]] = []

    async def on_write(*, action: str, target: str, content: str) -> None:
        mirrored.append((action, target, content))

    tool = MemoryTool(PromptMemoryStore(tmp_path, PromptMemoryConfig()), on_write=on_write)

    add_payload = json.loads(
        asyncio.run(
            tool.execute(
                action="add",
                target="user",
                content="Likes terse summaries.",
            )
        )
    )
    replace_payload = json.loads(
        asyncio.run(
            tool.execute(
                action="replace",
                target="memory",
                old_text="uses uv",
                content="Project uses uv and pytest.",
            )
        )
    )
    remove_payload = json.loads(
        asyncio.run(
            tool.execute(
                action="remove",
                target="user",
                old_text="terse",
            )
        )
    )

    assert add_payload["success"] is True
    assert replace_payload["success"] is True
    assert remove_payload["success"] is True
    assert mirrored == [
        ("add", "user", "Likes terse summaries."),
        ("replace", "memory", "Project uses uv and pytest."),
    ]
