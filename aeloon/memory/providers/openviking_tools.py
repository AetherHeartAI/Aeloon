"""OpenViking provider-native tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol

from aeloon.core.agent.tools.base import Tool

if TYPE_CHECKING:
    from aeloon.core.agent.turn import TurnContext


class OpenVikingToolServiceProtocol(Protocol):
    async def tool_search(
        self,
        *,
        session_key: str,
        query: str,
        mode: str = "auto",
        scope: str = "",
        limit: int = 10,
    ) -> str: ...

    async def tool_read(self, *, uri: str, level: str = "overview") -> str: ...

    async def tool_browse(self, *, action: str, path: str = "viking://") -> str: ...

    async def tool_remember(
        self,
        *,
        session_key: str,
        content: str,
        category: str = "",
    ) -> str: ...

    async def tool_add_resource(self, *, url: str, reason: str = "") -> str: ...


class _OpenVikingTool(Tool):
    def __init__(self, service: OpenVikingToolServiceProtocol) -> None:
        self.service = service
        self._ctx: TurnContext | None = None

    def on_turn_start(self, ctx: "TurnContext") -> None:
        self._ctx = ctx

    def _session_key(self) -> str:
        if self._ctx is None:
            raise RuntimeError("Turn context is not available")
        if self._ctx.session_key:
            return self._ctx.session_key
        if self._ctx.channel and self._ctx.chat_id:
            return f"{self._ctx.channel}:{self._ctx.chat_id}"
        raise RuntimeError("Turn context is not available")


class VikingSearchTool(_OpenVikingTool):
    @property
    def name(self) -> str:
        return "viking_search"

    @property
    def description(self) -> str:
        return (
            "Semantic search over the OpenViking knowledge base. Returns ranked viking:// "
            "results for deeper reading. Use mode='deep' for complex queries and 'fast' for "
            "simple lookups."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
                "mode": {
                    "type": "string",
                    "enum": ["auto", "fast", "deep"],
                    "description": "Search depth.",
                },
                "scope": {
                    "type": "string",
                    "description": "Optional viking:// prefix to scope search.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results to return.",
                    "minimum": 1,
                    "maximum": 20,
                },
            },
            "required": ["query"],
        }

    @property
    def concurrency_mode(self) -> Literal["read_only"]:
        return "read_only"

    async def execute(self, **kwargs: object) -> str:
        query = str(kwargs.get("query", ""))
        mode = str(kwargs.get("mode", "auto") or "auto")
        scope = str(kwargs.get("scope", "") or "")
        limit = kwargs.get("limit", 10)
        limit_value = limit if isinstance(limit, int) else int(str(limit or 10))
        return await self.service.tool_search(
            session_key=self._session_key(),
            query=query,
            mode=mode,
            scope=scope,
            limit=limit_value,
        )


class VikingReadTool(_OpenVikingTool):
    @property
    def name(self) -> str:
        return "viking_read"

    @property
    def description(self) -> str:
        return (
            "Read content at a viking:// URI. Use abstract for a short summary, overview for "
            "key points, and full only when you need complete content."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "uri": {"type": "string", "description": "viking:// URI to read."},
                "level": {
                    "type": "string",
                    "enum": ["abstract", "overview", "full"],
                    "description": "Detail level.",
                },
            },
            "required": ["uri"],
        }

    @property
    def concurrency_mode(self) -> Literal["read_only"]:
        return "read_only"

    async def execute(self, **kwargs: object) -> str:
        uri = str(kwargs.get("uri", ""))
        level = str(kwargs.get("level", "overview") or "overview")
        return await self.service.tool_read(uri=uri, level=level)


class VikingBrowseTool(_OpenVikingTool):
    @property
    def name(self) -> str:
        return "viking_browse"

    @property
    def description(self) -> str:
        return (
            "Browse the OpenViking store like a filesystem using list, tree, or stat over "
            "viking:// paths."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["tree", "list", "stat"],
                    "description": "Browse action.",
                },
                "path": {
                    "type": "string",
                    "description": "Viking URI path to browse.",
                },
            },
            "required": ["action"],
        }

    @property
    def concurrency_mode(self) -> Literal["read_only"]:
        return "read_only"

    async def execute(self, **kwargs: object) -> str:
        action = str(kwargs.get("action", "list") or "list")
        path = str(kwargs.get("path", "viking://") or "viking://")
        return await self.service.tool_browse(action=action, path=path)


class VikingRememberTool(_OpenVikingTool):
    @property
    def name(self) -> str:
        return "viking_remember"

    @property
    def description(self) -> str:
        return (
            "Store an important fact in OpenViking for extraction on session commit. Use this "
            "for durable preferences, entities, events, cases, or patterns."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The information to remember."},
                "category": {
                    "type": "string",
                    "enum": ["preference", "entity", "event", "case", "pattern"],
                    "description": "Optional memory category hint.",
                },
            },
            "required": ["content"],
        }

    @property
    def concurrency_mode(self) -> Literal["mutating"]:
        return "mutating"

    async def execute(self, **kwargs: object) -> str:
        content = str(kwargs.get("content", ""))
        category = str(kwargs.get("category", "") or "")
        return await self.service.tool_remember(
            session_key=self._session_key(),
            content=content,
            category=category,
        )


class VikingAddResourceTool(_OpenVikingTool):
    @property
    def name(self) -> str:
        return "viking_add_resource"

    @property
    def description(self) -> str:
        return "Add a URL or document to OpenViking so it can be indexed and searched later."

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL or local path to ingest."},
                "reason": {
                    "type": "string",
                    "description": "Why this resource matters.",
                },
            },
            "required": ["url"],
        }

    @property
    def concurrency_mode(self) -> Literal["mutating"]:
        return "mutating"

    async def execute(self, **kwargs: object) -> str:
        url = str(kwargs.get("url", ""))
        reason = str(kwargs.get("reason", "") or "")
        return await self.service.tool_add_resource(url=url, reason=reason)
