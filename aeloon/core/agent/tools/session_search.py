"""Transcript recall tool backed by the archive service."""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from aeloon.core.agent.tools.base import Tool
from aeloon.memory.archive_service import RecentArchivedSession, SessionArchiveService, SessionSearchHit
from aeloon.providers.base import LLMProvider

if TYPE_CHECKING:
    from aeloon.core.agent.turn import TurnContext


MAX_SESSION_CHARS = 100_000


def _format_timestamp(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _format_conversation(messages: list[dict[str, object]]) -> str:
    parts: list[str] = []
    for message in messages:
        role = str(message.get("role") or "unknown").upper()
        content = str(message.get("content") or "")
        tool_name = message.get("tool_name")
        if role == "TOOL" and tool_name:
            parts.append(f"[TOOL:{tool_name}]: {content}")
        elif role == "ASSISTANT" and message.get("tool_calls"):
            parts.append(f"[ASSISTANT]: [Called tools]")
            if content:
                parts.append(f"[ASSISTANT]: {content}")
        else:
            parts.append(f"[{role}]: {content}")
    return "\n\n".join(parts)


def _truncate_around_matches(full_text: str, query: str, max_chars: int = MAX_SESSION_CHARS) -> str:
    if len(full_text) <= max_chars:
        return full_text
    query_terms = query.lower().split()
    text_lower = full_text.lower()
    first_match = len(full_text)
    for term in query_terms:
        pos = text_lower.find(term)
        if pos != -1 and pos < first_match:
            first_match = pos
    if first_match == len(full_text):
        first_match = 0
    half = max_chars // 2
    start = max(0, first_match - half)
    end = min(len(full_text), start + max_chars)
    if end - start < max_chars:
        start = max(0, end - max_chars)
    truncated = full_text[start:end]
    prefix = "...[earlier conversation truncated]...\n\n" if start > 0 else ""
    suffix = "\n\n...[later conversation truncated]..." if end < len(full_text) else ""
    return prefix + truncated + suffix


class SessionSearchTool(Tool):
    """Search archived session transcripts."""

    def __init__(self, *, service: SessionArchiveService, provider: LLMProvider, model: str):
        self.service = service
        self.provider = provider
        self.model = model
        self._turn_context: TurnContext | None = None

    @property
    def name(self) -> str:
        return "session_search"

    @property
    def description(self) -> str:
        return (
            "Search past session transcripts or browse recent sessions. "
            "Use this when the user references prior work, asks what happened before, "
            "or you need cross-session recall."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Optional search query. Omit to browse recent sessions.",
                },
                "role_filter": {
                    "type": "string",
                    "description": "Optional comma-separated role filter.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max sessions to return.",
                    "default": 3,
                    "minimum": 1,
                    "maximum": 5,
                },
            },
            "required": [],
        }

    @property
    def concurrency_mode(self) -> Literal["read_only"]:
        return "read_only"

    def on_turn_start(self, ctx: "TurnContext") -> None:
        self._turn_context = ctx

    async def execute(self, **kwargs: object) -> str:
        query = str(kwargs.get("query") or "").strip()
        role_filter_value = kwargs.get("role_filter")
        limit_raw = kwargs.get("limit")
        limit = limit_raw if isinstance(limit_raw, int) else int(str(limit_raw or 3))
        current_session_key = self._turn_context.session_key if self._turn_context else None
        role_filter = self._parse_role_filter(role_filter_value)

        if not query:
            sessions = self.service.list_recent_sessions(
                limit=limit,
                current_session_key=current_session_key,
            )
            return json.dumps(
                {
                    "success": True,
                    "mode": "recent",
                    "count": len(sessions),
                    "results": [self._recent_result(session) for session in sessions],
                },
                ensure_ascii=False,
            )

        hits = self.service.search(
            query=query,
            limit=limit,
            role_filter=role_filter,
            current_session_key=current_session_key,
        )
        results = []
        for hit in hits:
            conversation_text = _truncate_around_matches(_format_conversation(hit.conversation), query)
            summary = await self._summarize_hit(hit, query, conversation_text)
            if not summary:
                preview = conversation_text[:500] + ("\n…[truncated]" if len(conversation_text) > 500 else "")
                summary = f"[Raw preview — summarization unavailable]\n{preview}"
            results.append(
                {
                    "session_key": hit.session_key,
                    "source": hit.source,
                    "started_at": _format_timestamp(hit.started_at),
                    "summary": summary,
                }
            )
        return json.dumps(
            {
                "success": True,
                "mode": "search",
                "query": query,
                "count": len(results),
                "results": results,
            },
            ensure_ascii=False,
        )

    async def _summarize_hit(
        self,
        hit: SessionSearchHit,
        query: str,
        conversation_text: str,
    ) -> str | None:
        try:
            response = await self.provider.chat_with_retry(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Summarize this past session with focus on the search topic. "
                            "Preserve concrete actions, files, commands, and outcomes."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Search topic: {query}\n"
                            f"Session key: {hit.session_key}\n"
                            f"Started at: {_format_timestamp(hit.started_at)}\n\n"
                            f"Conversation:\n{conversation_text}"
                        ),
                    },
                ],
                model=self.model,
                temperature=0.1,
                max_tokens=1200,
            )
        except Exception:
            return None
        if response.finish_reason == "error":
            return None
        return response.content.strip() if isinstance(response.content, str) and response.content.strip() else None

    @staticmethod
    def _recent_result(session: RecentArchivedSession) -> dict[str, object]:
        return {
            "session_key": session.session_key,
            "source": session.source,
            "started_at": _format_timestamp(session.started_at),
            "updated_at": _format_timestamp(session.updated_at),
            "message_count": session.message_count,
            "preview": session.preview,
            "title": session.title,
        }

    @staticmethod
    def _parse_role_filter(value: object) -> list[str] | None:
        if not isinstance(value, str) or not value.strip():
            return None
        roles = [item.strip() for item in value.split(",") if item.strip()]
        return roles or None
