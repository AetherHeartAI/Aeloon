import asyncio
import json
import re
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from aeloon.cli.commands import app
from aeloon.cli.interactive.session import (
    _load_ansi_banner_lines,
    _load_ansi_banner_theme_style,
)
from aeloon.cli.runtime_helpers import make_provider as _make_provider
from aeloon.core.bus.events import OutboundMessage
from aeloon.core.config.schema import Config
from aeloon.providers.litellm_provider import LiteLLMProvider
from aeloon.providers.openai_codex_provider import _strip_model_prefix
from aeloon.providers.registry import find_by_model


def _strip_ansi(text):
    """Remove ANSI escape codes from text."""
    ansi_escape = re.compile(r"\x1b\[[0-9;]*m")
    return ansi_escape.sub("", text)


runner = CliRunner()


class _StopGatewayError(RuntimeError):
    pass


def test_load_ansi_banner_lines(monkeypatch, tmp_path):
    """ANSI logo asset should load as Rich text when it fits."""
    banner_path = tmp_path / "banner.ansi"
    banner_path.write_text("\x1b[31mXX\x1b[0m\n\x1b[32mYY\x1b[0m\n", encoding="utf-8")
    monkeypatch.setattr("aeloon.cli.interactive.session._WELCOME_BANNER_PATH", banner_path)

    banner = _load_ansi_banner_lines(8)

    assert banner is not None
    lines, width = banner
    assert width == 2
    assert [line.plain for line in lines] == ["XX", "YY"]


def test_load_ansi_banner_lines_skips_oversized_art(monkeypatch, tmp_path):
    """ANSI logo asset should be ignored when the terminal is too narrow."""
    banner_path = tmp_path / "banner.ansi"
    banner_path.write_text("WIDE\n", encoding="utf-8")
    monkeypatch.setattr("aeloon.cli.interactive.session._WELCOME_BANNER_PATH", banner_path)

    assert _load_ansi_banner_lines(3) is None


def test_load_ansi_banner_theme_style(monkeypatch, tmp_path):
    """Theme color should be derived from ANSI truecolor foreground codes."""
    banner_path = tmp_path / "banner.ansi"
    banner_path.write_text(
        "\x1b[38;2;10;20;30mAA\x1b[0m\n"
        "\x1b[38;2;40;220;245mBB\x1b[0m\n"
        "\x1b[38;2;35;210;240mCC\x1b[0m\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("aeloon.cli.interactive.session._WELCOME_BANNER_PATH", banner_path)

    style = _load_ansi_banner_theme_style()

    assert style is not None
    assert style.startswith("rgb(")


@pytest.fixture
def mock_paths():
    """Mock config/workspace paths for test isolation."""
    with (
        patch("aeloon.core.config.loader.get_config_path") as mock_cp,
        patch("aeloon.core.config.loader.save_config") as mock_sc,
        patch("aeloon.core.config.loader.load_config") as mock_lc,
        patch("aeloon.cli.flows.onboard.get_workspace_path") as mock_ws,
    ):
        base_dir = Path("./test_onboard_data")
        if base_dir.exists():
            shutil.rmtree(base_dir)
        base_dir.mkdir()

        config_file = base_dir / "config.json"
        workspace_dir = base_dir / "workspace"

        mock_cp.return_value = config_file
        mock_ws.return_value = workspace_dir
        mock_lc.side_effect = lambda _config_path=None: Config()

        def _save_config(config: Config, config_path: Path | None = None):
            target = config_path or config_file
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(config.model_dump(by_alias=True)), encoding="utf-8")

        mock_sc.side_effect = _save_config

        yield config_file, workspace_dir, mock_ws

        if base_dir.exists():
            shutil.rmtree(base_dir)


