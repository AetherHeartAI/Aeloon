"""Built-in runtime settings slash commands."""

from __future__ import annotations

from loguru import logger

from aeloon.core.agent.commands import slash_command
from aeloon.plugins._sdk.types import CommandContext
from aeloon.providers.registry import PROVIDERS


def _list_available_models(ctx: CommandContext) -> list[str]:
    """Return configured runtime model candidates."""
    models: list[str] = []
    current_model = ctx.model
    if current_model:
        models.append(current_model)

    config = getattr(ctx.provider, "config", None)
    if config is not None:
        defaults = getattr(getattr(config, "agents", None), "defaults", None)
        model_name = getattr(defaults, "model", None)
        if isinstance(model_name, str) and model_name and model_name not in models:
            models.append(model_name)

        providers_cfg = getattr(config, "providers", None)
        for spec in PROVIDERS:
            provider_cfg = getattr(providers_cfg, spec.name, None) if providers_cfg else None
            api_key = getattr(provider_cfg, "api_key", "") if provider_cfg else ""
            api_base = getattr(provider_cfg, "api_base", None) if provider_cfg else None
            if not provider_cfg:
                continue
            if not (spec.is_oauth or spec.is_local or api_key or api_base):
                continue
            candidate = f"{spec.name}/{ctx.provider.get_default_model()}"
            if candidate not in models:
                models.append(candidate)
    return models


@slash_command(
    name="profile",
    help="Show or toggle profiling",
    slash_path=("profile",),
    slash_paths=(("profile", "on"), ("profile", "deep"), ("profile", "off")),
)
async def handle_profile(ctx: CommandContext, args_str: str) -> str | None:
    """Show or update profiler state."""
    args = args_str.split() if args_str else []
    profiler = ctx.profiler
    settings = ctx.runtime_settings
    if not args:
        status = "enabled" if profiler.enabled else "disabled"
        mode = settings.output_mode
        lines = [f"Profiling is {status}."]
        if mode in {"profile", "deep-profile"}:
            lines.append(f"Current profile mode: {mode}.")
        if profiler.last_report:
            report = profiler.report_deep_profile() if mode == "deep-profile" else profiler.report()
            lines.extend(["", report])
        else:
            lines.extend(["", "No profiling report available yet."])
        return "\n".join(lines)

    toggle = args[0].lower()
    if toggle == "on":
        profiler.enabled = True
        settings.output_mode = "profile"
        return "Profiling enabled (profile mode)."
    if toggle == "deep":
        profiler.enabled = True
        settings.output_mode = "deep-profile"
        return "Profiling enabled (deep-profile mode). Workflow stages will be shown."
    if toggle == "off":
        profiler.enabled = False
        settings.output_mode = "normal"
        return "Profiling disabled."

    return "Usage: /profile [on|deep|off]"


@slash_command(
    name="setting",
    help="Open settings menu",
    slash_path=("setting",),
    slash_paths=(
        ("setting", "output"),
        ("setting", "output", "normal"),
        ("setting", "output", "profile"),
        ("setting", "output", "deep-profile"),
        ("setting", "fast"),
        ("setting", "fast", "on"),
        ("setting", "fast", "off"),
        ("setting", "models"),
    ),
)
async def handle_setting(ctx: CommandContext, args_str: str) -> str | None:
    """Show or update runtime settings."""
    settings = ctx.runtime_settings
    normalized_args = args_str.split() if args_str else []
    if len(normalized_args) == 1 and "=" in normalized_args[0]:
        key, value = normalized_args[0].split("=", 1)
        if key and value:
            normalized_args = [key, value]

    if not normalized_args:
        return (
            "## Settings\n\n"
            f"- output: {settings.output_mode}\n"
            f"- fast: {'on' if settings.fast else 'off'}\n"
            "- models\n\n"
            "## Usage\n\n"
            "- `/setting output [normal|profile|deep-profile]`\n"
            "- `/setting fast <on|off>`\n"
            "- `/setting models`"
        )

    item = normalized_args[0].lower()
    if item == "output":
        if len(normalized_args) == 1:
            return (
                "Output modes: normal, profile, deep-profile\n"
                f"Current output mode: {settings.output_mode}"
            )
        mode = normalized_args[1].lower()
        if mode not in {"normal", "profile", "deep-profile"}:
            return "Usage: /setting output [normal|profile|deep-profile]"
        settings.output_mode = mode
        ctx.profiler.enabled = mode in {"profile", "deep-profile"}
        try:
            from aeloon.core.config.loader import load_config, save_config

            config = load_config()
            config.agents.defaults.output_mode = mode
            save_config(config)
        except Exception as exc:
            logger.warning("Failed to persist output mode setting: {}", exc)
            return f"Output mode set to {mode} for current runtime, but failed to persist it to config."
        return f"Output mode set to {mode} and saved to config."

    if item == "models":
        models = _list_available_models(ctx)
        lines = ["Available models:"]
        if models:
            lines.extend(f"- {model}" for model in models)
        else:
            lines.append("- No configured models found.")
        return "\n".join(lines)

    if item == "fast":
        if len(normalized_args) == 1:
            return f"Fast mode is {'on' if settings.fast else 'off'}. Usage: /setting fast <on|off>"
        toggle = normalized_args[1].lower()
        if toggle not in {"on", "off"}:
            return "Usage: /setting fast <on|off>"
        enabled = toggle == "on"
        settings.fast = enabled
        try:
            from aeloon.core.config.loader import load_config, save_config

            config = load_config()
            config.agents.defaults.fast = enabled
            save_config(config)
        except Exception as exc:
            logger.warning("Failed to persist fast mode setting: {}", exc)
            return f"Fast mode set to {toggle} for current runtime, but failed to persist it to config."
        return f"Fast mode set to {toggle} and saved to config. Restart Aeloon to apply startup-time LiteLLM behavior."

    return "Usage: /setting output [normal|profile|deep-profile] | /setting fast <on|off> | /setting models"
