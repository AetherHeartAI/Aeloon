from __future__ import annotations

import os
from pathlib import Path

from aeloon.cli.runtime_helpers import load_runtime_config
from aeloon.core.config.loader import load_config, set_config_path
from aeloon.core.config.paths import (
    get_archive_db_path,
    get_env_path,
    get_profile_name,
    get_profile_root,
    get_prompt_memory_dir,
    get_provider_state_dir,
)


def test_named_profile_paths_are_isolated(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AELOON_HOME", str(tmp_path))
    config_path = tmp_path / "profiles" / "work" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("{}", encoding="utf-8")

    assert get_profile_name(config_path) == "work"
    assert get_profile_root(config_path=config_path) == tmp_path / "profiles" / "work"
    assert get_env_path(config_path=config_path) == tmp_path / "profiles" / "work" / ".env"
    assert (
        get_prompt_memory_dir(config_path=config_path) == tmp_path / "profiles" / "work" / "memory"
    )
    assert (
        get_provider_state_dir(config_path=config_path)
        == tmp_path / "profiles" / "work" / "providers"
    )
    assert (
        get_archive_db_path(config_path=config_path)
        == tmp_path / "profiles" / "work" / "archive.db"
    )


def test_default_profile_paths_remain_backward_compatible(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AELOON_HOME", str(tmp_path))
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    assert get_profile_name(config_path) is None
    assert get_profile_root(config_path=config_path) == tmp_path
    assert get_env_path(config_path=config_path) == tmp_path / ".env"
    assert get_prompt_memory_dir(config_path=config_path) == tmp_path / "memory"
    assert get_provider_state_dir(config_path=config_path) == tmp_path / "providers"
    assert get_archive_db_path(config_path=config_path) == tmp_path / "archive.db"


def test_load_runtime_config_loads_profile_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AELOON_HOME", str(tmp_path))
    monkeypatch.delenv("OPENVIKING_API_KEY", raising=False)
    monkeypatch.delenv("OPENVIKING_ENDPOINT", raising=False)
    config_path = tmp_path / "profiles" / "work" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        '{"memory":{"provider":"openviking","providers":{"openviking":{}}}}',
        encoding="utf-8",
    )
    env_path = config_path.parent / ".env"
    env_path.write_text(
        "OPENVIKING_API_KEY=secret-value\nOPENVIKING_ENDPOINT=http://127.0.0.1:1933\n",
        encoding="utf-8",
    )

    loaded = load_runtime_config(config=str(config_path))

    assert loaded.memory.provider == "openviking"
    assert os.environ["OPENVIKING_API_KEY"] == "secret-value"
    assert os.environ["OPENVIKING_ENDPOINT"] == "http://127.0.0.1:1933"


def test_load_config_uses_aeloon_config_env(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "profiles" / "work" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text('{"memory":{"prompt":{"directory":"notes"}}}', encoding="utf-8")
    monkeypatch.setenv("AELOON_CONFIG", str(config_path))
    set_config_path(None)

    loaded = load_config()

    assert loaded.memory.prompt.directory == "notes"