def test_onboard_fresh_install(mock_paths, monkeypatch) -> None:
    """No existing config — should create from scratch."""
    config_file, workspace_dir, mock_ws = mock_paths
    monkeypatch.setattr("aeloon.channels.registry.discover_all", lambda: {})

    result = runner.invoke(app, ["onboard"])

    assert result.exit_code == 0
    assert "Created config" in result.stdout
    assert "Created workspace" in result.stdout
    assert "aeloon is ready" in result.stdout
    assert config_file.exists()
    assert (workspace_dir / "AGENTS.md").exists()
    assert (workspace_dir / "compiled_skills").exists()
    assert (workspace_dir / "memory" / "MEMORY.md").exists()
    assert (workspace_dir / "outputs").exists()
    saved = Config.model_validate(json.loads(config_file.read_text(encoding="utf-8")))
    raw_saved = json.loads(config_file.read_text(encoding="utf-8"))
    openviking = raw_saved["memory"]["backends"]["openviking"]
    dense = openviking["ovConfig"]["embedding"]["dense"]
    expected_workspace = Config().workspace_path
    stripped_output = _strip_ansi(result.stdout).lower()

    assert saved.memory.backend == "file"
    assert "file" in saved.memory.backends
    assert "openviking" in saved.memory.backends
    assert openviking["ovConfig"]["storage"] == {}
    assert openviking["ovConfig"]["vlm"] == {"provider": "", "api_key": "", "model": ""}
    assert dense["provider"] == ""
    assert dense["api_key"] == ""
    assert dense["model"] == ""
    assert dense["dimension"] == 1024
    assert dense["input"] == "multimodal"
    assert openviking["searchMode"] == "search"
    assert openviking["searchLimit"] == 3
    assert openviking["scoreThreshold"] is None
    assert openviking["targetUri"] == "viking://user/default/memories/"
    assert openviking["extraTargetUris"] == []
    assert openviking["maxCommitRounds"] == 5
    assert openviking["recallTimeoutS"] == 20.0
    assert openviking["waitProcessedTimeoutS"] == 30.0
    assert "memory backends:" in stripped_output
    assert "file" in stripped_output
    assert "openviking" in stripped_output
    assert mock_ws.call_args.args == (expected_workspace,)


def test_onboard_existing_config_refresh(mock_paths):
    """Config exists, user declines overwrite — should refresh (load-merge-save)."""
    config_file, workspace_dir, _ = mock_paths
    config_file.write_text('{"existing": true}')

    result = runner.invoke(app, ["onboard"], input="n\n")

    assert result.exit_code == 0
    assert "Config already exists" in result.stdout
    assert "existing values preserved" in result.stdout
    assert workspace_dir.exists()
    assert (workspace_dir / "AGENTS.md").exists()
    assert (workspace_dir / "outputs").exists()


def test_onboard_existing_config_overwrite(mock_paths):
    """Config exists, user confirms overwrite — should reset to defaults."""
    config_file, workspace_dir, _ = mock_paths
    config_file.write_text('{"existing": true}')

    result = runner.invoke(app, ["onboard"], input="y\n")

    assert result.exit_code == 0
    assert "Config already exists" in result.stdout
    assert "Config reset to defaults" in result.stdout
    assert workspace_dir.exists()


def test_onboard_existing_workspace_safe_create(mock_paths):
    """Workspace exists — should not recreate, but still add missing templates."""
    config_file, workspace_dir, _ = mock_paths
    workspace_dir.mkdir(parents=True)
    config_file.write_text("{}")

    result = runner.invoke(app, ["onboard"], input="n\n")

    assert result.exit_code == 0
    assert "Created workspace" not in result.stdout
    assert "Created AGENTS.md" in result.stdout
    assert (workspace_dir / "AGENTS.md").exists()
    assert (workspace_dir / "compiled_skills").exists()
    assert (workspace_dir / "outputs").exists()


def test_onboard_help_shows_workspace_and_config_options():
    result = runner.invoke(app, ["onboard", "--help"])

    assert result.exit_code == 0
    stripped_output = _strip_ansi(result.stdout)
    assert "--workspace" in stripped_output
    assert "-w" in stripped_output
    assert "--config" in stripped_output
    assert "-c" in stripped_output
    assert "--dir" not in stripped_output


def test_onboard_uses_explicit_config_and_workspace_paths(tmp_path, monkeypatch):
    config_path = tmp_path / "instance" / "config.json"
    workspace_path = tmp_path / "workspace"

    monkeypatch.setattr("aeloon.channels.registry.discover_all", lambda: {})

    result = runner.invoke(
        app,
        ["onboard", "--config", str(config_path), "--workspace", str(workspace_path)],
    )

    assert result.exit_code == 0
    saved = Config.model_validate(json.loads(config_path.read_text(encoding="utf-8")))
    assert saved.workspace_path == workspace_path
    assert (workspace_path / "AGENTS.md").exists()
    assert (workspace_path / "compiled_skills").exists()
    assert (workspace_path / "outputs").exists()
    stripped_output = _strip_ansi(result.stdout)
    compact_output = stripped_output.replace("\n", "")
    resolved_config = str(config_path.resolve())
    assert resolved_config in compact_output
    assert f"--config {resolved_config}" in compact_output


