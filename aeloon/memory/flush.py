"""Explicit flush-before-loss coordination."""

from __future__ import annotations

from aeloon.core.agent.tools.memory import MemoryTool
from aeloon.memory.prompt_store import PromptMemoryStore
from aeloon.memory.types import MessagePayload
from aeloon.providers.base import LLMProvider


class MemoryFlushCoordinator:
    """Run a focused memory-extraction turn before context loss."""

    def __init__(self, *, provider: LLMProvider, model: str, prompt_store: PromptMemoryStore):
        self.provider = provider
        self.model = model
        self.tool = MemoryTool(prompt_store)

    async def flush(
        self,
        *,
        session: object,
        pending_messages: list[MessagePayload],
        reason: str | None = None,
    ) -> None:
        if not pending_messages:
            return
        response = await self.provider.chat_with_retry(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are extracting durable memory before context loss. "
                        "Use only the memory tool. Prefer user preferences, corrections, "
                        "recurring patterns, and stable constraints."
                    ),
                },
                {
                    "role": "user",
                    "content": self._flush_prompt(reason, pending_messages),
                },
            ],
            tools=[self.tool.to_schema()],
            model=self.model,
            temperature=0.1,
            max_tokens=800,
            tool_choice="auto",
        )
        if response.finish_reason == "error":
            return
        for tool_call in response.tool_calls:
            if tool_call.name == "memory":
                await self.tool.execute(**tool_call.arguments)

    async def close(self) -> None:
        return None

    @staticmethod
    def _flush_prompt(reason: str | None, pending_messages: list[MessagePayload]) -> str:
        prefix = (
            "[System: The current conversation context is about to be discarded. "
            "Save anything worth remembering with the memory tool. "
            "Prioritize durable user preferences, corrections, and recurring constraints.]\n\n"
            f"Reason: {reason or 'context-loss'}\n\n"
            "Conversation fragment:\n"
        )
        lines: list[str] = []
        for message in pending_messages:
            role = str(message.get("role") or "unknown").upper()
            content = message.get("content")
            if isinstance(content, list):
                rendered: list[str] = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        rendered.append(str(item.get("text") or ""))
                text = "\n".join(part for part in rendered if part)
            else:
                text = str(content or "")
            if text:
                lines.append(f"[{role}] {text}")
        return prefix + "\n".join(lines)
