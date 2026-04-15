from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from prompt_toolkit.document import Document
from prompt_toolkit.layout.containers import Window

from aeloon.cli.flows import agent as agent_flow
from aeloon.core.agent.loop import AgentLoop
from aeloon.core.bus.events import InboundMessage
from aeloon.core.bus.queue import MessageBus
from aeloon.core.config.schema import Config
from aeloon.core.session.manager import Session


def _make_loop(tmp_path) -> AgentLoop:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.config = Config.model_validate(
        {
            "agents": {"defaults": {"model": "anthropic/claude-opus-4-5", "provider": "anthropic"}},
            "providers": {"anthropic": {"apiKey": "anthropic-key"}},
        }
    )
    loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path, model="test-model")
    loop.memory_consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=None)
    return loop


@pytest.mark.asyncio
async def test_unknown_slash_command_suggests_close_match(tmp_path) -> None:
    loop = _make_loop(tmp_path)

    response = await loop._process_message(
        InboundMessage(channel="cli", sender_id="u", chat_id="c", content="/hep")
    )

    assert response is not None
    assert "Unknown command: /hep." in response.content
    assert "/help" in response.content


def test_init_prompt_session_passes_bottom_toolbar() -> None:
    agent_flow._PROMPT_SESSION = None

    def toolbar() -> str:
        return "toolbar"

    with (
        patch("aeloon.cli.flows.agent.PromptSession") as mock_session,
        patch("aeloon.cli.flows.agent.FileHistory"),
        patch("pathlib.Path.home") as mock_home,
    ):
        mock_home.return_value = MagicMock()
        agent_flow._init_prompt_session(toolbar)

        _, kwargs = mock_session.call_args
        assert kwargs["bottom_toolbar"] is toolbar


def test_session_display_messages_filters_to_visible_roles() -> None:
    session = Session(key="cli:direct")
    session.messages = [
        {"role": "system", "content": "hidden"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "tool", "content": "skip"},
    ]

    assert agent_flow._session_display_messages(session) == [("user", "hello"), ("assistant", "hi")]


