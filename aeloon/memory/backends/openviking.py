"""OpenViking memory backend."""

from __future__ import annotations

import asyncio
import copy
import importlib
import importlib.util
import json
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha1
from pathlib import Path
from typing import Protocol, TypedDict, cast

from loguru import logger
from pydantic import Field, field_validator, model_validator

from aeloon.core.config.paths import get_logs_dir
from aeloon.core.session.manager import Session
from aeloon.memory.base import (
    MemoryBackend,
    MemoryBackendConfig,
    MemoryBackendDeps,
    PreparedMemoryContext,
)
from aeloon.memory.errors import MissingMemoryBackendDependencyError
from aeloon.memory.registry import register_backend
from aeloon.memory.types import MessagePayload
from aeloon.utils.helpers import estimate_message_tokens, estimate_prompt_tokens_chain


class OpenVikingSessionProtocol(Protocol):
    """Protocol for OpenViking session operations used by the backend."""

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
    """Protocol for the async OpenViking client used by the backend."""

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

    async def wait_processed(self, timeout: float | None = None) -> dict[str, object]: ...


class OpenVikingClientFactoryProtocol(Protocol):
    """Protocol for the AsyncOpenViking singleton class."""

    def __call__(self, path: str | None = None) -> OpenVikingClientProtocol: ...

    async def reset(self) -> None: ...


class OpenVikingConfigSingletonProtocol(Protocol):
    """Protocol for OpenVikingConfigSingleton."""

    def initialize(
        self,
        config_dict: dict[str, object] | None = None,
        config_path: str | None = None,
    ) -> object: ...

    def reset_instance(self) -> None: ...


@dataclass(frozen=True, slots=True)
class OpenVikingRuntime:
    """Loaded optional OpenViking runtime objects."""

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


def reset_openviking_runtime_for_tests() -> None:
    """Reset OpenViking singletons for test isolation when the dependency is installed."""

    runtime = _load_openviking_runtime()
    runtime.config_singleton.reset_instance()


class OpenVikingMemoryConfig(MemoryBackendConfig):
    """Configuration for the OpenViking memory backend."""

    storage_subdir: str = Field(default="openviking_memory", alias="storageSubdir")
    ov_config: dict[str, object] = Field(default_factory=dict, alias="ovConfig")
    search_mode: str = Field(default="search", alias="searchMode")
    search_limit: int = Field(default=3, alias="searchLimit", ge=1)
    score_threshold: float | None = Field(default=None, alias="scoreThreshold")
    target_uri: str = Field(default="", alias="targetUri")
    extra_target_uris: list[str] = Field(default_factory=list, alias="extraTargetUris")
    trigger_ratio: float = Field(default=1.0, alias="triggerRatio")
    target_ratio: float = Field(default=0.5, alias="targetRatio")
    max_commit_rounds: int = Field(default=5, alias="maxCommitRounds", ge=1)
    recall_timeout_s: float = Field(default=20.0, alias="recallTimeoutS", gt=0)
    wait_processed_timeout_s: float = Field(
        default=30.0,
        alias="waitProcessedTimeoutS",
        gt=0,
    )

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

    @model_validator(mode="after")
    def validate_config(self) -> "OpenVikingMemoryConfig":
        if not self.ov_config:
            raise ValueError("ovConfig is required when memory.backend is 'openviking'")
        return self


