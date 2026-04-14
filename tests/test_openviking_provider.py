from __future__ import annotations

import asyncio
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
    messages: list[tuple[str, str | None]] = field(default_factory=list)
    commit_calls: int = 0

    async def ensure_exists(self) -> None:
        self.existing_session_ids.add(self.session_id)

    async def add_message(
        self,
        role: str,
        content: str | None = None,
        parts: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        self.messages.append((role, content))
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
    initialized: bool = False
    closed: bool = False
    search_calls: list[dict[str, object]] = field(default_factory=list)
    find_calls: list[dict[str, object]] = field(default_factory=list)
    wait_processed_calls: list[float | None] = field(default_factory=list)
    commit_session_calls: list[str] = field(default_factory=list)
    delete_session_calls: list[str] = field(default_factory=list)
    sessions: dict[str, FakeOpenVikingSession] = field(default_factory=dict)
    existing_session_ids: set[str] = field(default_factory=set)

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
        self.existing_session_ids.add(session_id)
        return await self.session(session_id).add_message(role=role, content=content, parts=parts)

    async def commit_session(self, session_id: str) -> dict[str, object]:
        self.commit_session_calls.append(session_id)
        return await self.session(session_id).commit()

    async def delete_session(self, session_id: str) -> None:
        self.delete_session_calls.append(session_id)
        self.sessions.pop(session_id, None)
        self.existing_session_ids.discard(session_id)

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
        score_threshold: float | None = None,
        filter: dict[str, object] | None = None,
        telemetry: bool = False,
    ) -> FakeFindResult:
        self.find_calls.append(
            {
                "query": query,
                "target_uri": target_uri,
                "limit": limit,
                "score_threshold": score_threshold,
            }
        )
        return self.find_result

    async def wait_processed(self, timeout: float | None = None) -> dict[str, object]:
        self.wait_processed_calls.append(timeout)
        return {"status": "processed"}


@dataclass(slots=True)
class FakeOpenVikingFactory:
    clients: list[FakeOpenVikingClient] = field(default_factory=list)
    reset_calls: int = 0
    default_search_result: FakeFindResult = field(default_factory=FakeFindResult)
    default_find_result: FakeFindResult = field(default_factory=FakeFindResult)
    shared_sessions: dict[str, FakeOpenVikingSession] = field(default_factory=dict)
    shared_existing_session_ids: set[str] = field(default_factory=set)

    def __call__(self, path: str | None = None) -> FakeOpenVikingClient:
        client = FakeOpenVikingClient(
            path=path,
            search_result=self.default_search_result,
            find_result=self.default_find_result,
            sessions=self.shared_sessions,
            existing_session_ids=self.shared_existing_session_ids,
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
        config_singleton=cast(
            service_module.OpenVikingConfigSingletonProtocol, config_singleton
        ),
    )
    monkeypatch.setattr(service_module, "_load_openviking_runtime", lambda: runtime)
    return factory, config_singleton


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
    live_session = client.sessions["aeloon-live-cli_test"]
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
    assert client.commit_session_calls
    assert client.wait_processed_calls == [18.0]
    assert client.delete_session_calls == ["aeloon-live-cli_test"]


@pytest.mark.asyncio
async def test_openviking_provider_rejects_http_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aeloon.memory.providers.openviking import OpenVikingProvider

    _install_fake_runtime(monkeypatch)
    provider = OpenVikingProvider(
        {
            "mode": "http",
            "configPath": "/tmp/ov.conf",
            "ovConfig": {
                "storage": {"agfs": {"port": 1833}},
                "embedding": {"dense": {"provider": "mock", "model": "embed", "api_key": "k"}},
                "vlm": {"provider": "mock", "model": "vlm", "api_key": "k"},
            },
        },
        _make_deps(tmp_path),
    )

    with pytest.raises(RuntimeError, match="HTTP mode is not implemented"):
        await provider.prefetch(
            session=Session(key="cli:test"),
            query="hello",
            channel="cli",
            chat_id="direct",
            current_role="user",
        )


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