def test_onboard_refresh_adds_openviking_template_without_changing_default(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "config.json"
    workspace_path = tmp_path / "workspace"
    config_path.write_text(
        json.dumps(
            {
                "memory": {
                    "backend": "file",
                    "backends": {
                        "file": {},
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("aeloon.channels.registry.discover_all", lambda: {})

    result = runner.invoke(
        app,
        ["onboard", "--config", str(config_path), "--workspace", str(workspace_path)],
        input="n\n",
    )

    assert result.exit_code == 0
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    openviking = saved["memory"]["backends"]["openviking"]
    stripped_output = _strip_ansi(result.stdout).lower()

    assert saved["memory"]["backend"] == "file"
    assert saved["memory"]["backends"]["file"] == {}
    assert openviking["ovConfig"]["storage"] == {}
    assert openviking["searchMode"] == "search"
    assert openviking["recallTimeoutS"] == 20.0
    assert openviking["waitProcessedTimeoutS"] == 30.0
    assert "openviking" in stripped_output
    assert (workspace_path / "memory" / "MEMORY.md").exists()


def test_onboard_refresh_preserves_existing_openviking_config(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "config.json"
    workspace_path = tmp_path / "workspace"
    existing = {
        "memory": {
            "backend": "file",
            "backends": {
                "file": {},
                "openviking": {
                    "ovConfig": {"storage": {}},
                    "searchMode": "find",
                    "targetUri": "viking://custom/",
                    "recallTimeoutS": 9.5,
                    "waitProcessedTimeoutS": 14.0,
                },
            },
        }
    }
    config_path.write_text(json.dumps(existing), encoding="utf-8")
    monkeypatch.setattr("aeloon.channels.registry.discover_all", lambda: {})

    result = runner.invoke(
        app,
        ["onboard", "--config", str(config_path), "--workspace", str(workspace_path)],
        input="n\n",
    )

    assert result.exit_code == 0
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    openviking = saved["memory"]["backends"]["openviking"]

    assert saved["memory"]["backend"] == "file"
    assert openviking["searchMode"] == "find"
    assert openviking["targetUri"] == "viking://custom/"
    assert openviking["recallTimeoutS"] == 9.5
    assert openviking["waitProcessedTimeoutS"] == 14.0
    assert openviking["ovConfig"] == {"storage": {}}


def test_config_matches_github_copilot_codex_with_hyphen_prefix():
    config = Config()
    config.agents.defaults.model = "github-copilot/gpt-5.3-codex"

    assert config.get_provider_name() == "github_copilot"


def test_config_matches_openai_codex_with_hyphen_prefix():
    config = Config()
    config.agents.defaults.model = "openai-codex/gpt-5.1-codex"

    assert config.get_provider_name() == "openai_codex"


def test_config_matches_explicit_ollama_prefix_without_api_key():
    config = Config()
    config.agents.defaults.model = "ollama/llama3.2"

    assert config.get_provider_name() == "ollama"
    assert config.get_api_base() == "http://localhost:11434"


def test_config_explicit_ollama_provider_uses_default_localhost_api_base():
    config = Config()
    config.agents.defaults.provider = "ollama"
    config.agents.defaults.model = "llama3.2"

    assert config.get_provider_name() == "ollama"
    assert config.get_api_base() == "http://localhost:11434"


def test_config_auto_detects_ollama_from_local_api_base():
    config = Config.model_validate(
        {
            "agents": {"defaults": {"provider": "auto", "model": "llama3.2"}},
            "providers": {"ollama": {"apiBase": "http://localhost:11434"}},
        }
    )

    assert config.get_provider_name() == "ollama"
    assert config.get_api_base() == "http://localhost:11434"


def test_config_prefers_ollama_over_vllm_when_both_local_providers_configured():
    config = Config.model_validate(
        {
            "agents": {"defaults": {"provider": "auto", "model": "llama3.2"}},
            "providers": {
                "vllm": {"apiBase": "http://localhost:8000"},
                "ollama": {"apiBase": "http://localhost:11434"},
            },
        }
    )

    assert config.get_provider_name() == "ollama"
    assert config.get_api_base() == "http://localhost:11434"


def test_config_falls_back_to_vllm_when_ollama_not_configured():
    config = Config.model_validate(
        {
            "agents": {"defaults": {"provider": "auto", "model": "llama3.2"}},
            "providers": {
                "vllm": {"apiBase": "http://localhost:8000"},
            },
        }
    )

    assert config.get_provider_name() == "vllm"
    assert config.get_api_base() == "http://localhost:8000"


def test_find_by_model_prefers_explicit_prefix_over_generic_codex_keyword():
    spec = find_by_model("github-copilot/gpt-5.3-codex")

    assert spec is not None
    assert spec.name == "github_copilot"


def test_litellm_provider_canonicalizes_github_copilot_hyphen_prefix():
    provider = LiteLLMProvider(default_model="github-copilot/gpt-5.3-codex")

    resolved = provider._resolve_model("github-copilot/gpt-5.3-codex")

    assert resolved == "github_copilot/gpt-5.3-codex"


def test_openai_codex_strip_prefix_supports_hyphen_and_underscore():
    assert _strip_model_prefix("openai-codex/gpt-5.1-codex") == "gpt-5.1-codex"
    assert _strip_model_prefix("openai_codex/gpt-5.1-codex") == "gpt-5.1-codex"


def test_make_provider_passes_extra_headers_to_custom_provider():
    config = Config.model_validate(
        {
            "agents": {"defaults": {"provider": "custom", "model": "gpt-4o-mini"}},
            "providers": {
                "custom": {
                    "apiKey": "test-key",
                    "apiBase": "https://example.com/v1",
                    "extraHeaders": {
                        "APP-Code": "demo-app",
                        "x-session-affinity": "sticky-session",
                    },
                }
            },
        }
    )

    with patch("aeloon.providers.custom_provider.AsyncOpenAI") as mock_async_openai:
        _make_provider(config)

    kwargs = mock_async_openai.call_args.kwargs
    assert kwargs["api_key"] == "test-key"
    assert kwargs["base_url"] == "https://example.com/v1"
    assert kwargs["default_headers"]["APP-Code"] == "demo-app"
    assert kwargs["default_headers"]["x-session-affinity"] == "sticky-session"


@pytest.fixture
def mock_agent_runtime(tmp_path):
    """Mock agent command dependencies for focused CLI tests."""
    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "default-workspace")
    cron_dir = tmp_path / "data" / "cron"

    with (
        patch("aeloon.core.config.loader.load_config", return_value=config) as mock_load_config,
        patch("aeloon.core.config.paths.get_cron_dir", return_value=cron_dir),
        patch("aeloon.cli.flows.agent.sync_workspace_templates") as mock_sync_templates,
        patch("aeloon.cli.flows.agent.make_provider", return_value=object()),
        patch("aeloon.cli.flows.agent._print_agent_response") as mock_print_response,
        patch("aeloon.core.bus.queue.MessageBus"),
        patch("aeloon.services.cron.service.CronService"),
        patch("aeloon.core.agent.loop.AgentLoop") as mock_agent_loop_cls,
    ):
        agent_loop = MagicMock()
        agent_loop.channels_config = None
        agent_loop.process_direct = AsyncMock(return_value="mock-response")
        mock_msg = OutboundMessage(channel="cli", chat_id="direct", content="mock-response")
        agent_loop.process_direct_full = AsyncMock(return_value=mock_msg)
        agent_loop.close_mcp = AsyncMock(return_value=None)
        mock_agent_loop_cls.return_value = agent_loop

        yield {
            "config": config,
            "load_config": mock_load_config,
            "sync_templates": mock_sync_templates,
            "agent_loop_cls": mock_agent_loop_cls,
            "agent_loop": agent_loop,
            "print_response": mock_print_response,
        }


def test_agent_help_shows_workspace_and_config_options():
    result = runner.invoke(app, ["agent", "--help"])

    assert result.exit_code == 0
    stripped_output = _strip_ansi(result.stdout)
    assert "--workspace" in stripped_output
    assert "-w" in stripped_output
    assert "--config" in stripped_output
    assert "-c" in stripped_output


def test_agent_help_shows_profile_option():
    result = runner.invoke(app, ["agent", "--help"])

    assert result.exit_code == 0
    stripped_output = _strip_ansi(result.stdout)
    assert "--profile" in stripped_output
    assert "-P" in stripped_output


def test_agent_uses_default_config_when_no_workspace_or_config_flags(mock_agent_runtime):
    result = runner.invoke(app, ["agent", "-m", "hello"])

    assert result.exit_code == 0
    assert mock_agent_runtime["load_config"].call_args.args == (None,)
    assert mock_agent_runtime["sync_templates"].call_args.args == (
        mock_agent_runtime["config"].workspace_path,
    )
    assert mock_agent_runtime["sync_templates"].call_args.kwargs == {"include_file_memory": True}
    assert mock_agent_runtime["agent_loop_cls"].call_args.kwargs["workspace"] == (
        mock_agent_runtime["config"].workspace_path
    )
    mock_agent_runtime["agent_loop"].process_direct_full.assert_awaited_once()
    mock_agent_runtime["print_response"].assert_called_once_with(
        "mock-response", render_markdown=True
    )


def test_agent_wechat_login_message_uses_bus_backed_one_shot(monkeypatch, tmp_path: Path) -> None:
    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "workspace")
    cron_dir = tmp_path / "cron"
    printed: list[str] = []
    rendered_media: list[str] = []

    class _FakeDispatcher:
        def __init__(self) -> None:
            self.stopped = False

        def stop(self) -> None:
            self.stopped = True

    class _FakeAgentLoop:
        def __init__(self, *args, **kwargs) -> None:
            from aeloon.core.bus.queue import MessageBus

            self.bus = MessageBus()
            self.dispatcher = _FakeDispatcher()
            self.plugin_manager = None
            self.profiler = SimpleNamespace(enabled=False, last_report=None)
            self.runtime_settings = SimpleNamespace(output_mode="normal")
            self.channels_config = None

        async def run(self) -> None:
            msg = await self.bus.consume_inbound()
            assert msg.content == "/wechat login"
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel="cli",
                    chat_id=msg.chat_id,
                    content="Please scan this QR code with WeChat within 5 minutes.",
                    media=["/tmp/qr.png"],
                )
            )
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel="cli",
                    chat_id=msg.chat_id,
                    content="✅ WeChat login successful! Bot ID: bot-id",
                )
            )
            await asyncio.sleep(60)

        async def close_mcp(self) -> None:
            return None

    monkeypatch.setattr("aeloon.core.config.loader.load_config", lambda _path=None: config)
    monkeypatch.setattr("aeloon.core.config.paths.get_cron_dir", lambda: cron_dir)
    monkeypatch.setattr(
        "aeloon.cli.flows.agent.sync_workspace_templates",
        lambda _path, **_kwargs: None,
    )
    monkeypatch.setattr("aeloon.cli.flows.agent.make_provider", lambda _config: object())
    monkeypatch.setattr("aeloon.cli.flows.agent.boot_plugins", AsyncMock(return_value=None))
    monkeypatch.setattr("aeloon.core.agent.loop.AgentLoop", _FakeAgentLoop)
    monkeypatch.setattr(
        "aeloon.cli.flows.agent._print_agent_response",
        lambda response, **_kwargs: printed.append(response),
    )
    def _capture_rendered_media(path: str) -> bool:
        rendered_media.append(path)
        return True

    monkeypatch.setattr("aeloon.cli.flows.agent._try_render_inline_image", _capture_rendered_media)

    result = runner.invoke(app, ["agent", "-m", "/wechat login"])

    assert result.exit_code == 0
    assert any("Please scan" in item for item in printed)
    assert any("login successful" in item.lower() for item in printed)
    assert rendered_media == ["/tmp/qr.png"]


def test_agent_wechat_logout_message_uses_bus_backed_one_shot(monkeypatch, tmp_path: Path) -> None:
    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "workspace")
    cron_dir = tmp_path / "cron"
    printed: list[str] = []

    class _FakeDispatcher:
        def __init__(self) -> None:
            self.stopped = False

        def stop(self) -> None:
            self.stopped = True

    class _FakeAgentLoop:
        def __init__(self, *args, **kwargs) -> None:
            from aeloon.core.bus.queue import MessageBus

            self.bus = MessageBus()
            self.dispatcher = _FakeDispatcher()
            self.plugin_manager = None
            self.profiler = SimpleNamespace(enabled=False, last_report=None)
            self.runtime_settings = SimpleNamespace(output_mode="normal")
            self.channels_config = None
            self.process_direct_full = AsyncMock()

        async def run(self) -> None:
            msg = await self.bus.consume_inbound()
            assert msg.content == "/wechat logout"
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel="cli",
                    chat_id=msg.chat_id,
                    content="WeChat logged out",
                )
            )
            await asyncio.sleep(60)

        async def close_mcp(self) -> None:
            return None

    fake_loop: _FakeAgentLoop | None = None

    def _make_fake_loop(*args, **kwargs) -> _FakeAgentLoop:
        nonlocal fake_loop
        fake_loop = _FakeAgentLoop(*args, **kwargs)
        return fake_loop

    monkeypatch.setattr("aeloon.core.config.loader.load_config", lambda _path=None: config)
    monkeypatch.setattr("aeloon.core.config.paths.get_cron_dir", lambda: cron_dir)
    monkeypatch.setattr(
        "aeloon.cli.flows.agent.sync_workspace_templates",
        lambda _path, **_kwargs: None,
    )
    monkeypatch.setattr("aeloon.cli.flows.agent.make_provider", lambda _config: object())
    monkeypatch.setattr("aeloon.cli.flows.agent.boot_plugins", AsyncMock(return_value=None))
    monkeypatch.setattr("aeloon.core.agent.loop.AgentLoop", _make_fake_loop)
    monkeypatch.setattr(
        "aeloon.cli.flows.agent._print_agent_response",
        lambda response, **_kwargs: printed.append(response),
    )

    result = runner.invoke(app, ["agent", "-m", "/wechat logout"])

    assert result.exit_code == 0
    assert printed == ["WeChat logged out"]
    assert fake_loop is not None
    fake_loop.process_direct_full.assert_not_called()


