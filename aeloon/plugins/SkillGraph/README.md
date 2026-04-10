<p align="right">
<b>English</b> | <a href="./README-SG.md">中文</a>
</p>

# SkillGraph

The `SkillGraph` compiler is now embedded in `aeloon/plugins/SkillGraph/` and no longer depends on a separate `skillgraph/` package at the repository root.

Its responsibilities remain unchanged: compiling `SKILL.md` or complete skill directories into runnable Python artifacts for Aeloon to load as compiled workflows/tools in the workspace.

## Our Current Approach

Instead of forcing all skills into a single workflow, we first determine which delivery approach is most suitable:

- `workflow`: Skills with clear steps, commands, and execution flows are compiled into resumable LangGraph workflows
- `dispatcher`: Skills that are more like toolboxes or script collections are compiled into dispatcher-style runtimes
- `reference`: Skills that are primarily knowledge descriptions, interaction constraints, or reference documents are compiled into reference adapters

The core process and code remain consistent:

1. Scan input skill, build package manifest and `package_hash`
2. Determine compilability and strategy based on `SKILL.md` content, scripts, configuration, and command blocks
3. For `workflow` path: analysis -> `SkillGraph` IR -> normalize -> validate -> codegen
4. For `dispatcher` / `reference` paths: direct lowering to corresponding output
5. Generate sibling files and sandbox needed by the runtime alongside the output file

## Current Capability Boundaries

- Input support: single `SKILL.md`, or skill directory containing `SKILL.md`
- Compilation cache: supports reusing analysis cache by `package_hash`
- Output reports: supports generating compile reports
- Sandbox: creates `.sandbox/` next to output artifact, copies skill, and prepares dependencies and CLI wrappers when needed
- Runtime configuration: generates `skill_config.json` in output directory

The current directory handles "compilation" and "generating runtime artifacts"; restoration logic in Aeloon's main loop is handled by the upper-level plugin/runtime.

## CLI

After installation, you can use:

```bash
skillgraph-compile path/to/skill -o compiled/my_skill.py
```

You can also use the module entry directly within the repository:

```bash
python -m aeloon.plugins.SkillGraph.compile path/to/skill -o compiled/my_skill.py
```

The current CLI supports three modes:

- Full compilation
- `--analyze-only`
- `--validate-only`

Common parameters:

- `--model`
- `--runtime-model`
- `--base-url`
- `--api-key`
- `--cache-dir`
- `--report-path`
- `--strict-validate`

## Python API

```python
from aeloon.plugins.SkillGraph.skillgraph import compile

output = compile(
    skill_path="path/to/skill",
    output_path="compiled/my_skill.py",
    api_key="<compile-time-key>",
    base_url="https://openrouter.ai/api/v1",
    model="openai/gpt-5.4",
    runtime_model="openai/gpt-5.4",
    cache_dir="output/graphs",
    strict_validate=False,
)
```

## Main Artifacts

Taking `compiled/my_skill.py` as an example, the following content is currently generated:

- `compiled/my_skill.py`: Main artifact
- `compiled/my_skill.manifest.json`: Runtime manifest
- `compiled/skill_config.json`: Runtime configuration template
- `compiled/my_skill.sandbox/`: Skill copy, dependencies, and runtime environment
- `output/graphs/<slug>.json`: Analysis cache (if `cache_dir` is enabled)
- `output/graphs/<slug>.report.json`: Compilation report (if report is enabled)

## Directory Structure

- `skillgraph/__init__.py`: Public compilation entry `compile(...)`
- `skillgraph/package.py`: Skill scanning, asset classification, manifest/hash
- `skillgraph/compilability.py`: Select `workflow` / `dispatcher` / `reference`
- `skillgraph/analyzer.py`: Analysis phase for workflow path
- `skillgraph/normalize.py`: IR normalization
- `skillgraph/validator.py`: Validation and statistics
- `skillgraph/codegen.py`: Workflow codegen
- `skillgraph/dispatcher_codegen.py`: Dispatcher lowering
- `skillgraph/reference_codegen.py`: Reference lowering
- `skillgraph/sandbox.py`: Output sandbox
- `skillgraph/report.py`: Compile report
- `skillgraph/cli.py`: CLI entry

## Installation

```bash
pip install -e .
```
