"""Prompt-memory mutation tool."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Literal, cast

from aeloon.core.agent.tools.base import Tool
from aeloon.memory.prompt_store import MemoryTarget, PromptMemoryStore


class MemoryTool(Tool):
    """Mutate always-on prompt memory."""

    def __init__(
        self,
        store: PromptMemoryStore,
        on_write: Callable[..., Awaitable[None]] | None = None,
    ):
        self.store = store
        self._on_write = on_write

    @property
    def name(self) -> str:
        return "memory"

    @property
    def description(self) -> str:
        return (
            "Manage small durable prompt memory. Use target='memory' for stable project or "
            "environment facts, and target='user' for long-lived user preferences. "
            "Do not store transient task state or full conversation transcripts."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "replace", "remove"],
                    "description": "Mutation to apply.",
                },
                "target": {
                    "type": "string",
                    "enum": ["memory", "user"],
                    "description": "Which prompt-memory file to update.",
                },
                "content": {
                    "type": "string",
                    "description": "Entry content for add/remove operations.",
                },
                "old_text": {
                    "type": "string",
                    "description": "Substring that identifies the entry to replace.",
                },
                "new_content": {
                    "type": "string",
                    "description": "Replacement content for replace operations.",
                },
            },
            "required": ["action", "target"],
        }

    @property
    def concurrency_mode(self) -> Literal["mutating"]:
        return "mutating"

    async def execute(self, **kwargs: object) -> str:
        action = str(kwargs.get("action", ""))
        target = str(kwargs.get("target", ""))
        content = kwargs.get("content")
        old_text = kwargs.get("old_text")
        new_content = kwargs.get("new_content")
        if target not in {"memory", "user"}:
            return json.dumps(
                {"success": False, "error": f"Unsupported target: {target}"},
                ensure_ascii=False,
            )
        memory_target = cast(MemoryTarget, target)
        if action == "add":
            result = self.store.add(memory_target, str(content or ""))
        elif action == "replace":
            result = self.store.replace(memory_target, str(old_text or ""), str(new_content or ""))
        elif action == "remove":
            result = self.store.remove(memory_target, str(content or ""))
        else:
            result = {"success": False, "error": f"Unsupported action: {action}"}
        if result.get("success") is True and self._on_write is not None:
            write_content = str(new_content or content or "")
            await self._on_write(action=action, target=target, content=write_content)
        return json.dumps(result, ensure_ascii=False)
