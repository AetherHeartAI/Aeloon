<p align="right">
<b>English</b> | <a href="./README-PC.md">中文</a>
</p>

# PluginCreator Plugin Development Guide

A comprehensive guide for the PluginCreator plugin — covering architecture, runtime flow, data models, operations, and extension patterns.

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

### What is PluginCreator Plugin

The `PluginCreator` plugin is an **AI-assisted plugin development workflow** on Aeloon that transforms natural language plugin requirements into structured development plans. It operates on the existing Aeloon Agent Runtime to accomplish:

- Requirement interpretation
- Plugin architecture planning
- Phase decomposition
- Artifact specification
- Plan validation
- Resume/defer support

It is not a standalone application but an **incremental capability** built on top of Aeloon's existing Agent infrastructure.

### Design Goals

**Goal 1: Streamline Plugin Development Without Disrupting Normal Assistant Mode**

The PluginCreator plugin integrates via lazy loading — it only loads when the `/pc` command or `aeloon pc` CLI is triggered, avoiding impact on regular conversation flows.

**Goal 2: Model "Plugin Development" as Structured Objects**

Unlike regular chat, plugin development tasks typically include:

- Clear requirements
- Decomposable phases
- Dependency relationships
- Artifact specifications
- Verification gates
- Resume/defer capabilities

Therefore, the plugin models development as structured objects: `PlanPackage`, `PhaseContract`, `PlanItem`, `ArtifactSpec`, `ResumeBlock`.

**Goal 3: Reuse Aeloon Existing Infrastructure**

The plugin reuses:

- `AgentLoop`
- `Dispatcher`
- `MessageBus`
- `process_direct()` call path
- Tool Registry / Tool call chains
- Configuration system

This allows the plugin creator to directly benefit from Aeloon's existing channels, models, tools, sessions, logging, and security capabilities.

### Current Version Capabilities (v0.1.0)

Current version focuses on the "plan generation" vertical slice.

**Implemented:**

- Skeleton plan generation from requirements
- PlanPackage structure with phases and items
- JSONL persistence
- Status/history tracking
- Resume/defer stub support
- Validation framework

**Not Yet Implemented (Stubs Only):**

- LLM-driven intelligent planning
- Automatic code generation
- Multi-round clarification dialog
- Template-based scaffolding
- Plugin SDK integration

### Typical Usage Patterns

**Pattern 1: Slash command in channels**

```text
/pc create a weather plugin that fetches data from OpenWeatherMap
/pc status
/pc history
/pc help
```

**Pattern 2: CLI invocation**

```bash
aeloon pc -m "create a plugin for GitHub repository management"
```

**Pattern 3: Internal Python call**

```python
from aeloon.plugins.PluginCreator.pipeline import PluginCreatorPipeline

pipeline = PluginCreatorPipeline(runtime=runtime, storage_dir="/path/to/storage")
output, pkg = await pipeline.plan("create a todo list plugin")
```

### Suitable Problem Types

The current implementation is better suited for:

- Plugin requirement clarification
- Architecture planning
- Phase decomposition
- Artifact specification
- Development roadmap generation

Examples:

- "Create a plugin that integrates with Slack webhooks"
- "Build a weather data plugin with caching"
- "Design a plugin for automated code review"

### Default Workflow

The default planning workflow is roughly:

1. `background_snapshot` — capture requirements context
2. `design_review` — scope framing and key decisions
3. `phase_contracts` — decompose into executable phases
4. `plan_items` — define specific tasks per phase
5. `artifact_specs` — specify deliverables

---

## Architecture

### Architecture Overview

The PluginCreator plugin's architectural principles:

- **Thin entry points**: Dispatcher and CLI only handle access and forwarding
- **Centralized core**: `PluginCreatorPipeline` controls the main flow
- **Layered planning**: PlanningKernel produces PlanPackages, Views render output
- **Persistable state**: PlanPackage stored in JSONL
- **Resume support**: Defer/resume capabilities for long-running planning

### Module Layers

