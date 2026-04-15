from unittest.mock import AsyncMock, patch

from typer.main import get_command
from typer.testing import CliRunner

from aeloon.cli.app import BUILTIN_COMMAND_SPECS, app, command_catalog, create_builtin_catalog
from aeloon.cli.plugins import build_plugin_command_specs
from aeloon.plugins._sdk.registry import PluginRegistry
from aeloon.plugins._sdk.types import CLICommandSpec, CLIRecord, CommandRecord


def test_builtin_catalog_includes_expected_slash_commands() -> None:
    catalog = create_builtin_catalog()

    slash_commands = catalog.slash_commands()

    assert ("/help", "Show available commands") in slash_commands
    assert ("/outputs", "List recent output artifacts") in slash_commands
    assert ("/channel", "Manage one channel.") in slash_commands
    assert ("/channel wechat status", "Manage one channel.") in slash_commands
    assert ("/plugin", "Manage plugins.") in slash_commands
    assert ("/plugin activate", "Manage plugins.") in slash_commands
    assert ("/feishu", "Feishu login management") in slash_commands


def test_builtin_catalog_renders_nested_help_lines() -> None:
    lines = create_builtin_catalog().render_help_lines()

    assert "- `/channel` — Manage one channel." in lines
    assert "  - `wechat`" in lines
    assert "    - `status`" in lines


def test_shared_app_catalog_is_preloaded() -> None:
    assert command_catalog.all()
    assert len(command_catalog.all()) >= len(BUILTIN_COMMAND_SPECS)
    assert app.info.name == "aeloon"


def test_shared_app_mounts_extension_group() -> None:
    root = get_command(app)

    assert "ext" in root.commands
    assert "channel" in root.commands
    assert "wechat" in root.commands
    assert "feishu" in root.commands
    assert "whatsapp" in root.commands
    assert root.commands["ext"].commands
    assert "skill_compiler" in root.commands["ext"].commands
    assert "acp" in root.commands["ext"].commands


def test_shared_catalog_includes_lightweight_plugin_slash_commands() -> None:
    labels = {label for label, _desc in command_catalog.slash_commands()}

    assert "/pc" in labels
    assert "/pc plan" in labels
    assert "/pc status" in labels
    assert "/sr" in labels
    assert "/sr run" in labels
    assert "/sr history" in labels
    assert "/skill_compiler" in labels
    assert "/wiki" in labels
    assert "/wiki status" in labels
    assert "/wiki use local-only" in labels
    assert "/acp" in labels
    assert "/acp status" in labels


def test_plugin_catalog_builder_includes_nested_paths() -> None:
    registry = PluginRegistry()
    registry.commit_plugin(
        "test.pc",
        commands=[
            CommandRecord(
                plugin_id="test.pc",
                name="pc",
                handler=AsyncMock(),
                description="Create and manage plugin plans",
            )
        ],
        cli=[
            CLIRecord(
                plugin_id="test.pc",
                name="pc",
                builder=lambda _app: None,
                commands=(
                    CLICommandSpec(
                        group_name="pc",
                        command_name="plan",
                        help="Create a plugin plan from a requirement description.",
                        plugin_command="pc",
                        slash_paths=(("pc", "plan", "<message>"),),
                    ),
                ),
            )
        ],
    )

    specs = build_plugin_command_specs(registry)

    assert len(specs) == 2
    assert specs[0].slash_path == ("pc",)
    assert specs[1].slash_path == ("pc", "plan")
    assert specs[1].slash_paths == (("pc", "plan", "<message>"),)


def test_generated_extension_command_invokes_shared_runner() -> None:
    runner = CliRunner()

    with patch("aeloon.cli.plugins.run_plugin_cli_command") as mock_run:
        result = runner.invoke(app, ["ext", "pc", "plan", "-m", "hello"])

    assert result.exit_code == 0
    mock_run.assert_called_once_with(
        plugin_command="pc",
        args="plan hello",
        session_id=None,
        workspace=None,
        config=None,
    )


def test_generated_science_extension_command_invokes_shared_runner() -> None:
    runner = CliRunner()

    with patch("aeloon.cli.plugins.run_plugin_cli_command") as mock_run:
        result = runner.invoke(app, ["ext", "sr", "run", "-m", "hello"])

    assert result.exit_code == 0
    mock_run.assert_called_once_with(
        plugin_command="sr",
        args="run hello",
        session_id=None,
        workspace=None,
        config=None,
    )


