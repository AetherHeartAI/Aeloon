from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest

from aeloon.core.bus.queue import MessageBus
from aeloon.core.config.schema import Config
from aeloon.core.session.manager import Session


@dataclass(slots=True)
class _FakeSession:
    session_id: str

    async def ensure_exists(self) -> None:
        return None

    async def add_message(
        self,
        role: str,
        content: str | None = None,
        parts: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        return {"role": role, "content": content, "parts": parts}

    async def commit(self) -> dict[str, object]:
        return {"status": "committed"}

    async def delete(self) -> None:
        return None


@dataclass(slots=True)
class _FakeClient:
    initialized: bool = False
    closed: bool = False

    async def initialize(self) -> None:
        self.initialized = True

    async def close(self) -> None:
        self.closed = True

    def session(
        self,
        session_id: str | None = None,
        must_exist: bool = False,
    ) -> _FakeSession:
        del must_exist
        return _FakeSession(session_id or "generated")

    async def session_exists(self, session_id: str) -> bool:
        del session_id
        return False

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str | None = None,
        parts: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        del session_id
        return {"role": role, "content": content, "parts": parts}

    async def commit_session(self, session_id: str) -> dict[str, object]:
        return {"session_id": session_id}

    async def delete_session(self, session_id: str) -> None:
        del session_id
        return None

    async def search(
        self,
        query: str,
        target_uri: str = "",
        session: _FakeSession | None = None,
        session_id: str | None = None,
        limit: int = 10,
        score_threshold: float | None = None,
        filter: dict[str, object] | None = None,
        telemetry: bool = False,
    ) -> dict[str, object]:
        del query, target_uri, session, session_id, limit, score_threshold, filter, telemetry
        return {"memories": [], "resources": [], "skills": []}

    async def find(
        self,
        query: str,
        target_uri: str = "",
        limit: int = 10,
        mode: str = "auto",
        score_threshold: float | None = None,
        filter: dict[str, object] | None = None,
        telemetry: bool = False,
    ) -> dict[str, object]:
        del query, target_uri, limit, mode, score_threshold, filter, telemetry
        return {"memories": [], "resources": [], "skills": []}

    async def abstract(self, uri: str) -> str:
        return f"abstract:{uri}"

    async def overview(self, uri: str) -> str:
        return f"overview:{uri}"

    async def read(self, uri: str, offset: int = 0, limit: int = -1) -> str:
        del offset, limit
        return f"read:{uri}"

    async def ls(self, uri: str) -> list[dict[str, object]]:
        return [{"uri": uri, "rel_path": "resources", "isDir": True}]

    async def tree(self, uri: str) -> list[dict[str, object]]:
        return [{"uri": uri, "rel_path": "resources", "isDir": True}]

    async def stat(self, uri: str) -> dict[str, object]:
        return {"uri": uri, "exists": True}

    async def add_resource(self, path: str, reason: str = "") -> dict[str, object]:
        return {"root_uri": f"viking://resources/{path.split('/')[-1]}", "reason": reason}

    async def wait_processed(self, timeout: float | None = None) -> dict[str, object]:
        return {"timeout": timeout}


@dataclass(slots=True)
class _FakeFactory:
    clients: list[_FakeClient] = field(default_factory=list)

    def __call__(self, path: str | None = None) -> _FakeClient:
        del path
        client = _FakeClient()
        self.clients.append(client)
        return client

    async def reset(self) -> None:
        return None


@dataclass(slots=True)
class _FakeConfigSingleton:
    def initialize(
        self,
        config_dict: dict[str, object] | None = None,
        config_path: str | None = None,
    ) -> object:
        return {"config_dict": config_dict, "config_path": config_path}

    def reset_instance(self) -> None:
        return None


def _install_fake_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    import aeloon.memory.providers.openviking_service as service_module

    runtime = service_module.OpenVikingRuntime(
        async_openviking_cls=cast(service_module.OpenVikingClientFactoryProtocol, _FakeFactory()),
        config_singleton=cast(
            service_module.OpenVikingConfigSingletonProtocol,
            _FakeConfigSingleton(),
        ),
    )
    monkeypatch.setattr(service_module, "_load_openviking_runtime", lambda: runtime)


@pytest.mark.asyncio
async def test_agent_loop_registers_additive_openviking_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aeloon.core.agent.loop import AgentLoop

    _install_fake_runtime(monkeypatch)
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "MEMORY.md").write_text("Prompt memory remains active.", encoding="utf-8")
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    config = Config.model_validate(
        {
            "memory": {
                "provider": "openviking",
                "providers": {"openviking": {"ovConfig": {"storage": {}}}},
            }
        }
    )

    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        memory_config=config.memory,
    )

    assert "memory" in loop.tools.tool_names
    assert "session_search" in loop.tools.tool_names
    assert "viking_search" in loop.tools.tool_names
    assert "viking_read" in loop.tools.tool_names
    assert "viking_browse" in loop.tools.tool_names
    assert "viking_remember" in loop.tools.tool_names
    assert "viking_add_resource" in loop.tools.tool_names

    prepared = await loop.memory.prepare_turn(
        session=Session(key="cli:test"),
        query="find docs",
        channel="cli",
        chat_id="direct",
        current_role="user",
    )

    combined_sections = "\n".join(prepared.system_sections)
    assert "Prompt memory remains active." in combined_sections
    assert "OpenViking Knowledge Base" in combined_sections
    assert "viking_search" in combined_sections
    assert "openviking-memory" not in prepared.always_skill_names

    await loop.memory.close()
