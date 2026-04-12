"""Memory backend registry and factory helpers."""

from __future__ import annotations

import importlib
from collections.abc import Mapping

from aeloon.memory.base import MemoryBackend, MemoryBackendDeps
from aeloon.memory.errors import InvalidMemoryBackendClassError, UnknownMemoryBackendError

_REGISTRY: dict[str, type[MemoryBackend]] = {}


def _validate_backend_class(candidate: object) -> type[MemoryBackend]:
    if not isinstance(candidate, type) or not issubclass(candidate, MemoryBackend):
        raise InvalidMemoryBackendClassError("Memory backend must subclass MemoryBackend")
    return candidate


def register_backend(cls: type[MemoryBackend]) -> type[MemoryBackend]:
    """Register a backend class by its declared name."""
    backend_name = cls.backend_name.strip()
    if not backend_name:
        raise InvalidMemoryBackendClassError("Memory backend must define backend_name")
    _REGISTRY[backend_name] = _validate_backend_class(cls)
    return cls


def _get_class_path(raw_cfg: Mapping[str, object]) -> str | None:
    """Return canonical classPath; class_path remains supported for backward compatibility."""
    for key in ("classPath", "class_path"):
        value = raw_cfg.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def resolve_backend_class(name: str, raw_cfg: Mapping[str, object]) -> type[MemoryBackend]:
    """Resolve a backend either from the registry or an explicit class path."""
    class_path = _get_class_path(raw_cfg)
    if class_path is not None:
        module_name, _, attr_name = class_path.rpartition(".")
        if not module_name or not attr_name:
            raise InvalidMemoryBackendClassError(
                f"Invalid memory backend class path: {class_path}"
            )
        module = importlib.import_module(module_name)
        return _validate_backend_class(getattr(module, attr_name))

    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise UnknownMemoryBackendError(f"Unknown memory backend: {name}") from exc


def build_backend(
    name: str,
    raw_cfg: Mapping[str, object],
    deps: MemoryBackendDeps,
) -> MemoryBackend:
    """Construct a backend instance from raw config and shared dependencies."""
    backend_cls = resolve_backend_class(name, raw_cfg)
    config = backend_cls.config_model.model_validate(dict(raw_cfg))
    return backend_cls(config, deps)
