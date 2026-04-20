from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from aeloon.core.session.manager import Session, SessionManager
from aeloon.memory.types import MemoryRuntimeDeps
from aeloon.providers.base import LLMProvider, LLMResponse


class _DummyProvider(LLMProvider):
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
        return LLMResponse(content=None)

    def get_default_model(self) -> str:
        return "test-model"


def _make_deps(tmp_path: Path) -> MemoryRuntimeDeps:
    return MemoryRuntimeDeps(
        workspace=tmp_path,
        provider=_DummyProvider(),
        model="test-model",
        sessions=SessionManager(tmp_path),
        context_window_tokens=4096,
        build_messages=lambda **_kwargs: [],
        get_tool_definitions=lambda: [],
    )


@dataclass(slots=True)
class FakeMatchedContext:
    uri: str
    abstract: str = ""
    overview: str | None = None
    category: str = ""
    score: float = 0.0
    match_reason: str = ""


@dataclass(slots=True)
class FakeFindResult:
    memories: list[FakeMatchedContext] = field(default_factory=list)
    resources: list[FakeMatchedContext] = field(default_factory=list)
    skills: list[FakeMatchedContext] = field(default_factory=list)


@dataclass(slots=True)
class FakeOpenVikingSession:
    session_id: str
    existing_session_ids: set[str]
    broken_session_ids: set[str]
    messages: list[tuple[str, str | None]] = field(default_factory=list)
    parts_history: list[list[dict[str, object]] | None] = field(default_factory=list)
    commit_calls: int = 0

    async def ensure_exists(self) -> None:
        self.existing_session_ids.add(self.session_id)
        self.broken_session_ids.discard(self.session_id)

    async def add_message(
        self,
        role: str,
        content: str | None = None,
        parts: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        self.messages.append((role, content))
        self.parts_history.append(parts)
        return {"session_id": self.session_id, "message_count": len(self.messages)}

    async def commit(self) -> dict[str, object]:
        self.commit_calls += 1
        return {"status": "committed", "session_id": self.session_id}

    async def delete(self) -> None:
        self.existing_session_ids.discard(self.session_id)


@dataclass(slots=True)
class FakeOpenVikingClient:
    path: str | None
    search_result: FakeFindResult = field(default_factory=FakeFindResult)
    find_result: FakeFindResult = field(default_factory=FakeFindResult)
    abstract_result: str = "abstract"
    overview_result: str = "overview"
    read_result: str = "full"
    ls_result: list[dict[str, object]] = field(default_factory=list)
    tree_result: list[dict[str, object]] = field(default_factory=list)
    stat_result: dict[str, object] = field(default_factory=dict)
    add_resource_result: dict[str, object] = field(default_factory=dict)
    initialized: bool = False
    closed: bool = False
    search_calls: list[dict[str, object]] = field(default_factory=list)
    find_calls: list[dict[str, object]] = field(default_factory=list)
    abstract_calls: list[str] = field(default_factory=list)
    overview_calls: list[str] = field(default_factory=list)
    read_calls: list[str] = field(default_factory=list)
    ls_calls: list[str] = field(default_factory=list)
    tree_calls: list[str] = field(default_factory=list)
    stat_calls: list[str] = field(default_factory=list)
    add_resource_calls: list[dict[str, object]] = field(default_factory=list)
    wait_processed_calls: list[float | None] = field(default_factory=list)
    commit_session_calls: list[str] = field(default_factory=list)
    delete_session_calls: list[str] = field(default_factory=list)
    sessions: dict[str, FakeOpenVikingSession] = field(default_factory=dict)
    existing_session_ids: set[str] = field(default_factory=set)
    broken_session_ids: set[str] = field(default_factory=set)

    async def initialize(self) -> None:
        self.initialized = True

    async def close(self) -> None:
        self.closed = True

    def session(
        self,
        session_id: str | None = None,
        must_exist: bool = False,
    ) -> FakeOpenVikingSession:
        resolved = session_id or "generated"
        if must_exist and resolved not in self.existing_session_ids:
            raise RuntimeError(f"missing session: {resolved}")
        session = self.sessions.get(resolved)
        if session is None:
            session = FakeOpenVikingSession(
                session_id=resolved,
                existing_session_ids=self.existing_session_ids,
                broken_session_ids=self.broken_session_ids,
            )
            self.sessions[resolved] = session
        return session

    async def session_exists(self, session_id: str) -> bool:
        return session_id in self.existing_session_ids

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str | None = None,
        parts: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        if session_id in self.broken_session_ids:
            raise OSError(
                f"Failed to append to file viking://session/default/{session_id}/messages.jsonl: not found"
            )
        self.existing_session_ids.add(session_id)
        return await self.session(session_id).add_message(role=role, content=content, parts=parts)

    async def commit_session(self, session_id: str) -> dict[str, object]:
        self.commit_session_calls.append(session_id)
        return await self.session(session_id).commit()

    async def delete_session(self, session_id: str) -> None:
        self.delete_session_calls.append(session_id)
        self.sessions.pop(session_id, None)
        self.existing_session_ids.discard(session_id)
        self.broken_session_ids.discard(session_id)

    async def search(
        self,
        query: str,
        target_uri: str = "",
        session: FakeOpenVikingSession | None = None,
        session_id: str | None = None,
        limit: int = 10,
        score_threshold: float | None = None,
        filter: dict[str, object] | None = None,
        telemetry: bool = False,
    ) -> FakeFindResult:
        self.search_calls.append(
            {
                "query": query,
                "target_uri": target_uri,
                "session_id": session_id,
                "limit": limit,
                "score_threshold": score_threshold,
            }
        )
        return self.search_result

    async def find(
        self,
        query: str,
        target_uri: str = "",
        limit: int = 10,
        mode: str = "auto",
        score_threshold: float | None = None,
        filter: dict[str, object] | None = None,
        telemetry: bool = False,
    ) -> FakeFindResult:
        self.find_calls.append(
            {
                "query": query,
                "target_uri": target_uri,
                "limit": limit,
                "mode": mode,
                "score_threshold": score_threshold,
            }
        )
        return self.find_result

    async def abstract(self, uri: str) -> str:
        self.abstract_calls.append(uri)
        return self.abstract_result

    async def overview(self, uri: str) -> str:
        self.overview_calls.append(uri)
        return self.overview_result

    async def read(self, uri: str, offset: int = 0, limit: int = -1) -> str:
        self.read_calls.append(uri)
        return self.read_result

    async def ls(self, uri: str) -> list[dict[str, object]]:
        self.ls_calls.append(uri)
        return self.ls_result

    async def tree(self, uri: str) -> list[dict[str, object]]:
        self.tree_calls.append(uri)
        return self.tree_result

    async def stat(self, uri: str) -> dict[str, object]:
        self.stat_calls.append(uri)
        session_id = _session_id_from_uri(uri)
        if session_id not in self.existing_session_ids:
            raise OSError(f"not found: {uri}")
        if uri.endswith("/messages.jsonl") and session_id in self.broken_session_ids:
            raise OSError(f"not found: {uri}")
        return self.stat_result

    async def add_resource(
        self,
        path: str,
        reason: str = "",
    ) -> dict[str, object]:
        self.add_resource_calls.append({"path": path, "reason": reason})
        return self.add_resource_result

    async def wait_processed(self, timeout: float | None = None) -> dict[str, object]:
        self.wait_processed_calls.append(timeout)
        return {"status": "processed"}


@dataclass(slots=True)
class FakeOpenVikingFactory:
    clients: list[FakeOpenVikingClient] = field(default_factory=list)
    reset_calls: int = 0
    default_search_result: FakeFindResult = field(default_factory=FakeFindResult)
    default_find_result: FakeFindResult = field(default_factory=FakeFindResult)
    default_abstract_result: str = "abstract"
    default_overview_result: str = "overview"
    default_read_result: str = "full"
    default_ls_result: list[dict[str, object]] = field(default_factory=list)
    default_tree_result: list[dict[str, object]] = field(default_factory=list)
    default_stat_result: dict[str, object] = field(default_factory=dict)
    default_add_resource_result: dict[str, object] = field(default_factory=dict)
    shared_sessions: dict[str, FakeOpenVikingSession] = field(default_factory=dict)
    shared_existing_session_ids: set[str] = field(default_factory=set)
    shared_broken_session_ids: set[str] = field(default_factory=set)

    def __call__(self, path: str | None = None) -> FakeOpenVikingClient:
        client = FakeOpenVikingClient(
            path=path,
            search_result=self.default_search_result,
            find_result=self.default_find_result,
            abstract_result=self.default_abstract_result,
            overview_result=self.default_overview_result,
            read_result=self.default_read_result,
            ls_result=self.default_ls_result,
            tree_result=self.default_tree_result,
            stat_result=self.default_stat_result,
            add_resource_result=self.default_add_resource_result,
            sessions=self.shared_sessions,
            existing_session_ids=self.shared_existing_session_ids,
            broken_session_ids=self.shared_broken_session_ids,
        )
        self.clients.append(client)
        return client

    async def reset(self) -> None:
        self.reset_calls += 1


@dataclass(slots=True)
class FakeOpenVikingConfigSingleton:
    initialize_calls: list[dict[str, object]] = field(default_factory=list)
    reset_calls: int = 0

    def initialize(
        self,
        config_dict: dict[str, object] | None = None,
        config_path: str | None = None,
    ) -> object:
        call: dict[str, object] = {}
        if config_dict is not None:
            call["config_dict"] = config_dict
        if config_path is not None:
            call["config_path"] = config_path
        self.initialize_calls.append(call)
        return call

    def reset_instance(self) -> None:
        self.reset_calls += 1


def _install_fake_runtime(monkeypatch: pytest.MonkeyPatch):
    import aeloon.memory.providers.openviking_service as service_module

    factory = FakeOpenVikingFactory()
    config_singleton = FakeOpenVikingConfigSingleton()
    runtime = service_module.OpenVikingRuntime(
        async_openviking_cls=cast(service_module.OpenVikingClientFactoryProtocol, factory),
        config_singleton=cast(service_module.OpenVikingConfigSingletonProtocol, config_singleton),
    )
    monkeypatch.setattr(service_module, "_load_openviking_runtime", lambda: runtime)
    return factory, config_singleton


def _session_id_from_uri(uri: str) -> str:
    parts = uri.rstrip("/").split("/")
    if uri.endswith("/messages.jsonl"):
        return parts[-2]
    return parts[-1]


def test_openviking_provider_config_rejects_non_leaf_storage_subdir() -> None:
    from aeloon.memory.providers.openviking import OpenVikingProviderConfig

    with pytest.raises(ValidationError, match="storageSubdir"):
        OpenVikingProviderConfig.model_validate(
            {
                "storageSubdir": "../outside",
                "ovConfig": {"storage": {"agfs": {"port": 1833}}},
            }
        )


@pytest.mark.asyncio
async def test_openviking_provider_prefetch_returns_background_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aeloon.memory.providers.openviking import OpenVikingProvider

    factory, _ = _install_fake_runtime(monkeypatch)
    factory.default_search_result = FakeFindResult(
        memories=[
            FakeMatchedContext(
                uri="viking://memories/alpha",
                abstract="Format changes should preserve the existing layout.",
                category="memory",
                score=0.9,
            )
        ]
    )
    provider = OpenVikingProvider(
        {"ovConfig": {"storage": {"agfs": {"port": 1833}}}},
        _make_deps(tmp_path),
    )

    text = await provider.prefetch(
        session=Session(key="cli:test"),
        query="format changes",
        channel="cli",
        chat_id="direct",
        current_role="user",
    )

    assert "OpenViking Recall" in text
    assert "Format changes should preserve the existing layout." in text
    assert factory.clients[0].search_calls[0]["query"] == "format changes"


@pytest.mark.asyncio
async def test_openviking_provider_prefetch_propagates_imported_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aeloon.memory.providers.openviking import OpenVikingProvider

    factory, config_singleton = _install_fake_runtime(monkeypatch)
    factory.default_search_result = FakeFindResult()
    provider = OpenVikingProvider(
        {
            "mode": "embedded",
            "configPath": "/tmp/ov.conf",
            "ovConfig": {
                "storage": {"agfs": {"port": 1833}},
                "embedding": {"dense": {"provider": "mock", "model": "embed", "api_key": "k"}},
                "vlm": {"provider": "mock", "model": "vlm", "api_key": "k"},
            },
        },
        _make_deps(tmp_path),
    )

    await provider.prefetch(
        session=Session(key="cli:test"),
        query="hello",
        channel="cli",
        chat_id="direct",
        current_role="user",
    )

    config_dict = cast(
        dict[str, object],
        config_singleton.initialize_calls[0]["config_dict"],
    )
    storage = cast(dict[str, object], config_dict["storage"])

    assert cast(dict[str, object], config_dict["embedding"])["dense"] == {
        "provider": "mock",
        "model": "embed",
        "api_key": "k",
    }
    assert config_dict["vlm"] == {"provider": "mock", "model": "vlm", "api_key": "k"}
    assert storage["workspace"] == str(tmp_path / "memory" / "openviking_memory")


@pytest.mark.asyncio
async def test_openviking_provider_sync_turn_mirrors_messages_to_live_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aeloon.memory.providers.openviking import OpenVikingProvider

    factory, _ = _install_fake_runtime(monkeypatch)
    provider = OpenVikingProvider(
        {"ovConfig": {"storage": {"agfs": {"port": 1833}}}},
        _make_deps(tmp_path),
    )
    session = Session(key="cli:test")
    session.messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ]

    await provider.sync_turn(
        session=session,
        raw_new_messages=[],
        persisted_new_messages=list(session.messages),
        final_content="world",
    )

    client = factory.clients[0]
    live_session_id = provider.service._read_state(session)["liveSessionId"]
    live_session = client.sessions[live_session_id]
    assert live_session.messages == [("user", "hello"), ("assistant", "world")]


