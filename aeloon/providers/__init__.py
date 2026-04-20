"""LLM provider abstraction module.

Keep package import lightweight so commands like `aeloon onboard` do not pull in
heavy provider dependencies such as LiteLLM unless they are actually needed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aeloon.providers.base import LLMProvider, LLMResponse

if TYPE_CHECKING:
    from aeloon.providers.azure_openai_provider import AzureOpenAIProvider
    from aeloon.providers.litellm_provider import LiteLLMProvider
    from aeloon.providers.openai_codex_provider import OpenAICodexProvider

__all__ = [
    "LLMProvider",
    "LLMResponse",
    "LiteLLMProvider",
    "OpenAICodexProvider",
    "AzureOpenAIProvider",
]


def __getattr__(name: str) -> Any:
    """Lazily expose heavy provider classes on demand."""
    if name == "LiteLLMProvider":
        from aeloon.providers.litellm_provider import LiteLLMProvider

        return LiteLLMProvider
    if name == "OpenAICodexProvider":
        from aeloon.providers.openai_codex_provider import OpenAICodexProvider

        return OpenAICodexProvider
    if name == "AzureOpenAIProvider":
        from aeloon.providers.azure_openai_provider import AzureOpenAIProvider

        return AzureOpenAIProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