```text
┌──────────────────────────────────────────────┐
│ Integration Layer                            │
│ - Dispatcher (/pc)                           │
│ - CLI (aeloon pc -m "...")                   │
│ - Config (PluginCreatorConfig)               │
└──────────────────────────────────────────────┘
                    │
                    ▼
┌──────────────────────────────────────────────┐
│ Pipeline Layer                               │
│ - PluginCreatorPipeline                      │
│   Responsible for plan / status / history    │
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
│ PlanPackage (Domain Model)                   │
│ - BackgroundSnapshot                         │
│ - ProgrammeStructure                         │
│ - DesignReview                               │
│ - PhaseContract[]                            │
│ - PlanItem[]                                 │
│ - ArtifactSpec[]                             │
└──────────────────────────────────────────────┘
```

### Directory Structure

```text
aeloon/plugins/PluginCreator/
├── __init__.py
├── aeloon.plugin.json          # Plugin manifest (id, entry, provides, requires)
├── plugin.py                   # Plugin SDK entry (PluginCreatorPlugin)
├── pipeline.py                 # PluginCreatorPipeline main controller
├── config.py                   # PluginCreatorConfig configuration model
├── models/                     # Domain models
│   ├── __init__.py
│   ├── plan_package.py         # PlanPackage aggregate root
│   ├── phases.py               # PhaseContract, PlanItem
│   ├── artifacts.py            # ArtifactSpec
│   ├── governance.py           # PlanningStatus, RiskItem, etc.
│   └── resume.py               # ResumeBlock
├── planner/                    # Planning layer
│   ├── __init__.py
│   ├── kernel.py               # PlanningKernel
│   └── views.py                # Render functions (full, compact)
├── validator/                  # Validation layer
│   └── plan_package.py         # PlanPackage validation
├── storage/                    # Persistence layer
│   └── jsonl.py                # PlanStore JSONL storage
└── compat/                     # Compatibility layer
    └── envelope.py             # Envelope for compatibility modes
```

Where:

- `models/`: Domain model definitions
- `planner/kernel.py`: Core planning logic
- `planner/views.py`: Output rendering
- `pipeline.py`: Main control entry
- `validator/`: Plan validation
- `storage/jsonl.py`: Persistence

### Integration with Aeloon Core System

#### Plugin Registry Integration

The PluginCreator plugin registers via `aeloon.plugin.json`:

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

The `/pc` command is dynamically routed through Plugin Registry:

```text
Plugin SDK command dispatch
  → registry.commands["pc"]
    → CommandContext → PluginCreatorPlugin._handle_command()
      → "help"    → get_help_text()
      → "status"  → pipeline.get_status()
      → "history" → pipeline.get_history()
      → "plan"    → pipeline.plan(requirement)
      → default   → pipeline.plan(args)
```

#### CLI Integration

The plugin registers CLI via `PluginCreatorPlugin._build_cli()`:

1. Receive `--message/-m` parameter
2. Forward to Plugin Runtime execution path

#### Configuration Integration

The plugin registers config schema via `api.register_config_schema(PluginCreatorConfig)`:

- `PluginCreatorConfig`: enabled, workspace_dir, default_maturity, plan_first

### Key Class Responsibilities

#### `PluginCreatorPipeline`

Responsibilities:

- Accept planning requirements
- Call PlanningKernel to produce PlanPackage
- Return rendered views
- Persist PlanPackage to storage
- Provide status/history queries

#### `PlanningKernel`

Responsibilities:

- Convert raw requirements into structured PlanPackage
- Scope framing
- Design review synthesis
- Phase decomposition
- Plan item construction
- Validation

Current implementation:

- Sprint 1 stub: builds skeleton PlanPackage
- Future: LLM-driven intelligent planning

#### `PlanPackage`

Responsibilities:

- Root aggregate for plugin planning
- Contains all planning state
- Serializable to/from JSON

Components:

