"""Provider authentication and setup CLI commands."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
import shutil
import sys
from typing import Any

import typer

from aeloon import __logo__
from aeloon.cli.app import app, console

_DEFAULT_BOOTSTRAP_OPENROUTER_KEY = (
    "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"
)
_BOOTSTRAP_FREE_TERMS = ("qwen", "deepseek", "llama", "gemma", "mistral")

provider_app = typer.Typer(help="Manage providers")
app.add_typer(provider_app, name="provider")

_LOGIN_HANDLERS: dict[str, Callable[[], None]] = {}


def _resolve_config_path(config: str | None) -> Path:
    """Resolve the config path and make it active for this process."""
    from aeloon.core.config.loader import get_config_path, set_config_path

    if config:
        config_path = Path(config).expanduser().resolve()
        set_config_path(config_path)
        console.print(f"[dim]Using config: {config_path}[/dim]")
        return config_path
    return get_config_path()


def _provider_tag_text(record: dict[str, Any]) -> str:
    tags: list[str] = []
    if record.get("oauth"):
        tags.append("oauth")
    if record.get("local"):
        tags.append("local")
    return f" [{', '.join(tags)}]" if tags else ""


def _provider_menu_label(record: dict[str, Any]) -> str:
    recommended = record.get("recommended_model") or ""
    suffix = f" - {recommended}" if recommended else ""
    return f"{record['label']}{_provider_tag_text(record)}{suffix}"


def _can_use_arrow_menu() -> bool:
    """Return True when prompt_toolkit menus can safely run."""
    stdin = getattr(sys.stdin, "isatty", None)
    stdout = getattr(sys.stdout, "isatty", None)
    return bool(callable(stdin) and stdin() and callable(stdout) and stdout())


def _menu_visible_rows(total: int) -> int:
    """Return how many menu rows should be rendered at once."""
    terminal_lines = shutil.get_terminal_size((100, 24)).lines
    return max(8, min(total, terminal_lines - 8))


def _menu_window(total: int, index: int) -> tuple[int, int]:
    """Return the visible menu window for one selected index."""
    visible_rows = _menu_visible_rows(total)
    if total <= visible_rows:
        return 0, total

    half = visible_rows // 2
    start = max(0, index - half)
    end = start + visible_rows
    if end > total:
        end = total
        start = end - visible_rows
    return start, end


def _menu_lines(title: str, options: list[str], index: int) -> list[str]:
    """Build visible text lines for the arrow-key menu."""
    start, end = _menu_window(len(options), index)
    lines = [f"Use Up/Down, Enter to confirm, Esc to cancel [{index + 1}/{len(options)}]"]

    if start > 0:
        lines.append("  ...")
    for idx in range(start, end):
        prefix = "> " if idx == index else "  "
        lines.append(f"{prefix}{options[idx]}")
    if end < len(options):
        lines.append("  ...")
    return lines


def _prompt_menu_arrow(title: str, options: list[str], *, default_index: int = 0) -> int | None:
    """Prompt for a selection using an arrow-key menu."""
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.widgets import Box, Frame, TextArea

    index = default_index if 0 <= default_index < len(options) else 0
    result: dict[str, int | None] = {"value": None}
    body = TextArea(focusable=False, scrollbar=False)

    def _render() -> None:
        body.text = "\n".join(_menu_lines(title, options, index))

    kb = KeyBindings()

    @kb.add("up")
    def _up(_event) -> None:
        nonlocal index
        index = (index - 1) % len(options)
        _render()

    @kb.add("down")
    def _down(_event) -> None:
        nonlocal index
        index = (index + 1) % len(options)
        _render()

    @kb.add("pageup")
    def _page_up(_event) -> None:
        nonlocal index
        index = max(0, index - _menu_visible_rows(len(options)))
        _render()

    @kb.add("pagedown")
    def _page_down(_event) -> None:
        nonlocal index
        index = min(len(options) - 1, index + _menu_visible_rows(len(options)))
        _render()

    @kb.add("home")
    def _home(_event) -> None:
        nonlocal index
        index = 0
        _render()

    @kb.add("end")
    def _end(_event) -> None:
        nonlocal index
        index = len(options) - 1
        _render()

    @kb.add("enter")
    def _enter(event) -> None:
        result["value"] = index
        event.app.exit()

    @kb.add("escape")
    @kb.add("c-c")
    def _cancel(event) -> None:
        event.app.exit()

    _render()
    prompt_app = Application(
        layout=Layout(Box(Frame(body, title=title), padding=1)),
        key_bindings=kb,
        full_screen=False,
    )
    prompt_app.run()
    return result["value"]


def _prompt_menu(title: str, options: list[str], *, default_index: int = 0) -> int:
    """Prompt for a selection and return the chosen index."""
    if _can_use_arrow_menu():
        try:
            selected = _prompt_menu_arrow(title, options, default_index=default_index)
        except Exception:
            selected = None
        if selected is not None:
            return selected

    console.print(f"\n[bold]{title}[/bold]")
    for idx, option in enumerate(options, start=1):
        marker = "[green]*[/green] " if idx - 1 == default_index else "  "
        console.print(f"{marker}{idx}. {option}")

    while True:
        choice = typer.prompt("Choice", default=str(default_index + 1), show_default=True).strip()
        if choice.isdigit():
            selected = int(choice) - 1
            if 0 <= selected < len(options):
                return selected
        console.print(f"[red]Please enter a number between 1 and {len(options)}.[/red]")


def _reuse_or_prompt_api_key(provider: str, current_key: str) -> str:
    """Return a provider API key, optionally reusing the saved one."""
    label = provider.replace("_", "-")
    if current_key and typer.confirm(f"Reuse the saved API key for {label}?", default=True):
        return current_key
    return typer.prompt(
        f"API key for {label}",
        default="",
        show_default=False,
        hide_input=True,
    ).strip()


def _resolve_api_base_prompt(provider: str, current_base: str, default_base: str) -> str:
    """Return the selected API base for one provider."""
    existing_base = current_base.strip()
    normalized_default = default_base.strip()

    if provider in {"custom", "vllm"}:
        fallback = existing_base or "http://127.0.0.1:8000/v1"
        return typer.prompt("API base URL", default=fallback).strip()
    if provider == "azure_openai":
        fallback = existing_base or "https://your-resource.openai.azure.com"
        return typer.prompt("Azure OpenAI endpoint", default=fallback).strip()
    if provider == "ollama":
        fallback = existing_base or "http://127.0.0.1:11434"
        return typer.prompt("Ollama API base URL", default=fallback).strip()

    if existing_base and normalized_default and existing_base != normalized_default:
        if typer.confirm(f"Keep the saved API base {existing_base}?", default=True):
            return existing_base
        return typer.prompt("API base URL", default=normalized_default).strip()

    if normalized_default:
        if typer.confirm("Override the default API base?", default=False):
            return typer.prompt("API base URL", default=normalized_default).strip()
        return normalized_default

    return existing_base


def _pick_free_openrouter_model(result: dict[str, Any]) -> str:
    """Choose a free OpenRouter bootstrap model from detection results."""
    models = [str(model) for model in result.get("models") or []]
    free_models = [model for model in models if ":free" in model or model.endswith("/free")]
    for term in _BOOTSTRAP_FREE_TERMS:
        for model in free_models:
            if term in model.lower():
                return model
    if free_models:
        return free_models[0]
    return str(result.get("recommended") or "")


def _prompt_model_selection(
    provider: str,
    *,
    current_model: str,
    recommended: str,
    models: list[str],
) -> str:
    """Prompt for the model to save for the selected provider."""
    if models:
        options = list(models)
        options.append("Enter a custom model name")
        default_index = 0
        if current_model in models:
            default_index = models.index(current_model)
        elif recommended in models:
            default_index = models.index(recommended)
        selected = _prompt_menu(
            f"Select model for {provider.replace('_', '-')}", options, default_index=default_index
        )
        if selected < len(models):
            return models[selected]

    fallback = current_model or recommended
    return typer.prompt("Model", default=fallback).strip()


def _apply_provider_config(
    config: Any,
    *,
    provider: str,
    model: str,
    api_key: str = "",
    api_base: str = "",
) -> None:
    """Apply one provider selection to the config model."""
    provider_cfg = getattr(config.providers, provider)
    config.agents.defaults.provider = provider
    config.agents.defaults.model = model

    if provider not in {"openai_codex", "github_copilot", "ollama"}:
        provider_cfg.api_key = api_key
    elif provider == "ollama":
        provider_cfg.api_key = ""

    if api_base:
        provider_cfg.api_base = api_base
    elif provider == "ollama":
        provider_cfg.api_base = "http://127.0.0.1:11434"


async def _detect_provider_models(
    provider: str,
    *,
    api_key: str = "",
    api_base: str = "",
) -> dict[str, Any]:
    """Wrap shared model detection for CLI provider setup."""
    from aeloon.install_support import detect_models

    return await detect_models(provider, api_key=api_key or None, api_base=api_base or None)


def _current_provider_name(config: Any) -> str:
    """Return the currently configured provider name."""
    return str(config.agents.defaults.provider or config.get_provider_name() or "").strip()


@provider_app.command("setup")
def provider_setup(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    bootstrap_default: bool = typer.Option(
        False,
        "--bootstrap-default",
        help="Use the bundled OpenRouter free-model setup without prompts",
    ),
) -> None:
    """Configure the default provider and model."""
    from aeloon.core.config.loader import load_config, save_config
    from aeloon.install_support import provider_records, recommended_model, resolve_api_base

    config_path = _resolve_config_path(config)
    loaded = load_config(config_path)

    if bootstrap_default:
        resolved_base = resolve_api_base("openrouter")
        detected = asyncio.run(
            _detect_provider_models(
                "openrouter",
                api_key=_DEFAULT_BOOTSTRAP_OPENROUTER_KEY,
                api_base=resolved_base,
            )
        )
        selected_model = _pick_free_openrouter_model(detected)
        if not selected_model:
            console.print("[red]No fully free OpenRouter models were detected for bootstrap.[/red]")
            raise typer.Exit(1)
        _apply_provider_config(
            loaded,
            provider="openrouter",
            model=selected_model,
            api_key=_DEFAULT_BOOTSTRAP_OPENROUTER_KEY,
            api_base=str(detected.get("resolved_api_base") or resolved_base),
        )
        save_config(loaded, config_path)
        console.print(f"[green]✓[/green] Default provider set to openrouter ({selected_model})")
        return

    records = provider_records()
    current_provider = _current_provider_name(loaded)
    default_index = 0
    labels: list[str] = []
    for idx, record in enumerate(records):
        label = _provider_menu_label(record)
        if record["name"] == current_provider:
            label += " [dim](current)[/dim]"
            default_index = idx
        labels.append(label)

    provider_idx = _prompt_menu("Select provider", labels, default_index=default_index)
    selected_record = records[provider_idx]
    selected_provider = str(selected_record["name"])
    current_model = loaded.agents.defaults.model if current_provider == selected_provider else ""
    current_provider_cfg = getattr(loaded.providers, selected_provider)
    current_key = str(current_provider_cfg.api_key or "")
    current_base = str(current_provider_cfg.api_base or "")
    default_base = str(resolve_api_base(selected_provider, current_base or None) or "")
    recommended = str(
        selected_record.get("recommended_model") or recommended_model(selected_provider)
    )
    api_key = ""
    api_base = ""
    detected: dict[str, Any] = {
        "provider": selected_provider,
        "recommended": recommended,
        "resolved_api_base": default_base,
        "detected": False,
        "models": [],
        "message": "",
    }

    if selected_record.get("oauth"):
        console.print(
            f"[cyan]{selected_record['label']} uses OAuth.[/cyan] "
            f"Run [bold]aeloon provider login {selected_provider.replace('_', '-')}[/bold] after setup."
        )
    else:
        if selected_provider not in {"ollama"}:
            api_key = _reuse_or_prompt_api_key(selected_provider, current_key)
        api_base = _resolve_api_base_prompt(selected_provider, current_base, default_base)
        console.print("[dim]Detecting available models...[/dim]")
        detected = asyncio.run(
            _detect_provider_models(selected_provider, api_key=api_key, api_base=api_base)
        )
        api_base = str(detected.get("resolved_api_base") or api_base or default_base)

        models = [str(model) for model in detected.get("models") or []]
        if models:
            if detected.get("cache_hit"):
                console.print(f"[green]Loaded {len(models)} model(s) from cache.[/green]")
            else:
                console.print(f"[green]Detected {len(models)} model(s).[/green]")
        message = str(detected.get("message") or "")
        if message:
            console.print(f"[dim]{message}[/dim]")

    selected_model = _prompt_model_selection(
        selected_provider,
        current_model=current_model,
        recommended=str(detected.get("recommended") or recommended),
        models=[str(model) for model in detected.get("models") or []],
    )
    if not selected_model:
        console.print("[red]Model cannot be empty.[/red]")
        raise typer.Exit(1)

    _apply_provider_config(
        loaded,
        provider=selected_provider,
        model=selected_model,
        api_key=api_key,
        api_base=api_base,
    )
    save_config(loaded, config_path)

    console.print(
        f"[green]✓[/green] Default provider set to {selected_provider.replace('_', '-')} "
        f"([cyan]{selected_model}[/cyan])"
    )
    if selected_record.get("oauth"):
        console.print(
            f"[dim]Next: aeloon provider login {selected_provider.replace('_', '-')}[/dim]"
        )
    elif selected_provider == "openrouter" and not api_key:
        console.print("[yellow]Warning:[/yellow] OpenRouter API key is empty.")


def _register_login(name: str) -> Callable[[Callable[[], None]], Callable[[], None]]:
    """Register one provider login handler."""

    def decorator(fn: Callable[[], None]) -> Callable[[], None]:
        _LOGIN_HANDLERS[name] = fn
        return fn

    return decorator


@provider_app.command("login")
def provider_login(
    provider: str = typer.Argument(
        ...,
        help="OAuth provider (e.g. 'openai-codex', 'github-copilot')",
    ),
) -> None:
    """Authenticate with an OAuth provider."""
    from aeloon.providers.registry import PROVIDERS

    key = provider.replace("-", "_")
    spec = next((spec for spec in PROVIDERS if spec.name == key and spec.is_oauth), None)
    if not spec:
        names = ", ".join(spec.name.replace("_", "-") for spec in PROVIDERS if spec.is_oauth)
        console.print(f"[red]Unknown OAuth provider: {provider}[/red]  Supported: {names}")
        raise typer.Exit(1)

    handler = _LOGIN_HANDLERS.get(spec.name)
    if not handler:
        console.print(f"[red]Login not implemented for {spec.label}[/red]")
        raise typer.Exit(1)

    console.print(f"{__logo__} OAuth Login - {spec.label}\n")
    handler()


@_register_login("openai_codex")
def _login_openai_codex() -> None:
    try:
        from oauth_cli_kit import get_token, login_oauth_interactive

        token = None
        try:
            token = get_token()
        except Exception:
            pass
        if not (token and token.access):
            console.print("[cyan]Starting interactive OAuth login...[/cyan]\n")
            token = login_oauth_interactive(
                print_fn=lambda text: console.print(text),
                prompt_fn=lambda text: typer.prompt(text),
            )
        if not (token and token.access):
            console.print("[red]✗ Authentication failed[/red]")
            raise typer.Exit(1)
        console.print(
            f"[green]✓ Authenticated with OpenAI Codex[/green]  [dim]{token.account_id}[/dim]"
        )
    except ImportError:
        console.print("[red]oauth_cli_kit not installed. Run: pip install oauth-cli-kit[/red]")
        raise typer.Exit(1)


@_register_login("github_copilot")
def _login_github_copilot() -> None:
    console.print("[cyan]Starting GitHub Copilot device flow...[/cyan]\n")

    async def _trigger() -> None:
        from litellm import acompletion

        await acompletion(
            model="github_copilot/gpt-4o",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=1,
        )

    try:
        asyncio.run(_trigger())
        console.print("[green]✓ Authenticated with GitHub Copilot[/green]")
    except Exception as exc:
        console.print(f"[red]Authentication error: {exc}[/red]")
        raise typer.Exit(1)
