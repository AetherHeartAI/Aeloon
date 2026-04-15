"""Aeloon config schema."""

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from pydantic_settings import BaseSettings


class Base(BaseModel):
    """Base model that accepts camelCase and snake_case keys."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ChannelsConfig(Base):
    """Channel config container."""

    model_config = ConfigDict(extra="allow")

    send_progress: bool = True  # Stream agent text progress.
    send_tool_hints: bool = False  # Stream tool hints such as read_file("...").


class AgentDefaults(Base):
    """Default agent settings."""

    workspace: str = "~/.aeloon/workspace"
    model: str = "anthropic/claude-opus-4-5"
    provider: str = "auto"  # Provider name, or "auto".
    max_tokens: int = 8192
    context_window_tokens: int = 65_536
    temperature: float = 0.1
    max_tool_iterations: int = 40
    # Legacy field kept for config compatibility.
    memory_window: int | None = Field(default=None, exclude=True)
    reasoning_effort: str | None = None  # low / medium / high
    chat_timeout: int = 300  # Per-attempt LLM timeout in seconds.
    output_mode: str = "normal"  # normal / detail / debug / profile / deep-profile
    fast: bool = False  # Skip slower optional startup work.

    @property
    def should_warn_deprecated_memory_window(self) -> bool:
        """Return True when old memoryWindow is still in use."""
        return (
            self.memory_window is not None and "context_window_tokens" not in self.model_fields_set
        )


class AgentsConfig(Base):
    """Agent config."""

    defaults: AgentDefaults = Field(default_factory=AgentDefaults)


class ProviderConfig(Base):
    """LLM provider config."""

    api_key: str = ""
    api_base: str | None = None
    extra_headers: dict[str, str] | None = None  # Extra request headers.


class ProvidersConfig(Base):
    """Provider config."""

    custom: ProviderConfig = Field(
        default_factory=ProviderConfig
    )  # Any OpenAI-compatible endpoint.
    azure_openai: ProviderConfig = Field(default_factory=ProviderConfig)  # Azure OpenAI deployment.
    anthropic: ProviderConfig = Field(default_factory=ProviderConfig)
    openai: ProviderConfig = Field(default_factory=ProviderConfig)
    openrouter: ProviderConfig = Field(default_factory=ProviderConfig)
    deepseek: ProviderConfig = Field(default_factory=ProviderConfig)
    groq: ProviderConfig = Field(default_factory=ProviderConfig)
    zhipu: ProviderConfig = Field(default_factory=ProviderConfig)
    dashscope: ProviderConfig = Field(default_factory=ProviderConfig)
    vllm: ProviderConfig = Field(default_factory=ProviderConfig)
    ollama: ProviderConfig = Field(default_factory=ProviderConfig)  # Ollama local models.
    gemini: ProviderConfig = Field(default_factory=ProviderConfig)
    moonshot: ProviderConfig = Field(default_factory=ProviderConfig)
    minimax: ProviderConfig = Field(default_factory=ProviderConfig)
    aihubmix: ProviderConfig = Field(default_factory=ProviderConfig)  # AiHubMix gateway.
    siliconflow: ProviderConfig = Field(default_factory=ProviderConfig)  # SiliconFlow.
    volcengine: ProviderConfig = Field(default_factory=ProviderConfig)  # VolcEngine.
    volcengine_coding_plan: ProviderConfig = Field(
        default_factory=ProviderConfig
    )  # VolcEngine Coding Plan.
    byteplus: ProviderConfig = Field(default_factory=ProviderConfig)  # BytePlus.
    byteplus_coding_plan: ProviderConfig = Field(
        default_factory=ProviderConfig
    )  # BytePlus Coding Plan.
    openai_codex: ProviderConfig = Field(default_factory=ProviderConfig)  # OpenAI Codex OAuth.
    github_copilot: ProviderConfig = Field(default_factory=ProviderConfig)  # GitHub Copilot OAuth.


class HeartbeatConfig(Base):
    """Heartbeat settings."""

    enabled: bool = True
    interval_s: int = 30 * 60  # 30 minutes.


class GatewayConfig(Base):
    """Gateway settings."""

    host: str = "0.0.0.0"
    port: int = 18790
    heartbeat: HeartbeatConfig = Field(default_factory=HeartbeatConfig)


class WebSearchConfig(Base):
    """Web search settings."""

    provider: str = "duckduckgo"  # brave, tavily, duckduckgo, searxng, jina
    api_key: str = ""
    base_url: str = ""  # SearXNG base URL.
    max_results: int = 5
    search_timeout_s: float = 10.0
    fetch_timeout_s: float = 20.0
    fallback_fetch_timeout_s: float = 25.0


class WebToolsConfig(Base):
    """Web tool settings."""

    proxy: str | None = None  # HTTP or SOCKS5 proxy URL.
    search: WebSearchConfig = Field(default_factory=WebSearchConfig)


class ExecToolConfig(Base):
    """Shell exec settings."""

    timeout: int = 60
    path_append: str = ""


class MCPServerConfig(Base):
    """MCP server settings."""

    type: Literal["stdio", "sse", "streamableHttp"] | None = None  # Auto-detect when omitted.
    command: str = ""  # Stdio command.
    args: list[str] = Field(default_factory=list)  # Stdio arguments.
    env: dict[str, str] = Field(default_factory=dict)  # Stdio env vars.
    url: str = ""  # HTTP or SSE endpoint URL.
    headers: dict[str, str] = Field(default_factory=dict)  # Custom headers.
    tool_timeout: int = 30  # Tool timeout in seconds.
    enabled_tools: list[str] = Field(default_factory=lambda: ["*"])  # ["*"] means all tools.


class ToolsConfig(Base):
    """Tool settings."""

    web: WebToolsConfig = Field(default_factory=WebToolsConfig)
    exec: ExecToolConfig = Field(default_factory=ExecToolConfig)
    restrict_to_workspace: bool = False  # Restrict tool access to the workspace.
    mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)


class HookHandlerConfig(BaseModel):
    """Hook handler settings."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    type: Literal["command", "http"]
    # Command handler fields.
    command: str = ""
    timeout: int = 600
    async_exec: bool = False
    # HTTP handler fields.
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    method: str = "POST"
    http_timeout: int = 30