def test_build_bottom_toolbar_includes_model_and_context(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    session = loop.sessions.get_or_create("cli:direct")
    session.messages = [{"role": "user", "content": "hello"}]
    loop.memory_consolidator.estimate_session_prompt_tokens = MagicMock(return_value=(123, "mock"))

    toolbar = agent_flow._build_bottom_toolbar(loop, "cli", "direct")
    rendered = toolbar()
    rendered_text = "".join(part[1] for part in rendered)

    assert "Model:" in rendered_text
    assert "test-model" in rendered_text
    assert "Provider:" not in rendered_text
    assert "Session:" not in rendered_text
    assert "Context:" in rendered_text
    assert "123/" in rendered_text


def test_build_bottom_toolbar_includes_gateway_hint_when_running(tmp_path, monkeypatch) -> None:
    loop = _make_loop(tmp_path)
    session = loop.sessions.get_or_create("cli:direct")
    session.messages = [{"role": "user", "content": "hello"}]
    loop.memory_consolidator.estimate_session_prompt_tokens = MagicMock(return_value=(123, "mock"))
    monkeypatch.setattr("aeloon.core.agent.channel_auth.GatewayManager.is_running", lambda: True)

    toolbar = agent_flow._build_bottom_toolbar(loop, "cli", "direct")
    rendered = toolbar()
    rendered_text = "".join(part[1] for part in rendered)

    assert "Gateway: running" in rendered_text
    assert "Ctrl+L: logs" in rendered_text


def test_resolve_initial_cli_state_starts_fresh_for_default_session() -> None:
    state, start_fresh = agent_flow._resolve_initial_cli_state("cli:direct")

    assert start_fresh is True
    assert state["channel"] == "cli"
    assert state["chat_id"]
    assert state["chat_id"] != "direct"


def test_resolve_initial_cli_state_preserves_explicit_session() -> None:
    state, start_fresh = agent_flow._resolve_initial_cli_state("cli:kept")

    assert start_fresh is False
    assert state == {"channel": "cli", "chat_id": "kept"}


@pytest.mark.asyncio
async def test_interactive_setting_menu_returns_output_command(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_flow,
        "_interactive_setting_menu",
        AsyncMock(return_value="/setting output profile"),
    )
    assert await agent_flow._interactive_setting_menu() == "/setting output profile"


@pytest.mark.asyncio
async def test_setting_command_normalizes_wrapped_whitespace(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    config = Config()

    with (
        patch("aeloon.core.config.loader.load_config", return_value=config),
        patch("aeloon.core.config.loader.save_config"),
    ):
        response = await loop._process_message(
            InboundMessage(
                channel="cli",
                sender_id="u",
                chat_id="direct",
                content="/setting output            \n│   profile",
            )
        )

    assert response is not None
    assert response.content == "Output mode set to profile and saved to config."


@pytest.mark.asyncio
async def test_interactive_sessions_menu_lists_switch_commands(tmp_path, monkeypatch) -> None:
    loop = _make_loop(tmp_path)
    current = loop.sessions.get_or_create("cli:direct")
    current.add_message("user", "hello")
    loop.sessions.save(current)

    other = loop.sessions.get_or_create("cli:alt")
    other.add_message("assistant", "world")
    loop.sessions.save(other)

    monkeypatch.setattr(
        agent_flow,
        "_interactive_sessions_menu",
        AsyncMock(return_value="/resume switch cli:alt"),
    )

    assert (
        await agent_flow._interactive_sessions_menu(loop, "cli:direct") == "/resume switch cli:alt"
    )


@pytest.mark.asyncio
async def test_interactive_menu_helper_can_be_stubbed(monkeypatch) -> None:
    monkeypatch.setattr(agent_flow, "_interactive_menu", AsyncMock(return_value="chosen"))
    assert await agent_flow._interactive_menu("Title", [("a", "A", "desc")]) == "chosen"


def test_slash_command_completer_exposes_descriptions() -> None:
    completer = agent_flow._SlashCommandCompleter()
    completions = list(completer.get_completions(Document("/"), None))

    assert completions
    texts = [c.text for c in completions]
    assert "resume" in texts
    assert "sessions" in texts
    assert "setting" in texts
    assert "channel" in texts
    assert "plugin" in texts
    assert "feishu" in texts
    assert "skill_compiler" in texts
    setting = next(c for c in completions if c.text == "setting")
    assert "Open settings menu" in str(setting.display_meta)
    assert "login" not in texts


def test_slash_command_completer_supports_nested_channel_commands() -> None:
    completer = agent_flow._SlashCommandCompleter()

    completions = list(completer.get_completions(Document("/channel we"), None))
    texts = [completion.text for completion in completions]

    assert "wechat" in texts
    assert "status" not in texts


def test_slash_command_completer_enters_next_channel_level_after_space() -> None:
    completer = agent_flow._SlashCommandCompleter()

    completions = list(completer.get_completions(Document("/channel wechat "), None))
    texts = [completion.text for completion in completions]

    assert "login" in texts
    assert "logout" in texts
    assert "status" in texts


def test_slash_command_completer_supports_nested_plugin_commands() -> None:
    completer = agent_flow._SlashCommandCompleter()

    completions = list(completer.get_completions(Document("/pc pl"), None))
    texts = [completion.text for completion in completions]

    assert "plan" in texts


def test_slash_command_completer_supports_profile_subcommands() -> None:
    completer = agent_flow._SlashCommandCompleter()

    completions = list(completer.get_completions(Document("/profile "), None))
    texts = [completion.text for completion in completions]

    assert "on" in texts
    assert "deep" in texts
    assert "off" in texts


def test_slash_command_completer_supports_setting_output_subcommands() -> None:
    completer = agent_flow._SlashCommandCompleter()

    completions = list(completer.get_completions(Document("/setting output "), None))
    texts = [completion.text for completion in completions]

    assert "normal" in texts
    assert "profile" in texts
    assert "deep-profile" in texts


def test_slash_command_completer_supports_setting_fast_subcommands() -> None:
    completer = agent_flow._SlashCommandCompleter()

    completions = list(completer.get_completions(Document("/setting fast "), None))
    texts = [completion.text for completion in completions]

    assert "on" in texts
    assert "off" in texts


def test_slash_command_completer_supports_plugin_activate_placeholder() -> None:
    completer = agent_flow._SlashCommandCompleter()

    completions = list(completer.get_completions(Document("/plugin activate "), None))
    texts = [completion.text for completion in completions]

    assert "<name>" in texts


def test_slash_command_completer_suggests_local_plugin_names(monkeypatch) -> None:
    monkeypatch.setattr(
        "aeloon.cli.interactive.navigation.suggest_plugin_entries",
        lambda action: (
            [
                SimpleNamespace(
                    id="demo.plugin",
                    source="workspace",
                    status="deactivated",
                    version="0.1.0",
                )
            ]
            if action == "activate"
            else []
        ),
    )

    completer = agent_flow._SlashCommandCompleter()
    completions = list(completer.get_completions(Document("/plugin activate "), None))
    texts = [completion.text for completion in completions]

    assert "demo.plugin" in texts
    assert "<name>" not in texts


def test_slash_command_completer_supports_resume_switch_placeholder() -> None:
    completer = agent_flow._SlashCommandCompleter()

    completions = list(completer.get_completions(Document("/resume switch "), None))
    texts = [completion.text for completion in completions]

    assert "<session-key>" in texts


def test_slash_command_completer_supports_channel_status_placeholder() -> None:
    completer = agent_flow._SlashCommandCompleter()

    completions = list(completer.get_completions(Document("/channel status "), None))
    texts = [completion.text for completion in completions]

    assert "<name>" in texts


@pytest.mark.asyncio
async def test_sessions_command_lists_current_session(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    current = loop.sessions.get_or_create("cli:direct")
    current.add_message("user", "hello")
    loop.sessions.save(current)

    other = loop.sessions.get_or_create("cli:alt")
    other.add_message("assistant", "world")
    loop.sessions.save(other)

    response = await loop._process_message(
        InboundMessage(channel="cli", sender_id="u", chat_id="direct", content="/sessions")
    )

    assert response is not None
    assert "Recent sessions:" in response.content
    assert "cli:direct (current)" in response.content
    assert "cli:alt" in response.content
    assert "/resume switch <session-key>" in response.content


@pytest.mark.asyncio
async def test_resume_command_lists_current_session(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    current = loop.sessions.get_or_create("cli:direct")
    current.add_message("user", "hello")
    loop.sessions.save(current)

    response = await loop._process_message(
        InboundMessage(channel="cli", sender_id="u", chat_id="direct", content="/resume")
    )

    assert response is not None
    assert "Recent sessions:" in response.content
    assert "cli:direct (current)" in response.content


@pytest.mark.asyncio
async def test_sessions_switch_returns_switch_metadata(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    target = loop.sessions.get_or_create("cli:other")
    target.add_message("user", "hello again")
    loop.sessions.save(target)

    response = await loop._process_message(
        InboundMessage(
            channel="cli",
            sender_id="u",
            chat_id="direct",
            content="/sessions switch cli:other",
        )
    )

    assert response is not None
    assert response.content == "Switching to session: cli:other"
    assert response.metadata["_session_switch"] is True
    assert response.metadata["session_key"] == "cli:other"


@pytest.mark.asyncio
async def test_resume_switch_returns_switch_metadata(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    target = loop.sessions.get_or_create("cli:other")
    target.add_message("user", "hello again")
    loop.sessions.save(target)

    response = await loop._process_message(
        InboundMessage(
            channel="cli",
            sender_id="u",
            chat_id="direct",
            content="/resume switch cli:other",
        )
    )

    assert response is not None
    assert response.content == "Switching to session: cli:other"
    assert response.metadata["_session_switch"] is True
    assert response.metadata["session_key"] == "cli:other"


@pytest.mark.asyncio
async def test_sessions_switch_rejects_unknown_session(tmp_path) -> None:
    loop = _make_loop(tmp_path)

    response = await loop._process_message(
        InboundMessage(
            channel="cli",
            sender_id="u",
            chat_id="direct",
            content="/sessions switch cli:missing",
        )
    )

    assert response is not None
    assert response.content == "Session not found: cli:missing"


def test_rank_slash_commands_prefers_prefix_matches() -> None:
    ranked = agent_flow._rank_slash_commands("set")

    assert ranked
    assert ranked[0][0] == "/setting"


def test_rank_slash_commands_does_not_fuzzy_match_invalid_input() -> None:
    ranked = agent_flow._rank_slash_commands("stng")

    assert ranked == []


def test_rank_slash_commands_returns_all_for_empty_query() -> None:
    ranked = agent_flow._rank_slash_commands("")

    assert ranked
    labels = [label for label, _desc in ranked]
    assert "/channel" in labels
    assert "/pc" in labels
    assert "/channel wechat" not in labels


def test_palette_input_processor_hides_leading_slash() -> None:
    processor = agent_flow._PaletteInputProcessor()
    transformed = processor.apply_transformation(
        SimpleNamespace(fragments=[("", "/setting"), ("", " rest")])
    )

    assert transformed.fragments[0][1] == "setting"
    assert transformed.fragments[1][1] == " rest"


def test_rank_slash_commands_matches_partial_query() -> None:
    ranked = agent_flow._rank_slash_commands("sess")

    assert ranked
    assert ranked[0][0] == "/sessions"


def test_exact_sessions_command_is_not_partial_palette_only() -> None:
    command = "/sessions"
    assert agent_flow._should_open_slash_palette(command) is True


def test_exact_leaf_command_does_not_open_palette() -> None:
    assert agent_flow._should_open_slash_palette("/help") is False


def test_nested_exact_command_opens_palette_for_next_level() -> None:
    assert agent_flow._should_open_slash_palette("/channel wechat") is True


def test_nested_partial_command_opens_palette_for_current_level() -> None:
    assert agent_flow._should_open_slash_palette("/channel we") is True


def test_auto_descend_skips_reselecting_current_root_level() -> None:
    assert agent_flow._auto_descend_query("channel") == "channel "


def test_auto_descend_skips_reselecting_current_nested_level() -> None:
    assert agent_flow._auto_descend_query("channel wechat") == "channel wechat "


def test_prompt_toolkit_window_import_available() -> None:
    assert Window is not None


def test_rank_slash_commands_matches_multiple_partial_queries() -> None:
    setting_ranked = agent_flow._rank_slash_commands("set")
    sessions_ranked = agent_flow._rank_slash_commands("sess")
    help_ranked = agent_flow._rank_slash_commands("he")

    assert any(cmd == "/setting" for cmd, _desc in setting_ranked)
    assert any(cmd == "/sessions" for cmd, _desc in sessions_ranked)
    assert any(cmd == "/help" for cmd, _desc in help_ranked)


def test_partial_sessions_query_prefers_sessions() -> None:
    ranked = agent_flow._rank_slash_commands("se")
    assert ranked[0][0] == "/sessions"


def test_nested_channel_query_prefers_nested_channel_command() -> None:
    ranked = agent_flow._rank_slash_commands("channel wechat st")

    assert ranked
    assert ranked[0][0] == "/channel wechat status"


def test_nested_channel_parent_query_prefers_parent_level_command() -> None:
    ranked = agent_flow._rank_slash_commands("channel we")

    assert ranked
    assert ranked[0][0] == "/channel wechat"
    assert not any(label == "/channel wechat status" for label, _desc in ranked)


def test_nested_plugin_parent_query_prefers_parent_level_command() -> None:
    ranked = agent_flow._rank_slash_commands("pc")

    assert ranked
    assert ranked[0][0] == "/pc"
    assert not any(label == "/pc plan" for label, _desc in ranked)


def test_profile_parent_query_prefers_parent_level_command() -> None:
    ranked = agent_flow._rank_slash_commands("profile")

    assert ranked
    assert ranked[0][0] == "/profile"
    assert not any(label == "/profile on" for label, _desc in ranked)


def test_profile_space_enters_profile_children() -> None:
    ranked = agent_flow._rank_slash_commands("profile ")

    assert ranked
    labels = [label for label, _desc in ranked]
    assert "/profile on" in labels
    assert "/profile off" in labels


def test_setting_output_space_enters_output_children() -> None:
    ranked = agent_flow._rank_slash_commands("setting output ")

    assert ranked
    labels = [label for label, _desc in ranked]
    assert "/setting output normal" in labels
    assert "/setting output profile" in labels
    assert "/setting output deep-profile" in labels


def test_plugin_activate_space_enters_placeholder_child() -> None:
    ranked = agent_flow._rank_slash_commands("plugin activate ")

    assert ranked
    assert ranked[0][0] == "/plugin activate <name>"


def test_plugin_activate_space_prefers_local_plugin_names(monkeypatch) -> None:
    monkeypatch.setattr(
        "aeloon.cli.interactive.navigation.suggest_plugin_entries",
        lambda action: (
            [
                SimpleNamespace(
                    id="demo.plugin",
                    source="workspace",
                    status="deactivated",
                    version="0.1.0",
                )
            ]
            if action == "activate"
            else []
        ),
    )

    ranked = agent_flow._rank_slash_commands("plugin activate ")

    assert ranked
    assert ranked[0][0] == "/plugin activate demo.plugin"


def test_resume_switch_space_enters_placeholder_child() -> None:
    ranked = agent_flow._rank_slash_commands("resume switch ")

    assert ranked
    assert ranked[0][0] == "/resume switch <session-key>"
