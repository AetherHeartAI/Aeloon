<p align="right">
<a href="./README.md">English</a> | <b>中文</b>
</p>

# SkillGraph

`SkillGraph` 编译器现在内嵌在 `aeloon/plugins/SkillGraph/` 里，不再依赖仓库根目录单独的 `skillgraph/` 包。

它的职责不变：把 `SKILL.md` 或完整 skill 目录编译成可运行的 Python artifact，供 Aeloon 在 workspace 里加载为 compiled workflow/tool。

## 我们现在的做法

不是把所有 skill 都强行编译成一条 workflow，而是先判断它更适合哪种落地方式：

- `workflow`：有明确步骤、命令和执行流的 skill，编译成可恢复的 LangGraph workflow
- `dispatcher`：更像工具箱或脚本集合的 skill，编译成 dispatcher 风格的 runtime
- `reference`：主要是知识说明、交互约束或参考文档的 skill，编译成 reference adapter

核心流程和代码一致：

1. 扫描输入 skill，构建 package manifest 和 `package_hash`
2. 根据 `SKILL.md` 内容、脚本、配置、命令块判断 compilability 和 strategy
3. 对 `workflow` 路径执行分析 -> `SkillGraph` IR -> normalize -> validate -> codegen
4. 对 `dispatcher` / `reference` 路径直接走对应的 lowering
5. 在输出文件旁边生成 runtime 需要的 sibling 文件和 sandbox

## 当前能力边界

- 输入支持：单个 `SKILL.md`，或包含 `SKILL.md` 的 skill 目录
- 编译缓存：支持按 `package_hash` 复用分析缓存
- 输出报告：支持生成 compile report
- sandbox：会在输出 artifact 旁边创建 `.sandbox/`，复制 skill，并在需要时准备依赖和 CLI 包装
- runtime 配置：会在输出目录生成 `skill_config.json`

当前这个目录做的是“编译”和“生成 runtime artifact”，不是在这里直接承担 Aeloon 主循环里的恢复逻辑；恢复由上层 plugin/runtime 去接。

## CLI

安装后可以使用：

```bash
skillgraph-compile path/to/skill -o compiled/my_skill.py
```

仓库内也可以直接用模块入口：

```bash
python -m aeloon.plugins.SkillGraph.compile path/to/skill -o compiled/my_skill.py
```

当前 CLI 支持三种模式：

- 完整编译
- `--analyze-only`
- `--validate-only`

常用参数：

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

## 主要产物

以 `compiled/my_skill.py` 为例，当前会生成这些内容：

- `compiled/my_skill.py`：主 artifact
- `compiled/my_skill.manifest.json`：runtime manifest
- `compiled/skill_config.json`：runtime 配置模板
- `compiled/my_skill.sandbox/`：skill 副本、依赖和运行环境
- `output/graphs/<slug>.json`：分析缓存（如果启用 `cache_dir`）
- `output/graphs/<slug>.report.json`：编译报告（如果启用 report）

## 目录说明

- `skillgraph/__init__.py`：公开编译入口 `compile(...)`
- `skillgraph/package.py`：skill 扫描、资产分类、manifest/hash
- `skillgraph/compilability.py`：选择 `workflow` / `dispatcher` / `reference`
- `skillgraph/analyzer.py`：workflow 路径的分析阶段
- `skillgraph/normalize.py`：IR 规范化
- `skillgraph/validator.py`：校验和统计
- `skillgraph/codegen.py`：workflow codegen
- `skillgraph/dispatcher_codegen.py`：dispatcher lowering
- `skillgraph/reference_codegen.py`：reference lowering
- `skillgraph/sandbox.py`：输出 sandbox
- `skillgraph/report.py`：compile report
- `skillgraph/cli.py`：CLI 入口

## 安装

```bash
pip install -e .
```
