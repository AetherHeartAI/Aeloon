<p align="right">
<b>English</b> | <a href="./README-SR.md">中文</a>
</p>

# ScienceResearch Plugin Development Guide

A comprehensive guide for the ScienceResearch (AI4S) plugin — covering architecture, runtime flow, data models, operations, and extension patterns.

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Runtime Flow](#runtime-flow)
4. [Data Models](#data-models)
5. [Operations](#operations)
6. [Extension Guide](#extension-guide)
7. [API Reference](#api-reference)

---

## Overview

### What is ScienceResearch Plugin

The `ScienceResearch` plugin is an **AI4S (AI for Science)** mode on Aeloon that transforms natural language scientific research queries into executable multi-step task graphs. It operates on the existing Aeloon Agent Runtime to accomplish:

- Task interpretation
- Plan generation
- Node execution
- Output validation
- Result delivery
- Process archiving

It is not a standalone application but an **incremental mode** built on top of Aeloon's existing Agent capabilities.

### Design Goals

**Goal 1: Add Research Task Capabilities Without Disrupting Normal Assistant Mode**

The ScienceResearch plugin integrates into the `Dispatcher` via lazy loading — it only loads when the `/sr` command or `aeloon sr` CLI is triggered, avoiding impact on regular conversation flows.

**Goal 2: Model "Scientific Tasks" as Structured Objects**

Unlike regular chat, scientific tasks typically include:

- Clear objectives
- Decomposable sub-steps
- Dependency relationships
- Resource constraints
- Output specifications
- Verifiable standards

Therefore, the plugin models tasks as structured objects: `Task`, `ScienceTaskNode`, `ScienceTaskGraph`, `Execution`, `Validation`.

**Goal 3: Reuse Aeloon Existing Infrastructure**

The plugin reuses:

- `AgentLoop`
- `Dispatcher`
- `MessageBus`
- `process_direct()` call path
- Tool Registry / Tool call chains
- Middleware extension points
- Configuration system

This allows the science agent to directly benefit from Aeloon's existing channels, models, tools, sessions, logging, and security capabilities.

### Current Version Capabilities (v0.1.0)

Current version focuses on the "literature analysis" vertical slice.

**Implemented:**

- Rule-based task interpretation
- Literature retrieval/fetching/synthesis plan templates
- Dependency-based DAG orchestration
- Node-level retry
- Time/Token/Tool call budget constraints
- Structural and semantic validation
- JSONL persistence
- Asset templates and failure mode recording
- Audit logging
- Risk gate stubs

**Not Yet Implemented (Stubs Only):**

- LLM-based intent structured extraction
- Multi-round clarification dialog
- Human approval flow for red-risk levels
- SQLite storage backend
- Second scientific scenario (e.g., numerical computation, materials simulation)

### Typical Usage Patterns

**Pattern 1: Slash command in channels**

```text
/sr search for recent papers on perovskite solar cell efficiency
/sr status
/sr history
/sr help
```

**Pattern 2: CLI invocation**

```bash
aeloon sr -m "summarize the state of high-entropy alloy research in catalysis"
```

**Pattern 3: Internal Python call**

```python
from aeloon.plugins.ScienceResearch.pipeline import SciencePipeline

pipeline = SciencePipeline(runtime=runtime)
output, task = await pipeline.run("find recent papers on protein structure prediction")
```

### Suitable Problem Types

The current implementation is better suited for:

- Literature surveys
- Review summaries
- Thematic branch parallel retrieval
- Multi-source aggregation
- Markdown report generation with citations

Examples:

- "Retrieve papers on perovskite solar cell efficiency improvements in the past three years and summarize trends"
- "Compare major research routes in high-entropy alloy catalysis"
- "Summarize latest progress in protein structure prediction with representative works"

### Default Workflow

In single-scope tasks, the default plan is roughly:

1. `search`
2. `fetch`
3. `synthesize`

In multi-scope tasks, multiple `search_i -> fetch_i` branches are formed, eventually converging into a single `synthesize` node.

---

## Architecture

### Architecture Overview

The ScienceResearch plugin's architectural principles:

- **Thin entry points**: Dispatcher and CLI only handle access and forwarding
- **Centralized core**: `SciencePipeline` controls the main flow
- **Layered execution**: Planner produces graphs, Orchestrator executes graphs, Validator checks results
- **Persistable state**: Task / Execution stored in JSONL
- **Pluggable governance**: Budget, audit, risk gates through Middleware extensions

### Module Layers

```text
┌──────────────────────────────────────────────┐
│ Integration Layer                            │
│ - Dispatcher (/sr)                           │
│ - CLI (aeloon sr -m "...")                   │
│ - Config (ScienceConfig / GovernanceConfig)  │
└──────────────────────────────────────────────┘
                    │
                    ▼
┌──────────────────────────────────────────────┐
│ Pipeline Layer                               │
│ - SciencePipeline                            │
│   Responsible for interpret / plan /         │
│   orchestrate / validate / deliver           │
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

### Directory Structure

```text
aeloon/plugins/ScienceResearch/
├── __init__.py
├── aeloon.plugin.json          # Plugin manifest (id, entry, provides, requires)
├── plugin.py                   # Plugin SDK entry (SciencePlugin)
├── pipeline.py                 # SciencePipeline main controller
├── config.py                   # ScienceConfig configuration model
├── task.py                     # Domain models: Task, ScienceTaskNode, ...
├── planner.py                  # Task to graph: LinearPlanner, DAGPlanner
├── orchestrator.py             # Graph execution: SequentialOrchestrator, DAGOrchestrator
├── validator.py                # Output validation: StructuralValidator, SemanticValidator
├── capability.py               # Capability catalog
├── assets.py                   # Templates and failure experience assets
├── storage/
│   ├── __init__.py
│   └── jsonl.py                # JSONL persistence
└── middleware/
    ├── __init__.py
    ├── budget.py               # Budget middleware
    ├── audit.py                # Audit middleware
    └── risk_gate.py            # Risk gate middleware
```

Where:

- `task.py`: Domain model definitions
- `planner.py`: Task to graph conversion
- `orchestrator.py`: Graph execution
- `pipeline.py`: Main control entry
- `validator.py`: Output validation
- `capability.py`: Capability catalog
- `assets.py`: Templates and failure experience assets
- `storage/jsonl.py`: Persistence
- `middleware/*`: Governance chain

### Integration with Aeloon Core System

#### Plugin Registry Integration

The ScienceResearch plugin registers via `aeloon.plugin.json`:

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

The `/sr` command is dynamically routed through Plugin Registry:

```text
Plugin SDK command dispatch
  → registry.commands["sr"]
  → CommandContext → SciencePlugin._handle_command()
    → "help"    → get_help_text()
    → "status"  → pipeline.get_status()
    → "history" → pipeline.get_history()
    → default   → pipeline.run(query)
```

#### CLI Integration

The plugin registers CLI via `SEPlugin._build_cli()`:

1. Receive `--message/-m` parameter
2. Forward to Plugin Runtime execution path

#### Configuration Integration

The plugin registers config schema via `api.register_config_schema(ScienceConfig)`:

- `ScienceConfig`: enabled, budget defaults, workspace path, governance config

### Key Class Responsibilities

#### `SciencePipeline`

Responsibilities:

- Input query
- Generate `Task`
- Call Planner to produce `ScienceTaskGraph`
- Call Orchestrator to execute graph
- Call Validator to validate final output
- Format final delivery text
- Write state to storage

#### `Planner`

Responsibilities:

- Convert "a scientific task" into "an executable graph"
- Current implementations:
  - `LinearPlanner`
  - `DAGPlanner`

#### `Orchestrator`

Responsibilities:

- Execute nodes according to graph
- Manage dependencies
- Pass context
- Handle failure and retry
- Aggregate `Execution`

Current implementations:

- `SequentialOrchestrator`
- `DAGOrchestrator`

#### `Validator`

Responsibilities:

- Determine if node / final output meets delivery standards

Current implementations:

- `StructuralValidator`
- `SemanticValidator`
- `CompositeValidator`

#### `JsonlStorage`

Responsibilities:

- Save `Task`
- Save `Execution`
- List historical tasks
- Provide per-task artifact directory

#### `AssetManager`

Responsibilities:

- Extract successful task templates
- Record failure patterns
- Provide similar task retrieval

### Macro DAG vs Micro DAG

This is one of the most important designs in the entire implementation.

**Micro DAG: Aeloon Native Capability**

Aeloon's kernel already supports concurrent scheduling of multiple tool calls within a single LLM turn. This can be understood as the **micro DAG**.

**Macro DAG: New Capability from ScienceResearch Plugin**

The plugin adds **cross-step / cross-turn task graph scheduling**, i.e.:

- Which research steps go first
- Which steps can run in parallel
- Which steps depend on upstream results
- Which steps should retry or terminate upon failure

Can be understood as:

```text
Science Task DAG
  ├─ Node A: search
  │    └─ May trigger multiple tool calls internally (micro DAG)
  ├─ Node B: fetch
  │    └─ May also have micro DAG internally
  └─ Node C: synthesize
```

Thus, the ScienceResearch plugin does not replace Aeloon's kernel but adds a "scientific task orchestration layer" on top.

---

## Runtime Flow

### Overall Call Chain

Whether from channel `/sr` or CLI `aeloon sr -m "..."`, all paths converge to `SciencePipeline.run()`.

The overall flow:

```text
User Input
  │
  ├─ Channel entry: Plugin SDK dispatch → SciencePlugin._handle_command()
  └─ CLI entry: api.register_cli("sr") → Typer sub-command
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

### `/sr` Channel Entry Flow

The `/sr` command routes to `SciencePlugin._handle_command()`:

**help**

```text
/sr help
```

Returns help text from `get_help_text()`.

**status**

```text
/sr status
```

Calls `pipeline.get_status()` to view the most recent science task status in the current session.

**history**

```text
/sr history
```

Calls `pipeline.get_history()` to read archived task summaries from JSONL.

**query execution**

```text
/sr <query>
```

Constructs the query and calls:

```python
output, _task = await pipeline.run(
    query=args,
    on_progress=ctx.send_progress,
    session_id=ctx.session_key,
)
```

### CLI Entry Flow

The CLI `sr` subcommand registers as a Typer sub-application:

1. Validate `--message/-m` parameter
2. Output task description

### `SciencePipeline.run()` Phase Breakdown

`run()` is the main controller of the science subsystem.

**Phase 0: Clarification Check**

Calls `_check_clarification(query)`.

Current implementation:

- If query has fewer than 4 words, issue a reminder
- **Does not block flow**
- Just warns user that input is too short

**Phase 1: Intent Interpretation**

`_interpret()` is currently rule-based, directly converting query to `Task`:

- `goal = query.strip()`
- `scope = []`
- `constraints = Constraints()`
- `deliverables.required_sections = ["Summary", "Key Findings", "Sources"]`
- `budget = Budget()`
- `context.session_id = session_id`

Then:

- Sets `task.status = PLANNED`
- Saves once with `save_task(task)`

**Phase 2: Generate Execution Graph**

Calls `self._planner.plan(task)`.

Default uses `DAGPlanner`:

- If scope has 1 or fewer items, degrades to linear plan
- If scope has multiple items, generates parallel branch DAG

**Phase 3: Execute Task Graph**

Before execution:

- `task.status = RUNNING`
- Updates `updated_at`
- Writes again with `save_task(task)`

Then calls:

```python
executions = await self._orchestrator.run(task, graph, on_progress)
```

If throws:

- `BudgetExceededError`: Task fails, returns budget exceeded error
- Other exceptions: Task fails, returns generic error message

After successful execution:

- `self._current_executions = executions`
- Calls `save_execution(ex)` for each execution

**Phase 4: Failure Propagation**

If any execution object has `state == FAILED`:

- Task overall marked as `FAILED`
- Aggregates error reasons
- Returns `"Error: Science task failed — ..."`

**Phase 5: Final Output Validation**

If nodes haven't failed:

- `task.status = VALIDATING`
- Finds last execution result with `output`
- Calls default validator chain

```python
validation = self._validator.validate(
    last_exec,
    task.deliverables,
    task_goal=task.goal,
)
```

**Phase 6: Update Task Final State and Deliver**

Based on validation result:

- `DELIVER` or non-`failed` -> `task.status = COMPLETED`
- Otherwise -> `FAILED`

Finally calls `_format_output()` to output Markdown text.

### Planner Behavior

**`LinearPlanner`**

Linear plan template:

1. `search`
2. `fetch`
3. `synthesize`

Each node contains:

- `objective`
- `dependencies`
- `inputs`
- `expected_outputs`
- `assigned_role`
- `candidate_capabilities`
- `retry_policy`

**`DAGPlanner`**

When task has multiple scopes, generates:

```text
search_0 -> fetch_0 \
search_1 -> fetch_1  \
search_2 -> fetch_2   -> synthesize
```

Characteristics:

- Max 4 parallel branches
- Each branch search then fetch
- All fetches complete before synthesize

### Orchestrator Behavior

**`SequentialOrchestrator`**

Used for walking skeleton version.

Characteristics:

- Execute serially in topological order
- Previous step output appended to next step prompt
- Stop subsequent execution on single node failure

**`DAGOrchestrator`**

Current default executor.

Core behavior:

- Maintains `pending_deps`
- Each round finds all nodes with satisfied dependencies `ready_ids`
- Execute concurrently in waves
- Concurrency within wave via `asyncio.gather()`
- Budget check between rounds

**Node Execution**

Nodes are ultimately executed through Aeloon native capabilities:

```python
output = await self._agent_loop.process_direct(
    content=prompt,
    session_key=session_key,
    channel="science",
    chat_id=task.task_id,
    on_progress=on_progress,
)
```

Thus science nodes are essentially "driving Aeloon Agent to complete a task with contextual constraints".

**Retry Logic**

`_execute_with_retry()`:

- Reads `node.retry_policy`
- `max_attempts = 1 + max_retries`
- From 2nd attempt, sleep according to `backoff_seconds * (attempt - 1)`
- Throws last exception after all attempts fail

**Failure Handling**

If any node fails in a wave:

- Failed node marked as `FAILED`
- Other unrun nodes marked as `CANCELLED`
- Overall graph execution stops

**Deadlock Protection**

If `pending_deps` is non-empty but no `ready_ids`, indicates abnormal dependencies in graph, directly throws `RuntimeError("Deadlock ...")`.

### Validation Flow

Default validator:

```text
CompositeValidator(
  StructuralValidator(),
  SemanticValidator(),
)
```

**Structural Validation**

Checks:

- Whether output length is sufficient
- Whether required sections exist
- Whether source URLs / DOI / arXiv citations are present

**Semantic Validation**

Extracts keywords from `task_goal`, calculates output coverage.

If coverage below threshold:

- Marks warning
- Status typically `PARTIAL`

**Composite Validation**

Merge rules:

- Status worst-wins: `FAILED > PARTIAL > PASSED`
- `next_action` worst-wins
- `confidence` takes minimum value

---

## Data Models

### Core Domain Models

**Task**

The primary container for a scientific research task.

```python
class Task(BaseModel):
    task_id: str                    # UUID
    goal: str                       # Research objective
    scope: list[str]                # Research sub-scopes
    constraints: Constraints        # Time, budget, quality constraints
    deliverables: Deliverables      # Expected output format
    budget: Budget                  # Resource budget
    context: TaskContext            # Session and metadata
    status: TaskStatus              # CREATED -> PLANNED -> RUNNING -> ...
    created_at: datetime
    updated_at: datetime
```

**ScienceTaskNode**

Individual executable unit within a task graph.

```python
class ScienceTaskNode(BaseModel):
    node_id: str
    node_type: str                  # search, fetch, synthesize, ...
    objective: str                  # Node's specific goal
    dependencies: list[str]         # Upstream node IDs
    inputs: dict                    # Input parameters
    expected_outputs: list[str]     # Expected output descriptions
    assigned_role: str              # Role to execute this node
    candidate_capabilities: list[str]
    retry_policy: RetryPolicy
```

**ScienceTaskGraph**

The complete execution plan as a DAG.

```python
class ScienceTaskGraph(BaseModel):
    graph_id: str
    task_id: str
    nodes: list[ScienceTaskNode]
    edges: list[tuple[str, str]]    # (from_node, to_node)
    root_nodes: list[str]           # Entry points
    leaf_nodes: list[str]           # Terminal nodes
```

**Execution**

Record of a node's execution attempt.

```python
class Execution(BaseModel):
    execution_id: str
    task_id: str
    node_id: str
    state: ExecutionState           # PENDING, RUNNING, SUCCESS, FAILED
    output: str | None              # Execution result
    error: str | None               # Error message if failed
    started_at: datetime
    completed_at: datetime | None
    attempts: int                   # Number of retry attempts
    metadata: dict                  # Execution metadata
```

**Validation**

Validation result for an execution output.

```python
class Validation(BaseModel):
    validation_id: str
    execution_id: str
    status: ValidationStatus        # PASSED, PARTIAL, FAILED
    checks: list[ValidationCheck]   # Individual check results
    confidence: float               # 0.0 - 1.0
    next_action: str                # DELIVER, REVISE, ABORT
    feedback: str | None            # Human-readable feedback
```

### Supporting Models

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
    retry_on: list[str]             # Error types to retry
```

---

## Operations

### Configuration

User config (`~/.aeloon/config.toml`):

```toml
[plugins.aeloon_science]
enabled = true
storage_dir = "~/.aeloon/plugin_storage/aeloon.science"

[plugins.aeloon_science.governance]
max_budget_time_seconds = 300
max_budget_tokens = 10000
audit_enabled = true
```

### Storage Location

```
~/.aeloon/plugin_storage/aeloon.science/
├── tasks.jsonl         # Task records
├── executions.jsonl    # Execution records
├── validations.jsonl   # Validation records
└── artifacts/          # Per-task artifacts
    ├── {task_id}/
    │   ├── output.md
    │   └── intermediate/
    └── ...
```

### Common Operations

**Check plugin status:**

```bash
aeloon plugins list
```

**View recent tasks:**

```text
/sr history
```

**Check current task status:**

```text
/sr status
```

**Clean old records (manual):**

```bash
# Remove tasks older than 30 days
find ~/.aeloon/plugin_storage/aeloon.science/artifacts -type d -mtime +30 -exec rm -rf {} +
```

---

## Extension Guide

### Adding a New Planner

1. Inherit from `Planner` base class
2. Implement `plan(task: Task) -> ScienceTaskGraph`
3. Register in pipeline

```python
from aeloon.plugins.ScienceResearch.planner import Planner

class MyPlanner(Planner):
    def plan(self, task: Task) -> ScienceTaskGraph:
        # Your planning logic
        return ScienceTaskGraph(...)
```

### Adding a New Orchestrator

1. Inherit from `Orchestrator` base class
2. Implement `run(task, graph, on_progress) -> list[Execution]`

```python
from aeloon.plugins.ScienceResearch.orchestrator import Orchestrator

class MyOrchestrator(Orchestrator):
    async def run(self, task, graph, on_progress=None):
        # Your execution logic
        return executions
```

### Adding a New Validator

1. Inherit from `Validator` base class
2. Implement `validate(execution, deliverables, **kwargs) -> Validation`

```python
from aeloon.plugins.ScienceResearch.validator import Validator

class MyValidator(Validator):
    def validate(self, execution, deliverables, **kwargs):
        # Your validation logic
        return Validation(...)
```

### Adding Middleware

```python
from aeloon.agent.middleware import BaseAgentMiddleware

class MyMiddleware(BaseAgentMiddleware):
    async def __call__(self, context, next_fn):
        # Pre-processing
        result = await next_fn(context)
        # Post-processing
        return result
```

Register in `plugin.py`:

```python
def register(self, api: PluginAPI) -> None:
    api.register_middleware("my_middleware", MyMiddleware())
```

---

## API Reference

### SciencePlugin

| Method | Required | Description |
|--------|----------|-------------|
| `register(api)` | Yes | Sync. Register commands, CLI, config schema |
| `activate(api)` | No | Async. Initialize storage |
| `deactivate()` | No | Async. Cleanup |

### SciencePipeline

| Method | Returns | Description |
|--------|---------|-------------|
| `run(query, on_progress, session_id)` | `(str, Task)` | Execute a science task |
| `get_status()` | `str` | Get current task status |
| `get_history()` | `str` | Get task history |

### Planner

| Method | Returns | Description |
|--------|---------|-------------|
| `plan(task)` | `ScienceTaskGraph` | Convert task to executable graph |

### Orchestrator

| Method | Returns | Description |
|--------|---------|-------------|
| `run(task, graph, on_progress)` | `list[Execution]` | Execute the graph |

### Validator

| Method | Returns | Description |
|--------|---------|-------------|
| `validate(execution, deliverables, **kwargs)` | `Validation` | Validate execution output |

### JsonlStorage

| Method | Returns | Description |
|--------|---------|-------------|
| `save_task(task)` | `None` | Persist task |
| `save_execution(execution)` | `None` | Persist execution |
| `list_tasks()` | `list[Task]` | List all tasks |
| `get_task(task_id)` | `Task \| None` | Get specific task |

---

## Resources

- Plugin source: `aeloon/plugins/ScienceResearch/`
- Tests: `tests/test_*.py`
- General Plugin SDK Guide: `aeloon/plugins/README.md`