def test_generated_wiki_extension_command_invokes_shared_runner() -> None:
    runner = CliRunner()

    with patch("aeloon.cli.plugins.run_plugin_cli_command") as mock_run:
        result = runner.invoke(app, ["ext", "wiki", "status"])

    assert result.exit_code == 0
    mock_run.assert_called_once_with(
        plugin_command="wiki",
        args="status",
        session_id=None,
        workspace=None,
        config=None,
    )


def test_generated_wiki_positional_extension_command_invokes_shared_runner() -> None:
    runner = CliRunner()

    with patch("aeloon.cli.plugins.run_plugin_cli_command") as mock_run:
        result = runner.invoke(app, ["ext", "wiki", "init", "/tmp/wiki"])

    assert result.exit_code == 0
    mock_run.assert_called_once_with(
        plugin_command="wiki",
        args="init /tmp/wiki",
        session_id=None,
        workspace=None,
        config=None,
    )


def test_generated_wiki_flag_extension_command_invokes_shared_runner() -> None:
    runner = CliRunner()

    with patch("aeloon.cli.plugins.run_plugin_cli_command") as mock_run:
        result = runner.invoke(app, ["ext", "wiki", "remove", "--confirm"])

    assert result.exit_code == 0
    mock_run.assert_called_once_with(
        plugin_command="wiki",
        args="remove --confirm",
        session_id=None,
        workspace=None,
        config=None,
    )


def test_generated_skill_compiler_extension_command_invokes_shared_runner() -> None:
    runner = CliRunner()

    with patch("aeloon.cli.plugins.run_plugin_cli_command") as mock_run:
        result = runner.invoke(
            app,
            [
                "ext",
                "skill_compiler",
                "skills/demo",
                "--runtime-model",
                "override-model",
                "--strict-validate",
            ],
        )

    assert result.exit_code == 0
    mock_run.assert_called_once_with(
        plugin_command="skill_compiler",
        args="skills/demo --runtime-model override-model --strict-validate",
        session_id=None,
        workspace=None,
        config=None,
    )


def test_generated_acp_extension_command_invokes_shared_runner() -> None:
    runner = CliRunner()

    with patch("aeloon.cli.plugins.run_plugin_cli_command") as mock_run:
        result = runner.invoke(app, ["ext", "acp", "connect", "claude_code"])

    assert result.exit_code == 0
    mock_run.assert_called_once_with(
        plugin_command="acp",
        args="connect claude_code",
        session_id=None,
        workspace=None,
        config=None,
    )


def test_channel_namespace_invokes_wechat_runner() -> None:
    runner = CliRunner()

    with patch("aeloon.cli.channels._run_channel_auth_cli") as mock_run:
        result = runner.invoke(app, ["channel", "wechat", "status"])

    assert result.exit_code == 0
    mock_run.assert_called_once()


def test_wechat_root_alias_invokes_wechat_runner() -> None:
    runner = CliRunner()

    with patch("aeloon.cli.channels._run_channel_auth_cli") as mock_run:
        result = runner.invoke(app, ["wechat", "status"])

    assert result.exit_code == 0
    mock_run.assert_called_once()


def test_plugin_install_invokes_shared_admin_helper() -> None:
    runner = CliRunner()

    with (
        patch("aeloon.plugins._sdk.admin.install_plugin_archive") as mock_install,
        patch("aeloon.core.config.loader.get_aeloon_home") as mock_home,
    ):
        mock_home.return_value = __import__("pathlib").Path("/tmp/aeloon-test")
        mock_install.return_value = __import__("types").SimpleNamespace(ok=True, message="ok")
        result = runner.invoke(app, ["plugin", "install", "/tmp/demo.zip"])

    assert result.exit_code == 0
    mock_install.assert_called_once()


def test_plugin_activate_invokes_shared_admin_helper() -> None:
    runner = CliRunner()

    with (
        patch("aeloon.plugins._sdk.admin.set_plugin_enabled") as mock_toggle,
        patch("aeloon.core.config.loader.get_aeloon_home") as mock_home,
    ):
        mock_home.return_value = __import__("pathlib").Path("/tmp/aeloon-test")
        mock_toggle.return_value = __import__("types").SimpleNamespace(ok=True, message="ok")
        result = runner.invoke(app, ["plugin", "activate", "demo.plugin"])

    assert result.exit_code == 0
    mock_toggle.assert_called_once()
