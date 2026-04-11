<p align="right">
<a href="./README.md">English</a> | <b>中文</b>
</p>

# Aeloon 插件开发指南

Aeloon 插件开发完整指南 —— 从概念到 API 参考。

## 目录

1. [概述](#概述)
2. [快速开始](#快速开始)
3. [插件架构](#插件架构)
4. [核心概念](#核心概念)
5. [API 参考](#api-参考)
6. [插件专属指南](#插件专属指南)

---

## 概述

### 插件是什么

Aeloon 插件是扩展 Aeloon 核心能力的**模块化组件**，让你能够：

- **添加自定义命令**：如 `/mycommand` 响应用户请求
- **注册 Agent 工具**：供 LLM 调用的功能函数
- **启动后台服务**：定时轮询、数据同步等长期任务
- **拦截消息流**：审计、预算控制、风险门控等
- **自定义配置存储**：独立的配置命名空间和存储目录

插件是**增量扩展**——复用 Aeloon 的 Agent Runtime、消息总线、配置系统，同时保持模块隔离。

### Plugin SDK 支持

| 能力 | 说明 |
|------|------|
| 生命周期管理 | 自动发现 → 验证 → 加载 → 注册 → 激活 → 停用 |
| 统一注册机制 | 通过 `PluginAPI` 注册命令、工具、服务、中间件、配置 |
| 运行时访问 | LLM 调用、Agent 执行、存储目录、配置值 |
| 事件订阅 | 监听 AGENT_START、MESSAGE_RECEIVED 等事件 |
| 状态栏贡献 | 向底部状态栏添加状态信息 |
| 隔离性保证 | 插件间配置、存储、日志独立 |

### 开发范式

创建插件的五步范式：

1. **继承 Plugin 基类**
2. **实现 register(api)** —— 声明命令、工具、配置等
3. **实现生命周期方法** —— `activate()` 启动服务，`deactivate()` 清理资源
4. **编写业务处理函数** —— 处理命令逻辑
5. **创建清单文件** —— `aeloon.plugin.json` 声明插件元数据

**插件类型**：
- **Task Plugin**（命令 + 中间件）：适合请求-响应式工作流，如 `/sr`、`/wiki`
- **Hybrid Plugin**（命令 + 工具 + 服务）：适合长期运行代理，如市场监控

---

## 快速开始

### 最小插件结构

```
my_plugin/
├── aeloon.plugin.json    # 清单文件
├── __init__.py
└── plugin.py             # 插件类
```

**清单文件** (`aeloon.plugin.json`)：
- `id`: 反向 DNS 标识符，必须包含 `.`
- `name`: 可读名称
- `version`: 语义化版本
- `entry`: `模块:类名` 格式
- `provides`: 提供的命令、工具等
- `requires`: 依赖的 Aeloon 版本、其他插件等

**安装测试**：将插件复制到 `~/.aeloon/plugins/`，重启 Aeloon 即可使用。

---

## 插件架构

### 架构分层

```
┌─────────────────────────────────────────┐
│ 插件层 (Plugin Layer)                    │
│  - Task Plugin: 命令 + 中间件            │
│  - Hybrid Plugin: 命令 + 工具 + 服务     │
├─────────────────────────────────────────┤
│ SDK 层                                  │
│  - Plugin 基类、PluginAPI 接口           │
│  - 生命周期管理、注册机制                │
├─────────────────────────────────────────┤
│ 运行时层 (Runtime Layer)                 │
│  - AgentLoop、MessageBus                │
│  - LLM 访问、工具执行                    │
├─────────────────────────────────────────┤
│ 核心层 (Core Layer)                      │
│  - 配置系统、存储系统、渠道集成          │
└─────────────────────────────────────────┘
```

![Plugin SDK System Architecture](../../assets/fig2_plugin_sdk_system_architecture.svg)

### 生命周期

```
发现 → 验证 → 注册 → 提交 → 激活 → [运行中] → 停用
```

| 阶段 | 方法 | 说明 |
|------|------|------|
| 注册 | `register(api)` | 同步。声明命令、工具、服务 |
| 提交 | `api._commit()` | 原子写入注册表 |
| 激活 | `activate(api)` | 异步。启动服务、初始化状态（30秒超时） |
| 停用 | `deactivate()` | 异步。清理资源（30秒超时） |

### 发现来源

| 来源 | 优先级 | 位置 |
|------|--------|------|
| 内置 | 10 | `aeloon/plugins/` |
| Entry Points | 20 | `aeloon.plugins` setuptools 组 |
| 工作区 | 30 | `~/.aeloon/plugins/` |

---

## 核心概念

### 命令 (Commands)

Slash 命令是用户与插件交互的主要方式。

**注册参数**：
- `name`: 命令名称（不含 `/`）
- `handler`: 处理函数，接收 `CommandContext` 和参数字符串
- `description`: 命令描述

**子命令路由模式**：通过空格分割参数，第一个词作为子命令，其余作为参数。

### 工具 (Tools)

工具是 LLM 可调用的函数。需定义：
- `name`: 工具标识
- `description`: 功能描述
- `parameters`: JSON Schema 参数定义
- `execute(**kwargs)`: 执行逻辑

### 服务 (Services)

后台服务用于长期运行的任务，需实现：
- `start(runtime, config)`: 启动服务
- `stop()`: 停止服务
- `health_check()`: （可选）返回健康状态

**服务策略** (`ServicePolicy`)：
- `restart_policy`: 重启策略（never/on-failure/always）
- `max_restarts`: 最大重启次数
- `restart_delay_seconds`: 重启间隔
- `startup_timeout_seconds`: 启动超时
- `shutdown_timeout_seconds`: 关闭超时

### Hooks & Middleware

**Hooks**：响应生命周期事件，不耦合核心内部。

Hook 类型：
- `NOTIFY`: 触发即忘，错误记录但不传播
- `MUTATE`: 链式处理，每个处理器转换值
- `REDUCE`: 收集，所有返回值汇总为列表
- `GUARD`: 允许/拒绝/修改，首个拒绝生效

常用事件：AGENT_START、MESSAGE_RECEIVED、BEFORE_TOOL_CALL 等。

**Middleware**：包装每个 LLM turn 进行前置/后置处理，如审计日志、预算控制。

### 配置与存储

**配置**：使用 Pydantic BaseModel 定义配置模型，通过 `api.register_config_schema()` 注册。用户配置位于 `~/.aeloon/config.toml`。

**存储**：插件拥有独立存储目录 `~/.aeloon/plugin_storage/{plugin_id}/`，通过 `api.runtime.storage_path` 访问。

**LLM 访问**：通过 `api.runtime.llm` 访问，支持普通对话、结构化输出、完整 Agent 流水线。

### 状态栏

插件可向底部状态栏贡献状态片段，需同步返回 `StatusSegment` 列表。

---

## API 参考

### Plugin (抽象基类)

| 方法 | 必需 | 说明 |
|------|------|------|
| `register(api)` | 是 | 同步。注册命令、工具、服务 |
| `activate(api)` | 否 | 异步。启动服务（30秒超时） |
| `deactivate()` | 否 | 异步。清理（30秒超时） |
| `health_check()` | 否 | 返回健康状态字典 |

### PluginAPI

**属性**：
- `id`: 插件 ID
- `version`: 版本
- `config`: 配置字典
- `runtime`: 运行时访问

**注册方法**：
- `register_command(name, handler, description)`: 注册命令
- `register_tool(tool)`: 注册工具
- `register_service(name, service_cls, policy)`: 注册服务
- `register_middleware(name, middleware)`: 注册中间件
- `register_hook(event, handler, kind, priority)`: 注册 Hook
- `register_cli(name, builder)`: 注册 CLI 子命令
- `register_config_schema(schema_cls)`: 注册配置模型
- `register_status_provider(name, provider, priority)`: 注册状态栏提供者

**服务控制**：
- `start_service(name, config_overrides)`: 启动服务
- `stop_service(name)`: 停止服务
- `list_service_status()`: 列出服务状态

### PluginRuntime

**属性**：
- `agent_loop`: 主 Agent 循环
- `config`: 插件配置
- `storage_path`: 存储目录路径
- `logger`: 命名空间日志器
- `llm`: LLM 访问代理

**方法**：
- `process_direct(content, **kwargs)`: 委托给 Agent 流水线

## 插件专属指南

| 插件 | 指南 | 说明 |
|------|------|------|
| **ScienceResearch** | [`README-SR.md`](ScienceResearch/README-SR.md) | AI4S 科研任务插件完整指南 |
| **Wiki** | [`README-wiki.md`](Wiki/README-wiki.md) | 本地知识库管理插件完整指南 |
| **ACP Bridge** | [`README-ACP.md`](acp_bridge/README-ACP.md) | ACP 协议桥接：连接外部智能体服务器 |
| **PluginCreator** | [`README-PC.md`](PluginCreator/README-PC.md) | 插件开发工作流智能规划器完整指南 |
| **SkillGraph** | [`README-SG.md`](SkillGraph/README-SG.md) | Skill 编译器：将 SKILL.md 编译为可运行的 Python 产物 |

专属指南包含：架构详解、运行时流程、数据模型、运维配置、扩展模式。

---

## 资源

- **SDK 源码**：`aeloon/plugins/_sdk/`
- **内置示例**：`ScienceResearch/`, `Wiki/`, `StatusPannel/`,`SkillGraph`,`PluginCreator`
- **测试**：`tests/test_plugin_sdk.py`
