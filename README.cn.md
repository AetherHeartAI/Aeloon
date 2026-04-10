<p align="right">
<a href="./README.md">English</a> | <b>中文</b>
</p>

<div align="center">

<p align="left">
  <img src="./assets/AH.svg" alt="Aether Heart" height="60" />
</p>

<p align="center">
  <img src="./assets/aeloon-alive.png" alt="Aeloon alive" height="360" />
</p>

<p align="center">
  <img src="./assets/Aeloon.svg" alt="Aeloon" height="40" />
</p>

**安全 · 轻量 · 快速** 的 AI 智能体

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](./LICENSE)
[![Ruff](https://img.shields.io/badge/Linter-Ruff%200.15.6-orange.svg)](https://docs.astral.sh/ruff/)

</div>

---

## Aeloon 是什么？

Aeloon 是一个基于消息总线的 AI 助手运行时框架。它将大语言模型、工具调用、记忆系统和多渠道消息整合为一个统一的运行时——你可以通过 CLI、Web UI 或即时通讯平台与它对话，而它负责协调一切。

核心理念：**一个智能体，全平台可用，数据自持，隐私至上。**

---

## ✨ 核心特性

| 特性 | 说明 |
|------|------|
| 🔄 **消息总线架构** | 渠道 → MessageBus → Dispatcher → AgentLoop → 工具执行 → 路由回复，解耦且可扩展 |
| 📡 **多渠道支持** | Telegram、飞书、钉钉、企业微信、Slack、Discord、QQ、Email、WhatsApp、Matrix、WeChat |
| 🧠 **智能上下文** | 自动上下文构建、记忆合并、技能注入、子智能体委托 |
| 🔧 **内置工具** | 文件系统、Web 搜索/抓取、Shell 执行、定时任务（Cron）、MCP 协议、消息发送 |
| 🤖 **多 Provider** | OpenAI、Anthropic、DeepSeek、Gemini、通义千问、月之暗面、MiniMax、Groq、Ollama、vLLM、Azure OpenAI、OpenRouter、自定义端点 |
| 📝 **技能系统** | 纯 Markdown 驱动，零代码即可扩展智能体能力 |
| 🔌 **MCP 协议** | 内置 MCP 客户端，可连接外部工具服务器 |
| 🔧 **插件 SDK** | 完整插件开发框架：命令、工具、服务、中间件、状态栏、生命周期管理 |
| 🌉 **ACP 桥接** | 内置 ACP Bridge 插件，通过 ACP 协议连接外部智能体生态 |
| 📊 **任务图** | 工具调用编译为 DAG，只读操作并发执行，写操作串行保守调度 |
| 🔒 **安全优先** | 网络安全策略、执行沙箱、API Key 隔离 |
| 🐳 **Docker 就绪** | 一键 `docker compose up` 启动网关模式 |

---

## 🚀 快速开始

### 一键安装

```bash
curl -fsSL https://raw.githubusercontent.com/AetherHeartAI/Aeloon/main/install.sh | bash
```

安装脚本会交互式引导你配置 Provider 和渠道。也支持离线安装和从源码安装：

```bash
# 从本地源码安装
bash install.sh --from-source

# 指定版本
bash install.sh --version v1.0.0

# 离线模式（需本地 wheel）
bash install.sh --offline
```

### 手动安装

```bash
# 克隆仓库
git clone https://github.com/AetherHeartAI/Aeloon.git
cd Aeloon
pip install -e .

# 首次配置
aeloon onboard
```

### 第一次对话

```bash
# CLI 一轮对话
aeloon agent -m "你好，Aeloon."

# 启动网关模式（连接各渠道）
aeloon gateway

# 查看渠道状态
aeloon channels status
```


---

## 🔌 渠道支持

**微信** 和 **飞书** 支持扫码直接登录（`/wechat login`、飞书应用内免登），开箱即用。

其他渠道：Telegram、钉钉、企业微信、Slack、Discord、QQ、Email、WhatsApp、Matrix。

所有渠道继承 `BaseChannel`，**自动发现**——放入 `aeloon/channels/` 即可启用，无需注册。

---

## 🤖 Provider 支持

国内外主流模型一键接入：OpenAI、Anthropic、DeepSeek、Gemini、通义千问、月之暗面、MiniMax、Groq、OpenRouter（含免费额度）、Azure OpenAI、Ollama、vLLM，以及 OAuth 认证的 OpenAI Codex / GitHub Copilot。

选择优先级：显式指定 → 模型关键词匹配 → API Key 前缀 → API Base 匹配。

---

## 🔧 工具系统

| 工具 | 模块 | 功能 |
|------|------|------|
| FileSystem | `filesystem.py` | 读写文件、目录列表、搜索 |
| Web | `web.py` | 搜索（DuckDuckGo）、抓取网页、提取正文 |
| Shell | `shell.py` | 执行命令（带安全策略） |
| Cron | `cron.py` | 创建/列表/删除定时任务 |
| MCP | `mcp.py` | 连接外部 MCP 工具服务器 |
| Message | `message.py` | 跨渠道发送消息 |
| Policy | `policy.py` | 工具执行策略控制 |
| Spawn | `spawn.py` | 子智能体委派 |

---

## 📝 技能系统

**纯 Markdown** 驱动，零代码扩展智能体能力。在工作区 `.aeloon/skills/` 下创建 `SKILL.md` 即可注入新技能到系统提示。内置技能涵盖定时任务、文档转换、GitHub 操作、记忆管理、摘要、终端等。

---

## 🔌 插件系统

插件是扩展 Aeloon 核心能力的模块化组件，支持：自定义 Slash 命令、注册 Agent 工具、后台服务、消息中间件、状态栏贡献，以及独立的配置与存储命名空间。

**插件类型**：
- **Task Plugin**（命令 + 中间件）—— 请求-响应式工作流，如 `/sr`、`/wiki`
- **Hybrid Plugin**（命令 + 工具 + 服务）—— 长期运行代理，如市场监控
- **Service / Status Plugin**（服务或状态栏贡献）—— 服务型扩展或 CLI 状态栏贡献，如 `StatusPannel`

**内置插件**：

| 插件 | 类型 | 说明 |
|------|------|------|
| [**ScienceResearch**](./aeloon/plugins/ScienceResearch/README-SR.md) | Task | AI4S 科研工作流：文献检索、ArXiv、风险评估、编排器、结构化输出 |
| [**SkillGraph**](./aeloon/plugins/SkillGraph/) | Task | 技能依赖图编译与可视化 |
| [**Wiki**](./aeloon/plugins/Wiki/README.md) | Hybrid | 本地知识库：内容采集、智能摘要、对话知识增强、知识图谱 |
| [**ACP Bridge**](./aeloon/plugins/acp_bridge/README.md) | Hybrid | 外部 ACP 智能体桥接，连接第三方 Agent 协议 |
| [**StatusPannel**](./aeloon/plugins/StatusPannel/) | Service | CLI 状态栏：模型名称、上下文 Token 用量 |

**生命周期**：发现 → 验证 → 注册 → 提交 → 激活 → 运行 → 停用

**五步开发范式**：继承 `Plugin` → 实现 `register(api)` → 实现 `activate()` → 编写业务处理 → 创建 `aeloon.plugin.json` 清单。

完整开发指南见 [aeloon/plugins/README.md](./aeloon/plugins/README.md)。

---

## 🐳 Docker 部署

```bash
# 网关模式
docker compose up

# CLI 交互模式
docker compose run --rm aeloon-cli
```

`docker-compose.yml` 默认配置：
- 网关端口：`18790`
- 配置目录挂载：`~/.aeloon:/root/.aeloon`
- 资源限制：1 CPU / 1GB 内存

---

## ⚙️ 配置

配置文件位于 `~/.aeloon/config.json`，支持 camelCase 别名。加载优先级：

```
--config 标志  >  AELOON_CONFIG 环境变量  >  ~/.aeloon/config.json
```

最小配置示例：

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

## 🧪 开发

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行测试
pytest

# 运行指定测试
pytest tests/test_kernel.py
pytest -k "telegram"

# 代码检查 + 自动修复
ruff check --fix .

# 格式化
ruff format .
```

**要求**：`ruff==0.15.6`（CI 强制），Python ≥ 3.11，所有公开函数需要类型注解，异步 I/O 优先使用 `async/await`。

### 提交规范

采用 [Conventional Commits](https://www.conventionalcommits.org/) 格式：

```
<type>(<scope>): <short summary>
```

类型：`feat`、`fix`、`chore`、`docs`、`test`、`refactor`、`perf`、`ci`

---

## 🔒 安全

- **网络策略**：`_network_safety.py` 限制可访问的 URL，防止 SSRF
- **执行沙箱**：Shell 工具通过策略控制可执行命令
- **API Key 隔离**：配置文件存储 API Key，不记录到日志
- **渠道认证**：每条渠道支持 `allowFrom` 白名单
- **MCP 安全**：MCP 连接可配置允许的工具范围

---

## 📜 许可证

[MIT License](./LICENSE) © 2026 Aether Heart contributors

---

<div align="center">

*"始于鳌，成于龙."*
Born from the deep. Rising into something more.

</div>
