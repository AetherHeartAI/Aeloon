"""Reusable OpenViking provider service."""

from __future__ import annotations

import asyncio
import copy
import importlib
import importlib.util
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol, TypedDict, cast

import httpx
from pydantic import BaseModel, Field, field_validator

from aeloon.core.config.paths import get_logs_dir
from aeloon.core.session.manager import Session
from aeloon.memory.types import MemoryRuntimeDeps, MessagePayload


class OpenVikingSessionProtocol(Protocol):
    session_id: str

    async def ensure_exists(self) -> None: ...

    async def add_message(
        self,
        role: str,
        content: str | None = None,
        parts: list[dict[str, object]] | None = None,
    ) -> dict[str, object]: ...

    async def commit(self) -> dict[str, object]: ...

    async def delete(self) -> None: ...


class OpenVikingClientProtocol(Protocol):
    async def initialize(self) -> None: ...

    async def close(self) -> None: ...

    def session(
        self,
        session_id: str | None = None,
        must_exist: bool = False,
    ) -> OpenVikingSessionProtocol: ...

    async def session_exists(self, session_id: str) -> bool: ...

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str | None = None,
        parts: list[dict[str, object]] | None = None,
    ) -> dict[str, object]: ...

    async def commit_session(self, session_id: str) -> dict[str, object]: ...

    async def delete_session(self, session_id: str) -> None: ...

    async def search(
        self,
        query: str,
        target_uri: str = "",
        session: OpenVikingSessionProtocol | None = None,
        session_id: str | None = None,
        limit: int = 10,
        score_threshold: float | None = None,
        filter: dict[str, object] | None = None,
        telemetry: bool = False,
    ) -> object: ...

    async def find(
        self,
        query: str,
        target_uri: str = "",
        limit: int = 10,
        score_threshold: float | None = None,
        filter: dict[str, object] | None = None,
        telemetry: bool = False,
    ) -> object: ...

    async def abstract(self, uri: str) -> str: ...

    async def overview(self, uri: str) -> str: ...

    async def read(self, uri: str, offset: int = 0, limit: int = -1) -> str: ...

    async def ls(self, uri: str) -> list[object]: ...

    async def tree(self, uri: str) -> list[object]: ...

    async def stat(self, uri: str) -> dict[str, object]: ...

    async def add_resource(
        self,
        path: str,
        reason: str = "",
    ) -> dict[str, object]: ...

    async def wait_processed(self, timeout: float | None = None) -> dict[str, object]: ...


class OpenVikingTransportProtocol(Protocol):
    async def initialize(self) -> None: ...

    async def close(self) -> None: ...

    async def ensure_session(self, session_id: str) -> None: ...

    async def session_exists(self, session_id: str) -> bool: ...

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str | None = None,
        parts: list[dict[str, object]] | None = None,
    ) -> dict[str, object]: ...

    async def commit_session(self, session_id: str) -> dict[str, object]: ...

    async def delete_session(self, session_id: str) -> None: ...

    async def search(
        self,
        query: str,
        target_uri: str = "",
        session_id: str | None = None,
        limit: int = 10,
        score_threshold: float | None = None,
    ) -> object: ...

    async def find(
        self,
        query: str,
        target_uri: str = "",
        limit: int = 10,
        score_threshold: float | None = None,
        mode: str = "auto",
    ) -> object: ...

    async def abstract(self, uri: str) -> str: ...

    async def overview(self, uri: str) -> str: ...

    async def read(self, uri: str) -> str: ...

    async def ls(self, uri: str) -> list[object]: ...

    async def tree(self, uri: str) -> list[object]: ...

    async def stat(self, uri: str) -> dict[str, object]: ...

    async def add_resource(self, path: str, reason: str = "") -> dict[str, object]: ...

    async def wait_processed(self, timeout: float | None = None) -> dict[str, object]: ...


class OpenVikingClientFactoryProtocol(Protocol):
    def __call__(self, path: str | None = None) -> OpenVikingClientProtocol: ...

    async def reset(self) -> None: ...


class OpenVikingConfigSingletonProtocol(Protocol):
    def initialize(
        self,
        config_dict: dict[str, object] | None = None,
        config_path: str | None = None,
    ) -> object: ...

    def reset_instance(self) -> None: ...


@dataclass(frozen=True, slots=True)
class OpenVikingRuntime:
    async_openviking_cls: OpenVikingClientFactoryProtocol
    config_singleton: OpenVikingConfigSingletonProtocol


class _LazyOpenVikingConfigSingleton:
    def __init__(self) -> None:
        self._singleton: OpenVikingConfigSingletonProtocol | None = None

    def _load(self) -> OpenVikingConfigSingletonProtocol:
        if self._singleton is None:
            config_module = importlib.import_module(
                "openviking_cli.utils.config.open_viking_config"
            )
            self._singleton = cast(
                OpenVikingConfigSingletonProtocol,
                getattr(config_module, "OpenVikingConfigSingleton"),
            )
        return self._singleton

    def initialize(
        self,
        config_dict: dict[str, object] | None = None,
        config_path: str | None = None,
    ) -> object:
        return self._load().initialize(config_dict=config_dict, config_path=config_path)

    def reset_instance(self) -> None:
        self._load().reset_instance()


