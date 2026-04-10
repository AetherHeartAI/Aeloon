<p align="right">
<b>English</b> | <a href="./README.cn.md">中文</a>
</p>

<div align="center">

<p align="left">
  <img src="./assets/AE-logo.svg#gh-light-mode-only" alt="Aether Heart" height="60" />
  <img src="./assets/AE-logo-dark.svg#gh-dark-mode-only" alt="Aether Heart" height="60" />
</p>

<p align="center">
  <img src="./assets/aeloon-alive.png" alt="Aeloon alive" height="360" />
</p>

<p align="center">
  <img src="./assets/Aeloon.svg#gh-light-mode-only" alt="Aeloon" height="40" />
  <img src="./assets/Aeloon-dark.svg#gh-dark-mode-only" alt="Aeloon" height="40" />
</p>

A **safe · light · fast** AI Agent

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](./LICENSE)
[![Ruff](https://img.shields.io/badge/Linter-Ruff%200.15.6-orange.svg)](https://docs.astral.sh/ruff/)

</div>

---

## What is Aeloon?

Aeloon is a message-bus-based AI assistant runtime. It unifies LLMs, tool calls, memory, and multi-channel messaging into a single runtime — you talk to it via CLI, Web UI, or IM platforms, and it orchestrates everything.

Core philosophy: **one agent, all platforms, self-hosted data, privacy first.**

---

## ✨ Key Features

| Feature | Description |
|---------|-------------|
| 🔄 **Message Bus Architecture** | Channel → MessageBus → Dispatcher → AgentLoop → Tool execution → Routed reply — decoupled and extensible |
| 📡 **Multi-Channel** | Telegram, Feishu, DingTalk, WeCom, Slack, Discord, QQ, Email, WhatsApp, Matrix, WeChat |
| 🧠 **Smart Context** | Automatic context building, memory consolidation, skill injection, sub-agent delegation |
| 🔧 **Built-in Tools** | FileSystem, Web search/fetch, Shell exec, Cron, MCP protocol, Message sending |
| 🤖 **Multi-Provider** | OpenAI, Anthropic, DeepSeek, Gemini, Zhipu, Moonshot, MiniMax, Groq, Ollama, vLLM, Azure OpenAI, OpenRouter, custom endpoints |
| 📝 **Skill System** | Pure Markdown-driven — extend agent capabilities with zero code |
| 🔌 **MCP Protocol** | Built-in MCP client to connect external tool servers |
| 🔧 **Plugin SDK** | Complete plugin development framework: commands, tools, services, middleware, status bar, lifecycle management |
| 🌉 **ACP Bridge** | Built-in ACP Bridge plugin connecting to external agent ecosystems via ACP protocol |
| 📊 **Task Graph** | Tool calls compiled into a DAG — read-only ops run concurrently, writes serialized conservatively |
| 🔒 **Security First** | Network safety policies, execution sandbox, API key isolation |
| 🐳 **Docker Ready** | One-command `docker compose up` for gateway mode |

---

## 🚀 Quick Start

### One-Click Install

```bash
curl -fsSL https://raw.githubusercontent.com/AetherHeartAI/Aeloon/main/install.sh | bash
```

The installer walks you through provider and channel setup interactively. Offline and source installs are also supported:

```bash
# Install from local source
bash install.sh --from-source

# Install a specific version
bash install.sh --version v1.0.0

# Offline mode (requires local wheel)
bash install.sh --offline
```

### Manual Install

```bash
# Install from source
git clone https://github.com/AetherHeartAI/Aeloon.git
cd Aeloon
pip install -e .

# First-time config
aeloon onboard
```

### Build a wheel

```bash
# Output to dist/ by default
bash scripts/build_wheel.sh

# Use a custom directory and clear old artifacts
bash scripts/build_wheel.sh ./artifacts --clear
```

### First Conversation

```bash
# One-shot CLI chat
aeloon agent -m "Hello Aeloon."

# Start gateway mode (connects all channels)
aeloon gateway

# Check channel status
aeloon channels status
```
---

## 🔌 Channel Support

**WeChat** and **Feishu** support QR scan login out of the box (`/wechat login`, Feishu in-app auth).

Other channels: Telegram, DingTalk, WeCom, Slack, Discord, QQ, Email, WhatsApp, Matrix.

All channels inherit `BaseChannel` and are **auto-discovered** — drop a file in `aeloon/channels/` and it works, no registration needed.

---

## 🤖 Provider Support

Major LLMs with one-click setup: OpenAI, Anthropic, DeepSeek, Gemini, Zhipu, Moonshot, MiniMax, Groq, OpenRouter (free tier included), Azure OpenAI, Ollama, vLLM, plus OAuth-based OpenAI Codex / GitHub Copilot.

Selection priority: explicit name → model keyword match → API key prefix → API base match.

---

## 🔧 Tool System

| Tool | Module | Function |
|------|--------|----------|
| FileSystem | `filesystem.py` | Read/write files, list directories, search |
| Web | `web.py` | Search (DuckDuckGo), fetch pages, extract content |
| Shell | `shell.py` | Execute commands (with safety policy) |
| Cron | `cron.py` | Create/list/remove scheduled tasks |
| MCP | `mcp.py` | Connect to external MCP tool servers |
| Message | `message.py` | Cross-channel message sending |
| Policy | `policy.py` | Tool execution policy control |
| Spawn | `spawn.py` | Sub-agent delegation |

---

## 📝 Skill System

**Pure Markdown** — extend agent capabilities with zero code. Drop a `SKILL.md` into `.aeloon/skills/` and it's injected into the system prompt. Built-in skills cover scheduling, doc conversion, GitHub, memory, summarization, terminal, and more.

---

## 🔌 Plugin System

Plugins are modular components that extend Aeloon's core: custom slash commands, agent tools, background services, message middleware, status bar contributions, and isolated config/storage namespaces.

**Plugin types**:
- **Task Plugin** (commands + middleware) — request-response workflows, e.g. `/sr`, `/se`
- **Hybrid Plugin** (commands + tools + services) — long-running agents, e.g. market monitoring
- **Service / Status Plugin** (services or status bar contributions) — service-style extensions or CLI status bar contributions, e.g. `StatusPannel`

**Built-in plugins**:

| Plugin | Type | Description |
|--------|------|-------------|
| [**ScienceResearch**](./aeloon/plugins/ScienceResearch/README-SR.md) | Task | AI4S research workflow: literature search, ArXiv, risk assessment, orchestrator, structured output |
| [**SkillGraph**](./aeloon/plugins/SkillGraph/) | Task | Skill dependency graph compilation and visualization |
| [**Wiki**](./aeloon/plugins/Wiki/README.md) | Hybrid | Local knowledge base: content ingestion, smart digest, conversational knowledge enhancement, knowledge graph |
| [**ACP Bridge**](./aeloon/plugins/acp_bridge/README.md) | Hybrid | External ACP agent bridging — connect third-party Agent protocols |
| [**StatusPannel**](./aeloon/plugins/StatusPannel/) | Service | CLI status bar: model name, context token usage |

**Lifecycle**: Discover → Validate → Register → Commit → Activate → Running → Deactivate

**Five-step dev paradigm**: Extend `Plugin` → Implement `register(api)` → Implement `activate()` → Write handlers → Create `aeloon.plugin.json` manifest.

Full development guide: [aeloon/plugins/README.md](./aeloon/plugins/README.md).

---

## 🐳 Docker Deployment

```bash
# Gateway mode
docker compose up

# CLI interactive mode
docker compose run --rm aeloon-cli
```

Default `docker-compose.yml` configuration:
- Gateway port: `18790`
- Config mount: `~/.aeloon:/root/.aeloon`
- Resource limits: 1 CPU / 1GB memory

---

## ⚙️ Configuration

Config lives at `~/.aeloon/config.json` and supports camelCase aliases. Loading precedence:

```
--config flag  >  AELOON_CONFIG env var  >  ~/.aeloon/config.json
```

Minimal example:

```json
{
  "agents": {
    "defaults": {
      "provider": "openrouter",
      "model": "deepseek/deepseek-r1:free"
    }
  },
  "providers": {
    "openrouter": {
      "apiKey": "sk-or-v1-..."
    }
  }
}
```

---

## 🧪 Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run specific tests
pytest tests/test_kernel.py
pytest -k "telegram"

# Lint + auto-fix
ruff check --fix .

# Format
ruff format .
```

**Requirements**: `ruff==0.15.6` (CI enforced), Python ≥ 3.11, type hints on all public functions, `async/await` preferred for I/O.

### Commit Convention

Uses [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <short summary>
```

Types: `feat`, `fix`, `chore`, `docs`, `test`, `refactor`, `perf`, `ci`

---

## 🔒 Security

- **Network policy**: `_network_safety.py` restricts reachable URLs to prevent SSRF
- **Execution sandbox**: Shell tool uses policy controls for allowed commands
- **API key isolation**: Keys stored in config, never logged
- **Channel auth**: Each channel supports `allowFrom` whitelisting
- **MCP security**: MCP connections can configure allowed tool scopes

---

## 📜 License

[MIT License](./LICENSE) © 2026 Aether Heart contributors

---

<div align="center">

*"始于鳌，成于龙."*
Born from the deep. Rising into something more.

</div>