- `BackgroundSnapshot`: Context capture
- `ProgrammeStructure`: Phase ordering
- `DesignReview`: Scope and decisions
- `PhaseContract[]`: Phase definitions
- `PlanItem[]`: Executable tasks
- `ArtifactSpec[]`: Deliverables

#### `PlanStore`

Responsibilities:

- Save PlanPackage to JSONL
- List stored plans
- Retrieve plan by project_id

---

## Runtime Flow

### Overall Call Chain

Whether from channel `/pc` or CLI `aeloon pc -m "..."`, all paths converge to `PluginCreatorPipeline.plan()`.

The overall flow:

```text
User Input
  │
  ├─ Channel entry: Plugin SDK dispatch → PluginCreatorPlugin._handle_command()
  └─ CLI entry: api.register_cli("pc") → Typer sub-command
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

### `/pc` Channel Entry Flow

The `/pc` command routes to `PluginCreatorPlugin._handle_command()`:

**help**

```text
/pc help
```

Returns help text from `get_help_text()`.

**status**

```text
/pc status
```

Calls `pipeline.get_status()` to view stored plan status.

**history**

```text
/pc history
```

Calls `pipeline.get_history()` to read archived plans from JSONL.

**plan execution**

```text
/pc <requirement>
```

Constructs the requirement and calls:

```python
output, pkg = await pipeline.plan(
    requirement=args,
    project_id=ctx.session_key,
)
```

### CLI Entry Flow

The CLI `pc` subcommand registers as a Typer sub-application:

1. Validate `--message/-m` parameter
2. Output task description

### `PlanningKernel.plan()` Phase Breakdown

`plan()` is the main controller of the planning subsystem.

**Phase 1: Build Skeleton**

Calls `_build_skeleton(inp)`:

- Creates minimal valid PlanPackage
- Populates from PlanningKernelInput
- Returns PlanPackage

**Phase 2: Validate**

Calls `validate_plan_package(pkg)`:

- Structural validation
- Required field checks
- Cross-reference validation
- Returns validation errors

**Phase 3: Render Views**

Calls render functions:

- `render_full_plan(pkg)` — detailed Markdown output
- `render_compact_plan(pkg)` — summary view

**Phase 4: Persist**

Pipeline saves PlanPackage:

```python
self._store.save(output.plan_package)
```

### PlanningKernel Behavior

**Sprint 1 Stub**

Current implementation builds minimal skeleton:

- Single phase: "Analysis"
- Single item: "scope"
- Basic structure only

**Future Implementation**

Full LLM-driven planning will include:

- Intelligent scope framing
- Multi-phase decomposition
- Dependency analysis
- Artifact specification
- Risk assessment

---

## Data Models

### Core Models

#### `PlanPackage`

Root aggregate containing:

| Field | Type | Description |
|-------|------|-------------|
| `project_id` | str | Unique project identifier |
| `planning_status` | PlanningStatus | Current status |
| `background_snapshot` | BackgroundSnapshot | Requirements context |
| `programme_structure` | ProgrammeStructure | Phase ordering |
| `design_review` | DesignReview | Scope and decisions |
| `phase_contracts` | list[PhaseContract] | Phase definitions |
| `plan_items` | list[PlanItem] | Executable tasks |
| `artifact_specs` | list[ArtifactSpec] | Deliverables |
| `resume_block` | ResumeBlock | Resume/defer info |

#### `BackgroundSnapshot`

| Field | Type | Description |
|-------|------|-------------|
| `summary` | str | Raw requirement summary |
| `sdk_constraints` | list[str] | SDK version constraints |
| `baseline_capabilities` | list[str] | Required capabilities |
| `input_sources` | list[str] | Input sources |
| `output_constraints` | list[str] | Output constraints |
| `assumptions` | list[str] | Planning assumptions |
| `non_goals` | list[str] | Explicitly out of scope |

#### `PhaseContract`

| Field | Type | Description |
|-------|------|-------------|
| `phase_id` | str | Unique phase identifier |
| `phase_name` | str | Human-readable name |
| `goal` | str | Phase objective |
| `task_ids` | list[str] | Associated plan items |

#### `PlanItem`

| Field | Type | Description |
|-------|------|-------------|
| `item_id` | str | Unique item identifier |
| `kind` | PlanItemKind | Item type |
| `title` | str | Short title |
| `description` | str | Detailed description |
| `acceptance_criteria` | list[str] | Completion criteria |

#### `ArtifactSpec`

| Field | Type | Description |
|-------|------|-------------|
| `artifact_id` | str | Unique artifact identifier |
| `name` | str | Artifact name |
| `description` | str | Description |
| `artifact_kind` | str | Type (code, doc, config) |

### Storage Format

**JSONL Storage**

Each line is a JSON object:

```json
{
  "project_id": "uuid-or-session-key",
  "saved_at": "2025-01-01T00:00:00Z",
  "plan_package": { ... }
}
```

**Storage Location**

- Default: `~/.aeloon/plugin_storage/aeloon.plugincreator/`
- Configurable via `PluginCreatorConfig.workspace_dir`

---

## Operations

### Configuration

**Enable Plugin**

In `~/.aeloon/config.json`:

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

**Maturity Levels**

- `prototype`: Quick proof-of-concept
- `mvp`: Minimum viable plugin
- `production_ready`: Full-featured, tested plugin

### Commands

**Create a Plan**

```
/pc create a Slack webhook integration plugin
```

**Check Status**

```
/pc status
```

Output:
```
Stored plans: 3 projects (proj_1, proj_2, proj_3)
```

**View History**

```
/pc history
```

Output:
```
PluginCreator history:
  proj_1
  proj_2
  proj_3