class _LazyOpenVikingClientFactory:
    def __init__(self) -> None:
        self._factory: OpenVikingClientFactoryProtocol | None = None

    def _load(self) -> OpenVikingClientFactoryProtocol:
        if self._factory is None:
            async_client_module = importlib.import_module("openviking.async_client")
            self._factory = cast(
                OpenVikingClientFactoryProtocol,
                getattr(async_client_module, "AsyncOpenViking"),
            )
        return self._factory

    def __call__(self, path: str | None = None) -> OpenVikingClientProtocol:
        return self._load()(path=path)

    async def reset(self) -> None:
        await self._load().reset()


def _load_openviking_runtime() -> OpenVikingRuntime:
    if (
        importlib.util.find_spec("openviking") is None
        or importlib.util.find_spec("openviking_cli") is None
    ):
        raise ImportError("OpenViking runtime is not installed")
    return OpenVikingRuntime(
        async_openviking_cls=_LazyOpenVikingClientFactory(),
        config_singleton=_LazyOpenVikingConfigSingleton(),
    )


def _default_ov_config() -> dict[str, object]:
    return {"storage": {}}


def _prepare_inline_config(
    config: "OpenVikingProviderConfig",
    storage_root: Path,
) -> dict[str, object]:
    inline = copy.deepcopy(config.ov_config)
    raw_storage = inline.get("storage")
    storage = dict(raw_storage) if isinstance(raw_storage, Mapping) else {}
    storage["workspace"] = str(storage_root)
    inline["storage"] = storage
    if "log" not in inline:
        inline["log"] = {
            "level": "WARNING",
            "output": str(get_logs_dir() / "openviking.log"),
        }
    return inline


class EmbeddedOpenVikingTransport:
    def __init__(
        self,
        *,
        config: "OpenVikingProviderConfig",
        storage_root: Path,
    ) -> None:
        self.config = config
        self.storage_root = storage_root
        self.runtime: OpenVikingRuntime | None = None
        self._client: OpenVikingClientProtocol | None = None
        self._client_init_lock = asyncio.Lock()

    async def initialize(self) -> None:
        await self._ensure_client()

    async def _ensure_client(self) -> OpenVikingClientProtocol:
        if self._client is not None:
            return self._client

        async with self._client_init_lock:
            if self._client is not None:
                return self._client
            self.runtime = _load_openviking_runtime()
            inline_config = _prepare_inline_config(self.config, self.storage_root)
            self.storage_root.mkdir(parents=True, exist_ok=True)
            self.runtime.config_singleton.initialize(
                config_dict=inline_config,
                config_path=self.config.config_path,
            )
            client = self.runtime.async_openviking_cls(path=str(self.storage_root))
            await client.initialize()
            self._client = client
            return client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
        if self.runtime is not None:
            self.runtime.config_singleton.reset_instance()
            await self.runtime.async_openviking_cls.reset()
            self.runtime = None

    @staticmethod
    def _session_uri(session_id: str) -> str:
        return f"viking://session/default/{session_id}"

    @classmethod
    def _session_messages_uri(cls, session_id: str) -> str:
        return f"{cls._session_uri(session_id)}/messages.jsonl"

    async def _is_session_writable(
        self,
        client: OpenVikingClientProtocol,
        session_id: str,
    ) -> bool:
        try:
            await client.stat(self._session_messages_uri(session_id))
        except Exception:
            return False
        return True

    async def ensure_session(self, session_id: str) -> None:
        client = await self._ensure_client()
        if await client.session_exists(session_id):
            if await self._is_session_writable(client, session_id):
                return
            await client.delete_session(session_id)
        session = client.session(session_id=session_id)
        await session.ensure_exists()

    async def session_exists(self, session_id: str) -> bool:
        client = await self._ensure_client()
        return await client.session_exists(session_id)

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str | None = None,
        parts: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        client = await self._ensure_client()
        return await client.add_message(
            session_id=session_id,
            role=role,
            content=content,
            parts=parts,
        )

    async def commit_session(self, session_id: str) -> dict[str, object]:
        client = await self._ensure_client()
        return await client.commit_session(session_id)

    async def delete_session(self, session_id: str) -> None:
        client = await self._ensure_client()
        await client.delete_session(session_id)

    async def search(
        self,
        query: str,
        target_uri: str = "",
        session_id: str | None = None,
        limit: int = 10,
        score_threshold: float | None = None,
    ) -> object:
        client = await self._ensure_client()
        return await client.search(
            query=query,
            target_uri=target_uri,
            session_id=session_id,
            limit=limit,
            score_threshold=score_threshold,
        )

    async def find(
        self,
        query: str,
        target_uri: str = "",
        limit: int = 10,
        score_threshold: float | None = None,
        mode: str = "auto",
    ) -> object:
        del mode
        client = await self._ensure_client()
        return await client.find(
            query=query,
            target_uri=target_uri,
            limit=limit,
            score_threshold=score_threshold,
        )

    async def abstract(self, uri: str) -> str:
        client = await self._ensure_client()
        return await client.abstract(uri)

    async def overview(self, uri: str) -> str:
        client = await self._ensure_client()
        return await client.overview(uri)

    async def read(self, uri: str) -> str:
        client = await self._ensure_client()
        return await client.read(uri)

    async def ls(self, uri: str) -> list[object]:
        client = await self._ensure_client()
        return await client.ls(uri)

    async def tree(self, uri: str) -> list[object]:
        client = await self._ensure_client()
        return await client.tree(uri)

    async def stat(self, uri: str) -> dict[str, object]:
        client = await self._ensure_client()
        return await client.stat(uri)

    async def add_resource(self, path: str, reason: str = "") -> dict[str, object]:
        client = await self._ensure_client()
        return await client.add_resource(path=path, reason=reason)

    async def wait_processed(self, timeout: float | None = None) -> dict[str, object]:
        client = await self._ensure_client()
        return await client.wait_processed(timeout=timeout)