def test_agent_profile_flag_enables_profiler_and_prints_report(mock_agent_runtime):
    profiler = MagicMock()
    profiler.last_report = object()
    profiler.report.return_value = "Profile Report"
    mock_agent_runtime["agent_loop"].profiler = profiler

    with patch("aeloon.cli.flows.agent._print_stderr_profile_report") as mock_profile_print:
        result = runner.invoke(app, ["agent", "-m", "hello", "--profile"])

    assert result.exit_code == 0
    assert mock_agent_runtime["agent_loop"].profiler.enabled is True
    mock_profile_print.assert_called_once_with("Profile Report")


def test_agent_uses_configured_deep_profile_and_prints_deep_report(mock_agent_runtime):
    profiler = MagicMock()
    profiler.last_report = object()
    profiler.report_deep_profile.return_value = "Deep Profile Report"
    mock_agent_runtime["agent_loop"].profiler = profiler
    mock_agent_runtime["agent_loop"].runtime_settings.output_mode = "deep-profile"

    with patch("aeloon.cli.flows.agent._print_stderr_profile_report") as mock_profile_print:
        result = runner.invoke(app, ["agent", "-m", "hello"])

    assert result.exit_code == 0
    assert mock_agent_runtime["agent_loop"].profiler.enabled is True
    mock_profile_print.assert_called_once_with("Deep Profile Report")