@register_backend
class OpenVikingMemoryBackend(MemoryBackend):
    """Backend that routes memory behavior through embedded OpenViking."""

    backend_name = "openviking"
    config_model = OpenVikingMemoryConfig
    hidden_skill_names = ["memory"]
    config: OpenVikingMemoryConfig

    def __init__(self, config: OpenVikingMemoryConfig, deps: MemoryBackendDeps):
        super().__init__(config, deps)
        try:
            self.runtime = _load_openviking_runtime()
        except ImportError as exc:
            raise MissingMemoryBackendDependencyError(
                "OpenViking memory backend requires the optional dependency. "
                "Install it with `pip install openviking --upgrade --force-reinstall`."
            ) from exc
        self.storage_root = deps.workspace / "memory" / config.storage_subdir
        self.sessions = deps.sessions
        self.context_window_tokens = deps.context_window_tokens
        self._build_messages = deps.build_messages
        self._get_tool_definitions = deps.get_tool_definitions
        self._client: OpenVikingClientProtocol | None = None
        self._client_init_lock = asyncio.Lock()
        self._locks: dict[str, asyncio.Lock] = {}

    def _get_lock(self, session_key: str) -> asyncio.Lock:
        return self._locks.setdefault(session_key, asyncio.Lock())

    def _prepare_inline_config(self) -> dict[str, object]:
        inline = copy.deepcopy(self.config.ov_config)
        raw_storage = inline.get("storage")
        if not isinstance(raw_storage, Mapping):
            raise RuntimeError("OpenViking ovConfig must contain a 'storage' object.")
        storage = dict(raw_storage)
        storage["workspace"] = str(self.storage_root)
        inline["storage"] = storage
        if "log" not in inline:
            inline["log"] = {
                "level": "WARNING",
                "output": str(get_logs_dir() / "openviking.log"),
            }
        return inline

    async def _ensure_client(self) -> OpenVikingClientProtocol:
        if self._client is not None:
            return self._client

        async with self._client_init_lock:
            if self._client is not None:
                return self._client

            inline_config = self._prepare_inline_config()
            self.storage_root.mkdir(parents=True, exist_ok=True)
            self.runtime.config_singleton.initialize(config_dict=inline_config)
            client = self.runtime.async_openviking_cls(path=str(self.storage_root))
            await client.initialize()
            self._client = client
            return client

    def _live_session_id(self, session_key: str) -> str:
        return f"aeloon-live-{session_key.replace(':', '_')}"

    def _archive_session_id(self, session_key: str, messages: list[MessagePayload]) -> str:
        digest = sha1(self._archive_digest_input(messages).encode("utf-8")).hexdigest()[:12]
        return f"aeloon-archive-{session_key.replace(':', '_')}-{digest}"

    def _active_search_session_id(self, session: object) -> str | None:
        session_key = getattr(session, "key", None)
        if not isinstance(session_key, str) or not session_key:
            return None
        state = self._read_state(session)
        if state["mirroredCount"] <= state["archivedThrough"]:
            return None
        return state["liveSessionId"]

    async def _ensure_session_exists(
        self,
        client: OpenVikingClientProtocol,
        session_id: str,
    ) -> None:
        if await client.session_exists(session_id):
            return
        session = client.session(session_id=session_id)
        await session.ensure_exists()

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
        client: OpenVikingClientProtocol,
        query: str,
        target_uri: str,
        session_id: str | None,
    ) -> object:
        if self.config.search_mode == "find":
            return await asyncio.wait_for(
                client.find(
                    query=query,
                    target_uri=target_uri,
                    limit=self.config.search_limit,
                    score_threshold=self.config.score_threshold,
                ),
                timeout=self.config.recall_timeout_s,
            )
        return await asyncio.wait_for(
            client.search(
                query=query,
                target_uri=target_uri,
                session_id=session_id,
                limit=self.config.search_limit,
                score_threshold=self.config.score_threshold,
            ),
            timeout=self.config.recall_timeout_s,
        )

    def _merge_recall_results(self, results: list[object]) -> "OpenVikingRecallBuckets":
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

    async def _recall(self, session: object, query: str) -> OpenVikingRecallBuckets:
        client = await self._ensure_client()
        session_id = self._active_search_session_id(session)
        if session_id is not None and not await client.session_exists(session_id):
            session_id = None
        results: list[object] = []
        targets = self._recall_targets()
        for target_uri in targets:
            logger.info(
                "OpenViking recall start query={!r} mode={} target_uri={} limit={} threshold={} session_id={}",
                query,
                self.config.search_mode,
                target_uri,
                self.config.search_limit,
                self.config.score_threshold,
                session_id,
            )
            result = await self._recall_one(
                client=client,
                query=query,
                target_uri=target_uri,
                session_id=session_id,
            )
            logger.info(
                "OpenViking recall result query={!r} target_uri={} raw_counts={{memories:{}, resources:{}, skills:{}}} filtered_hits={}",
                query,
                target_uri,
                len(self._result_contexts(result, "memories")),
                len(self._result_contexts(result, "resources")),
                len(self._result_contexts(result, "skills")),
                [
                    uri
                    for match in self._filtered_contexts(result)
                    if isinstance((uri := self._value(match, "uri")), str) and uri
                ],
            )
            if self.config.search_mode == "search":
                query_plan = self._value(result, "query_plan")
                if query_plan is not None:
                    logger.info(
                        "OpenViking recall query_plan for {!r} target_uri={}: {}",
                        query,
                        target_uri,
                        query_plan,
                    )
            results.append(result)
        return self._merge_recall_results(results)

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

    def _archive_digest_input(self, messages: list[MessagePayload]) -> str:
        parts: list[str] = []
        for message in messages:
            transcript = self._transcript_message(message)
            if transcript is None:
                continue
            parts.append(f"{transcript[0]}:{transcript[1]}")
        return "\n".join(parts)

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

    def _persist_state(self, session: object, state: "OpenVikingState") -> None:
        memory_state = getattr(session, "memory_state", None)
        if isinstance(memory_state, dict):
            memory_state["openviking"] = dict(state)
        if isinstance(session, Session):
            self.sessions.save(session)

    def _default_state(self, session_key: str) -> "OpenVikingState":
        return {
            "liveSessionId": self._live_session_id(session_key),
            "mirroredCount": 0,
            "archivedThrough": 0,
            "archiveRound": 0,
        }

    def _read_state(self, session: object) -> "OpenVikingState":
        session_key = self._session_key(session)
        if session_key is None:
            return {
                "liveSessionId": "",
                "mirroredCount": 0,
                "archivedThrough": 0,
                "archiveRound": 0,
            }
        state = self._default_state(session_key)
        memory_state = getattr(session, "memory_state", None)
        raw_state = memory_state.get("openviking") if isinstance(memory_state, dict) else None
        if not isinstance(raw_state, Mapping):
            return state

        live_session_id = raw_state.get("liveSessionId")
        if isinstance(live_session_id, str) and live_session_id:
            state["liveSessionId"] = live_session_id
        mirrored_count = raw_state.get("mirroredCount")
        if isinstance(mirrored_count, int) and mirrored_count >= 0:
            state["mirroredCount"] = mirrored_count
        archived_through = raw_state.get("archivedThrough")
        if isinstance(archived_through, int) and archived_through >= 0:
            state["archivedThrough"] = archived_through
        archive_round = raw_state.get("archiveRound")
        if isinstance(archive_round, int) and archive_round >= 0:
            state["archiveRound"] = archive_round
        return state

    async def _replace_session_messages(
        self,
        client: OpenVikingClientProtocol,
        session_id: str,
        messages: list[MessagePayload],
    ) -> None:
        if await client.session_exists(session_id):
            await client.delete_session(session_id)
        await self._ensure_session_exists(client, session_id)
        for message in messages:
            transcript = self._transcript_message(message)
            if transcript is None:
                continue
            await client.add_message(
                session_id=session_id, role=transcript[0], content=transcript[1]
            )

    def _estimate_prompt_tokens(self, session: Session, start_index: int) -> int:
        history = session.get_history(start_index=start_index, max_messages=0)
        channel, chat_id = session.key.split(":", 1) if ":" in session.key else (None, None)
        probe_messages = self._build_messages(
            history=history,
            current_message="[token-probe]",
            extra_system_sections=[],
            runtime_lines=["Memory backend: openviking"],
            extra_always_skills=["openviking-memory"],
            channel=channel,
            chat_id=chat_id,
        )
        estimated, _ = estimate_prompt_tokens_chain(
            self.deps.provider,
            self.deps.model,
            probe_messages,
            self._get_tool_definitions(),
        )
        return estimated

    def estimate_session_prompt_tokens(self, session: Session) -> tuple[int, str]:
        state = self._read_state(session)
        estimated = self._estimate_prompt_tokens(session, state["archivedThrough"])
        return estimated, "openviking"

    def _pick_archive_boundary(
        self,
        session: Session,
        start_index: int,
        tokens_to_remove: int,
    ) -> int | None:
        if start_index >= len(session.messages) or tokens_to_remove <= 0:
            return None

        removed_tokens = 0
        last_boundary: int | None = None
        for idx in range(start_index, len(session.messages)):
            message = session.messages[idx]
            if idx > start_index and message.get("role") == "user":
                last_boundary = idx
                if removed_tokens >= tokens_to_remove:
                    return last_boundary
            removed_tokens += estimate_message_tokens(message)
        return last_boundary

    async def _archive_slice(
        self,
        session: Session,
        state: "OpenVikingState",
        end_index: int,
    ) -> bool:
        chunk = session.messages[state["archivedThrough"] : end_index]
        if not chunk:
            return False

        client = await self._ensure_client()
        archive_session_id = self._archive_session_id(session.key, chunk)
        await self._replace_session_messages(client, archive_session_id, chunk)
        await client.commit_session(archive_session_id)
        await client.wait_processed(timeout=self.config.wait_processed_timeout_s)
        await self._replace_session_messages(
            client, state["liveSessionId"], session.messages[end_index:]
        )

        state["archivedThrough"] = end_index
        state["mirroredCount"] = len(session.messages)
        state["archiveRound"] += 1
        self._persist_state(session, state)
        return True

    async def _maybe_archive_by_tokens_locked(self, session: Session) -> None:
        if not session.messages or self.context_window_tokens <= 0:
            return

        state = self._read_state(session)
        trigger = max(1, int(self.context_window_tokens * self.config.trigger_ratio))
        target = max(1, int(self.context_window_tokens * self.config.target_ratio))
        estimated = self._estimate_prompt_tokens(session, state["archivedThrough"])
        if estimated < trigger:
            return

        for _ in range(self.config.max_commit_rounds):
            if estimated <= target:
                return
            boundary = self._pick_archive_boundary(
                session,
                state["archivedThrough"],
                max(1, estimated - target),
            )
            if boundary is None:
                return
            if self.deps.flush_before_loss is not None:
                chunk = session.messages[state["archivedThrough"] : boundary]
                await self.deps.flush_before_loss(
                    session=session,
                    pending_messages=chunk,
                    reason="compression",
                )
            archived = await self._archive_slice(session, state, boundary)
            if not archived:
                return
            estimated = self._estimate_prompt_tokens(session, state["archivedThrough"])

    async def prepare_turn(
        self,
        *,
        session: object,
        query: str,
        channel: str | None,
        chat_id: str | None,
        current_role: str,
    ) -> PreparedMemoryContext:
        session_key = self._session_key(session)
        if session_key is not None:
            lock = self._get_lock(session_key)
            async with lock:
                if isinstance(session, Session):
                    await self._maybe_archive_by_tokens_locked(session)
                result = await self._recall(session, query)
                state = self._read_state(session)
        else:
            result = await self._recall(session, query)
            state = self._read_state(session)
        return PreparedMemoryContext(
            history_start_index=state["archivedThrough"],
            system_sections=[self._build_recall_section(result)],
            runtime_lines=[
                "Memory backend: openviking",
                f"OpenViking storage: {self.storage_root}",
            ],
            always_skill_names=["openviking-memory"],
        )

    async def after_turn(
        self,
        *,
        session: object,
        raw_new_messages: list[MessagePayload],
        persisted_new_messages: list[MessagePayload],
        final_content: str | None,
    ) -> None:
        session_key = self._session_key(session)
        if session_key is None or not persisted_new_messages:
            return None

        lock = self._get_lock(session_key)
        async with lock:
            if not self._session_has_suffix(session, persisted_new_messages):
                return None

            client = await self._ensure_client()
            messages = self._session_messages(session)
            if messages is None:
                return None

            state = self._read_state(session)
            if state["mirroredCount"] > len(messages):
                return None

            live_session_id = state["liveSessionId"]
            await self._ensure_session_exists(client, live_session_id)
            for message in messages[state["mirroredCount"] :]:
                transcript = self._transcript_message(message)
                if transcript is None:
                    continue
                await client.add_message(
                    session_id=live_session_id,
                    role=transcript[0],
                    content=transcript[1],
                )

            state["mirroredCount"] = len(messages)
            self._persist_state(session, state)
        return None

    def pending_start_index(self, session: object) -> int:
        return self._read_state(session)["archivedThrough"]

    async def on_new_session(
        self,
        *,
        session: object,
        pending_messages: list[MessagePayload],
    ) -> None:
        session_key = self._session_key(session)
        if session_key is None:
            return None

        lock = self._get_lock(session_key)
        async with lock:
            client = await self._ensure_client()
            if pending_messages:
                archive_session_id = self._archive_session_id(session_key, pending_messages)
                await self._replace_session_messages(client, archive_session_id, pending_messages)
                await client.commit_session(archive_session_id)
                await client.wait_processed(timeout=self.config.wait_processed_timeout_s)
            await client.delete_session(self._live_session_id(session_key))
        return None

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
        self.runtime.config_singleton.reset_instance()
        await self.runtime.async_openviking_cls.reset()


class OpenVikingState(TypedDict):
    liveSessionId: str
    mirroredCount: int
    archivedThrough: int
    archiveRound: int


class OpenVikingRecallBuckets(TypedDict):
    memories: list[object]
    resources: list[object]
    skills: list[object]
