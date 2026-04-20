from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from aeloon.cli.commands import app
from aeloon.memory.providers.base import MemoryProvider
from aeloon.memory.providers.registry import MemoryProviderRegistry, MemoryProviderSpec

runner = CliRunner()


def test_memory_setup_writes_provider_config_and_env(tmp_path: Path) -> None:
    config_path = tmp_path / "profiles" / "work" / "config.json"
    workspace = tmp_path / "workspace"
    ov_conf_path = tmp_path / "ov.conf"
    ov_conf_path.write_text(
        json.dumps(
            {
                "vlm": {
                    "provider": "volcengine",
                    "api_key": "vlm-key",
                    "model": "doubao-seed-1-8-251228",
                },
                "embedding": {
                    "dense": {
                        "provider": "volcengine",
                        "api_key": "embed-key",
                        "model": "doubao-embedding-vision-251215",
                        "dimension": 1024,
                        "input": "multimodal",
                    }
                },
                "server": {
                    "port": 1933,
                },
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "memory",
            "setup",
            "openviking",
            "--config",
            str(config_path),
            "--workspace",
            str(workspace),
        ],
        input=f"\nsecret-key\n\nsearch\nembedded\n{ov_conf_path}\n",
    )

    assert result.exit_code == 0
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    env_text = (config_path.parent / ".env").read_text(encoding="utf-8")

    assert saved["memory"]["provider"] == "openviking"
    assert saved["memory"]["providers"]["openviking"]["mode"] == "embedded"
    assert saved["memory"]["providers"]["openviking"]["configPath"] == str(ov_conf_path)
    assert saved["memory"]["providers"]["openviking"]["searchMode"] == "search"
    assert (
        saved["memory"]["providers"]["openviking"]["ovConfig"]["embedding"]["dense"]["model"]
        == "doubao-embedding-vision-251215"
    )
    assert (
        saved["memory"]["providers"]["openviking"]["ovConfig"]["vlm"]["model"]
        == "doubao-seed-1-8-251228"
    )
    assert "server" not in saved["memory"]["providers"]["openviking"]["ovConfig"]
    assert "Imported OpenViking config from" in result.stdout
    assert str(ov_conf_path) in result.stdout
    assert "Mode: embedded" in result.stdout
    assert "backend" not in saved["memory"]
    assert "backends" not in saved["memory"]
    assert "OPENVIKING_API_KEY=secret-key" in env_text


def test_memory_setup_rejects_missing_openviking_config_path(tmp_path: Path) -> None:
    config_path = tmp_path / "profiles" / "work" / "config.json"
    workspace = tmp_path / "workspace"
    missing_ov_conf_path = tmp_path / "missing-ov.conf"

    result = runner.invoke(
        app,
        [
            "memory",
            "setup",
            "openviking",
            "--config",
            str(config_path),
            "--workspace",
            str(workspace),
        ],
        input=f"\nsecret-key\n\nsearch\nembedded\n{missing_ov_conf_path}\n",
    )

    assert result.exit_code != 0
    assert not config_path.exists()
    assert "OpenViking config file not found" in result.stdout


def test_memory_status_reports_planes_and_profile(tmp_path: Path) -> None:
    config_path = tmp_path / "profiles" / "work" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            {
                "memory": {
                    "provider": "openviking",
                    "providers": {
                        "openviking": {
                            "mode": "embedded",
                            "configPath": "/tmp/ov.conf",
                            "searchMode": "search",
                            "ovConfig": {"embedding": {"dense": {"provider": "mock"}}},
                        }
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["memory", "status", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "active profile: work" in result.stdout.lower()
    assert "prompt memory: on" in result.stdout.lower()
    assert "archive: on" in result.stdout.lower()
    assert "provider: openviking" in result.stdout.lower()
    assert "mode: embedded" in result.stdout.lower()
    assert "config source: /tmp/ov.conf" in result.stdout.lower()


def test_memory_off_disables_provider(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "memory": {
                    "backend": "openviking",
                    "backends": {
                        "file": {"memoryDir": "memory"},
                        "openviking": {"searchMode": "search"},
                    },
                    "provider": "openviking",
                    "providers": {"openviking": {"searchMode": "search"}},
                }
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["memory", "off", "--config", str(config_path)])

    assert result.exit_code == 0
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["memory"]["provider"] is None
    assert saved["memory"]["providers"]["openviking"]["searchMode"] == "search"
    assert "backend" not in saved["memory"]
    assert "backends" not in saved["memory"]


def test_memory_setup_uses_provider_prepare_hook(tmp_path: Path, monkeypatch) -> None:
    class FakeProvider(MemoryProvider):
        name = "fake"

        @classmethod
        def config_schema(cls) -> list[dict[str, object]]:
            return [
                {
                    "key": "token",
                    "description": "Provider token",
                    "default": "abc",
                }
            ]

        @classmethod
        def prepare_setup_values(
            cls,
            values: dict[str, object],
        ) -> tuple[dict[str, object], list[str]]:
            prepared = dict(values)
            prepared["prepared"] = True
            return prepared, ["Prepared by fake provider"]

    registry = MemoryProviderRegistry()
    registry.register(
        MemoryProviderSpec(
            name=FakeProvider.name,
            provider_cls=FakeProvider,
            description="fake provider",
        )
    )
    monkeypatch.setattr("aeloon.cli.memory.MEMORY_PROVIDER_REGISTRY", registry)

    config_path = tmp_path / "config.json"
    result = runner.invoke(
        app,
        ["memory", "setup", "fake", "--config", str(config_path)],
        input="hello\n",
    )

    assert result.exit_code == 0
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["memory"]["provider"] == "fake"
    assert saved["memory"]["providers"]["fake"] == {
        "token": "hello",
        "prepared": True,
    }
    assert "Prepared by fake provider" in result.stdout


def test_memory_status_uses_provider_status_lines(tmp_path: Path, monkeypatch) -> None:
    class FakeProvider(MemoryProvider):
        name = "fake"

        @classmethod
        def status_lines(cls, config: dict[str, object]) -> list[str]:
            return [f"Mode: {config.get('mode', 'missing')}"]

    registry = MemoryProviderRegistry()
    registry.register(
        MemoryProviderSpec(
            name=FakeProvider.name,
            provider_cls=FakeProvider,
            description="fake provider",
        )
    )
    monkeypatch.setattr("aeloon.cli.memory.MEMORY_PROVIDER_REGISTRY", registry)

    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "memory": {
                    "provider": "fake",
                    "providers": {"fake": {"mode": "custom"}},
                }
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["memory", "status", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "Provider: fake" in result.stdout
    assert "Mode: custom" in result.stdout
