from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic import ValidationError

from aeloon.core.agent.context import ContextBuilder
from aeloon.core.config.schema import Config
from aeloon.core.session.manager import Session
from aeloon.providers.base import LLMProvider, LLMResponse


class DummyProvider(LLMProvider):
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


def _build_messages_for_estimate(
    *,
    history: list[dict[str, object]],
    current_message: str,
    **_: object,
) -> list[dict[str, object]]:
    return [*history, {"role": "user", "content": current_message}]


def _make_deps(tmp_path: Path, *, context_window_tokens: int = 4096):
    from aeloon.core.session.manager import SessionManager
    from aeloon.memory.base import MemoryBackendDeps

    return MemoryBackendDeps(
        workspace=tmp_path,
        provider=DummyProvider(),
        model="test-model",
        sessions=SessionManager(tmp_path),
        context_window_tokens=context_window_tokens,
        build_messages=_build_messages_for_estimate,
        get_tool_definitions=lambda: [],
    )


@pytest.fixture(autouse=True)
def _patch_openviking_log_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aeloon.memory.backends import openviking as openviking_module

    monkeypatch.setattr(openviking_module, "get_logs_dir", lambda: tmp_path / "logs")


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
    query_plan: object | None = None
    total: int = 0

    def __post_init__(self) -> None:
        self.total = len(self.memories) + len(self.resources) + len(self.skills)


@dataclass(slots=True)
class FakeOpenVikingSession:
    session_id: str
    existing_session_ids: set[str]
    messages: list[tuple[str, str | None]] = field(default_factory=list)
    commit_calls: int = 0
    deleted: bool = False

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
        self.deleted = True


@dataclass(slots=True)
class FakeOpenVikingClient:
    path: str | None
    search_result: FakeFindResult = field(default_factory=FakeFindResult)
    find_result: FakeFindResult = field(default_factory=FakeFindResult)
    search_results_by_target: dict[str, FakeFindResult] = field(default_factory=dict)
    find_results_by_target: dict[str, FakeFindResult] = field(default_factory=dict)
    initialized: bool = False
    closed: bool = False
    search_calls: list[dict[str, object]] = field(default_factory=list)
    find_calls: list[dict[str, object]] = field(default_factory=list)
    wait_processed_calls: list[float | None] = field(default_factory=list)
    commit_session_calls: list[str] = field(default_factory=list)
    delete_session_calls: list[str] = field(default_factory=list)
    sessions: dict[str, FakeOpenVikingSession] = field(default_factory=dict)
    existing_session_ids: set[str] = field(default_factory=set)
    require_existing_session_for_add: bool = False
    wait_processed_release: asyncio.Event | None = None
    wait_processed_entered: asyncio.Event | None = None

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
        if self.require_existing_session_for_add and session_id not in self.existing_session_ids:
            raise OSError(f"missing session root for {session_id}")
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
        return self.search_results_by_target.get(target_uri, self.search_result)

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
        return self.find_results_by_target.get(target_uri, self.find_result)

    async def wait_processed(self, timeout: float | None = None) -> dict[str, object]:
        if self.wait_processed_entered is not None:
            self.wait_processed_entered.set()
        if self.wait_processed_release is not None:
            await self.wait_processed_release.wait()
        self.wait_processed_calls.append(timeout)
        return {"status": "processed"}


