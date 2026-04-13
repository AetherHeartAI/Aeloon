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
    archive = _as_dict(memory.get("archive"))
    flush = _as_dict(memory.get("flush"))
    providers = _as_dict(memory.get("providers"))
    backends = _as_dict(memory.get("backends"))

    backend_raw = memory.get("backend")
    backend = backend_raw.strip() if isinstance(backend_raw, str) else ""
    provider_raw = memory.get("provider")
    provider = provider_raw.strip() if isinstance(provider_raw, str) else ""

    file_cfg = _as_dict(backends.get("file"))
    if "enabled" not in prompt:
        prompt["enabled"] = True
    if "directory" not in prompt:
        prompt["directory"] = file_cfg.get("memoryDir", "memory")
    if "memoryFile" not in prompt:
        prompt["memoryFile"] = "MEMORY.md"
    if "userFile" not in prompt:
        prompt["userFile"] = "USER.md"
    if "memoryCharLimit" not in prompt:
        prompt["memoryCharLimit"] = 2200
    if "userCharLimit" not in prompt:
        prompt["userCharLimit"] = 1375

    if "enabled" not in archive:
        archive["enabled"] = True
    if "database" not in archive:
        archive["database"] = "archive.db"

    if "enabled" not in flush:
        flush["enabled"] = True

    if not provider and backend and backend != "file":
        provider = backend
    if provider and provider not in providers:
        provider_cfg = _as_dict(backends.get(provider))
        if provider_cfg:
            providers[provider] = provider_cfg

    compat_backend = provider or "file"
    compat_backends = {"file": file_cfg}
    if "memoryDir" not in compat_backends["file"] and isinstance(prompt.get("directory"), str):
        compat_backends["file"]["memoryDir"] = prompt["directory"]
    if provider:
        compat_backends[provider] = _as_dict(providers.get(provider))

    memory["prompt"] = prompt
    memory["archive"] = archive
    memory["flush"] = flush
    memory["provider"] = provider or None
    memory["providers"] = providers
    memory["backend"] = compat_backend
    memory["backends"] = compat_backends
