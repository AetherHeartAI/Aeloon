<p align="right">
<b>English</b> | <a href="./README.cn.md">中文</a>
</p>

# Aeloon Plugin Development Guide

Complete guide for Aeloon plugin development — from concepts to API reference.

## Table of Contents

1. [Overview](#overview)
2. [Quick Start](#quick-start)
3. [Plugin Architecture](#plugin-architecture)
4. [Core Concepts](#core-concepts)
5. [API Reference](#api-reference)
6. [Plugin-Specific Guides](#plugin-specific-guides)

---

## Overview

### What is a Plugin

Aeloon plugins are **modular components** that extend Aeloon's core capabilities, allowing you to:

- **Add custom commands**: Such as `/mycommand` to respond to user requests
- **Register Agent tools**: Functional functions for LLM to call
- **Start background services**: Long-running tasks like scheduled polling and data synchronization
- **Intercept message flow**: Auditing, budget control, risk gating, etc.
- **Custom configuration storage**: Independent configuration namespace and storage directory

Plugins are **incremental extensions** — they reuse Aeloon's Agent Runtime, message bus, and configuration system while maintaining module isolation.

### Plugin SDK Support

| Capability | Description |
|------------|-------------|
| Lifecycle Management | Auto-discovery → validation → loading → registration → activation → deactivation |
| Unified Registration | Register commands, tools, services, middleware, and configuration via `PluginAPI` |
| Runtime Access | LLM invocation, Agent execution, storage directory, configuration values |
| Event Subscription | Listen to events like AGENT_START, MESSAGE_RECEIVED, etc. |
| Status Bar Contribution | Add status information to the bottom status bar |
| Isolation Guarantee | Independent configuration, storage, and logging between plugins |

### Development Paradigm

Five-step paradigm for creating plugins:

1. **Inherit from Plugin base class**
2. **Implement register(api)** — Declare commands, tools, configuration, etc.
3. **Implement lifecycle methods** — `activate()` to start services, `deactivate()` to clean up resources
4. **Write business processing functions** — Handle command logic
5. **Create manifest file** — `aeloon.plugin.json` to declare plugin metadata

**Plugin Types**:
- **Task Plugin** (commands + middleware): Suitable for request-response workflows, such as `/sr`, `/wiki`
- **Hybrid Plugin** (commands + tools + services): Suitable for long-running agents, such as market monitoring

---

## Quick Start

### Minimal Plugin Structure

```
my_plugin/
├── aeloon.plugin.json    # Manifest file
├── __init__.py
└── plugin.py             # Plugin class
```

**Manifest File** (`aeloon.plugin.json`):
- `id`: Reverse DNS identifier, must contain `.`
- `name`: Human-readable name
- `version`: Semantic version
- `entry`: Format as `module:ClassName`
- `provides`: Commands, tools, etc. provided by the plugin
- `requires`: Dependencies on Aeloon version, other plugins, etc.

**Installation Test**: Copy the plugin to `~/.aeloon/plugins/`, restart Aeloon to use it.

---

## Plugin Architecture

### Architecture Layers

```
┌─────────────────────────────────────────┐
│ Plugin Layer                            │
│  - Task Plugin: Commands + Middleware   │
│  - Hybrid Plugin: Commands + Tools + Services │
├─────────────────────────────────────────┤
│ SDK Layer                               │
│  - Plugin base class, PluginAPI interface │
│  - Lifecycle management, registration mechanism │
├─────────────────────────────────────────┤
│ Runtime Layer                           │
│  - AgentLoop, MessageBus                │
│  - LLM access, tool execution           │
├─────────────────────────────────────────┤
│ Core Layer                              │
│  - Configuration system, storage system, channel integration │
└─────────────────────────────────────────┘
```

![Plugin SDK System Architecture](../../assets/fig2_plugin_sdk_system_architecture.svg)

### Lifecycle

```
Discovery → Validation → Registration → Commit → Activation → [Running] → Deactivation
```

| Phase | Method | Description |
|-------|--------|-------------|
| Registration | `register(api)` | Synchronous. Declare commands, tools, services |
| Commit | `api._commit()` | Atomic write to registry |
| Activation | `activate(api)` | Asynchronous. Start services, initialize state (30s timeout) |
| Deactivation | `deactivate()` | Asynchronous. Clean up resources (30s timeout) |

### Discovery Sources

| Source | Priority | Location |
|--------|----------|----------|
| Built-in | 10 | `aeloon/plugins/` |
| Entry Points | 20 | `aeloon.plugins` setuptools group |
| Workspace | 30 | `~/.aeloon/plugins/` |

---

## Core Concepts

### Commands

Slash commands are the primary way users interact with plugins.

**Registration Parameters**:
- `name`: Command name (without `/`)
- `handler`: Handler function, receives `CommandContext` and parameter string
- `description`: Command description

**Subcommand Routing Pattern**: Split parameters by space, first word as subcommand, rest as parameters.

### Tools

Tools are functions callable by LLM. Need to define:
- `name`: Tool identifier
- `description`: Function description
- `parameters`: JSON Schema parameter definition
- `execute(**kwargs)`: Execution logic

### Services

Background services are used for long-running tasks, need to implement:
- `start(runtime, config)`: Start service
- `stop()`: Stop service
- `health_check()`: (Optional) Return health status

**Service Policy** (`ServicePolicy`):
- `restart_policy`: Restart policy (never/on-failure/always)
- `max_restarts`: Maximum number of restarts
- `restart_delay_seconds`: Restart interval
- `startup_timeout_seconds`: Startup timeout
- `shutdown_timeout_seconds`: Shutdown timeout

### Hooks & Middleware

**Hooks**: Respond to lifecycle events without coupling to core internals.

Hook Types:
- `NOTIFY`: Fire-and-forget, errors are logged but not propagated
- `MUTATE`: Chain processing, each handler transforms the value
- `REDUCE`: Collection, all return values aggregated as list
- `GUARD`: Allow/reject/modify, first rejection takes effect

Common events: AGENT_START, MESSAGE_RECEIVED, BEFORE_TOOL_CALL, etc.

**Middleware**: Wrap each LLM turn for pre/post processing, such as audit logging, budget control.

### Configuration & Storage

**Configuration**: Use Pydantic BaseModel to define configuration model, register via `api.register_config_schema()`. User configuration is located at `~/.aeloon/config.toml`.

**Storage**: Plugins have independent storage directories at `~/.aeloon/plugin_storage/{plugin_id}/`, accessed via `api.runtime.storage_path`.

**LLM Access**: Accessed via `api.runtime.llm`, supports regular chat, structured output, and full Agent pipeline.

### Status Bar

Plugins can contribute status segments to the bottom status bar, need to synchronously return a list of `StatusSegment`.

---

## API Reference

### Plugin (Abstract Base Class)

| Method | Required | Description |
|--------|----------|-------------|
| `register(api)` | Yes | Synchronous. Register commands, tools, services |
| `activate(api)` | No | Asynchronous. Start services (30s timeout) |
| `deactivate()` | No | Asynchronous. Clean up (30s timeout) |
| `health_check()` | No | Return health status dictionary |

### PluginAPI

**Properties**:
- `id`: Plugin ID
- `version`: Version
- `config`: Configuration dictionary
- `runtime`: Runtime access

**Registration Methods**:
- `register_command(name, handler, description)`: Register command
- `register_tool(tool)`: Register tool
- `register_service(name, service_cls, policy)`: Register service
- `register_middleware(name, middleware)`: Register middleware
- `register_hook(event, handler, kind, priority)`: Register Hook
- `register_cli(name, builder)`: Register CLI subcommand
- `register_config_schema(schema_cls)`: Register configuration model
- `register_status_provider(name, provider, priority)`: Register status bar provider

**Service Control**:
- `start_service(name, config_overrides)`: Start service
- `stop_service(name)`: Stop service
- `list_service_status()`: List service status

### PluginRuntime

**Properties**:
- `agent_loop`: Main Agent loop
- `config`: Plugin configuration
- `storage_path`: Storage directory path
- `logger`: Namespaced logger
- `llm`: LLM access proxy

**Methods**:
- `process_direct(content, **kwargs)`: Delegate to Agent pipeline

### CommandContext

| Field | Type | Description |
|-------|------|-------------|
| `session_key` | `str` | Session identifier |
| `channel` | `str` | Channel name (cli/telegram, etc.) |
| `reply` | `async (str) -> None` | Send intermediate reply |
| `send_progress` | `async (str, **kwargs) -> None` | Send progress update |
| `plugin_config` | `Mapping` | Plugin-specific configuration |

### PluginManifest (Manifest Model)

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | Reverse DNS identifier (must contain `.`) |
| `name` | Yes | Human-readable name |
| `version` | Yes | Semantic version |
| `entry` | Yes | Format as `module:ClassName` |
| `description` | No | Short description |
| `author` | No | Author |
| `provides` | No | Commands, tools, services provided |
| `requires` | No | Dependencies on Aeloon version, plugins, etc. |

---

## Plugin-Specific Guides
| Plugin | Guide | Description |
|--------|-------|-------------|
| **ScienceResearch** | [`README-SR.md`](ScienceResearch/README-SR.md) | Complete guide for AI4S scientific research task plugin |
| **Wiki** | [`README.md`](Wiki/README.md) | Complete guide for local knowledge base management plugin |
| **ACP Bridge** | [`README.md`](acp_bridge/README.md) | ACP protocol bridge: connect to external agent servers |
| **PluginCreator** | [`README-PC.md`](PluginCreator/README-PC.md) | Complete guide for plugin development workflow planner |

Specific guides include: detailed architecture, runtime flow, data models, operational configuration, and extension patterns.

---

## Resources

- **SDK Source Code**: `aeloon/plugins/_sdk/`
- **Built-in Examples**: `ScienceResearch/`, `market/`, `fs/`
- **Tests**: `tests/test_plugin_sdk.py`
