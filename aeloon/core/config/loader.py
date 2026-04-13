"""Load and save config files."""

import json
import os
from pathlib import Path

from aeloon.core.config.schema import Config

# Track the active config path for multi-instance runs.
_current_config_path: Path | None = None


def set_config_path(path: Path | None) -> None:
    """Set the active config path."""
    global _current_config_path
    _current_config_path = path.expanduser() if path is not None else None


def get_aeloon_home() -> Path:
    """Return the base Aeloon home directory."""
    env_home = os.environ.get("AELOON_HOME", "").strip()
    if env_home:
        return Path(env_home).expanduser()
    return Path.home() / ".aeloon"


def get_config_path() -> Path:
    """Return the config file path."""
    if _current_config_path:
        return _current_config_path
    env_config = os.environ.get("AELOON_CONFIG", "").strip()
    if env_config:
        return Path(env_config).expanduser()
    return get_aeloon_home() / "config.json"


def load_config(config_path: Path | None = None) -> Config:
    """Load config from disk or return defaults."""
    path = (config_path or get_config_path()).expanduser()

    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("Config root must be a JSON object")
            data = _migrate_config(data)
            return Config.model_validate(data)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Warning: Failed to load config from {path}: {e}")
            print("Using default configuration.")

    return Config()


def save_config(config: Config, config_path: Path | None = None) -> None:
    """Write config to disk."""
    path = (config_path or get_config_path()).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = config.model_dump(by_alias=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _migrate_config(data: dict[str, object]) -> dict[str, object]:
    """Apply small config migrations in place."""
    # Move the legacy nested workspace flag to its current location.
    tools = _as_dict(data.get("tools"))
    exec_cfg = _as_dict(tools.get("exec"))
    if "restrictToWorkspace" in exec_cfg and "restrictToWorkspace" not in tools:
        tools["restrictToWorkspace"] = exec_cfg.pop("restrictToWorkspace")
    if tools:
        data["tools"] = tools

    memory = _as_dict(data.get("memory"))
    if memory:
        _migrate_memory_config(memory)
        data["memory"] = memory
    return data


def _as_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _migrate_memory_config(memory: dict[str, object]) -> None:
    prompt = _as_dict(memory.get("prompt"))
    local = _as_dict(memory.get("local"))
    archive = _as_dict(memory.get("archive"))
    flush = _as_dict(memory.get("flush"))
    providers = _as_dict(memory.get("providers"))
    legacy_backends = _as_dict(memory.get("backends"))

    backend_raw = memory.get("backend")
    legacy_backend = backend_raw.strip() if isinstance(backend_raw, str) else ""
    provider_raw = memory.get("provider")
    provider = provider_raw.strip() if isinstance(provider_raw, str) else ""

    file_cfg = _as_dict(legacy_backends.get("file"))
    prompt.setdefault("enabled", True)
    prompt.setdefault("directory", file_cfg.get("memoryDir", "memory"))
    prompt.setdefault("memoryFile", file_cfg.get("longTermFilename", "MEMORY.md"))
    prompt.setdefault("userFile", "USER.md")
    prompt.setdefault("memoryCharLimit", 2200)
    prompt.setdefault("userCharLimit", 1375)

    local.setdefault("historyFile", file_cfg.get("historyFilename", "HISTORY.md"))
    local.setdefault(
        "maxFailuresBeforeRawArchive",
        file_cfg.get("maxFailuresBeforeRawArchive", 3),
    )
    local.setdefault("triggerRatio", file_cfg.get("triggerRatio", 1.0))
    local.setdefault("targetRatio", file_cfg.get("targetRatio", 0.5))
    local.setdefault(
        "maxConsolidationRounds",
        file_cfg.get("maxConsolidationRounds", 5),
    )

    archive.setdefault("enabled", True)
    archive.setdefault("database", "archive.db")
    flush.setdefault("enabled", True)

    if not provider and legacy_backend and legacy_backend != "file":
        provider = legacy_backend

    for backend_name, backend_value in legacy_backends.items():
        if backend_name == "file" or backend_name in providers:
            continue
        provider_cfg = _as_dict(backend_value)
        if provider_cfg:
            providers[backend_name] = provider_cfg

    memory["prompt"] = prompt
    memory["local"] = local
    memory["archive"] = archive
    memory["flush"] = flush
    memory["provider"] = provider or None
    memory["providers"] = providers
    memory.pop("backend", None)
    memory.pop("backends", None)