def test_agent_profile_flag_overrides_configured_deep_profile(mock_agent_runtime):
    profiler = MagicMock()
    profiler.last_report = object()
    profiler.report.return_value = "Profile Report"
    profiler.report_deep_profile.return_value = "Deep Profile Report"
    mock_agent_runtime["agent_loop"].profiler = profiler
    mock_agent_runtime["agent_loop"].runtime_settings.output_mode = "deep-profile"

    with patch("aeloon.cli.flows.agent._print_stderr_profile_report") as mock_profile_print:
        result = runner.invoke(app, ["agent", "-m", "hello", "--profile"])

    assert result.exit_code == 0
    assert mock_agent_runtime["agent_loop"].profiler.enabled is True
    mock_profile_print.assert_called_once_with("Profile Report")


def test_agent_uses_explicit_config_path(mock_agent_runtime, tmp_path: Path):
    config_path = tmp_path / "agent-config.json"
    config_path.write_text("{}")

    result = runner.invoke(app, ["agent", "-m", "hello", "-c", str(config_path)])

    assert result.exit_code == 0
    assert mock_agent_runtime["load_config"].call_args.args == (config_path.resolve(),)


def test_agent_config_sets_active_path(monkeypatch, tmp_path: Path) -> None:
    config_file = tmp_path / "instance" / "config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("{}")

    config = Config()
    seen: dict[str, Path] = {}

    monkeypatch.setattr(
        "aeloon.core.config.loader.set_config_path",
        lambda path: seen.__setitem__("config_path", path),
    )
    monkeypatch.setattr("aeloon.core.config.loader.load_config", lambda _path=None: config)
    monkeypatch.setattr(
        "aeloon.core.config.paths.get_cron_dir", lambda: config_file.parent / "cron"
    )
    monkeypatch.setattr(
        "aeloon.cli.flows.agent.sync_workspace_templates",
        lambda _path, **_kwargs: None,
    )
    monkeypatch.setattr("aeloon.cli.flows.agent.make_provider", lambda _config: object())
    monkeypatch.setattr("aeloon.core.bus.queue.MessageBus", lambda: object())
    monkeypatch.setattr("aeloon.services.cron.service.CronService", lambda _store: object())

    class _FakeAgentLoop:
        def __init__(self, *args, **kwargs) -> None:
            self.profiler = type(
                "_P",
                (),
                {"enabled": False, "last_report": None, "report": lambda self: ""},
            )()
            self.runtime_settings = type("_R", (), {"output_mode": "normal"})()

        async def process_direct(self, *_args, **_kwargs) -> str:
            return "ok"

        async def process_direct_full(self, *_args, **_kwargs):
            from aeloon.core.bus.events import OutboundMessage

            return OutboundMessage(channel="cli", chat_id="direct", content="ok")

        async def close_mcp(self) -> None:
            return None

    monkeypatch.setattr("aeloon.core.agent.loop.AgentLoop", _FakeAgentLoop)
    monkeypatch.setattr(
        "aeloon.cli.flows.agent._print_agent_response", lambda *_args, **_kwargs: None
    )

    result = runner.invoke(app, ["agent", "-m", "hello", "-c", str(config_file)])

    assert result.exit_code == 0
    assert seen["config_path"] == config_file.resolve()