class HttpOpenVikingTransport:
    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self._client = client
        self._owns_client = client is None

    async def initialize(self) -> None:
        await self._ensure_client()

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"Accept": "application/json"}
            if self.api_key:
                headers["X-API-Key"] = self.api_key
            self._client = httpx.AsyncClient(base_url=self.endpoint, headers=headers)
        return self._client

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
        self._client = None

    @staticmethod
    def _unwrap(response: httpx.Response) -> object:
        response.raise_for_status()
        if not response.content:
            return {}
        payload = response.json()
        if isinstance(payload, dict):
            status = payload.get("status")
            if status == "error":
                error = payload.get("error")
                if isinstance(error, Mapping):
                    message = error.get("message")
                    if isinstance(message, str) and message:
                        raise RuntimeError(message)
                raise RuntimeError("OpenViking request failed")
            if "result" in payload:
                return payload["result"]
        return payload

    async def _get(
        self,
        path: str,
        params: dict[str, str | int | float | bool | None],
    ) -> object:
        client = await self._ensure_client()
        response = await client.get(path, params=params)
        return self._unwrap(response)

    async def _post(self, path: str, payload: dict[str, object]) -> object:
        client = await self._ensure_client()
        response = await client.post(path, json=payload)
        return self._unwrap(response)

    async def _delete(self, path: str) -> None:
        client = await self._ensure_client()
        response = await client.delete(path)
        self._unwrap(response)

    async def ensure_session(self, session_id: str) -> None:
        del session_id
        return None

    async def session_exists(self, session_id: str) -> bool:
        client = await self._ensure_client()
        response = await client.get(f"/api/v1/sessions/{session_id}")
        return response.status_code == 200

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str | None = None,
        parts: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {"role": role}
        if parts is not None:
            payload["parts"] = parts
        elif content is not None:
            payload["content"] = content
        result = await self._post(f"/api/v1/sessions/{session_id}/messages", payload)
        return result if isinstance(result, dict) else {}

    async def commit_session(self, session_id: str) -> dict[str, object]:
        result = await self._post(f"/api/v1/sessions/{session_id}/commit", {"telemetry": False})
        return result if isinstance(result, dict) else {}

    async def delete_session(self, session_id: str) -> None:
        await self._delete(f"/api/v1/sessions/{session_id}")

    async def search(
        self,
        query: str,
        target_uri: str = "",
        session_id: str | None = None,
        limit: int = 10,
        score_threshold: float | None = None,
    ) -> object:
        payload: dict[str, object] = {
            "query": query,
            "target_uri": target_uri,
            "session_id": session_id,
            "limit": limit,
            "score_threshold": score_threshold,
        }
        return await self._post("/api/v1/search/search", payload)

    async def find(
        self,
        query: str,
        target_uri: str = "",
        limit: int = 10,
        score_threshold: float | None = None,
        mode: str = "auto",
    ) -> object:
        payload: dict[str, object] = {
            "query": query,
            "target_uri": target_uri,
            "limit": limit,
            "score_threshold": score_threshold,
        }
        if mode != "auto":
            payload["mode"] = mode
        return await self._post("/api/v1/search/find", payload)

    async def abstract(self, uri: str) -> str:
        result = await self._get("/api/v1/content/abstract", {"uri": uri})
        return result if isinstance(result, str) else str(result)

    async def overview(self, uri: str) -> str:
        result = await self._get("/api/v1/content/overview", {"uri": uri})
        return result if isinstance(result, str) else str(result)

    async def read(self, uri: str) -> str:
        result = await self._get("/api/v1/content/read", {"uri": uri})
        return result if isinstance(result, str) else str(result)

    async def ls(self, uri: str) -> list[object]:
        result = await self._get("/api/v1/fs/ls", {"uri": uri})
        return result if isinstance(result, list) else []

    async def tree(self, uri: str) -> list[object]:
        result = await self._get("/api/v1/fs/tree", {"uri": uri})
        return result if isinstance(result, list) else []

    async def stat(self, uri: str) -> dict[str, object]:
        result = await self._get("/api/v1/fs/stat", {"uri": uri})
        return result if isinstance(result, dict) else {}

    async def add_resource(self, path: str, reason: str = "") -> dict[str, object]:
        payload: dict[str, object] = {"path": path}
        if reason:
            payload["reason"] = reason
        result = await self._post("/api/v1/resources", payload)
        return result if isinstance(result, dict) else {}

    async def wait_processed(self, timeout: float | None = None) -> dict[str, object]:
        result = await self._post("/api/v1/system/wait", {"timeout": timeout})
        return result if isinstance(result, dict) else {}


