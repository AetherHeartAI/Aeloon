from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from aeloon.cli.commands import app

runner = CliRunner()


def test_memory_setup_writes_provider_config_and_env(tmp_path: Path) -> None:
    config_path = tmp_path / "profiles" / "work" / "config.json"
    workspace = tmp_path / "workspace"

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
        input="\nsecret-key\n\nsearch\n",
    )

    assert result.exit_code == 0
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    env_text = (config_path.parent / ".env").read_text(encoding="utf-8")

    assert saved["memory"]["provider"] == "openviking"
    assert saved["memory"]["backend"] == "file"
    assert saved["memory"]["providers"]["openviking"]["searchMode"] == "search"
    assert "OPENVIKING_API_KEY=secret-key" in env_text


def test_memory_status_reports_planes_and_profile(tmp_path: Path) -> None:
    config_path = tmp_path / "profiles" / "work" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            {
                "memory": {
                    "backend": "file",
                    "provider": "openviking",
                    "providers": {"openviking": {"searchMode": "search"}},
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


def test_memory_off_disables_provider(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "memory": {
                    "backend": "file",
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
