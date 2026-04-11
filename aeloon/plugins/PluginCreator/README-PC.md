<p align="right">
<a href="./README.md">English</a> | <b>中文</b>
</p>

# PluginCreator 插件开发指南

PluginCreator 插件的全面指南 —— 涵盖架构、运行时流程、数据模型、操作和扩展模式。

## 目录

1. [概述](#概述)
2. [架构](#架构)
3. [运行时流程](#运行时流程)
4. [数据模型](#数据模型)
5. [操作](#操作)
6. [扩展指南](#扩展指南)
7. [API 参考](#api-参考)

---

## 概述

### 什么是 PluginCreator 插件

`PluginCreator` 插件是 Aeloon 上的** AI 辅助插件开发工作流**，将自然语言插件需求转换为结构化的开发计划。它基于现有的 Aeloon Agent 运行时运行，实现以下功能：

- 需求解读
- 插件架构规划
- 阶段分解
- 产物规范
- 计划验证
- 恢复/延迟支持

它不是一个独立的应用程序，而是建立在 Aeloon 现有 Agent 基础设施之上的**增量能力**。

### 设计目标

**目标 1：在不干扰正常助手模式的情况下简化插件开发**

PluginCreator 插件通过懒加载集成 —— 只有在触发 `/pc` 命令或 `aeloon pc` CLI 时才会加载，避免影响常规对话流程。

**目标 2：将"插件开发"建模为结构化对象**

与普通聊天不同，插件开发任务通常包括：

- 明确的需求
- 可分解的阶段
- 依赖关系
- 产物规范
- 验证关卡
- 恢复/延迟能力

因此，插件将开发建模为结构化对象：`PlanPackage`、`PhaseContract`、`PlanItem`、`ArtifactSpec`、`ResumeBlock`。

**目标 3：复用 Aeloon 现有基础设施**

该插件复用：

- `AgentLoop`
- `Dispatcher`
- `MessageBus`
- `process_direct()` 调用路径
- 工具注册表 / 工具调用链
- 配置系统

这使得插件创建者能够直接受益于 Aeloon 现有的通道、模型、工具、会话、日志记录和安全能力。

### 当前版本能力 (v0.1.0)

当前版本专注于"计划生成"垂直切片。

**已实现：**

- 从需求生成骨架计划
- 包含阶段和项的 PlanPackage 结构
- JSONL 持久化
- 状态/历史跟踪
- 恢复/延迟存根支持
- 验证框架

**尚未实现（仅存根）：**

- LLM 驱动的智能规划
- 自动代码生成
- 多轮澄清对话
- 基于模板的脚手架
- 插件 SDK 集成

### 典型使用模式

**模式 1：频道中的斜杠命令**

```text
/pc 创建一个从 OpenWeatherMap 获取数据的天气插件
/pc status
/pc history
/pc help
```

**模式 2：CLI 调用**

```bash
aeloon pc -m "创建一个用于 GitHub 仓库管理的插件"
```

**模式 3：内部 Python 调用**

```python
from aeloon.plugins.PluginCreator.pipeline import PluginCreatorPipeline

pipeline = PluginCreatorPipeline(runtime=runtime, storage_dir="/path/to/storage")
output, pkg = await pipeline.plan("创建一个待办事项列表插件")
```

### 适合的问题类型

当前实现更适合：

- 插件需求澄清
- 架构规划
- 阶段分解
- 产物规范
- 开发路线图生成

示例：

- "创建一个与 Slack webhook 集成的插件"
- "构建一个带缓存的天气数据插件"
- "设计一个用于自动化代码审查的插件"

### 默认工作流

默认规划工作流大致如下：

1. `background_snapshot` —— 捕获需求上下文
2. `design_review` —— 范围界定和关键决策
3. `phase_contracts` —— 分解为可执行阶段
4. `plan_items` —— 定义每个阶段的具体任务
5. `artifact_specs` —— 指定可交付成果

---

## 架构

### 架构概述

PluginCreator 插件的架构原则：

- **薄入口点**：Dispatcher 和 CLI 只处理访问和转发
- **集中核心**：`PluginCreatorPipeline` 控制主流程
- **分层规划**：PlanningKernel 生成 PlanPackages，Views 渲染输出
- **可持久化状态**：PlanPackage 存储在 JSONL 中
- **恢复支持**：长时间规划的延迟/恢复能力

### 模块层次

```text
┌──────────────────────────────────────────────┐
│ 集成层                                       │
│ - Dispatcher (/pc)                           │
│ - CLI (aeloon pc -m "...")                   │
│ - Config (PluginCreatorConfig)               │
└──────────────────────────────────────────────┘
                    │
                    ▼
┌──────────────────────────────────────────────┐
│ Pipeline 层                                  │
│ - PluginCreatorPipeline                      │
│   负责 plan / status / history               │
└──────────────────────────────────────────────┘
                    │
        ┌───────────┴───────────┐
        ▼                       ▼
┌──────────────┐       ┌──────────────┐
│  Planning    │       │   Storage    │
│   Kernel     │       │    JSONL     │
└──────────────┘       └──────────────┘
        │
        ▼
┌──────────────────────────────────────────────┐
│ PlanPackage (领域模型)                       │
│ - BackgroundSnapshot                         │
│ - ProgrammeStructure                         │
│ - DesignReview                               │
│ - PhaseContract[]                            │
│ - PlanItem[]                                 │
│ - ArtifactSpec[]                             │
└──────────────────────────────────────────────┘
```

### 目录结构

```text
aeloon/plugins/PluginCreator/
├── __init__.py
├── aeloon.plugin.json          # 插件清单 (id, entry, provides, requires)
├── plugin.py                   # 插件 SDK 入口 (PluginCreatorPlugin)
├── pipeline.py                 # PluginCreatorPipeline 主控制器
├── config.py                   # PluginCreatorConfig 配置模型
├── models/                     # 领域模型
│   ├── __init__.py
│   ├── plan_package.py         # PlanPackage 聚合根
│   ├── phases.py               # PhaseContract, PlanItem
│   ├── artifacts.py            # ArtifactSpec
│   ├── governance.py           # PlanningStatus, RiskItem, etc.
│   └── resume.py               # ResumeBlock
├── planner/                    # 规划层
│   ├── __init__.py
│   ├── kernel.py               # PlanningKernel
│   └── views.py                # 渲染函数 (full, compact)
├── validator/                  # 验证层
│   └── plan_package.py         # PlanPackage 验证
├── storage/                    # 持久化层
│   └── jsonl.py                # PlanStore JSONL 存储
└── compat/                     # 兼容层
    └── envelope.py             # 兼容模式信封
```

其中：

- `models/`：领域模型定义
- `planner/kernel.py`：核心规划逻辑
- `planner/views.py`：输出渲染
- `pipeline.py`：主控制入口
- `validator/`：计划验证
- `storage/jsonl.py`：持久化

### 与 Aeloon 核心系统集成

#### 插件注册表集成

PluginCreator 插件通过 `aeloon.plugin.json` 注册：

```json
{
  "id": "aeloon.plugincreator",
  "name": "PluginCreator",
  "version": "0.1.0",
  "entry": "aeloon.plugins.PluginCreator.plugin:PluginCreatorPlugin",
  "provides": {
    "commands": ["pc"],
    "config_schema": "PluginCreatorConfig"
  }
}
```

`/pc` 命令通过插件注册表动态路由：

```text
插件 SDK 命令分派
  → registry.commands["pc"]
    → CommandContext → PluginCreatorPlugin._handle_command()
      → "help"    → get_help_text()
      → "status"  → pipeline.get_status()
      → "history" → pipeline.get_history()
      → "plan"    → pipeline.plan(requirement)
      → default   → pipeline.plan(args)
```

#### CLI 集成

插件通过 `PluginCreatorPlugin._build_cli()` 注册 CLI：

1. 接收 `--message/-m` 参数
2. 转发到插件运行时执行路径

#### 配置集成

插件通过 `api.register_config_schema(PluginCreatorConfig)` 注册配置模式：

- `PluginCreatorConfig`：enabled, workspace_dir, default_maturity, plan_first

### 关键类职责

#### `PluginCreatorPipeline`

职责：

- 接受规划需求
- 调用 PlanningKernel 生成 PlanPackage
- 返回渲染视图
- 将 PlanPackage 持久化到存储
- 提供状态/历史查询

#### `PlanningKernel`

职责：

- 将原始需求转换为结构化的 PlanPackage
- 范围界定
- 设计评审综合
- 阶段分解
- 计划项构建
- 验证

当前实现：

- Sprint 1 存根：构建骨架 PlanPackage
- 未来：LLM 驱动的智能规划

#### `PlanPackage`

职责：

- 插件规划的聚合根
- 包含所有规划状态
- 可序列化为 JSON

组件：

- `BackgroundSnapshot`：上下文捕获
- `ProgrammeStructure`：阶段排序
- `DesignReview`：范围和决策
- `PhaseContract[]`：阶段定义
- `PlanItem[]`：可执行任务
- `ArtifactSpec[]`：可交付成果

#### `PlanStore`

职责：

- 将 PlanPackage 保存到 JSONL
- 列出存储的计划
- 通过 project_id 检索计划

---

## 运行时流程

### 整体调用链

无论是从频道 `/pc` 还是 CLI `aeloon pc -m "..."`，所有路径都汇聚到 `PluginCreatorPipeline.plan()`。

整体流程：

```text
用户输入
  │
  ├─ 频道入口：插件 SDK 分派 → PluginCreatorPlugin._handle_command()
  └─ CLI 入口：api.register_cli("pc") → Typer 子命令
            │
            ▼
      PluginCreatorPipeline.plan()
            │
            ├─ PlanningKernel.plan()
            │     ├─ _build_skeleton()
            │     ├─ validate_plan_package()
            │     ├─ render_full_plan()
            │     └─ render_compact_plan()
            │
            ├─ PlanStore.save()
            └─ return (full_view, plan_package)
```

### `/pc` 频道入口流程

`/pc` 命令路由到 `PluginCreatorPlugin._handle_command()`：

**help**

```text
/pc help
```

从 `get_help_text()` 返回帮助文本。

**status**

```text
/pc status
```

调用 `pipeline.get_status()` 查看存储的计划状态。

**history**

```text
/pc history
```

调用 `pipeline.get_history()` 从 JSONL 读取归档计划。

**plan 执行**

```text
/pc <requirement>
```

构建需求并调用：

```python
output, pkg = await pipeline.plan(
    requirement=args,
    project_id=ctx.session_key,
)
```

### CLI 入口流程

CLI `pc` 子命令注册为 Typer 子应用：

1. 验证 `--message/-m` 参数
2. 输出任务描述

### `PlanningKernel.plan()` 阶段分解

`plan()` 是规划子系统的主控制器。

**阶段 1：构建骨架**

调用 `_build_skeleton(inp)`：

- 创建最小有效的 PlanPackage
- 从 PlanningKernelInput 填充
- 返回 PlanPackage

**阶段 2：验证**

调用 `validate_plan_package(pkg)`：

- 结构验证
- 必填字段检查
- 交叉引用验证
- 返回验证错误

**阶段 3：渲染视图**

调用渲染函数：

- `render_full_plan(pkg)` —— 详细的 Markdown 输出
- `render_compact_plan(pkg)` —— 摘要视图

**阶段 4：持久化**

Pipeline 保存 PlanPackage：

```python
self._store.save(output.plan_package)
```

### PlanningKernel 行为

**Sprint 1 存根**

当前实现构建最小骨架：

- 单阶段："Analysis"
- 单项："scope"
- 仅基本结构

**未来实现**

完整的 LLM 驱动规划将包括：

- 智能范围界定
- 多阶段分解
- 依赖分析
- 产物规范
- 风险评估

---

## 数据模型

### 核心模型

#### `PlanPackage`

聚合根包含：

| 字段 | 类型 | 描述 |
|------|------|------|
| `project_id` | str | 唯一项目标识符 |
| `planning_status` | PlanningStatus | 当前状态 |
| `background_snapshot` | BackgroundSnapshot | 需求上下文 |
| `programme_structure` | ProgrammeStructure | 阶段排序 |
| `design_review` | DesignReview | 范围和决策 |
| `phase_contracts` | list[PhaseContract] | 阶段定义 |
| `plan_items` | list[PlanItem] | 可执行任务 |
| `artifact_specs` | list[ArtifactSpec] | 可交付成果 |
| `resume_block` | ResumeBlock | 恢复/延迟信息 |

#### `BackgroundSnapshot`

| 字段 | 类型 | 描述 |
|------|------|------|
| `summary` | str | 原始需求摘要 |
| `sdk_constraints` | list[str] | SDK 版本约束 |
| `baseline_capabilities` | list[str] | 所需能力 |
| `input_sources` | list[str] | 输入源 |
| `output_constraints` | list[str] | 输出约束 |
| `assumptions` | list[str] | 规划假设 |
| `non_goals` | list[str] | 明确超出范围的内容 |

#### `PhaseContract`

| 字段 | 类型 | 描述 |
|------|------|------|
| `phase_id` | str | 唯一阶段标识符 |
| `phase_name` | str | 人类可读的名称 |
| `goal` | str | 阶段目标 |
| `task_ids` | list[str] | 关联的计划项 |

#### `PlanItem`

| 字段 | 类型 | 描述 |
|------|------|------|
| `item_id` | str | 唯一项标识符 |
| `kind` | PlanItemKind | 项类型 |
| `title` | str | 简短标题 |
| `description` | str | 详细描述 |
| `acceptance_criteria` | list[str] | 完成标准 |

#### `ArtifactSpec`

| 字段 | 类型 | 描述 |
|------|------|------|
| `artifact_id` | str | 唯一产物标识符 |
| `name` | str | 产物名称 |
| `description` | str | 描述 |
| `artifact_kind` | str | 类型 (code, doc, config) |

### 存储格式

**JSONL 存储**

每行是一个 JSON 对象：

```json
{
  "project_id": "uuid-or-session-key",
  "saved_at": "2025-01-01T00:00:00Z",
  "plan_package": { ... }
}
```

**存储位置**

- 默认：`~/.aeloon/plugin_storage/aeloon.plugincreator/`
- 可通过 `PluginCreatorConfig.workspace_dir` 配置

---

## 操作

### 配置

**启用插件**

在 `~/.aeloon/config.json` 中：

```json
{
  "plugins": {
    "aeloon_plugincreator": {
      "enabled": true,
      "defaultMaturity": "mvp",
      "planFirst": true
    }
  }
}
```

**成熟度级别**

- `prototype`：快速概念验证
- `mvp`：最小可行插件
- `production_ready`：功能完整、经过测试的插件

### 命令

**创建计划**

```
/pc 创建一个 Slack webhook 集成插件
```

**检查状态**

```
/pc status
```

输出：
```
Stored plans: 3 projects (proj_1, proj_2, proj_3)
```

**查看历史**

```
/pc history
```

输出：
```
PluginCreator history:
  proj_1
  proj_2
  proj_3
```

**获取帮助**

```
/pc help
```

### 存储管理

**定位存储**

```bash
ls ~/.aeloon/plugin_storage/aeloon.plugincreator/
```

**备份计划**

```bash
cp ~/.aeloon/plugin_storage/aeloon.plugincreator/plans.jsonl \
   ~/.aeloon/plugin_storage/aeloon.plugincreator/plans.backup.jsonl
```

**清除历史**

```bash
rm ~/.aeloon/plugin_storage/aeloon.plugincreator/plans.jsonl
```

---

## 扩展指南

### 添加新的规划策略

**步骤 1：实现 PlanningStrategy 协议**

```python
from aeloon.plugins.PluginCreator.planner.kernel import PlanningKernel

class MyCustomPlanner:
    async def plan(self, inp: PlanningKernelInput) -> PlanPackage:
        # 自定义规划逻辑
        pass
```

**步骤 2：在 Kernel 中注册**

修改 `PlanningKernel._build_skeleton()` 或添加策略选择器。

### 添加新的产物类型

**步骤 1：扩展 ArtifactSpec**

```python
from aeloon.plugins.PluginCreator.models import ArtifactSpec

class CustomArtifactSpec(ArtifactSpec):
    custom_field: str
```

**步骤 2：更新验证**

修改 `validator/plan_package.py` 以验证新字段。

### 自定义存储后端

**步骤 1：实现存储协议**

```python
class MyCustomStore:
    def save(self, pkg: PlanPackage) -> None:
        pass
    
    def load(self, project_id: str) -> PlanPackage | None:
        pass
    
    def list_project_ids(self) -> list[str]:
        pass
```

**步骤 2：在 Pipeline 中替换**

修改 `PluginCreatorPipeline.__init__()` 以使用自定义存储。

---

## API 参考

### PluginCreatorPipeline

```python
class PluginCreatorPipeline:
    def __init__(self, runtime: PluginRuntime, storage_dir: str) -> None
    async def plan(self, requirement: str, **kwargs) -> tuple[str, PlanPackage | None]
    def get_status(self) -> str
    def get_history(self) -> str
```

### PlanningKernel

```python
class PlanningKernel:
    def __init__(self, runtime: PluginRuntime) -> None
    async def plan(self, inp: PlanningKernelInput) -> PlanningKernelOutput
```

### PlanningKernelInput

```python
@dataclass
class PlanningKernelInput:
    project_id: str
    raw_requirement: str
    diagram_inputs: list[str] = field(default_factory=list)
    user_constraints: dict[str, Any] = field(default_factory=dict)
    maturity: Literal["prototype", "mvp", "production_ready"] = "mvp"
```

### PlanStore

```python
class PlanStore:
    def __init__(self, storage_dir: str) -> None
    def save(self, pkg: PlanPackage) -> None
    def load(self, project_id: str) -> PlanPackage | None
    def list_project_ids(self) -> list[str]
```

### 配置

```python
class PluginCreatorConfig(BaseModel):
    enabled: bool = False
    workspace_dir: str = "~/.aeloon/plugincreator/workspaces"
    default_maturity: Literal["prototype", "mvp", "production_ready"] = "mvp"
    plan_first: bool = True
```

---

## 资源

- **插件 SDK 文档**：`aeloon/plugins/_sdk/`
- **示例插件**：`ScienceResearch/`、`SkillGraph/`、`Wiki/`
- **ACP Bridge**：用于连接外部代理
- **测试**：`tests/test_plugin_sdk.py`