class HookEntryConfig(BaseModel):
    """One hook entry."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    matcher: str | None = None
    priority: int = 0
    handler: HookHandlerConfig


class Config(BaseSettings):
    """Root Aeloon config."""

    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    plugins: dict[str, Any] = Field(default_factory=dict)
    hooks: dict[str, list[HookEntryConfig]] = Field(default_factory=dict)

    @property
    def workspace_path(self) -> Path:
        """Return the expanded workspace path."""
        return Path(self.agents.defaults.workspace).expanduser()

    def _match_provider(
        self, model: str | None = None
    ) -> tuple["ProviderConfig | None", str | None]:
        """Return the matched provider config and name."""
        from aeloon.providers.registry import PROVIDERS

        forced = self.agents.defaults.provider
        if forced != "auto":
            p = getattr(self.providers, forced, None)
            return (p, forced) if p else (None, None)

        model_lower = (model or self.agents.defaults.model).lower()
        model_normalized = model_lower.replace("-", "_")
        model_prefix = model_lower.split("/", 1)[0] if "/" in model_lower else ""
        normalized_prefix = model_prefix.replace("-", "_")

        def _kw_matches(kw: str) -> bool:
            kw = kw.lower()
            return kw in model_lower or kw.replace("-", "_") in model_normalized

        # Exact provider prefixes win.
        for spec in PROVIDERS:
            p = getattr(self.providers, spec.name, None)
            if p and model_prefix and normalized_prefix == spec.name:
                if spec.is_oauth or spec.is_local or p.api_key:
                    return p, spec.name

        # Then match by keyword.
        for spec in PROVIDERS:
            p = getattr(self.providers, spec.name, None)
            if p and any(_kw_matches(kw) for kw in spec.keywords):
                if spec.is_oauth or spec.is_local or p.api_key:
                    return p, spec.name

        # Then try configured local providers.
        local_fallback: tuple[ProviderConfig, str] | None = None
        for spec in PROVIDERS:
            if not spec.is_local:
                continue
            p = getattr(self.providers, spec.name, None)
            if not (p and p.api_base):
                continue
            if spec.detect_by_base_keyword and spec.detect_by_base_keyword in p.api_base:
                return p, spec.name
            if local_fallback is None:
                local_fallback = (p, spec.name)
        if local_fallback:
            return local_fallback

        # Last fallback: non-OAuth providers with keys.
        for spec in PROVIDERS:
            if spec.is_oauth:
                continue
            p = getattr(self.providers, spec.name, None)
            if p and p.api_key:
                return p, spec.name
        return None, None

    def get_provider(self, model: str | None = None) -> ProviderConfig | None:
        """Return the matched provider config."""
        p, _ = self._match_provider(model)
        return p

    def get_provider_name(self, model: str | None = None) -> str | None:
        """Return the matched provider name."""
        _, name = self._match_provider(model)
        return name

    def get_api_key(self, model: str | None = None) -> str | None:
        """Return the API key for a model."""
        p = self.get_provider(model)
        return p.api_key if p else None

    def get_api_base(self, model: str | None = None) -> str | None:
        """Return the API base URL for a model."""
        from aeloon.providers.registry import find_by_name

        p, name = self._match_provider(model)
        if p and p.api_base:
            return p.api_base
        # Only gateways and local providers get a default api_base here.
        if name:
            spec = find_by_name(name)
            if spec and (spec.is_gateway or spec.is_local) and spec.default_api_base:
                return spec.default_api_base
        return None

    model_config = ConfigDict(env_prefix="AELOON_", env_nested_delimiter="__", extra="ignore")