class OpenVikingProviderConfig(BaseModel):
    model_config = {"populate_by_name": True}

    endpoint: str = Field(default="http://127.0.0.1:1933", alias="endpoint")
    api_key: str = Field(default="", alias="apiKey")
    mode: str = Field(default="embedded", alias="mode")
    config_path: str | None = Field(default=None, alias="configPath")
    storage_subdir: str = Field(default="openviking_memory", alias="storageSubdir")
    ov_config: dict[str, object] = Field(
        default_factory=_default_ov_config,
        alias="ovConfig",
    )
    search_mode: str = Field(default="search", alias="searchMode")
    search_limit: int = Field(default=3, alias="searchLimit", ge=1)
    score_threshold: float | None = Field(default=None, alias="scoreThreshold")
    target_uri: str = Field(default="", alias="targetUri")
    extra_target_uris: list[str] = Field(default_factory=list, alias="extraTargetUris")
    recall_timeout_s: float = Field(default=20.0, alias="recallTimeoutS", gt=0)
    wait_processed_timeout_s: float = Field(default=30.0, alias="waitProcessedTimeoutS", gt=0)

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        mode = value.strip().lower()
        if mode not in {"embedded", "http"}:
            raise ValueError("mode must be either 'embedded' or 'http'")
        return mode

    @field_validator("endpoint", "api_key", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ValueError("endpoint/apiKey must be strings")
        return value.strip()

    @field_validator("config_path", mode="before")
    @classmethod
    def normalize_config_path(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("configPath must be a string")
        text = value.strip()
        return text or None

    @field_validator("storage_subdir")
    @classmethod
    def validate_storage_subdir(cls, value: str) -> str:
        leaf = value.strip()
        if (
            not leaf
            or leaf in {".", ".."}
            or "/" in leaf
            or "\\" in leaf
            or Path(leaf).is_absolute()
        ):
            raise ValueError(
                "storageSubdir must be a single leaf directory name under <workspace>/memory"
            )
        return leaf

    @field_validator("search_mode")
    @classmethod
    def validate_search_mode(cls, value: str) -> str:
        mode = value.strip().lower()
        if mode not in {"search", "find"}:
            raise ValueError("searchMode must be either 'search' or 'find'")
        return mode

    @field_validator("extra_target_uris", mode="before")
    @classmethod
    def validate_extra_target_uris(cls, value: object) -> list[str]:
        if value in (None, ""):
            return []
        if not isinstance(value, list):
            raise ValueError("extraTargetUris must be a list of strings")
        cleaned: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("extraTargetUris must be a list of strings")
            text = item.strip()
            if text:
                cleaned.append(text)
        return cleaned


class OpenVikingState(TypedDict):
    liveSessionId: str
    liveGeneration: int
    mirroredCount: int
    archivedThrough: int
    archiveRound: int
    staleSessionIds: list[str]


class OpenVikingRecallBuckets(TypedDict):
    memories: list[object]
    resources: list[object]
    skills: list[object]


class OpenVikingService:
    def __init__(self, config: OpenVikingProviderConfig, deps: MemoryRuntimeDeps) -> None:
        self.config = config
        self.deps = deps
        self.storage_root = deps.workspace / "memory" / config.storage_subdir
        self.sessions = deps.sessions
        self._transport: OpenVikingTransportProtocol | None = None
        self._transport_init_lock = asyncio.Lock()
        self._locks: dict[str, asyncio.Lock] = {}

    def _get_lock(self, session_key: str) -> asyncio.Lock:
        return self._locks.setdefault(session_key, asyncio.Lock())

    def _build_transport(self) -> OpenVikingTransportProtocol:
        if self.config.mode == "http":
            return HttpOpenVikingTransport(
                endpoint=self.config.endpoint,
                api_key=self.config.api_key,
            )
        return EmbeddedOpenVikingTransport(
            config=self.config,
            storage_root=self.storage_root,
        )

    async def _ensure_transport(self) -> OpenVikingTransportProtocol:
        if self._transport is not None:
            return self._transport

        async with self._transport_init_lock:
            if self._transport is not None:
                return self._transport
            transport = self._build_transport()
            await transport.initialize()
            self._transport = transport
            return transport

    @staticmethod
    def _slug_token(value: str) -> str:
        cleaned = "".join(
            ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value.strip()
        ).strip("_")
        return cleaned or "session"

    def _session_key_token(self, session: object) -> str:
        session_key = self._session_key(session) or "session"
        return self._slug_token(session_key)

    def _session_instance_token(self, session: object) -> str:
        archive_session_id = getattr(session, "archive_session_id", None)
        if isinstance(archive_session_id, str) and archive_session_id:
            return self._slug_token(archive_session_id.removeprefix("session-"))[:12]
        created_at = getattr(session, "created_at", None)
        if isinstance(created_at, datetime):
            return created_at.strftime("%Y%m%d%H%M")
        session_key = self._session_key(session) or "session"
        return self._slug_token(session_key)[:12]

    def _live_session_id(self, session: object, live_generation: int = 0) -> str:
        return (
            f"aeloon-live-{self._session_key_token(session)}-"
            f"{self._session_instance_token(session)}-g{live_generation:03d}"
        )

    def _archive_session_id(self, session: object, archive_round: int) -> str:
        return (
            f"aeloon-archive-{self._session_key_token(session)}-"
            f"{self._session_instance_token(session)}-r{archive_round:03d}"
        )

    def _default_state(self, session: object) -> OpenVikingState:
        return {
            "liveSessionId": self._live_session_id(session),
            "liveGeneration": 0,
            "mirroredCount": 0,
            "archivedThrough": 0,
            "archiveRound": 0,
            "staleSessionIds": [],
        }

    def _read_state(self, session: object) -> OpenVikingState:
        session_key = self._session_key(session)
        if session_key is None:
            return {
                "liveSessionId": "",
                "liveGeneration": 0,
                "mirroredCount": 0,
                "archivedThrough": 0,
                "archiveRound": 0,
                "staleSessionIds": [],
            }
        state = self._default_state(session)
        memory_state = getattr(session, "memory_state", None)
        raw_state = memory_state.get("openviking") if isinstance(memory_state, dict) else None
        if not isinstance(raw_state, Mapping):
            return state

        live_session_id = raw_state.get("liveSessionId")
        if isinstance(live_session_id, str) and live_session_id:
            state["liveSessionId"] = live_session_id
        live_generation = raw_state.get("liveGeneration")
        if isinstance(live_generation, int) and live_generation >= 0:
            state["liveGeneration"] = live_generation
        mirrored_count = raw_state.get("mirroredCount")
        if isinstance(mirrored_count, int) and mirrored_count >= 0:
            state["mirroredCount"] = mirrored_count
        archived_through = raw_state.get("archivedThrough")
        if isinstance(archived_through, int) and archived_through >= 0:
            state["archivedThrough"] = archived_through
        archive_round = raw_state.get("archiveRound")
        if isinstance(archive_round, int) and archive_round >= 0:
            state["archiveRound"] = archive_round
        stale_session_ids = raw_state.get("staleSessionIds")
        if isinstance(stale_session_ids, list):
            state["staleSessionIds"] = [
                item for item in stale_session_ids if isinstance(item, str) and item
            ]
        return state

    def _persist_state(self, session: object, state: OpenVikingState) -> None:
        memory_state = getattr(session, "memory_state", None)
        if isinstance(memory_state, dict):
            memory_state["openviking"] = dict(state)
        if isinstance(session, Session):
            self.sessions.save(session)

    def _active_search_session_id(self, session: object) -> str | None:
        session_key = self._session_key(session)
        if session_key is None:
            return None
        state = self._read_state(session)
        if state["mirroredCount"] <= state["archivedThrough"]:
            return None
        return state["liveSessionId"]

    @staticmethod
    def _remember_stale_session_id(state: OpenVikingState, session_id: str) -> None:
        if session_id and session_id not in state["staleSessionIds"]:
            state["staleSessionIds"].append(session_id)

    async def _ensure_session_exists(
        self,
        transport: OpenVikingTransportProtocol,
        session_id: str,
    ) -> None:
        await transport.ensure_session(session_id)

    def _recall_targets(self) -> list[str]:
        unique: list[str] = []
        seen: set[str] = set()
        for raw_target in [self.config.target_uri, *self.config.extra_target_uris]:
            target = raw_target.strip()
            if not target or target in seen:
                continue
            seen.add(target)
            unique.append(target)
        if unique:
            return unique
        return [self.config.target_uri.strip()]

    async def _recall_one(
        self,
        *,
        transport: OpenVikingTransportProtocol,
        query: str,
        target_uri: str,
        session_id: str | None,
    ) -> object:
        if self.config.search_mode == "find":
            return await asyncio.wait_for(
                transport.find(
                    query=query,
                    target_uri=target_uri,
                    limit=self.config.search_limit,
                    score_threshold=self.config.score_threshold,
                ),
                timeout=self.config.recall_timeout_s,
            )
        return await asyncio.wait_for(
            transport.search(
                query=query,
                target_uri=target_uri,
                session_id=session_id,
                limit=self.config.search_limit,
                score_threshold=self.config.score_threshold,
            ),
            timeout=self.config.recall_timeout_s,
        )

    @staticmethod
    def _result_contexts(result: object, field: str) -> list[object]:
        if isinstance(result, Mapping):
            value = result.get(field)
        else:
            value = getattr(result, field, None)
        return list(value) if isinstance(value, list) else []

    @staticmethod
    def _value(obj: object, name: str) -> object | None:
        if isinstance(obj, Mapping):
            return obj.get(name)
        return getattr(obj, name, None)

    def _context_score(self, context: object) -> float | None:
        raw_score = self._value(context, "score")
        if isinstance(raw_score, int | float):
            return float(raw_score)
        return None

    def _sorted_contexts(self, matches: list[object]) -> list[object]:
        return sorted(
            matches,
            key=lambda match: self._context_score(match) or 0.0,
            reverse=True,
        )

    def _merge_recall_results(self, results: list[object]) -> OpenVikingRecallBuckets:
        merged: dict[str, dict[str, object]] = {
            "memories": {},
            "resources": {},
            "skills": {},
        }
        for result in results:
            for field in ("memories", "resources", "skills"):
                bucket = merged[field]
                for context in self._result_contexts(result, field):
                    raw_uri = self._value(context, "uri")
                    uri = raw_uri if isinstance(raw_uri, str) else ""
                    key = uri or f"{field}:{context!r}"
                    existing = bucket.get(key)
                    if existing is None:
                        bucket[key] = context
                        continue
                    existing_score = self._context_score(existing)
                    current_score = self._context_score(context)
                    resolved_existing = (
                        existing_score if existing_score is not None else float("-inf")
                    )
                    resolved_current = current_score if current_score is not None else float("-inf")
                    if resolved_current > resolved_existing:
                        bucket[key] = context

        return {
            "memories": self._sorted_contexts(list(merged["memories"].values())),
            "resources": self._sorted_contexts(list(merged["resources"].values())),
            "skills": self._sorted_contexts(list(merged["skills"].values())),
        }

    def _filtered_contexts(self, result: object) -> list[object]:
        matches = self._sorted_contexts(
            [
                *self._result_contexts(result, "memories"),
                *self._result_contexts(result, "resources"),
                *self._result_contexts(result, "skills"),
            ]
        )
        threshold = self.config.score_threshold
        if threshold is None:
            return matches[: self.config.search_limit]
        filtered = [
            match
            for match in matches
            if (score := self._context_score(match)) is None or score >= threshold
        ]
        return filtered[: self.config.search_limit]

    def _format_context(self, context: object) -> str:
        abstract = self._value(context, "abstract")
        overview = self._value(context, "overview")
        category = self._value(context, "category")
        uri = self._value(context, "uri")
        match_reason = self._value(context, "match_reason")
        score = self._context_score(context)

        text = abstract if isinstance(abstract, str) and abstract else ""
        if not text and isinstance(overview, str) and overview:
            text = overview
        if not text and isinstance(uri, str):
            text = uri

        details: list[str] = []
        if isinstance(category, str) and category:
            details.append(category)
        if isinstance(uri, str) and uri:
            details.append(uri)
        if score is not None:
            details.append(f"score={score:.2f}")
        if isinstance(match_reason, str) and match_reason:
            details.append(match_reason)

        suffix = f" ({'; '.join(details)})" if details else ""
        return f"- {text}{suffix}"

    def _build_recall_section(self, result: object) -> str:
        matches = self._filtered_contexts(result)
        if not matches:
            return "# OpenViking Recall\n\n(none)"
        body = "\n".join(self._format_context(match) for match in matches)
        return f"# OpenViking Recall\n\n{body}"

    def _session_key(self, session: object) -> str | None:
        key = getattr(session, "key", None)
        return key if isinstance(key, str) and key else None

    def _session_messages(self, session: object) -> list[MessagePayload] | None:
        messages = getattr(session, "messages", None)
        if not isinstance(messages, list):
            return None
        return messages

    def _message_content(self, content: object) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, Mapping):
                    if item.get("type") == "text":
                        text = item.get("text")
                        if isinstance(text, str) and text:
                            parts.append(text)
                            continue
                    meta = item.get("_meta")
                    if item.get("type") == "image_url" and isinstance(meta, Mapping):
                        path = meta.get("path")
                        if isinstance(path, str) and path:
                            parts.append(f"[image: {path}]")
                            continue
                parts.append(json.dumps(item, ensure_ascii=False))
            return "\n".join(part for part in parts if part)
        if content is None:
            return ""
        return json.dumps(content, ensure_ascii=False)

    def _transcript_message(self, message: MessagePayload) -> tuple[str, str] | None:
        role = message.get("role")
        if not isinstance(role, str):
            return None
        if role == "tool":
            name = message.get("name")
            tool_name = name if isinstance(name, str) and name else "tool"
            content = self._message_content(message.get("content"))
            return "assistant", f"[tool:{tool_name}] {content}".strip()
        if role not in {"user", "assistant"}:
            return None
        content = self._message_content(message.get("content"))
        return role, content

    async def _replace_session_messages(
        self,
        transport: OpenVikingTransportProtocol,
        session_id: str,
        messages: list[MessagePayload],
    ) -> None:
        if await transport.session_exists(session_id):
            await transport.delete_session(session_id)
        await self._ensure_session_exists(transport, session_id)
        for message in messages:
            transcript = self._transcript_message(message)
            if transcript is None:
                continue
            await transport.add_message(
                session_id=session_id,
                role=transcript[0],
                content=transcript[1],
            )

    async def _sync_live_session(
        self,
        *,
        transport: OpenVikingTransportProtocol,
        session: object,
        state: OpenVikingState,
        messages: list[MessagePayload],
    ) -> None:
        del session
        if state["mirroredCount"] > len(messages):
            return
        live_session_id = state["liveSessionId"]
        await self._ensure_session_exists(transport, live_session_id)
        for message in messages[state["mirroredCount"] :]:
            transcript = self._transcript_message(message)
            if transcript is None:
                continue
            await transport.add_message(
                session_id=live_session_id,
                role=transcript[0],
                content=transcript[1],
            )
        state["mirroredCount"] = len(messages)

    async def _commit_archive_session(
        self,
        *,
        transport: OpenVikingTransportProtocol,
        archive_session_id: str,
        messages: list[MessagePayload],
    ) -> None:
        await self._replace_session_messages(transport, archive_session_id, messages)
        await transport.commit_session(archive_session_id)
        await transport.wait_processed(timeout=self.config.wait_processed_timeout_s)

    async def _rotate_live_session(
        self,
        *,
        transport: OpenVikingTransportProtocol,
        session: object,
        state: OpenVikingState,
        remaining_messages: list[MessagePayload],
    ) -> None:
        previous_live_session_id = state["liveSessionId"]
        if previous_live_session_id:
            self._remember_stale_session_id(state, previous_live_session_id)
        state["liveGeneration"] += 1
        state["liveSessionId"] = self._live_session_id(session, state["liveGeneration"])
        await self._replace_session_messages(
            transport,
            state["liveSessionId"],
            remaining_messages,
        )

    def _format_tool_search_result(self, result: object) -> dict[str, object]:
        formatted: list[dict[str, object]] = []
        for bucket_name in ("memories", "resources", "skills"):
            for item in self._result_contexts(result, bucket_name):
                entry: dict[str, object] = {
                    "uri": self._value(item, "uri") or "",
                    "type": bucket_name.rstrip("s"),
                    "abstract": self._value(item, "abstract") or "",
                }
                score = self._context_score(item)
                if score is not None:
                    entry["score"] = round(score, 3)
                relations = self._value(item, "relations")
                if isinstance(relations, list):
                    related = [
                        relation_uri
                        for relation in relations[:3]
                        if isinstance(relation, Mapping)
                        and isinstance((relation_uri := relation.get("uri")), str)
                    ]
                    if related:
                        entry["related"] = related
                formatted.append(entry)
        total = self._value(result, "total")
        return {
            "results": formatted,
            "total": total if isinstance(total, int) else len(formatted),
        }

    async def _append_live_message(
        self,
        *,
        session_key: str,
        role: str,
        content: str | None = None,
        parts: list[dict[str, object]] | None = None,
    ) -> None:
        transport = await self._ensure_transport()
        session = self.sessions.get_or_create(session_key)
        state = self._read_state(session)
        live_session_id = state["liveSessionId"]
        await self._ensure_session_exists(transport, live_session_id)
        await transport.add_message(
            session_id=live_session_id,
            role=role,
            content=content,
            parts=parts,
        )
        self._persist_state(session, state)

    @staticmethod
    def _read_result_content(result: object) -> str:
        if isinstance(result, str):
            return result
        if isinstance(result, Mapping):
            content = result.get("content")
            if isinstance(content, str):
                return content
        return str(result)

    @staticmethod
    def _extract_root_uri(result: object) -> str:
        if isinstance(result, Mapping):
            for key in ("root_uri", "uri", "resource_uri"):
                value = result.get(key)
                if isinstance(value, str) and value:
                    return value
        return ""

    async def queue_prefetch(self, *, session: object, query: str) -> None:
        if not query.strip():
            return
        await self._recall(session, query)

    async def tool_search(
        self,
        *,
        session_key: str,
        query: str,
        mode: str = "auto",
        scope: str = "",
        limit: int = 10,
    ) -> str:
        del session_key
        transport = await self._ensure_transport()
        result = await transport.find(
            query=query,
            target_uri=scope,
            limit=limit,
            score_threshold=self.config.score_threshold,
            mode=mode,
        )
        return json.dumps(self._format_tool_search_result(result), ensure_ascii=False)

    async def tool_read(self, *, uri: str, level: str = "overview") -> str:
        transport = await self._ensure_transport()
        if level == "abstract":
            result = await transport.abstract(uri)
        elif level == "full":
            result = await transport.read(uri)
        else:
            result = await transport.overview(uri)
        content = self._read_result_content(result)
        if len(content) > 8000:
            content = (
                content[:8000] + "\n\n[... truncated, use a more specific URI or abstract level]"
            )
        return json.dumps(
            {
                "uri": uri,
                "level": level,
                "content": content,
            },
            ensure_ascii=False,
        )

    async def tool_browse(self, *, action: str, path: str = "viking://") -> str:
        transport = await self._ensure_transport()
        result: object
        if action == "tree":
            result = await transport.tree(path)
        elif action == "stat":
            stat_result = await transport.stat(path)
            return json.dumps(stat_result, ensure_ascii=False)
        else:
            result = await transport.ls(path)

        entries: list[dict[str, object]] = []
        if isinstance(result, list):
            for item in result[:50]:
                if not isinstance(item, Mapping):
                    continue
                entries.append(
                    {
                        "name": item.get("rel_path", item.get("name", "")),
                        "uri": item.get("uri", ""),
                        "type": "dir" if bool(item.get("isDir")) else "file",
                        "abstract": item.get("abstract", ""),
                    }
                )
        return json.dumps({"path": path, "entries": entries}, ensure_ascii=False)

    async def tool_remember(
        self,
        *,
        session_key: str,
        content: str,
        category: str = "",
    ) -> str:
        text = f"[Remember] {content}"
        if category:
            text = f"[Remember — {category}] {content}"
        await self._append_live_message(
            session_key=session_key,
            role="user",
            parts=[{"type": "text", "text": text}],
        )
        return json.dumps(
            {
                "status": "stored",
                "message": "Memory recorded. Will be extracted and indexed on session commit.",
            },
            ensure_ascii=False,
        )

    async def tool_add_resource(self, *, url: str, reason: str = "") -> str:
        transport = await self._ensure_transport()
        result = await transport.add_resource(path=url, reason=reason)
        return json.dumps(
            {
                "status": "added",
                "root_uri": self._extract_root_uri(result),
                "message": "Resource queued for processing. Use viking_search after a moment to find it.",
            },
            ensure_ascii=False,
        )

    async def mirror_prompt_memory_write(
        self,
        *,
        action: str,
        target: str,
        content: str,
        session_key: str | None = None,
    ) -> None:
        if action not in {"add", "replace"} or not content or not session_key:
            return
        await self._append_live_message(
            session_key=session_key,
            role="user",
            parts=[
                {
                    "type": "text",
                    "text": f"[Memory note — {target}] {content}",
                }
            ],
        )

    async def finalize_session(
        self,
        *,
        session: object,
        pending_messages: list[MessagePayload],
        reason: str | None = None,
    ) -> None:
        del reason
        session_key = self._session_key(session)
        if session_key is None:
            return

        lock = self._get_lock(session_key)
        async with lock:
            transport = await self._ensure_transport()
            messages = self._session_messages(session)
            state = self._read_state(session)
            del pending_messages
            if messages is None:
                return
            await self._sync_live_session(
                transport=transport,
                session=session,
                state=state,
                messages=messages,
            )
            archive_messages = messages[state["archivedThrough"] :]
            if archive_messages:
                next_archive_round = state["archiveRound"] + 1
                await self._commit_archive_session(
                    transport=transport,
                    archive_session_id=self._archive_session_id(session, next_archive_round),
                    messages=archive_messages,
                )
                state["archiveRound"] = next_archive_round
                state["archivedThrough"] = len(messages)
            live_session_id = state["liveSessionId"]
            if live_session_id and await transport.session_exists(live_session_id):
                self._remember_stale_session_id(state, live_session_id)
            self._persist_state(session, state)

    def _session_has_suffix(
        self,
        session: object,
        persisted_new_messages: list[MessagePayload],
    ) -> bool:
        messages = self._session_messages(session)
        if messages is None:
            return False
        if not persisted_new_messages:
            return True
        if len(messages) < len(persisted_new_messages):
            return False
        return messages[-len(persisted_new_messages) :] == persisted_new_messages

    async def build_recall_section(self, *, session: object, query: str) -> str:
        if not query.strip():
            return ""
        session_key = self._session_key(session)
        if session_key is None:
            result = await self._recall(session, query)
            return self._build_recall_section(result)

        lock = self._get_lock(session_key)
        async with lock:
            result = await self._recall(session, query)
            return self._build_recall_section(result)

    async def _recall(self, session: object, query: str) -> OpenVikingRecallBuckets:
        transport = await self._ensure_transport()
        session_id = self._active_search_session_id(session)
        if session_id is not None and not await transport.session_exists(session_id):
            session_id = None
        results: list[object] = []
        for target_uri in self._recall_targets():
            result = await self._recall_one(
                transport=transport,
                query=query,
                target_uri=target_uri,
                session_id=session_id,
            )
            results.append(result)
        return self._merge_recall_results(results)

    async def mirror_turn(
        self,
        *,
        session: object,
        persisted_new_messages: list[MessagePayload],
    ) -> None:
        session_key = self._session_key(session)
        if session_key is None or not persisted_new_messages:
            return

        lock = self._get_lock(session_key)
        async with lock:
            if not self._session_has_suffix(session, persisted_new_messages):
                return

            transport = await self._ensure_transport()
            messages = self._session_messages(session)
            if messages is None:
                return

            state = self._read_state(session)
            await self._sync_live_session(
                transport=transport,
                session=session,
                state=state,
                messages=messages,
            )
            self._persist_state(session, state)

    async def archive_pending_slice(
        self,
        *,
        session: object,
        pending_messages: list[MessagePayload],
    ) -> None:
        session_key = self._session_key(session)
        if session_key is None:
            return

        lock = self._get_lock(session_key)
        async with lock:
            transport = await self._ensure_transport()
            messages = self._session_messages(session)
            if messages is None:
                return
            state = self._read_state(session)
            await self._sync_live_session(
                transport=transport,
                session=session,
                state=state,
                messages=messages,
            )
            if not pending_messages:
                self._persist_state(session, state)
                return
            next_archive_round = state["archiveRound"] + 1
            await self._commit_archive_session(
                transport=transport,
                archive_session_id=self._archive_session_id(session, next_archive_round),
                messages=pending_messages,
            )
            state["archiveRound"] = next_archive_round
            state["archivedThrough"] = min(
                len(messages),
                state["archivedThrough"] + len(pending_messages),
            )
            await self._rotate_live_session(
                transport=transport,
                session=session,
                state=state,
                remaining_messages=messages[state["archivedThrough"] :],
            )
            self._persist_state(session, state)

    async def shutdown(self) -> None:
        if self._transport is not None:
            await self._transport.close()
            self._transport = None
