import json
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from aeloon.core.config.loader import load_config, save_config
from aeloon.core.config.schema import Config


def test_memory_config_exposes_backendless_defaults() -> None:
    cfg = Config()
    dumped = cfg.model_dump(by_alias=True)

    assert cfg.memory.prompt.enabled is True
    assert cfg.memory.prompt.directory == "memory"
    assert cfg.memory.local.history_file == "HISTORY.md"
    assert cfg.memory.archive.enabled is True
    assert cfg.memory.archive.database == "archive.db"
    assert cfg.memory.flush.enabled is True
    assert cfg.memory.provider is None
    assert cfg.memory.providers == {}
    assert "backend" not in dumped["memory"]
    assert "backends" not in dumped["memory"]


def test_memory_config_requires_openviking_section_when_selected() -> None:
    with pytest.raises(ValidationError):
        Config.model_validate(
            {
                "memory": {
                    "provider": "openviking",
                    "providers": {},
                }
            }
        )


def test_save_and_load_round_trip_memory_config(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config = Config.model_validate(
        {
            "memory": {
                "provider": "dummy",
                "providers": {
                    "dummy": {"foo": "bar"},
                },
                "prompt": {"directory": "notes"},
                "local": {"historyFile": "history.md"},
                "archive": {"database": "archive.db"},
            }
        }
    )

    save_config(config, config_path)

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["memory"]["provider"] == "dummy"
    assert saved["memory"]["providers"]["dummy"]["foo"] == "bar"
    assert saved["memory"]["prompt"]["directory"] == "notes"
    assert saved["memory"]["local"]["historyFile"] == "history.md"
    assert saved["memory"]["archive"]["database"] == "archive.db"
    assert "backend" not in saved["memory"]
    assert "backends" not in saved["memory"]

    loaded = load_config(config_path)
    assert loaded.memory.provider == "dummy"
    assert loaded.memory.prompt.directory == "notes"
    assert loaded.memory.local.history_file == "history.md"
    assert loaded.memory.archive.database == "archive.db"
    assert loaded.memory.providers["dummy"]["foo"] == "bar"
    assert not hasattr(loaded.memory, "backend")
    assert not hasattr(loaded.memory, "backends")


def test_save_and_load_round_trip_openviking_memory_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config = Config.model_validate(
        {
            "memory": {
                "provider": "openviking",
                "providers": {
                    "openviking": {
                        "storageSubdir": "openviking_memory",
                        "searchMode": "search",
                        "searchLimit": 6,
                        "scoreThreshold": 0.25,
                        "targetUri": "viking://memory/",
                        "extraTargetUris": ["viking://session/default"],
                        "recallTimeoutS": 12.5,
                        "waitProcessedTimeoutS": 18.0,
                        "triggerRatio": 0.8,
                        "targetRatio": 0.4,
                        "maxCommitRounds": 3,
                        "ovConfig": {
                            "storage": {"agfs": {"port": 1833}},
                            "embedding": {"dense": {"provider": "mock"}},
                        },
                    }
                },
            }
        }
    )

    save_config(config, config_path)

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    openviking = saved["memory"]["providers"]["openviking"]
    assert "backend" not in saved["memory"]
    assert "backends" not in saved["memory"]
    assert openviking["storageSubdir"] == "openviking_memory"
    assert openviking["searchMode"] == "search"
    assert openviking["extraTargetUris"] == ["viking://session/default"]
    assert openviking["recallTimeoutS"] == 12.5
    assert openviking["waitProcessedTimeoutS"] == 18.0
    assert openviking["ovConfig"]["storage"]["agfs"]["port"] == 1833

    loaded = load_config(config_path)
    assert loaded.memory.provider == "openviking"
    assert loaded.memory.providers["openviking"]["searchMode"] == "search"
    assert loaded.memory.providers["openviking"]["searchLimit"] == 6
    assert loaded.memory.providers["openviking"]["extraTargetUris"] == ["viking://session/default"]
    assert loaded.memory.providers["openviking"]["recallTimeoutS"] == 12.5
    assert loaded.memory.providers["openviking"]["waitProcessedTimeoutS"] == 18.0
    assert not hasattr(loaded.memory, "backend")
    assert not hasattr(loaded.memory, "backends")
    loaded_ov_config = cast(dict[str, object], loaded.memory.providers["openviking"]["ovConfig"])
    loaded_embedding = cast(dict[str, object], loaded_ov_config["embedding"])
    loaded_dense = cast(dict[str, object], loaded_embedding["dense"])
    assert loaded_dense["provider"] == "mock"


def test_load_config_migrates_legacy_file_backend_to_backendless_memory(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "memory": {
                    "backend": "file",
                    "backends": {
                        "file": {
                            "memoryDir": "notes",
                            "historyFilename": "ARCHIVE.md",
                            "maxFailuresBeforeRawArchive": 7,
                        }
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    loaded = load_config(config_path)

    assert loaded.memory.prompt.enabled is True
    assert loaded.memory.prompt.directory == "notes"
    assert loaded.memory.local.history_file == "ARCHIVE.md"
    assert loaded.memory.local.max_failures_before_raw_archive == 7
    assert loaded.memory.archive.enabled is True
    assert loaded.memory.provider is None
    assert not hasattr(loaded.memory, "backend")
    assert not hasattr(loaded.memory, "backends")


def test_load_config_migrates_legacy_openviking_backend_to_provider_only(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "memory": {
                    "backend": "openviking",
                    "backends": {
                        "file": {
                            "memoryDir": "memory",
                        },
                        "openviking": {
                            "searchMode": "search",
                            "searchLimit": 8,
                        },
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    loaded = load_config(config_path)

    assert loaded.memory.prompt.enabled is True
    assert loaded.memory.archive.enabled is True
    assert loaded.memory.provider == "openviking"
    assert loaded.memory.providers["openviking"]["searchMode"] == "search"
    assert loaded.memory.providers["openviking"]["searchLimit"] == 8
    assert not hasattr(loaded.memory, "backend")
    assert not hasattr(loaded.memory, "backends")
