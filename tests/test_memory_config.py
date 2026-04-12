import json
from typing import cast

from aeloon.core.config.loader import load_config, save_config
from aeloon.core.config.schema import Config


def test_memory_config_supports_backend_name_and_raw_backend_sections() -> None:
    cfg = Config.model_validate(
        {
            "memory": {
                "backend": "file",
                "backends": {
                    "file": {"memoryDir": "memory"},
                    "dummy": {"foo": "bar"},
                },
            }
        }
    )

    assert cfg.memory.backend == "file"
    assert cfg.memory.backends["file"]["memoryDir"] == "memory"
    assert cfg.memory.backends["dummy"]["foo"] == "bar"


def test_memory_config_reserves_openviking_section_by_default() -> None:
    cfg = Config()

    assert cfg.memory.backend == "file"
    assert cfg.memory.backends["file"] == {}
    openviking = cfg.memory.backends["openviking"]
    assert openviking["targetUri"] == "viking://user/default/memories/"
    assert openviking["extraTargetUris"] == []


def test_save_and_load_round_trip_memory_config(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config = Config.model_validate(
        {
            "memory": {
                "backend": "dummy",
                "backends": {
                    "file": {"memoryDir": "memory"},
                    "dummy": {"foo": "bar"},
                },
            }
        }
    )

    save_config(config, config_path)

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["memory"]["backend"] == "dummy"
    assert saved["memory"]["backends"]["dummy"]["foo"] == "bar"

    loaded = load_config(config_path)
    assert loaded.memory.backend == "dummy"
    assert loaded.memory.backends["file"]["memoryDir"] == "memory"
    assert loaded.memory.backends["dummy"]["foo"] == "bar"


def test_save_and_load_round_trip_openviking_memory_config(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config = Config.model_validate(
        {
            "memory": {
                "backend": "openviking",
                "backends": {
                    "file": {},
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
                    },
                },
            }
        }
    )

    save_config(config, config_path)

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    openviking = saved["memory"]["backends"]["openviking"]
    assert openviking["storageSubdir"] == "openviking_memory"
    assert openviking["searchMode"] == "search"
    assert openviking["extraTargetUris"] == ["viking://session/default"]
    assert openviking["recallTimeoutS"] == 12.5
    assert openviking["waitProcessedTimeoutS"] == 18.0
    assert openviking["ovConfig"]["storage"]["agfs"]["port"] == 1833

    loaded = load_config(config_path)
    assert loaded.memory.backend == "openviking"
    assert loaded.memory.backends["openviking"]["searchMode"] == "search"
    assert loaded.memory.backends["openviking"]["searchLimit"] == 6
    assert loaded.memory.backends["openviking"]["extraTargetUris"] == ["viking://session/default"]
    assert loaded.memory.backends["openviking"]["recallTimeoutS"] == 12.5
    assert loaded.memory.backends["openviking"]["waitProcessedTimeoutS"] == 18.0
    loaded_ov_config = cast(dict[str, object], loaded.memory.backends["openviking"]["ovConfig"])
    loaded_embedding = cast(dict[str, object], loaded_ov_config["embedding"])
    loaded_dense = cast(dict[str, object], loaded_embedding["dense"])
    assert loaded_dense["provider"] == "mock"