def test_agent_overrides_workspace_path(mock_agent_runtime):
    workspace_path = Path("/tmp/agent-workspace")

    result = runner.invoke(app, ["agent", "-m", "hello", "-w", str(workspace_path)])

    assert result.exit_code == 0
    assert mock_agent_runtime["config"].agents.defaults.workspace == str(workspace_path)
    assert mock_agent_runtime["sync_templates"].call_args.args == (workspace_path,)
    assert mock_agent_runtime["sync_templates"].call_args.kwargs == {"include_file_memory": True}
    assert mock_agent_runtime["agent_loop_cls"].call_args.kwargs["workspace"] == workspace_path


def test_agent_workspace_override_wins_over_config_workspace(mock_agent_runtime, tmp_path: Path):
    config_path = tmp_path / "agent-config.json"
    config_path.write_text("{}")
    workspace_path = Path("/tmp/agent-workspace")

    result = runner.invoke(
        app,
        ["agent", "-m", "hello", "-c", str(config_path), "-w", str(workspace_path)],
    )

    assert result.exit_code == 0
    assert mock_agent_runtime["load_config"].call_args.args == (config_path.resolve(),)
    assert mock_agent_runtime["config"].agents.defaults.workspace == str(workspace_path)
    assert mock_agent_runtime["sync_templates"].call_args.args == (workspace_path,)
    assert mock_agent_runtime["sync_templates"].call_args.kwargs == {"include_file_memory": True}
    assert mock_agent_runtime["agent_loop_cls"].call_args.kwargs["workspace"] == workspace_path


