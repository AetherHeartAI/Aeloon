<p align="right">
<a href="./README.md">English</a> | <b>中文</b>
</p>

# ScienceResearch 插件开发指南

ScienceResearch (AI4S) 插件综合指南 —— 涵盖架构、运行时流程、数据模型、运维和扩展模式。

## 目录

1. [概述](#概述)
2. [架构](#架构)
3. [运行时流程](#运行时流程)
4. [数据模型](#数据模型)
5. [运维](#运维)
6. [扩展指南](#扩展指南)
7. [API 参考](#api-参考)

---

## 概述

### 什么是 ScienceResearch 插件

`ScienceResearch` 插件是 Aeloon 上的 **AI4S (AI for Science)** 模式，将自然语言的科学研究查询转换为可执行的多步骤任务图。它在现有的 Aeloon Agent Runtime 上运行，完成以下功能：

- 任务解释
- 计划生成
- 节点执行
- 输出验证
- 结果交付
- 过程存档

它不是一个独立的应用程序，而是建立在 Aeloon 现有 Agent 能力之上的**增量模式**。

### 设计目标

**目标一：在不干扰正常助手模式的情况下添加研究任务能力**

ScienceResearch 插件通过延迟加载集成到 `Dispatcher` 中 —— 仅在触发 `/sr` 命令或 `aeloon sr` CLI 时加载，避免影响常规对话流程。

**目标二：将"科学任务"建模为结构化对象**

与普通聊天不同，科学任务通常包括：

- 明确的目标
- 可分解的子步骤
- 依赖关系
- 资源约束
- 输出规范
- 可验证的标准

因此，插件将任务建模为结构化对象：`Task`、`ScienceTaskNode`、`ScienceTaskGraph`、`Execution`、`Validation`。

**目标三：复用 Aeloon 现有基础设施**

插件复用了：

- `AgentLoop`
- `Dispatcher`
- `MessageBus`
- `process_direct()` 调用路径
- 工具注册表 / 工具调用链
- 中间件扩展点
- 配置系统

这使得科学智能体能够直接受益于 Aeloon 现有的频道、模型、工具、会话、日志记录和安全能力。

### 当前版本能力 (v0.1.0)

当前版本专注于"文献分析"垂直领域。

**已实现：**

- 基于规则的任务解释
- 文献检索/获取/综合计划模板
- 基于依赖的 DAG 编排
- 节点级重试
- 时间/令牌/工具调用预算约束
- 结构和语义验证
- JSONL 持久化
- 资产模板和失败模式记录
- 审计日志
- 风险门控存根

**尚未实现（仅存根）：**

- 基于 LLM 的意图结构化提取
- 多轮澄清对话
- 红色风险级别的人工审批流程
- SQLite 存储后端
- 第二种科学场景（例如数值计算、材料模拟）

### 典型使用模式

**模式一：频道中的斜杠命令**

```text
/sr search for recent papers on perovskite solar cell efficiency
/sr status
/sr history
/sr help
```

**模式二：CLI 调用**

```bash
aeloon sr -m "summarize the state of high-entropy alloy research in catalysis"
```

**模式三：内部 Python 调用**

```python
from aeloon.plugins.ScienceResearch.pipeline import SciencePipeline

pipeline = SciencePipeline(runtime=runtime)
output, task = await pipeline.run("find recent papers on protein structure prediction")
```

### 适用的问题类型

当前实现更适合：

- 文献调研
- 综述总结
- 主题分支并行检索
- 多源聚合
- 带引用的 Markdown 报告生成

示例：

- "检索过去三年钙钛矿太阳能电池效率提升的论文并总结趋势"
- "比较高熵合金催化中的主要研究路线"
- "总结蛋白质结构预测的最新进展及代表性工作"

### 默认工作流

在单范围任务中，默认计划大致为：

1. `search`
2. `fetch`
3. `synthesize`

在多范围任务中，形成多个 `search_i -> fetch_i` 分支，最终汇聚到单个 `synthesize` 节点。

---

## 架构

### 架构概述

ScienceResearch 插件的架构原则：

- **薄入口点**：Dispatcher 和 CLI 仅处理访问和转发
- **集中核心**：`SciencePipeline` 控制主流程
- **分层执行**：Planner 生成图，Orchestrator 执行图，Validator 检查结果
- **可持久化状态**：Task / Execution 存储在 JSONL 中
- **可插拔治理**：通过中间件扩展实现预算、审计、风险门控

### 模块层次

```text
┌──────────────────────────────────────────────┐
│ 集成层                                        │
│ - Dispatcher (/sr)                           │
│ - CLI (aeloon sr -m "...")                   │
│ - Config (ScienceConfig / GovernanceConfig)  │
└──────────────────────────────────────────────┘
                    │
                    ▼
┌──────────────────────────────────────────────┐
│ 流水线层                                     │
│ - SciencePipeline                            │
│   负责解释 / 计划 / 编排 / 验证 / 交付       │
└──────────────────────────────────────────────┘
                    │
        ┌───────────┼───────────┐
        ▼           ▼           ▼
┌────────────┐ ┌────────────┐ ┌────────────┐
│ Planner    │ │Orchestrator│ │ Validator  │
│ Linear/DAG │ │Seq / DAG   │ │Struct/Sem  │
└────────────┘ └────────────┘ └────────────┘
        │           │           │
        └──────┬────┴──────┬────┘
               ▼           ▼
        ┌────────────┐ ┌────────────┐
        │ Persistence│ │ Governance │
        │ JSONL      │ │ Budget/Audit/Risk │
        └────────────┘ └────────────┘
```

### 目录结构

```text
aeloon/plugins/ScienceResearch/
├── __init__.py
├── aeloon.plugin.json          # 插件清单 (id, entry, provides, requires)
├── plugin.py                   # Plugin SDK 入口 (SciencePlugin)
├── pipeline.py                 # SciencePipeline 主控制器
├── config.py                   # ScienceConfig 配置模型
├── task.py                     # 领域模型: Task, ScienceTaskNode, ...
├── planner.py                  # 任务转图: LinearPlanner, DAGPlanner
├── orchestrator.py             # 图执行: SequentialOrchestrator, DAGOrchestrator
├── validator.py                # 输出验证: StructuralValidator, SemanticValidator
├── capability.py               # 能力目录
├── assets.py                   # 模板和失败经验资产
├── storage/
│   ├── __init__.py
│   └── jsonl.py                # JSONL 持久化
└── middleware/
    ├── __init__.py
    ├── budget.py               # 预算中间件
    ├── audit.py                # 审计中间件
    └── risk_gate.py            # 风险门控中间件
```

其中：

- `task.py`：领域模型定义
- `planner.py`：任务转图
- `orchestrator.py`：图执行
- `pipeline.py`：主控制入口
- `validator.py`：输出验证
- `capability.py`：能力目录
- `assets.py`：模板和失败经验资产
- `storage/jsonl.py`：持久化
- `middleware/*`：治理链

### 与 Aeloon 核心系统集成

#### 插件注册表集成

ScienceResearch 插件通过 `aeloon.plugin.json` 注册：

```json
{
  "id": "aeloon.science",
  "name": "AI4S Science Agent",
  "version": "0.1.0",
  "entry": "aeloon.plugins.ScienceResearch.plugin:SciencePlugin",
  "provides": {
    "commands": ["sr"],
    "middlewares": ["science_audit", "science_budget", "science_risk_gate"]
  }
}
```

`/sr` 命令通过插件注册表动态路由：

```text
Plugin SDK 命令分发
  → registry.commands["sr"]
  → CommandContext → SciencePlugin._handle_command()
    → "help"    → get_help_text()
    → "status"  → pipeline.get_status()
    → "history" → pipeline.get_history()
    → default   → pipeline.run(query)
```

#### CLI 集成

插件通过 `SEPlugin._build_cli()` 注册 CLI：

1. 接收 `--message/-m` 参数
2. 转发到插件运行时执行路径

#### 配置集成

插件通过 `api.register_config_schema(ScienceConfig)` 注册配置模式：

- `ScienceConfig`：启用状态、预算默认值、工作区路径、治理配置

### 核心类职责

#### `SciencePipeline`

职责：

- 输入查询
- 生成 `Task`
- 调用 Planner 生成 `ScienceTaskGraph`
- 调用 Orchestrator 执行图
- 调用 Validator 验证最终输出
- 格式化最终交付文本
- 将状态写入存储

#### `Planner`

职责：

- 将"科学任务"转换为"可执行图"
- 当前实现：
  - `LinearPlanner`
  - `DAGPlanner`

#### `Orchestrator`

职责：

- 按图执行节点
- 管理依赖
- 传递上下文
- 处理失败和重试
- 聚合 `Execution`

当前实现：

- `SequentialOrchestrator`
- `DAGOrchestrator`

#### `Validator`

职责：

- 确定节点/最终输出是否达到交付标准

当前实现：

- `StructuralValidator`
- `SemanticValidator`
- `CompositeValidator`

#### `JsonlStorage`

职责：

- 保存 `Task`
- 保存 `Execution`
- 列出历史任务
- 提供每任务产物目录

#### `AssetManager`

职责：

- 提取成功任务模板
- 记录失败模式
- 提供相似任务检索

### 宏观 DAG vs 微观 DAG

这是整个实现中最重要的设计之一。

**微观 DAG：Aeloon 原生能力**

Aeloon 的内核已经支持在单个 LLM 回合并发调度多个工具调用。这可以理解为**微观 DAG**。

**宏观 DAG：ScienceResearch 插件的新能力**

该插件增加了**跨步骤/跨回合的任务图调度**，即：

- 哪些研究步骤先执行
- 哪些步骤可以并行运行
- 哪些步骤依赖上游结果
- 哪些步骤应在失败时重试或终止

可以理解为：

```text
科学任务 DAG
  ├─ 节点 A: search
  │    └─ 内部可能触发多个工具调用 (微观 DAG)
  ├─ 节点 B: fetch
  │    └─ 内部也可能有微观 DAG
  └─ 节点 C: synthesize
```

因此，ScienceResearch 插件不会替代 Aeloon 的内核，而是在其上添加了一个"科学任务编排层"。

---

## 运行时流程

### 总调用链

无论是从频道 `/sr` 还是 CLI `aeloon sr -m "..."`，所有路径都汇聚到 `SciencePipeline.run()`。

整体流程：

```text
用户输入
  │
  ├─ 频道入口: Plugin SDK 分发 → SciencePlugin._handle_command()
  └─ CLI 入口: api.register_cli("sr") → Typer 子命令
            │
            ▼
      SciencePipeline.run()
            │
            ├─ _check_clarification()
            ├─ _interpret()
            ├─ planner.plan()
            ├─ orchestrator.run()
            ├─ validator.validate()
            ├─ _format_output()
            └─ return (output, task)
```

### `/sr` 频道入口流程

`/sr` 命令路由到 `SciencePlugin._handle_command()`：

**help**

```text
/sr help
```

从 `get_help_text()` 返回帮助文本。

**status**

```text
/sr status
```

调用 `pipeline.get_status()` 查看当前会话中最近的科学任务状态。

**history**

```text
/sr history
```

调用 `pipeline.get_history()` 从 JSONL 读取已存档的任务摘要。

**查询执行**

```text
/sr <query>
```

构建查询并调用：

```python
output, _task = await pipeline.run(
    query=args,
    on_progress=ctx.send_progress,
    session_id=ctx.session_key,
)
```

### CLI 入口流程

CLI `sr` 子命令注册为 Typer 子应用：

1. 验证 `--message/-m` 参数
2. 输出任务描述

### `SciencePipeline.run()` 阶段分解

`run()` 是科学子系统的主控制器。

**阶段 0：澄清检查**

调用 `_check_clarification(query)`。

当前实现：

- 如果查询少于 4 个词，发出提醒
- **不阻断流程**
- 只是警告用户输入太短

**阶段 1：意图解释**

`_interpret()` 目前基于规则，直接将查询转换为 `Task`：

- `goal = query.strip()`
- `scope = []`
- `constraints = Constraints()`
- `deliverables.required_sections = ["Summary", "Key Findings", "Sources"]`
- `budget = Budget()`
- `context.session_id = session_id`

然后：

- 设置 `task.status = PLANNED`
- 使用 `save_task(task)` 保存一次

**阶段 2：生成执行图**

调用 `self._planner.plan(task)`。

默认使用 `DAGPlanner`：

- 如果范围有 1 个或更少项目，降级为线性计划
- 如果范围有多个项目，生成分支并行 DAG

**阶段 3：执行任务图**

执行前：

- `task.status = RUNNING`
- 更新 `updated_at`
- 再次使用 `save_task(task)` 写入

然后调用：

```python
executions = await self._orchestrator.run(task, graph, on_progress)
```

如果抛出：

- `BudgetExceededError`：任务失败，返回预算超出错误
- 其他异常：任务失败，返回通用错误消息

成功执行后：

- `self._current_executions = executions`
- 对每个执行调用 `save_execution(ex)`

**阶段 4：失败传播**

如果任何执行对象的 `state == FAILED`：

- 任务整体标记为 `FAILED`
- 聚合错误原因
- 返回 `"Error: Science task failed — ..."`

**阶段 5：最终输出验证**

如果节点没有失败：

- `task.status = VALIDATING`
- 查找最后一个有 `output` 的执行结果
- 调用默认验证器链

```python
validation = self._validator.validate(
    last_exec,
    task.deliverables,
    task_goal=task.goal,
)
```

**阶段 6：更新任务最终状态并交付**

基于验证结果：

- `DELIVER` 或非 `failed` -> `task.status = COMPLETED`
- 否则 -> `FAILED`

最后调用 `_format_output()` 输出 Markdown 文本。

### Planner 行为

**`LinearPlanner`**

线性计划模板：

1. `search`
2. `fetch`
3. `synthesize`

每个节点包含：

- `objective`
- `dependencies`
- `inputs`
- `expected_outputs`
- `assigned_role`
- `candidate_capabilities`
- `retry_policy`

**`DAGPlanner`**

当任务有多个范围时，生成：

```text
search_0 -> fetch_0 \
search_1 -> fetch_1  \
search_2 -> fetch_2   -> synthesize
```

特点：

- 最多 4 个并行分支
- 每个分支先搜索后获取
- 所有获取完成后才合成

### Orchestrator 行为

**`SequentialOrchestrator`**

用于骨架版本。

特点：

- 按拓扑顺序串行执行
- 上一步输出拼接到下一步 prompt
- 单节点失败停止后续执行

**`DAGOrchestrator`**

当前默认执行器。

核心行为：

- 维护 `pending_deps`
- 每轮找出依赖已满足的节点 `ready_ids`
- 以 wave 形式并发执行
- wave 内通过 `asyncio.gather()` 并发
- 轮次间检查预算

**节点执行**

节点最终通过 Aeloon 原生能力执行：

```python
output = await self._agent_loop.process_direct(
    content=prompt,
    session_key=session_key,
    channel="science",
    chat_id=task.task_id,
    on_progress=on_progress,
)
```

因此科学节点本质上是"驱动 Aeloon Agent 在上下文约束下完成任务"。

**重试逻辑**

`_execute_with_retry()`：

- 读取 `node.retry_policy`
- `max_attempts = 1 + max_retries`
- 从第 2 次尝试开始，按 `backoff_seconds * (attempt - 1)` 休眠
- 所有尝试失败后抛出最后一个异常

**失败处理**

如果 wave 中任何节点失败：

- 失败节点标记为 `FAILED`
- 其他未运行节点标记为 `CANCELLED`
- 整体图执行停止

**死锁保护**

如果 `pending_deps` 非空但没有 `ready_ids`，表示图中依赖异常，直接抛出 `RuntimeError("Deadlock ...")`。

### 验证流程

默认验证器：

```text
CompositeValidator(
  StructuralValidator(),
  SemanticValidator(),
)
```

**结构验证**

检查：

- 输出长度是否足够
- 必需章节是否存在
- 源 URL / DOI / arXiv 引用是否存在

**语义验证**

从 `task_goal` 提取关键词，计算输出覆盖率。

如果覆盖率低于阈值：

- 标记警告
- 状态通常为 `PARTIAL`

**复合验证**

合并规则：

- 状态最差胜出：`FAILED > PARTIAL > PASSED`
- `next_action` 最差胜出
- `confidence` 取最小值

---

## 数据模型

### 核心领域模型

**Task**

科学研究任务的主容器。

```python
class Task(BaseModel):
    task_id: str                    # UUID
    goal: str                       # 研究目标
    scope: list[str]                # 研究子范围
    constraints: Constraints        # 时间、预算、质量约束
    deliverables: Deliverables      # 预期输出格式
    budget: Budget                  # 资源预算
    context: TaskContext            # 会话和元数据
    status: TaskStatus              # CREATED -> PLANNED -> RUNNING -> ...
    created_at: datetime
    updated_at: datetime
```

**ScienceTaskNode**

任务图中的单个可执行单元。

```python
class ScienceTaskNode(BaseModel):
    node_id: str
    node_type: str                  # search, fetch, synthesize, ...
    objective: str                  # 节点的具体目标
    dependencies: list[str]         # 上游节点 ID
    inputs: dict                    # 输入参数
    expected_outputs: list[str]     # 预期输出描述
    assigned_role: str              # 执行此节点的角色
    candidate_capabilities: list[str]
    retry_policy: RetryPolicy
```

**ScienceTaskGraph**

作为 DAG 的完整执行计划。

```python
class ScienceTaskGraph(BaseModel):
    graph_id: str
    task_id: str
    nodes: list[ScienceTaskNode]
    edges: list[tuple[str, str]]    # (from_node, to_node)
    root_nodes: list[str]           # 入口点
    leaf_nodes: list[str]           # 终止节点
```

**Execution**

节点执行尝试的记录。

```python
class Execution(BaseModel):
    execution_id: str
    task_id: str
    node_id: str
    state: ExecutionState           # PENDING, RUNNING, SUCCESS, FAILED
    output: str | None              # 执行结果
    error: str | None               # 失败时的错误消息
    started_at: datetime
    completed_at: datetime | None
    attempts: int                   # 重试尝试次数
    metadata: dict                  # 执行元数据
```

**Validation**

执行输出的验证结果。

```python
class Validation(BaseModel):
    validation_id: str
    execution_id: str
    status: ValidationStatus        # PASSED, PARTIAL, FAILED
    checks: list[ValidationCheck]   # 单个检查结果
    confidence: float               # 0.0 - 1.0
    next_action: str                # DELIVER, REVISE, ABORT
    feedback: str | None            # 人类可读的反馈
```

### 支持模型

**Constraints**

```python
class Constraints(BaseModel):
    max_time_seconds: int | None
    max_tokens: int | None
    max_tool_calls: int | None
    quality_threshold: float        # 0.0 - 1.0
```

**Deliverables**

```python
class Deliverables(BaseModel):
    format: str                     # markdown, json, etc.
    required_sections: list[str]
    min_length: int | None
    max_length: int | None
    citation_required: bool
```

**Budget**

```python
class Budget(BaseModel):
    time_used_seconds: int = 0
    tokens_used: int = 0
    tool_calls_used: int = 0
    time_limit_seconds: int | None = None
    token_limit: int | None = None
    tool_calls_limit: int | None = None
```

**RetryPolicy**

```python
class RetryPolicy(BaseModel):
    max_retries: int = 2
    backoff_seconds: float = 1.0
    retry_on: list[str]             # 要重试的错误类型
```

---

## 运维

### 配置

用户配置 (`~/.aeloon/config.toml`)：

```toml
[plugins.aeloon_science]
enabled = true
storage_dir = "~/.aeloon/plugin_storage/aeloon.science"

[plugins.aeloon_science.governance]
max_budget_time_seconds = 300
max_budget_tokens = 10000
audit_enabled = true
```

### 存储位置

```
~/.aeloon/plugin_storage/aeloon.science/
├── tasks.jsonl         # 任务记录
├── executions.jsonl    # 执行记录
├── validations.jsonl   # 验证记录
└── artifacts/          # 每任务产物
    ├── {task_id}/
    │   ├── output.md
    │   └── intermediate/
    └── ...
```

### 常用操作

**检查插件状态：**

```bash
aeloon plugins list
```

**查看最近任务：**

```text
/sr history
```

**检查当前任务状态：**

```text
/sr status
```

**清理旧记录（手动）：**

```bash
# 删除 30 天前的任务
find ~/.aeloon/plugin_storage/aeloon.science/artifacts -type d -mtime +30 -exec rm -rf {} +
```

---

## 扩展指南

### 添加新的 Planner

1. 继承 `Planner` 基类
2. 实现 `plan(task: Task) -> ScienceTaskGraph`
3. 在 pipeline 中注册

```python
from aeloon.plugins.ScienceResearch.planner import Planner

class MyPlanner(Planner):
    def plan(self, task: Task) -> ScienceTaskGraph:
        # 你的计划逻辑
        return ScienceTaskGraph(...)
```

### 添加新的 Orchestrator

1. 继承 `Orchestrator` 基类
2. 实现 `run(task, graph, on_progress) -> list[Execution]`

```python
from aeloon.plugins.ScienceResearch.orchestrator import Orchestrator

class MyOrchestrator(Orchestrator):
    async def run(self, task, graph, on_progress=None):
        # 你的执行逻辑
        return executions
```

### 添加新的 Validator

1. 继承 `Validator` 基类
2. 实现 `validate(execution, deliverables, **kwargs) -> Validation`

```python
from aeloon.plugins.ScienceResearch.validator import Validator

class MyValidator(Validator):
    def validate(self, execution, deliverables, **kwargs):
        # 你的验证逻辑
        return Validation(...)
```

### 添加中间件

```python
from aeloon.agent.middleware import BaseAgentMiddleware

class MyMiddleware(BaseAgentMiddleware):
    async def __call__(self, context, next_fn):
        # 预处理
        result = await next_fn(context)
        # 后处理
        return result
```

在 `plugin.py` 中注册：

```python
def register(self, api: PluginAPI) -> None:
    api.register_middleware("my_middleware", MyMiddleware())
```

---

## API 参考

### SciencePlugin

| 方法 | 必需 | 说明 |
|------|------|------|
| `register(api)` | 是 | 同步。注册命令、CLI、配置模式 |
| `activate(api)` | 否 | 异步。初始化存储 |
| `deactivate()` | 否 | 异步。清理 |

### SciencePipeline

| 方法 | 返回 | 说明 |
|------|------|------|
| `run(query, on_progress, session_id)` | `(str, Task)` | 执行科学任务 |
| `get_status()` | `str` | 获取当前任务状态 |
| `get_history()` | `str` | 获取任务历史 |

### Planner

| 方法 | 返回 | 说明 |
|------|------|------|
| `plan(task)` | `ScienceTaskGraph` | 将任务转换为可执行图 |

### Orchestrator

| 方法 | 返回 | 说明 |
|------|------|------|
| `run(task, graph, on_progress)` | `list[Execution]` | 执行图 |

### Validator

| 方法 | 返回 | 说明 |
|------|------|------|
| `validate(execution, deliverables, **kwargs)` | `Validation` | 验证执行输出 |

### JsonlStorage

| 方法 | 返回 | 说明 |
|------|------|------|
| `save_task(task)` | `None` | 持久化任务 |
| `save_execution(execution)` | `None` | 持久化执行 |
| `list_tasks()` | `list[Task]` | 列出所有任务 |
| `get_task(task_id)` | `Task \| None` | 获取特定任务 |

---

## 资源

- 插件源码：`aeloon/plugins/ScienceResearch/`
- 测试：`tests/test_*.py`
- 通用插件 SDK 指南：`aeloon/plugins/README.md`