@pytest.mark.asyncio
async def test_openviking_provider_on_pre_compress_archives_pending_messages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aeloon.memory.providers.openviking import OpenVikingProvider

    factory, _ = _install_fake_runtime(monkeypatch)
    provider = OpenVikingProvider(
        {
            "ovConfig": {"storage": {"agfs": {"port": 1833}}},
            "waitProcessedTimeoutS": 18.0,
        },
        _make_deps(tmp_path),
    )

    await provider.on_pre_compress(
        session=Session(key="cli:test"),
        pending_messages=[{"role": "user", "content": "pending"}],
    )

    client = factory.clients[0]
    assert len(client.commit_session_calls) == 1
    assert client.commit_session_calls[0].startswith("aeloon-archive-cli_test-")
    assert client.wait_processed_calls == [18.0]
    assert client.delete_session_calls == []


def test_openviking_provider_exposes_tool_surface(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aeloon.memory.providers.openviking import OpenVikingProvider

    _install_fake_runtime(monkeypatch)
    provider = OpenVikingProvider({"ovConfig": {"storage": {}}}, _make_deps(tmp_path))

    assert provider.always_skill_names() == []
    assert [tool.name for tool in provider.build_tools()] == [
        "viking_search",
        "viking_read",
        "viking_browse",
        "viking_remember",
        "viking_add_resource",
    ]
    assert "viking_search" in provider.system_prompt_block()
    assert "viking_add_resource" in provider.system_prompt_block()


@pytest.mark.asyncio
async def test_openviking_provider_on_memory_write_mirrors_prompt_memory_notes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aeloon.memory.providers.openviking import OpenVikingProvider

    factory, _ = _install_fake_runtime(monkeypatch)
    provider = OpenVikingProvider({"ovConfig": {"storage": {}}}, _make_deps(tmp_path))

    await provider.on_memory_write(
        action="add",
        target="memory",
        content="Project prefers terse summaries.",
        session_key="cli:test",
    )

    client = factory.clients[0]
    live_session = client.sessions[
        provider.service._read_state(provider.service.sessions.get_or_create("cli:test"))[
            "liveSessionId"
        ]
    ]
    assert live_session.messages == [("user", None)]
    assert live_session.parts_history[0] == [
        {
            "type": "text",
            "text": "[Memory note — memory] Project prefers terse summaries.",
        }
    ]


@pytest.mark.asyncio
async def test_openviking_provider_on_session_end_commits_archive_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aeloon.memory.providers.openviking import OpenVikingProvider

    factory, _ = _install_fake_runtime(monkeypatch)
    provider = OpenVikingProvider(
        {
            "ovConfig": {"storage": {}},
            "waitProcessedTimeoutS": 12.0,
        },
        _make_deps(tmp_path),
    )
    session = Session(key="cli:test")
    session.messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ]

    await provider.sync_turn(
        session=session,
        raw_new_messages=[],
        persisted_new_messages=list(session.messages),
        final_content="world",
    )
    await provider.on_session_end(
        session=session,
        pending_messages=list(session.messages),
        reason="shutdown",
    )

    client = factory.clients[0]
    assert len(client.commit_session_calls) == 1
    assert client.commit_session_calls[0].startswith("aeloon-archive-cli_test-")
    assert client.wait_processed_calls == [12.0]
    assert client.delete_session_calls == []