```

**Get Help**

```
/pc help
```

### Storage Management

**Locate Storage**

```bash
ls ~/.aeloon/plugin_storage/aeloon.plugincreator/
```

**Backup Plans**

```bash
cp ~/.aeloon/plugin_storage/aeloon.plugincreator/plans.jsonl \
   ~/.aeloon/plugin_storage/aeloon.plugincreator/plans.backup.jsonl
```

**Clear History**

```bash
rm ~/.aeloon/plugin_storage/aeloon.plugincreator/plans.jsonl
```

---

## Extension Guide

### Adding a New Planning Strategy

**Step 1: Implement PlanningStrategy Protocol**

```python
from aeloon.plugins.PluginCreator.planner.kernel import PlanningKernel

class MyCustomPlanner:
    async def plan(self, inp: PlanningKernelInput) -> PlanPackage:
        # Custom planning logic
        pass
```

**Step 2: Register in Kernel**

Modify `PlanningKernel._build_skeleton()` or add strategy selector.

### Adding New Artifact Types

**Step 1: Extend ArtifactSpec**

```python
from aeloon.plugins.PluginCreator.models import ArtifactSpec

class CustomArtifactSpec(ArtifactSpec):
    custom_field: str
```

**Step 2: Update Validation**

Modify `validator/plan_package.py` to validate new fields.

### Custom Storage Backend

**Step 1: Implement Storage Protocol**

```python
class MyCustomStore:
    def save(self, pkg: PlanPackage) -> None:
        pass
    
    def load(self, project_id: str) -> PlanPackage | None:
        pass
    
    def list_project_ids(self) -> list[str]:
        pass
```

**Step 2: Replace in Pipeline**

Modify `PluginCreatorPipeline.__init__()` to use custom store.

---

## API Reference

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

### Configuration

```python
class PluginCreatorConfig(BaseModel):
    enabled: bool = False
    workspace_dir: str = "~/.aeloon/plugincreator/workspaces"
    default_maturity: Literal["prototype", "mvp", "production_ready"] = "mvp"
    plan_first: bool = True
```

---

## Resources

- **Plugin SDK Docs**: `aeloon/plugins/_sdk/`
- **Example Plugins**: `ScienceResearch/`, `SkillGraph/`, `Wiki/`
- **ACP Bridge**: For connecting external agents
- **Tests**: `tests/test_plugin_sdk.py`
