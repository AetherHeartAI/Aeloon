from __future__ import annotations

import json

import pytest

from aeloon.core.agent.tools.registry import ToolRegistry
from aeloon.core.agent.turn import TurnContext


class _RecordingService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def tool_search(
        self,
        *,
        session_key: str,
        query: str,
        mode: str = "auto",
        scope: str = "",
        limit: int = 10,
    ) -> str:
        self.calls.append(
            (
                "search",
                {
                    "session_key": session_key,
                    "query": query,
                    "mode": mode,
                    "scope": scope,
                    "limit": limit,
                },
            )
        )
        return json.dumps({"results": [{"uri": "viking://memories/test"}]}, ensure_ascii=False)

    async def tool_read(self, *, uri: str, level: str = "overview") -> str:
        self.calls.append(("read", {"uri": uri, "level": level}))
        return json.dumps({"uri": uri, "level": level, "content": "overview"}, ensure_ascii=False)

    async def tool_browse(self, *, action: str, path: str = "viking://") -> str:
        self.calls.append(("browse", {"action": action, "path": path}))
        return json.dumps({"action": action, "path": path}, ensure_ascii=False)

    async def tool_remember(
        self,
        *,
        session_key: str,
        content: str,
        category: str = "",
    ) -> str:
        self.calls.append(
            (
                "remember",
                {
                    "session_key": session_key,
                    "content": content,
                    "category": category,
                },
            )
        )
        return json.dumps({"status": "stored"}, ensure_ascii=False)

    async def tool_add_resource(self, *, url: str, reason: str = "") -> str:
        self.calls.append(("add_resource", {"url": url, "reason": reason}))
        return json.dumps(
            {"status": "added", "root_uri": "viking://resources/test"}, ensure_ascii=False
        )


def _turn_context() -> TurnContext:
    return TurnContext(channel="cli", chat_id="direct", session_key="cli:test")


@pytest.mark.asyncio
async def test_viking_search_tool_casts_and_executes() -> None:
    from aeloon.memory.providers.openviking_tools import VikingSearchTool

    service = _RecordingService()
    registry = ToolRegistry()
    registry.register(VikingSearchTool(service))
    registry.notify_turn_start(_turn_context())

    payload = json.loads(
        await registry.execute(
            "viking_search",
            {"query": "hello", "mode": "deep", "limit": "2", "scope": "viking://resources/"},
        )
    )

    assert payload["results"][0]["uri"] == "viking://memories/test"
    assert service.calls == [
        (
            "search",
            {
                "session_key": "cli:test",
                "query": "hello",
                "mode": "deep",
                "scope": "viking://resources/",
                "limit": 2,
            },
        )
    ]


@pytest.mark.asyncio
async def test_viking_read_tool_routes_requested_level() -> None:
    from aeloon.memory.providers.openviking_tools import VikingReadTool

    service = _RecordingService()
    registry = ToolRegistry()
    registry.register(VikingReadTool(service))
    registry.notify_turn_start(_turn_context())

    payload = json.loads(
        await registry.execute(
            "viking_read",
            {"uri": "viking://resources/test", "level": "full"},
        )
    )

    assert payload["level"] == "full"
    assert service.calls == [
        ("read", {"uri": "viking://resources/test", "level": "full"}),
    ]


@pytest.mark.asyncio
async def test_viking_browse_tool_routes_requested_action() -> None:
    from aeloon.memory.providers.openviking_tools import VikingBrowseTool

    service = _RecordingService()
    registry = ToolRegistry()
    registry.register(VikingBrowseTool(service))
    registry.notify_turn_start(_turn_context())

    payload = json.loads(
        await registry.execute(
            "viking_browse",
            {"action": "tree", "path": "viking://resources/"},
        )
    )

    assert payload["action"] == "tree"
    assert service.calls == [
        ("browse", {"action": "tree", "path": "viking://resources/"}),
    ]


@pytest.mark.asyncio
async def test_viking_remember_tool_requires_turn_context() -> None:
    from aeloon.memory.providers.openviking_tools import VikingRememberTool

    service = _RecordingService()
    tool = VikingRememberTool(service)

    with pytest.raises(RuntimeError, match="Turn context is not available"):
        await tool.execute(content="remember this")


@pytest.mark.asyncio
async def test_viking_mutating_tools_execute_with_session_context() -> None:
    from aeloon.memory.providers.openviking_tools import VikingAddResourceTool, VikingRememberTool

    service = _RecordingService()
    remember = VikingRememberTool(service)
    add_resource = VikingAddResourceTool(service)
    remember.on_turn_start(_turn_context())
    add_resource.on_turn_start(_turn_context())

    remember_payload = json.loads(
        await remember.execute(content="Important fact", category="pattern")
    )
    add_payload = json.loads(
        await add_resource.execute(url="https://example.com/doc", reason="reference")
    )

    assert remember.concurrency_mode == "mutating"
    assert add_resource.concurrency_mode == "mutating"
    assert remember_payload["status"] == "stored"
    assert add_payload["root_uri"] == "viking://resources/test"
    assert service.calls == [
        (
            "remember",
            {
                "session_key": "cli:test",
                "content": "Important fact",
                "category": "pattern",
            },
        ),
        (
            "add_resource",
            {
                "url": "https://example.com/doc",
                "reason": "reference",
            },
        ),
    ]