def test_agent_warns_about_deprecated_memory_window(mock_agent_runtime):
    mock_agent_runtime["config"].agents.defaults.memory_window = 100

    result = runner.invoke(app, ["agent", "-m", "hello"])

    assert result.exit_code == 0
    assert "memoryWindow" in result.stdout
    assert "contextWindowTokens" in result.stdout


def test_gateway_uses_workspace_from_config_by_default(monkeypatch, tmp_path: Path) -> None:
    config_file = tmp_path / "instance" / "config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("{}")

    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "config-workspace")
    seen: dict[str, Path] = {}

    monkeypatch.setattr(
        "aeloon.core.config.loader.set_config_path",
        lambda path: seen.__setitem__("config_path", path),
    )
    monkeypatch.setattr("aeloon.core.config.loader.load_config", lambda _path=None: config)
    monkeypatch.setattr(
        "aeloon.cli.flows.gateway.sync_workspace_templates",
        lambda path, **_kwargs: seen.__setitem__("workspace", path),
    )
    monkeypatch.setattr(
        "aeloon.cli.flows.gateway.make_provider",
        lambda _config: (_ for _ in ()).throw(_StopGatewayError("stop")),
    )

    result = runner.invoke(app, ["gateway", "--config", str(config_file)])

    assert isinstance(result.exception, _StopGatewayError)
    assert seen["config_path"] == config_file.resolve()
    assert seen["workspace"] == Path(config.agents.defaults.workspace)


def test_gateway_workspace_option_overrides_config(monkeypatch, tmp_path: Path) -> None:
    config_file = tmp_path / "instance" / "config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("{}")

    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "config-workspace")
    override = tmp_path / "override-workspace"
    seen: dict[str, Path] = {}

    monkeypatch.setattr("aeloon.core.config.loader.set_config_path", lambda _path: None)
    monkeypatch.setattr("aeloon.core.config.loader.load_config", lambda _path=None: config)
    monkeypatch.setattr(
        "aeloon.cli.flows.gateway.sync_workspace_templates",
        lambda path, **_kwargs: seen.__setitem__("workspace", path),
    )
    monkeypatch.setattr(
        "aeloon.cli.flows.gateway.make_provider",
        lambda _config: (_ for _ in ()).throw(_StopGatewayError("stop")),
    )

    result = runner.invoke(
        app,
        ["gateway", "--config", str(config_file), "--workspace", str(override)],
    )

    assert isinstance(result.exception, _StopGatewayError)
    assert seen["workspace"] == override
    assert config.workspace_path == override