@dataclass(slots=True)
class FakeOpenVikingFactory:
    clients: list[FakeOpenVikingClient] = field(default_factory=list)
    reset_calls: int = 0
    default_search_result: FakeFindResult = field(default_factory=FakeFindResult)
    default_find_result: FakeFindResult = field(default_factory=FakeFindResult)
    default_search_results_by_target: dict[str, FakeFindResult] = field(default_factory=dict)
    default_find_results_by_target: dict[str, FakeFindResult] = field(default_factory=dict)
    default_client_kwargs: dict[str, bool] = field(default_factory=dict)
    shared_sessions: dict[str, FakeOpenVikingSession] = field(default_factory=dict)
    shared_existing_session_ids: set[str] = field(default_factory=set)

    def __call__(self, path: str | None = None) -> FakeOpenVikingClient:
        client = FakeOpenVikingClient(
            path=path,
            search_result=self.default_search_result,
            find_result=self.default_find_result,
            search_results_by_target=dict(self.default_search_results_by_target),
            find_results_by_target=dict(self.default_find_results_by_target),
            sessions=self.shared_sessions,
            existing_session_ids=self.shared_existing_session_ids,
        )
        client.require_existing_session_for_add = self.default_client_kwargs.get(
            "require_existing_session_for_add",
            False,
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
    from aeloon.memory.backends import openviking as openviking_module

    factory = FakeOpenVikingFactory()
    config_singleton = FakeOpenVikingConfigSingleton()
    runtime = openviking_module.OpenVikingRuntime(
        async_openviking_cls=cast(openviking_module.OpenVikingClientFactoryProtocol, factory),
        config_singleton=cast(openviking_module.OpenVikingConfigSingletonProtocol, config_singleton),
    )
    monkeypatch.setattr(openviking_module, "_load_openviking_runtime", lambda: runtime)
    return factory, config_singleton


def test_openviking_config_rejects_non_leaf_storage_subdir() -> None:
    from aeloon.memory.backends.openviking import OpenVikingMemoryConfig

    with pytest.raises(ValidationError, match="storageSubdir"):
        OpenVikingMemoryConfig.model_validate(
            {
                "storageSubdir": "../outside",
                "ovConfig": {"storage": {"agfs": {"port": 1833}}},
            }
        )

    with pytest.raises(ValidationError, match="storageSubdir"):
        OpenVikingMemoryConfig.model_validate(
            {
                "storageSubdir": "/tmp/openviking",
                "ovConfig": {"storage": {"agfs": {"port": 1833}}},
            }
        )


def test_openviking_config_normalizes_extra_target_uris() -> None:
    from aeloon.memory.backends.openviking import OpenVikingMemoryConfig

    config = OpenVikingMemoryConfig.model_validate(
        {
            "ovConfig": {
                "storage": {"agfs": {"port": 1833}},
                "embedding": {"dense": {"provider": "mock"}},
            },
            "extraTargetUris": [
                "  viking://session/default  ",
                "",
                "   ",
                "viking://session/default",
                "viking://session/default/archive",
            ],
        }
    )

    assert config.extra_target_uris == [
        "viking://session/default",
        "viking://session/default",
        "viking://session/default/archive",
    ]


def test_openviking_backend_missing_dependency_raises_actionable_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aeloon.memory.backends import openviking as openviking_module
    from aeloon.memory.manager import MemoryManager

    def _missing_runtime():
        raise ImportError("No module named 'openviking'")

    monkeypatch.setattr(openviking_module, "_load_openviking_runtime", _missing_runtime)

    config = Config.model_validate(
        {
            "memory": {
                "backend": "openviking",
                "backends": {
                    "file": {},
                    "openviking": {
                        "ovConfig": {"storage": {"agfs": {"port": 1833}}},
                    },
                },
            }
        }
    )

    with pytest.raises(RuntimeError, match="pip install openviking"):
        MemoryManager(memory_config=config.memory, deps=_make_deps(tmp_path))


@pytest.mark.asyncio
async def test_openviking_backend_rejects_malformed_ov_config_storage_section(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aeloon.memory.manager import MemoryManager

    _install_fake_runtime(monkeypatch)
    config = Config.model_validate(
        {
            "memory": {
                "backend": "openviking",
                "backends": {
                    "file": {},
                    "openviking": {
                        "ovConfig": {"embedding": {"dense": {"provider": "mock"}}},
                    },
                },
            }
        }
    )

    manager = MemoryManager(memory_config=config.memory, deps=_make_deps(tmp_path))
    with pytest.raises(RuntimeError, match="storage"):
        await manager.prepare_turn(
            session=Session(key="cli:test"),
            query="hello",
            channel="cli",
            chat_id="direct",
            current_role="user",
        )


def test_file_backend_does_not_load_openviking_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aeloon.memory.backends import openviking as openviking_module
    from aeloon.memory.manager import MemoryManager

    called = False

    def _fail_if_called():
        nonlocal called
        called = True
        raise AssertionError("openviking runtime should not load for file backend")

    monkeypatch.setattr(openviking_module, "_load_openviking_runtime", _fail_if_called)

    config = Config()
    manager = MemoryManager(memory_config=config.memory, deps=_make_deps(tmp_path))

    assert manager.backend.backend_name == "file"
    assert called is False


@pytest.mark.asyncio
async def test_openviking_backend_overrides_storage_root_without_mutating_source_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aeloon.memory.manager import MemoryManager

    factory, config_singleton = _install_fake_runtime(monkeypatch)
    raw_ov_config = {
        "storage": {
            "workspace": "/tmp/should-not-leak",
            "agfs": {"port": 1833},
        },
        "embedding": {"dense": {"provider": "mock"}},
    }
    config = Config.model_validate(
        {
            "memory": {
                "backend": "openviking",
                "backends": {
                    "file": {},
                    "openviking": {
                        "ovConfig": raw_ov_config,
                    },
                },
            }
        }
    )

    manager = MemoryManager(memory_config=config.memory, deps=_make_deps(tmp_path))
    prepared = await manager.prepare_turn(
        session=Session(key="cli:test"),
        query="hello",
        channel="cli",
        chat_id="direct",
        current_role="user",
    )

    expected_root = tmp_path / "memory" / "openviking_memory"
    assert factory.clients[0].path == str(expected_root)
    initialized = config_singleton.initialize_calls[0]["config_dict"]
    assert isinstance(initialized, dict)
    assert initialized["storage"]["workspace"] == str(expected_root)
    raw_storage = raw_ov_config["storage"]
    assert isinstance(raw_storage, dict)
    assert raw_storage["workspace"] == "/tmp/should-not-leak"
    assert any(str(expected_root) in line for line in prepared.runtime_lines)

    await manager.close()
    assert factory.clients[0].closed is True


@pytest.mark.asyncio
async def test_openviking_backend_injects_quiet_log_defaults_without_overriding_explicit_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aeloon.memory.backends import openviking as openviking_module
    from aeloon.memory.manager import MemoryManager

    _, config_singleton = _install_fake_runtime(monkeypatch)
    monkeypatch.setattr(openviking_module, "get_logs_dir", lambda: tmp_path / "logs")
    config = Config.model_validate(
        {
            "memory": {
                "backend": "openviking",
                "backends": {
                    "file": {},
                    "openviking": {
                        "ovConfig": {
                            "storage": {"agfs": {"port": 1833}},
                            "embedding": {"dense": {"provider": "mock"}},
                        },
                    },
                },
            }
        }
    )

    manager = MemoryManager(memory_config=config.memory, deps=_make_deps(tmp_path))
    await manager.prepare_turn(
        session=Session(key="cli:test"),
        query="hello",
        channel="cli",
        chat_id="direct",
        current_role="user",
    )

    initialized = config_singleton.initialize_calls[0]["config_dict"]
    assert isinstance(initialized, dict)
    assert initialized["log"]["level"] == "WARNING"
    assert Path(initialized["log"]["output"]).name == "openviking.log"
    assert initialized["log"]["output"] not in {"stdout", "stderr"}

    await manager.close()

    explicit_config = Config.model_validate(
        {
            "memory": {
                "backend": "openviking",
                "backends": {
                    "file": {},
                    "openviking": {
                        "ovConfig": {
                            "storage": {"agfs": {"port": 1833}},
                            "embedding": {"dense": {"provider": "mock"}},
                            "log": {
                                "level": "INFO",
                                "output": "/tmp/custom-openviking.log",
                            },
                        },
                    },
                },
            }
        }
    )

    explicit_manager = MemoryManager(memory_config=explicit_config.memory, deps=_make_deps(tmp_path))
    await explicit_manager.prepare_turn(
        session=Session(key="cli:explicit"),
        query="hello",
        channel="cli",
        chat_id="direct",
        current_role="user",
    )

    explicit_initialized = config_singleton.initialize_calls[1]["config_dict"]
    assert isinstance(explicit_initialized, dict)
    assert explicit_initialized["log"] == {
        "level": "INFO",
        "output": "/tmp/custom-openviking.log",
    }

    await explicit_manager.close()


@pytest.mark.asyncio
async def test_openviking_prepare_turn_injects_recall_and_hides_file_skill(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aeloon.memory.manager import MemoryManager

    factory, _ = _install_fake_runtime(monkeypatch)
    config = Config.model_validate(
        {
            "memory": {
                "backend": "openviking",
                "backends": {
                    "file": {},
                    "openviking": {
                        "ovConfig": {
                            "storage": {"agfs": {"port": 1833}},
                            "embedding": {"dense": {"provider": "mock"}},
                        },
                        "searchMode": "search",
                        "searchLimit": 4,
                        "scoreThreshold": 0.2,
                    },
                },
            }
        }
    )

    factory.default_search_result = FakeFindResult(
        memories=[
            FakeMatchedContext(
                uri="viking://memories/alpha",
                abstract="Alice prefers exact diffs.",
                category="memory",
                score=0.91,
                match_reason="recent preference",
            )
        ]
    )
    manager = MemoryManager(memory_config=config.memory, deps=_make_deps(tmp_path))
    prepared = await manager.prepare_turn(
        session=Session(key="cli:test"),
        query="how should I format changes?",
        channel="cli",
        chat_id="direct",
        current_role="user",
    )

    prompt = ContextBuilder(tmp_path).build_system_prompt(
        extra_system_sections=prepared.system_sections,
        runtime_lines=prepared.runtime_lines,
        extra_always_skills=prepared.always_skill_names,
        exclude_skill_names=getattr(manager.backend, "hidden_skill_names", []),
    )

    assert "OpenViking Recall" in prompt
    assert "Alice prefers exact diffs." in prompt
    assert "### Skill: openviking-memory" in prompt
    assert "### Skill: memory" not in prompt
    assert "<name>memory</name>" not in prompt
    assert "memory/MEMORY.md" not in prompt
    assert "memory/HISTORY.md" not in prompt
    assert prepared.always_skill_names == ["openviking-memory"]
    assert factory.clients[0].search_calls[0]["limit"] == 4
    assert factory.clients[0].search_calls[0]["score_threshold"] == 0.2

    await manager.close()


@pytest.mark.asyncio
async def test_openviking_backend_supports_status_line_token_estimation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aeloon.memory.manager import MemoryManager
    from aeloon.plugins._sdk.status_line import StatusLineManager

    _install_fake_runtime(monkeypatch)
    config = Config.model_validate(
        {
            "memory": {
                "backend": "openviking",
                "backends": {
                    "file": {},
                    "openviking": {
                        "ovConfig": {
                            "storage": {"agfs": {"port": 1833}},
                            "embedding": {"dense": {"provider": "mock"}},
                        },
                    },
                },
            }
        }
    )

    manager = MemoryManager(memory_config=config.memory, deps=_make_deps(tmp_path))
    session = Session(key="cli:test")
    session.messages = [{"role": "user", "content": "hello from openviking"}]
    loop = SimpleNamespace(
        sessions=SimpleNamespace(get_or_create=lambda session_key: session),
        memory_consolidator=manager.backend,
        context_window_tokens=4096,
        model="test-model",
    )

    rendered = StatusLineManager(loop).build_toolbar("cli", "test")
    rendered_text = "".join(part[1] for part in rendered)

    assert "Model:" in rendered_text
    assert "Context:" in rendered_text

    await manager.close()


@pytest.mark.asyncio
async def test_openviking_prepare_turn_uses_find_mode_when_requested(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aeloon.memory.manager import MemoryManager

    factory, _ = _install_fake_runtime(monkeypatch)
    config = Config.model_validate(
        {
            "memory": {
                "backend": "openviking",
                "backends": {
                    "file": {},
                    "openviking": {
                        "ovConfig": {
                            "storage": {"agfs": {"port": 1833}},
                            "embedding": {"dense": {"provider": "mock"}},
                        },
                        "searchMode": "find",
                        "targetUri": "viking://memory/",
                    },
                },
            }
        }
    )

    manager = MemoryManager(memory_config=config.memory, deps=_make_deps(tmp_path))
    await manager.prepare_turn(
        session=Session(key="cli:test"),
        query="project rules",
        channel="cli",
        chat_id="direct",
        current_role="user",
    )

    assert factory.clients[0].find_calls[0]["query"] == "project rules"
    assert factory.clients[0].find_calls[0]["target_uri"] == "viking://memory/"
    assert factory.clients[0].search_calls == []

    await manager.close()


@pytest.mark.asyncio
async def test_openviking_prepare_turn_searches_primary_and_extra_targets_in_find_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aeloon.memory.manager import MemoryManager

    factory, _ = _install_fake_runtime(monkeypatch)
    factory.default_find_results_by_target = {
        "viking://user/default/memories/": FakeFindResult(
            memories=[
                FakeMatchedContext(
                    uri="viking://user/default/memories/prefs",
                    abstract="memory hit",
                    score=0.82,
                )
            ]
        ),
        "viking://session/default": FakeFindResult(
            memories=[
                FakeMatchedContext(
                    uri="viking://session/default/chat-1/history/archive_001/.overview.md",
                    abstract="session hit",
                    score=0.91,
                )
            ]
        ),
    }
    config = Config.model_validate(
        {
            "memory": {
                "backend": "openviking",
                "backends": {
                    "file": {},
                    "openviking": {
                        "ovConfig": {
                            "storage": {"agfs": {"port": 1833}},
                            "embedding": {"dense": {"provider": "mock"}},
                        },
                        "searchMode": "find",
                        "searchLimit": 5,
                        "targetUri": "viking://user/default/memories/",
                        "extraTargetUris": [
                            "viking://session/default",
                            "viking://user/default/memories/",
                            "   ",
                        ],
                    },
                },
            }
        }
    )

    manager = MemoryManager(memory_config=config.memory, deps=_make_deps(tmp_path))
    prepared = await manager.prepare_turn(
        session=Session(key="cli:test"),
        query="project rules",
        channel="cli",
        chat_id="direct",
        current_role="user",
    )

    assert [call["target_uri"] for call in factory.clients[0].find_calls] == [
        "viking://user/default/memories/",
        "viking://session/default",
    ]
    assert "memory hit" in prepared.system_sections[0]
    assert "session hit" in prepared.system_sections[0]

    await manager.close()


@pytest.mark.asyncio
async def test_openviking_prepare_turn_merges_multi_target_search_results_by_highest_score(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aeloon.memory.manager import MemoryManager

    factory, _ = _install_fake_runtime(monkeypatch)
    factory.default_search_results_by_target = {
        "viking://user/default/memories/": FakeFindResult(
            memories=[
                FakeMatchedContext(
                    uri="viking://shared/context",
                    abstract="lower score copy",
                    score=0.4,
                )
            ]
        ),
        "viking://session/default": FakeFindResult(
            memories=[
                FakeMatchedContext(
                    uri="viking://shared/context",
                    abstract="higher score copy",
                    score=0.9,
                )
            ]
        ),
    }
    config = Config.model_validate(
        {
            "memory": {
                "backend": "openviking",
                "backends": {
                    "file": {},
                    "openviking": {
                        "ovConfig": {
                            "storage": {"agfs": {"port": 1833}},
                            "embedding": {"dense": {"provider": "mock"}},
                        },
                        "searchMode": "search",
                        "searchLimit": 5,
                        "targetUri": "viking://user/default/memories/",
                        "extraTargetUris": ["viking://session/default"],
                    },
                },
            }
        }
    )

    manager = MemoryManager(memory_config=config.memory, deps=_make_deps(tmp_path))
    session = Session(key="cli:test")
    session.messages = [
        {"role": "user", "content": "First question"},
        {"role": "assistant", "content": "First answer"},
    ]
    await manager.backend.after_turn(
        session=session,
        raw_new_messages=list(session.messages),
        persisted_new_messages=list(session.messages),
        final_content="First answer",
    )
    prepared = await manager.prepare_turn(
        session=session,
        query="reuse earlier context",
        channel="cli",
        chat_id="direct",
        current_role="user",
    )

    assert [call["target_uri"] for call in factory.clients[0].search_calls] == [
        "viking://user/default/memories/",
        "viking://session/default",
    ]
    assert all(call["session_id"] == "aeloon-live-cli_test" for call in factory.clients[0].search_calls)
    assert "higher score copy" in prepared.system_sections[0]
    assert "lower score copy" not in prepared.system_sections[0]

    await manager.close()


@pytest.mark.asyncio
async def test_openviking_prepare_turn_represents_empty_recall_explicitly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aeloon.memory.manager import MemoryManager

    factory, _ = _install_fake_runtime(monkeypatch)
    config = Config.model_validate(
        {
            "memory": {
                "backend": "openviking",
                "backends": {
                    "file": {},
                    "openviking": {
                        "ovConfig": {
                            "storage": {"agfs": {"port": 1833}},
                            "embedding": {"dense": {"provider": "mock"}},
                        },
                    },
                },
            }
        }
    )

    factory.default_search_result = FakeFindResult()
    manager = MemoryManager(memory_config=config.memory, deps=_make_deps(tmp_path))
    prepared = await manager.prepare_turn(
        session=Session(key="cli:test"),
        query="nothing relevant",
        channel="cli",
        chat_id="direct",
        current_role="user",
    )

    assert prepared.system_sections == ["# OpenViking Recall\n\n(none)"]

    await manager.close()


@pytest.mark.asyncio
async def test_openviking_prepare_turn_filters_low_score_recall_entries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aeloon.memory.manager import MemoryManager

    factory, _ = _install_fake_runtime(monkeypatch)
    config = Config.model_validate(
        {
            "memory": {
                "backend": "openviking",
                "backends": {
                    "file": {},
                    "openviking": {
                        "ovConfig": {
                            "storage": {"agfs": {"port": 1833}},
                            "embedding": {"dense": {"provider": "mock"}},
                        },
                        "scoreThreshold": 0.5,
                    },
                },
            }
        }
    )

    factory.default_search_result = FakeFindResult(
        memories=[
            FakeMatchedContext(
                uri="viking://memories/high",
                abstract="keep this",
                score=0.9,
            ),
            FakeMatchedContext(
                uri="viking://memories/low",
                abstract="drop this",
                score=0.2,
            ),
        ]
    )
    manager = MemoryManager(memory_config=config.memory, deps=_make_deps(tmp_path))
    prepared = await manager.prepare_turn(
        session=Session(key="cli:test"),
        query="filter scores",
        channel="cli",
        chat_id="direct",
        current_role="user",
    )

    assert "keep this" in prepared.system_sections[0]
    assert "drop this" not in prepared.system_sections[0]

    await manager.close()


@pytest.mark.asyncio
async def test_openviking_prepare_turn_logs_recall_inputs_results_and_query_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aeloon.memory.backends import openviking as openviking_module
    from aeloon.memory.manager import MemoryManager

    factory, _ = _install_fake_runtime(monkeypatch)
    config = Config.model_validate(
        {
            "memory": {
                "backend": "openviking",
                "backends": {
                    "file": {},
                    "openviking": {
                        "ovConfig": {
                            "storage": {"agfs": {"port": 1833}},
                            "embedding": {"dense": {"provider": "mock"}},
                        },
                        "searchMode": "search",
                        "scoreThreshold": 0.5,
                    },
                },
            }
        }
    )

    factory.default_search_result = FakeFindResult(
        memories=[
            FakeMatchedContext(
                uri="viking://memories/high",
                abstract="keep this",
                score=0.9,
            ),
            FakeMatchedContext(
                uri="viking://memories/low",
                abstract="drop this",
                score=0.2,
            ),
        ],
        resources=[
            FakeMatchedContext(
                uri="viking://resources/profile",
                abstract="profile summary",
                score=0.8,
            )
        ],
        query_plan={"expanded_queries": ["what is my major", "profile major"]},
    )
    logged_lines: list[str] = []

    def _info(message: str, *args: object) -> None:
        logged_lines.append(message.format(*args))

    monkeypatch.setattr(
        openviking_module,
        "logger",
        SimpleNamespace(info=_info),
        raising=False,
    )

    manager = MemoryManager(memory_config=config.memory, deps=_make_deps(tmp_path))
    await manager.prepare_turn(
        session=Session(key="cli:test"),
        query="what is my major",
        channel="cli",
        chat_id="test",
        current_role="user",
    )

    assert any("OpenViking recall start query='what is my major'" in line for line in logged_lines)
    assert any("mode=search" in line and "threshold=0.5" in line for line in logged_lines)
    assert any("raw_counts={memories:2, resources:1, skills:0}" in line for line in logged_lines)
    assert any("viking://memories/high" in line for line in logged_lines)
    assert all("viking://memories/low" not in line for line in logged_lines)
    assert any("viking://resources/profile" in line for line in logged_lines)
    assert any("query_plan" in line and "profile major" in line for line in logged_lines)

    await manager.close()


@pytest.mark.asyncio
async def test_openviking_after_turn_mirrors_suffix_and_persists_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aeloon.memory.manager import MemoryManager

    factory, _ = _install_fake_runtime(monkeypatch)
    config = Config.model_validate(
        {
            "memory": {
                "backend": "openviking",
                "backends": {
                    "file": {},
                    "openviking": {
                        "ovConfig": {
                            "storage": {"agfs": {"port": 1833}},
                            "embedding": {"dense": {"provider": "mock"}},
                        },
                    },
                },
            }
        }
    )

    deps = _make_deps(tmp_path)
    manager = MemoryManager(memory_config=config.memory, deps=deps)
    session = Session(key="cli:test")
    session.messages = [
        {"role": "user", "content": "Question"},
        {"role": "assistant", "content": "Answer"},
        {"role": "tool", "name": "search_docs", "content": {"result": "ok"}},
    ]

    await manager.after_turn(
        session=session,
        raw_new_messages=list(session.messages),
        persisted_new_messages=list(session.messages),
        final_content="Answer",
    )
    await manager.close()

    live_session_id = "aeloon-live-cli_test"
    assert factory.clients[0].sessions[live_session_id].messages == [
        ("user", "Question"),
        ("assistant", "Answer"),
        ("assistant", '[tool:search_docs] {"result": "ok"}'),
    ]
    assert session.memory_state["openviking"]["liveSessionId"] == live_session_id
    assert session.memory_state["openviking"]["mirroredCount"] == 3
    assert session.memory_state["openviking"]["archivedThrough"] == 0

    deps.sessions.invalidate(session.key)
    reloaded = deps.sessions.get_or_create(session.key)
    assert reloaded.memory_state["openviking"]["mirroredCount"] == 3


@pytest.mark.asyncio
async def test_openviking_after_turn_appends_only_new_suffix_on_subsequent_turns(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aeloon.memory.manager import MemoryManager

    factory, _ = _install_fake_runtime(monkeypatch)
    config = Config.model_validate(
        {
            "memory": {
                "backend": "openviking",
                "backends": {
                    "file": {},
                    "openviking": {
                        "ovConfig": {
                            "storage": {"agfs": {"port": 1833}},
                            "embedding": {"dense": {"provider": "mock"}},
                        },
                    },
                },
            }
        }
    )

    manager = MemoryManager(memory_config=config.memory, deps=_make_deps(tmp_path))
    session = Session(key="cli:test")
    session.messages = [
        {"role": "user", "content": "First question"},
        {"role": "assistant", "content": "First answer"},
    ]
    await manager.backend.after_turn(
        session=session,
        raw_new_messages=list(session.messages),
        persisted_new_messages=list(session.messages),
        final_content="First answer",
    )

    session.messages.extend(
        [
            {"role": "user", "content": "Second question"},
            {"role": "assistant", "content": "Second answer"},
        ]
    )
    await manager.backend.after_turn(
        session=session,
        raw_new_messages=session.messages[2:],
        persisted_new_messages=session.messages[2:],
        final_content="Second answer",
    )
    await manager.close()

    live_session_id = "aeloon-live-cli_test"
    assert factory.clients[0].sessions[live_session_id].messages == [
        ("user", "First question"),
        ("assistant", "First answer"),
        ("user", "Second question"),
        ("assistant", "Second answer"),
    ]
    assert session.memory_state["openviking"]["mirroredCount"] == 4


@pytest.mark.asyncio
async def test_openviking_after_turn_materializes_live_session_before_first_append(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aeloon.memory.manager import MemoryManager

    factory, _ = _install_fake_runtime(monkeypatch)
    factory.default_client_kwargs["require_existing_session_for_add"] = True
    config = Config.model_validate(
        {
            "memory": {
                "backend": "openviking",
                "backends": {
                    "file": {},
                    "openviking": {
                        "ovConfig": {
                            "storage": {"agfs": {"port": 1833}},
                            "embedding": {"dense": {"provider": "mock"}},
                        },
                    },
                },
            }
        }
    )

    manager = MemoryManager(memory_config=config.memory, deps=_make_deps(tmp_path))
    session = Session(key="cli:test")
    session.messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ]

    await manager.backend.after_turn(
        session=session,
        raw_new_messages=list(session.messages),
        persisted_new_messages=list(session.messages),
        final_content="world",
    )

    live_session_id = "aeloon-live-cli_test"
    client = factory.clients[0]
    assert live_session_id in client.existing_session_ids
    assert client.sessions[live_session_id].messages == [
        ("user", "hello"),
        ("assistant", "world"),
    ]

    await manager.close()


@pytest.mark.asyncio
async def test_openviking_prepare_turn_archive_rebuild_recreates_live_session_before_replay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aeloon.memory.backends.openviking import OpenVikingMemoryBackend
    from aeloon.memory.manager import MemoryManager

    factory, _ = _install_fake_runtime(monkeypatch)
    factory.default_client_kwargs["require_existing_session_for_add"] = True
    config = Config.model_validate(
        {
            "memory": {
                "backend": "openviking",
                "backends": {
                    "file": {},
                    "openviking": {
                        "ovConfig": {
                            "storage": {"agfs": {"port": 1833}},
                            "embedding": {"dense": {"provider": "mock"}},
                        },
                        "triggerRatio": 1.0,
                        "targetRatio": 0.6,
                        "maxCommitRounds": 2,
                    },
                },
            }
        }
    )

    deps = _make_deps(tmp_path, context_window_tokens=150)
    manager = MemoryManager(memory_config=config.memory, deps=deps)
    backend = cast(OpenVikingMemoryBackend, manager.backend)
    client = cast(FakeOpenVikingClient, await backend._ensure_client())
    client.existing_session_ids.add("aeloon-live-cli_test")
    client.session("aeloon-live-cli_test")

    session = Session(key="cli:test")
    session.messages = [
        {"role": "user", "content": "u1 " * 120},
        {"role": "assistant", "content": "a1 " * 120},
        {"role": "user", "content": "u2 " * 120},
        {"role": "assistant", "content": "a2 " * 120},
    ]

    await backend.after_turn(
        session=session,
        raw_new_messages=list(session.messages),
        persisted_new_messages=list(session.messages),
        final_content="a2",
    )

    prepared = await manager.prepare_turn(
        session=session,
        query="next turn",
        channel="cli",
        chat_id="direct",
        current_role="user",
    )

    assert prepared.history_start_index == 2
    assert client.sessions["aeloon-live-cli_test"].messages == [
        ("user", "u2 " * 120),
        ("assistant", "a2 " * 120),
    ]

    await manager.close()


@pytest.mark.asyncio
async def test_openviking_backend_ignores_existing_file_memory_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aeloon.memory.manager import MemoryManager

    factory, _ = _install_fake_runtime(monkeypatch)
    config = Config.model_validate(
        {
            "memory": {
                "backend": "openviking",
                "backends": {
                    "file": {},
                    "openviking": {
                        "ovConfig": {
                            "storage": {"agfs": {"port": 1833}},
                            "embedding": {"dense": {"provider": "mock"}},
                        },
                    },
                },
            }
        }
    )

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    memory_file = memory_dir / "MEMORY.md"
    history_file = memory_dir / "HISTORY.md"
    memory_file.write_text("legacy memory", encoding="utf-8")
    history_file.write_text("legacy history", encoding="utf-8")

    manager = MemoryManager(memory_config=config.memory, deps=_make_deps(tmp_path))
    session = Session(key="cli:test")
    await manager.prepare_turn(
        session=session,
        query="ignore legacy files",
        channel="cli",
        chat_id="direct",
        current_role="user",
    )
    session.messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ]
    await manager.after_turn(
        session=session,
        raw_new_messages=list(session.messages),
        persisted_new_messages=list(session.messages),
        final_content="world",
    )
    await manager.close()

    assert factory.clients[0].sessions["aeloon-live-cli_test"].messages == [
        ("user", "hello"),
        ("assistant", "world"),
    ]
    assert memory_file.read_text(encoding="utf-8") == "legacy memory"
    assert history_file.read_text(encoding="utf-8") == "legacy history"


@pytest.mark.asyncio
async def test_openviking_prepare_turn_archives_old_history_and_rebuilds_live_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aeloon.memory.manager import MemoryManager

    factory, _ = _install_fake_runtime(monkeypatch)
    config = Config.model_validate(
        {
            "memory": {
                "backend": "openviking",
                "backends": {
                    "file": {},
                    "openviking": {
                        "ovConfig": {
                            "storage": {"agfs": {"port": 1833}},
                            "embedding": {"dense": {"provider": "mock"}},
                        },
                        "triggerRatio": 1.0,
                        "targetRatio": 0.6,
                        "maxCommitRounds": 2,
                    },
                },
            }
        }
    )

    deps = _make_deps(tmp_path, context_window_tokens=150)
    manager = MemoryManager(memory_config=config.memory, deps=deps)
    session = Session(key="cli:test")
    session.messages = [
        {"role": "user", "content": "u1 " * 120},
        {"role": "assistant", "content": "a1 " * 120},
        {"role": "user", "content": "u2 " * 120},
        {"role": "assistant", "content": "a2 " * 120},
    ]

    await manager.after_turn(
        session=session,
        raw_new_messages=list(session.messages),
        persisted_new_messages=list(session.messages),
        final_content="a2",
    )
    await asyncio.sleep(0)
    prepared = await manager.prepare_turn(
        session=session,
        query="next turn",
        channel="cli",
        chat_id="direct",
        current_role="user",
    )
    await manager.close()

    live_session_id = "aeloon-live-cli_test"
    client = factory.clients[0]

    assert prepared.history_start_index == 2
    assert manager.pending_start_index(session) == 2
    assert session.memory_state["openviking"]["archivedThrough"] == 2
    assert client.commit_session_calls
    assert client.commit_session_calls[0].startswith("aeloon-archive-cli_test")
    assert client.wait_processed_calls == [None]
    assert live_session_id in client.delete_session_calls
    assert client.sessions[live_session_id].messages == [
        ("user", "u2 " * 120),
        ("assistant", "a2 " * 120),
    ]


@pytest.mark.asyncio
async def test_openviking_new_session_archives_pending_slice_and_resets_live_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aeloon.memory.manager import MemoryManager

    factory, _ = _install_fake_runtime(monkeypatch)
    config = Config.model_validate(
        {
            "memory": {
                "backend": "openviking",
                "backends": {
                    "file": {},
                    "openviking": {
                        "ovConfig": {
                            "storage": {"agfs": {"port": 1833}},
                            "embedding": {"dense": {"provider": "mock"}},
                        },
                    },
                },
            }
        }
    )

    manager = MemoryManager(memory_config=config.memory, deps=_make_deps(tmp_path))
    session = Session(key="cli:test")
    session.messages = [
        {"role": "user", "content": "Old question"},
        {"role": "assistant", "content": "Old answer"},
    ]

    await manager.after_turn(
        session=session,
        raw_new_messages=list(session.messages),
        persisted_new_messages=list(session.messages),
        final_content="Old answer",
    )
    await manager.close()

    live_session_id = "aeloon-live-cli_test"
    client = factory.clients[0]
    assert live_session_id in client.sessions

    session.clear()
    second_manager = MemoryManager(memory_config=config.memory, deps=_make_deps(tmp_path))

    await second_manager.on_new_session(
        session=session,
        pending_messages=[
            {"role": "user", "content": "Old question"},
            {"role": "assistant", "content": "Old answer"},
        ],
    )
    await second_manager.close()
    second_client = factory.clients[1]

    assert second_client.commit_session_calls
    archive_session_id = second_client.commit_session_calls[0]
    assert archive_session_id.startswith("aeloon-archive-cli_test")
    assert second_client.wait_processed_calls == [None]
    assert live_session_id in second_client.delete_session_calls
    assert session.memory_state == {}


@pytest.mark.asyncio
async def test_openviking_new_session_with_no_pending_history_only_clears_live_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aeloon.memory.manager import MemoryManager

    factory, _ = _install_fake_runtime(monkeypatch)
    config = Config.model_validate(
        {
            "memory": {
                "backend": "openviking",
                "backends": {
                    "file": {},
                    "openviking": {
                        "ovConfig": {
                            "storage": {"agfs": {"port": 1833}},
                            "embedding": {"dense": {"provider": "mock"}},
                        },
                    },
                },
            }
        }
    )

    manager = MemoryManager(memory_config=config.memory, deps=_make_deps(tmp_path))
    session = Session(key="cli:test")
    await manager.prepare_turn(
        session=session,
        query="init",
        channel="cli",
        chat_id="direct",
        current_role="user",
    )
    await manager.on_new_session(session=session, pending_messages=[])
    await manager.close()

    client = factory.clients[0]
    assert client.commit_session_calls == []
    assert client.delete_session_calls == ["aeloon-live-cli_test"]


@pytest.mark.asyncio
async def test_openviking_repeated_new_session_calls_do_not_duplicate_archival(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aeloon.memory.manager import MemoryManager

    factory, _ = _install_fake_runtime(monkeypatch)
    config = Config.model_validate(
        {
            "memory": {
                "backend": "openviking",
                "backends": {
                    "file": {},
                    "openviking": {
                        "ovConfig": {
                            "storage": {"agfs": {"port": 1833}},
                            "embedding": {"dense": {"provider": "mock"}},
                        },
                    },
                },
            }
        }
    )

    manager = MemoryManager(memory_config=config.memory, deps=_make_deps(tmp_path))
    session = Session(key="cli:test")
    await manager.on_new_session(
        session=session,
        pending_messages=[{"role": "user", "content": "pending"}],
    )
    await manager.on_new_session(session=session, pending_messages=[])
    await manager.close()

    client = factory.clients[0]
    assert len(client.commit_session_calls) == 1
    assert client.delete_session_calls == ["aeloon-live-cli_test", "aeloon-live-cli_test"]


@pytest.mark.asyncio
async def test_openviking_prepare_turn_waits_for_inflight_new_session_archival(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aeloon.memory.manager import MemoryManager

    factory, _ = _install_fake_runtime(monkeypatch)
    config = Config.model_validate(
        {
            "memory": {
                "backend": "openviking",
                "backends": {
                    "file": {},
                    "openviking": {
                        "ovConfig": {
                            "storage": {"agfs": {"port": 1833}},
                            "embedding": {"dense": {"provider": "mock"}},
                        },
                    },
                },
            }
        }
    )

    manager = MemoryManager(memory_config=config.memory, deps=_make_deps(tmp_path))
    session = Session(key="cli:test")
    await manager.prepare_turn(
        session=session,
        query="init",
        channel="cli",
        chat_id="direct",
        current_role="user",
    )
    client = factory.clients[0]

    release_wait = asyncio.Event()
    entered_wait = asyncio.Event()
    client.wait_processed_release = release_wait
    client.wait_processed_entered = entered_wait

    archive_task = asyncio.create_task(
        manager.backend.on_new_session(
            session=session,
            pending_messages=[{"role": "user", "content": "pending"}],
        )
    )
    await entered_wait.wait()

    fresh_session = Session(key="cli:test")
    prepare_task = asyncio.create_task(
        manager.prepare_turn(
            session=fresh_session,
            query="after new",
            channel="cli",
            chat_id="direct",
            current_role="user",
        )
    )
    await asyncio.sleep(0)
    assert prepare_task.done() is False

    release_wait.set()
    await archive_task
    await prepare_task
    await manager.close()
