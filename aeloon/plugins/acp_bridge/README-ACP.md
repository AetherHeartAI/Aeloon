<p align="right">
<a href="./README.md">English</a> | <b>中文</b>
</p>

# ACP Bridge 插件

Agent Client Protocol (ACP) 桥接插件 —— 连接 Aeloon 与外部 ACP 智能体服务器。

## 目录

1. [什么是 ACP](#什么是-acp)
2. [功能特性](#功能特性)
3. [安装与配置](#安装与配置)
4. [命令参考](#命令参考)
5. [连接其他智能体服务器](#连接其他智能体服务器)
6. [安全配置](#安全配置)
7. [架构设计](#架构设计)

---

## 什么是 ACP

**Agent Client Protocol (ACP)** 是一种标准化的智能体通信协议，允许不同的 AI 助手和智能体系统相互协作。ACP Bridge 插件让 Aeloon 能够：

- **连接外部智能体**：如 Claude Code、其他 ACP 兼容的智能体
- **委派任务**：将特定任务发送给专门的智能体处理
- **接收结果**：获取外部智能体的执行结果并整合到对话中
- **实时流式输出**：查看外部智能体的执行进度

### 工作流程

```
用户 → Aeloon → ACP Bridge → ACP 智能体服务器
                ↑                    ↓
         接收流式更新 ← 执行任务 → 返回结果
```

---

## 功能特性

| 特性 | 说明 |
|------|------|
| **多配置文件** | 支持配置多个 ACP 后端，快速切换 |
| **stdio 传输** | 通过标准输入输出与子进程智能体通信 |
| **权限控制** | 细粒度的文件读写、Shell 执行权限管理 |
| **实时流式** | 实时查看外部智能体的执行进度和输出 |
| **自动重连** | 连接失败时自动重试（可配置策略） |
| **状态监控** | 状态栏显示连接状态和活跃会话数 |

---

## 安装与配置

### 1. 安装 ACP Python SDK

```bash
pip install agent-client-protocol
```

### 2. 启用插件

在 `~/.aeloon/acp.json` 中添加：

```json
{
  "plugins": {
    "aeloon_acp_bridge": {
      "enabled": true,
      "defaultProfile": "claude_code",
      "autoConnect": false,
      "profiles": {
        "claude_code": {
          "command": ["npx", "@agentclientprotocol/claude-agent-acp"],
          "cwd": "~",
          "timeoutSeconds": 30,
          "env": {
            "ACP_PERMISSION_MODE": "acceptEdits"
          }
        }
      },
      "policy": {
        "autoApproveSafeRequests": false,
        "allowFileRead": false,
        "allowFileWrite": false,
        "allowShell": false
      }
    }
  }
}
```

### 3. 配置环境变量

如果使用 Claude Code，需要设置 API Key：

```bash
export ANTHROPIC_API_KEY=your_api_key_here
```

或使用 Claude CLI 登录：

```bash
claude login
```

---

## 命令参考

### `/acp connect [profile]`

连接到指定的 ACP 后端配置文件。

```
/acp connect              # 使用默认配置（claude_code）
/acp connect claude_code  # 显式指定配置
/acp connect my_agent     # 连接到自定义智能体
```

### `/acp list`

列出当前可用的 ACP 后端配置，并标记默认配置。

```
/acp list
```

输出示例：
```
Available ACP backends:
- claude_code — npx @agentclientprotocol/claude-agent-acp
- kimi_cli (default) — kimi acp
```

### `/acp chat <message>`

向已连接的 ACP 智能体发送消息。

```
/acp chat 请帮我分析这个项目的代码结构
/acp chat 运行测试并生成报告
```

### `/acp disconnect`

断开当前 ACP 连接。

```
/acp disconnect
```

### `/acp status`

查看连接状态和统计信息。

```
/acp status
```

输出示例：
```
State: connected
Profile: claude_code
Sessions: 2
```

### `/acp help`

显示帮助信息。

---

## 连接其他智能体服务器

ACP Bridge 不仅限于连接 Claude Code，可以连接任何实现 ACP 协议的智能体服务器。

### 配置示例：Kimi CLI（推荐）

**Kimi CLI** 是 Moonshot AI 自研的命令行智能体工具，原生支持 ACP 协议：

```bash
# 安装 Kimi CLI
uv tool install kimi-cli

# 设置 API Key
export MOONSHOT_API_KEY="your_api_key"
```

配置文件中添加 Kimi CLI 配置：

```json
{
  "plugins": {
    "aeloon_acp_bridge": {
      "profiles": {
        "kimi_cli": {
          "command": ["kimi", "acp"],
          "cwd": "~",
          "timeoutSeconds": 60,
          "env": {
            "MOONSHOT_API_KEY": "${MOONSHOT_API_KEY}"
          }
        }
      }
    }
  }
}
```

**特点**：
- 原生 ACP 支持，无需包装脚本
- 支持 256K 长上下文
- 支持 MCP 工具扩展
- 支持 Shell 模式 (Ctrl+K)

**连接使用**：
```
/acp list
/acp connect kimi_cli
/acp chat 分析这个项目的代码结构
```

### 配置示例：OpenAI Codex CLI

**注意**：OpenAI Codex CLI 官方版本暂不支持原生 ACP 协议。目前推荐使用 Claude Agent ACP：

```json
{
  "plugins": {
    "aeloon_acp_bridge": {
      "profiles": {
        "codex_like": {
          "command": ["npx", "@agentclientprotocol/claude-agent-acp"],
          "cwd": "~",
          "timeoutSeconds": 30,
          "env": {
            "ACP_PERMISSION_MODE": "acceptEdits"
          }
        }
      }
    }
  }
}
```

**说明**：OpenAI 已宣布将在未来版本为 Codex CLI 添加 ACP 支持，届时可直接使用 `codex --acp` 启动。

### 配置示例：自定义 ACP 智能体

假设你有一个自定义的 ACP 智能体服务器脚本：

```json
{
  "plugins": {
    "aeloon_acp_bridge": {
      "profiles": {
        "my_custom_agent": {
          "command": ["python", "/path/to/my_acp_agent.py"],
          "cwd": "~/projects",
          "timeoutSeconds": 60,
          "env": {
            "MY_AGENT_API_KEY": "secret_key",
            "LOG_LEVEL": "debug"
          }
        }
      }
    }
  }
}
```

### 配置示例：Docker 容器中的智能体

连接运行在 Docker 容器中的 ACP 智能体：

```json
{
  "plugins": {
    "aeloon_acp_bridge": {
      "profiles": {
        "docker_agent": {
          "command": [
            "docker", "run", "-i", "--rm",
            "-e", "API_KEY=${DOCKER_AGENT_KEY}",
            "-v", "${HOME}/projects:/workspace",
            "my-registry/acp-agent:latest"
          ],
          "cwd": "~",
          "timeoutSeconds": 120,
          "env": {}
        }
      }
    }
  }
}
```

### 配置示例：远程 SSH 智能体

通过 SSH 连接远程服务器上的 ACP 智能体：

```json
{
  "plugins": {
    "aeloon_acp_bridge": {
      "profiles": {
        "remote_server": {
          "command": [
            "ssh", "-t", "user@remote-server",
            "cd /opt/acp-agent && ./start-agent.sh"
          ],
          "cwd": "~",
          "timeoutSeconds": 60,
          "env": {}
        }
      }
    }
  }
}
```

### 配置示例：Node.js ACP 智能体

连接使用 Node.js 实现的 ACP 智能体：

```json
{
  "plugins": {
    "aeloon_acp_bridge": {
      "profiles": {
        "nodejs_agent": {
          "command": ["node", "/path/to/acp-agent/dist/main.js"],
          "cwd": "~/nodejs-project",
          "timeoutSeconds": 45,
          "env": {
            "NODE_ENV": "production",
            "AGENT_CONFIG_PATH": "./config.json"
          }
        }
      }
    }
  }
}
```

### 快速切换配置文件

```
# 连接 Claude Code
/acp connect claude_code

# 切换到自定义智能体
/acp connect my_custom_agent

# 切换到远程服务器智能体
/acp connect remote_server
```

---

## 安全配置

### 权限策略

在配置中设置权限策略，控制 ACP 智能体的能力：

```json
{
  "policy": {
    "autoApproveSafeRequests": false,
    "allowFileRead": true,
    "allowFileWrite": false,
    "allowShell": false
  }
}
```

| 权限 | 说明 | 建议 |
|------|------|------|
| `autoApproveSafeRequests` | 自动批准"安全"请求 | 生产环境建议关闭 |
| `allowFileRead` | 允许读取文件 | 按需开启 |
| `allowFileWrite` | 允许写入文件 | 谨慎开启 |
| `allowShell` | 允许执行 Shell 命令 | 高安全风险 |

### 环境变量隔离

每个配置文件可以设置独立的环境变量，实现不同智能体的凭据隔离：

```json
{
  "profiles": {
    "prod_agent": {
      "env": { "API_KEY": "prod_key", "ENV": "production" }
    },
    "dev_agent": {
      "env": { "API_KEY": "dev_key", "ENV": "development" }
    }
  }
}
```

---

## 架构设计

### 组件架构

```
┌─────────────────────────────────────────────────────────────┐
│                     ACP Bridge 插件                          │
├─────────────────────────────────────────────────────────────┤
│  Commands          │  Service              │  Config        │
│  ─────────         │  ───────              │  ───────       │
│  /acp connect      │  ACPConnectionService │  Profiles      │
│  /acp chat         │  ├─ ACPClient         │  Policy        │
│  /acp disconnect   │  ├─ Session Manager   │                │
│  /acp status       │  └─ Health Monitor    │                │
├─────────────────────────────────────────────────────────────┤
│                     ACP 协议层                               │
├─────────────────────────────────────────────────────────────┤
│  stdio Transport   │  Handshake   │  Session Management     │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│              外部 ACP 智能体服务器 (子进程)                   │
│         (Claude Code / 自定义智能体 / 远程智能体)             │
└─────────────────────────────────────────────────────────────┘
```

### 数据流

```
用户输入 → /acp chat
    ↓
ACPConnectionService
    ↓
ACPClient.connect(profile)
    ↓
stdio transport → 子进程智能体
    ↓
流式响应 ← 实时更新回调
    ↓
Aeloon 回复用户
```

### 生命周期

1. **注册阶段**：插件注册命令、服务、配置模式
2. **激活阶段**：启动 ACPConnectionService，可选自动连接
3. **连接阶段**：用户执行 `/acp connect`，建立 stdio 传输
4. **交互阶段**：通过 `/acp chat` 发送消息，接收流式响应
5. **断开阶段**：用户执行 `/acp disconnect` 或 Agent 停止时清理

---

## 故障排除

### 连接失败：缺少 Python 模块

```
Failed to connect: missing Python module `acp`
```

**解决**：
```bash
pip install agent-client-protocol
```

### 连接失败：命令未找到

```
Failed to connect: [Errno 2] No such file or directory: 'npx'
```

**解决**：确保 Node.js 和 npm 已安装：
```bash
npm --version
node --version
```

### 权限被拒绝

检查配置文件中的权限策略设置，确保 `allowFileRead`、`allowFileWrite`、`allowShell` 按需开启。

### 超时

增加配置中的 `timeoutSeconds` 值，或检查智能体服务器是否正常运行。