@pytest.mark.asyncio
async def test_openviking_provider_http_mode_supports_tool_helpers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import httpx

    import aeloon.memory.providers.openviking_service as service_module
    from aeloon.memory.providers.openviking import OpenVikingProvider

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/search/find":
            body = json.loads(request.content.decode("utf-8"))
            assert body["mode"] == "deep"
            return httpx.Response(
                200,
                json={
                    "result": {
                        "memories": [
                            {
                                "uri": "viking://memories/test",
                                "abstract": "Search hit",
                                "score": 0.9,
                            }
                        ]
                    }
                },
            )
        if request.url.path == "/api/v1/content/overview":
            return httpx.Response(200, json={"result": "Overview text"})
        if request.url.path == "/api/v1/fs/tree":
            return httpx.Response(
                200,
                json={"result": [{"rel_path": "docs", "uri": "viking://resources/docs/"}]},
            )
        if request.url.path == "/api/v1/resources":
            return httpx.Response(200, json={"result": {"root_uri": "viking://resources/test"}})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    transport = httpx.MockTransport(handler)
    original_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        service_module.httpx,
        "AsyncClient",
        lambda **kwargs: original_async_client(
            transport=transport,
            base_url=kwargs["base_url"],
            headers=kwargs.get("headers"),
        ),
    )
    provider = OpenVikingProvider(
        {
            "mode": "http",
            "endpoint": "http://127.0.0.1:1933",
            "apiKey": "secret",
            "ovConfig": {"storage": {}},
        },
        _make_deps(tmp_path),
    )

    search_result = json.loads(
        await provider.service.tool_search(
            session_key="cli:test",
            query="hello",
            mode="deep",
        )
    )
    read_result = json.loads(
        await provider.service.tool_read(uri="viking://resources/test", level="overview")
    )
    browse_result = json.loads(
        await provider.service.tool_browse(action="tree", path="viking://resources/")
    )
    add_result = json.loads(
        await provider.service.tool_add_resource(url="https://example.com/doc", reason="context")
    )

    assert search_result["results"][0]["uri"] == "viking://memories/test"
    assert read_result["content"] == "Overview text"
    assert browse_result["entries"][0]["uri"] == "viking://resources/docs/"
    assert add_result["root_uri"] == "viking://resources/test"
    await provider.shutdown()


@pytest.mark.asyncio
async def test_openviking_provider_shutdown_closes_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aeloon.memory.providers.openviking import OpenVikingProvider

    factory, config_singleton = _install_fake_runtime(monkeypatch)
    provider = OpenVikingProvider(
        {"ovConfig": {"storage": {"agfs": {"port": 1833}}}},
        _make_deps(tmp_path),
    )
    await provider.prefetch(
        session=Session(key="cli:test"),
        query="hello",
        channel="cli",
        chat_id="direct",
        current_role="user",
    )

    await provider.shutdown()

    assert factory.clients[0].closed is True
    assert factory.reset_calls == 1
    assert config_singleton.reset_calls == 1