def test_gateway_warns_about_deprecated_memory_window(monkeypatch, tmp_path: Path) -> None:
    config_file = tmp_path / "instance" / "config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("{}")

    config = Config()
    config.agents.defaults.memory_window = 100

    monkeypatch.setattr("aeloon.core.config.loader.set_config_path", lambda _path: None)
    monkeypatch.setattr("aeloon.core.config.loader.load_config", lambda _path=None: config)
    monkeypatch.setattr(
        "aeloon.cli.flows.gateway.sync_workspace_templates",
        lambda _path, **_kwargs: None,
    )
    monkeypatch.setattr(
        "aeloon.cli.flows.gateway.make_provider",
        lambda _config: (_ for _ in ()).throw(_StopGatewayError("stop")),
    )

    result = runner.invoke(app, ["gateway", "--config", str(config_file)])

    assert isinstance(result.exception, _StopGatewayError)
    assert "memoryWindow" in result.stdout
    assert "contextWindowTokens" in result.stdout


def test_gateway_uses_config_directory_for_cron_store(monkeypatch, tmp_path: Path) -> None:
    config_file = tmp_path / "instance" / "config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("{}")

    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "config-workspace")
    seen: dict[str, Path] = {}

    monkeypatch.setattr("aeloon.core.config.loader.set_config_path", lambda _path: None)
    monkeypatch.setattr("aeloon.core.config.loader.load_config", lambda _path=None: config)
    monkeypatch.setattr(
        "aeloon.core.config.paths.get_cron_dir", lambda: config_file.parent / "cron"
    )
    monkeypatch.setattr(
        "aeloon.cli.flows.gateway.sync_workspace_templates",
        lambda _path, **_kwargs: None,
    )
    monkeypatch.setattr("aeloon.cli.flows.gateway.make_provider", lambda _config: object())
    monkeypatch.setattr("aeloon.core.bus.queue.MessageBus", lambda: object())
    monkeypatch.setattr("aeloon.core.session.manager.SessionManager", lambda _workspace: object())

    class _StopCron:
        def __init__(self, store_path: Path) -> None:
            seen["cron_store"] = store_path
            raise _StopGatewayError("stop")

    monkeypatch.setattr("aeloon.services.cron.service.CronService", _StopCron)

    result = runner.invoke(app, ["gateway", "--config", str(config_file)])

    assert isinstance(result.exception, _StopGatewayError)
    assert seen["cron_store"] == config_file.parent / "cron" / "jobs.json"


def test_gateway_uses_configured_port_when_cli_flag_is_missing(monkeypatch, tmp_path: Path) -> None:
    config_file = tmp_path / "instance" / "config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("{}")

    config = Config()
    config.gateway.port = 18791

    monkeypatch.setattr("aeloon.core.config.loader.set_config_path", lambda _path: None)
    monkeypatch.setattr("aeloon.core.config.loader.load_config", lambda _path=None: config)
    monkeypatch.setattr(
        "aeloon.cli.flows.gateway.sync_workspace_templates",
        lambda _path, **_kwargs: None,
    )
    monkeypatch.setattr(
        "aeloon.cli.flows.gateway.make_provider",
        lambda _config: (_ for _ in ()).throw(_StopGatewayError("stop")),
    )

    result = runner.invoke(app, ["gateway", "--config", str(config_file)])

    assert isinstance(result.exception, _StopGatewayError)
    assert "port 18791" in result.stdout


def test_gateway_cli_port_overrides_configured_port(monkeypatch, tmp_path: Path) -> None:
    config_file = tmp_path / "instance" / "config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("{}")

    config = Config()
    config.gateway.port = 18791

    monkeypatch.setattr("aeloon.core.config.loader.set_config_path", lambda _path: None)
    monkeypatch.setattr("aeloon.core.config.loader.load_config", lambda _path=None: config)
    monkeypatch.setattr(
        "aeloon.cli.flows.gateway.sync_workspace_templates",
        lambda _path, **_kwargs: None,
    )
    monkeypatch.setattr(
        "aeloon.cli.flows.gateway.make_provider",
        lambda _config: (_ for _ in ()).throw(_StopGatewayError("stop")),
    )

    result = runner.invoke(app, ["gateway", "--config", str(config_file), "--port", "18792"])

    assert isinstance(result.exception, _StopGatewayError)
    assert "port 18792" in result.stdout
