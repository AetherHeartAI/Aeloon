<p align="right">
<a href="./README.md">English</a> | <b>中文</b>
</p>

# Wiki 插件

本地 Wiki 与知识库管理插件 —— 支持内容采集、自动摘要和对话增强。

## 目录

1. [概述](#概述)
2. [安装与启用](#安装与启用)
3. [命令参考](#命令参考)
4. [使用模式](#使用模式)
5. [配置选项](#配置选项)
6. [使用工作流](#使用工作流)
7. [架构设计](#架构设计)

---

## 概述

Wiki 插件是一个**混合类型插件**，为你的 Aeloon 助手提供本地知识库管理能力。它允许你将外部文档、网页、论文等资源采集到本地，自动生成结构化的 Wiki 条目，并在对话中提供智能化的知识增强。

### 核心能力

| 能力 | 说明 |
|------|------|
| **内容采集** | 支持 URL、arXiv 论文、本地文件（PDF、DOCX、Markdown、TXT、CSV） |
| **智能摘要** | 使用 LLM 分析内容，生成结构化知识条目 |
| **知识关联** | 自动构建领域、摘要、概念三级知识图谱 |
| **对话增强** | 根据用户问题自动检索相关知识，注入对话上下文 |
| **后台任务** | 长时间运行的采集和处理任务异步执行 |

### 知识库结构

```
wiki_root/
├── WIKI_HARNESS.md       # 知识库使用规范
├── raw/                  # 原始内容存储
│   ├── links/           # URL 链接原始内容
│   ├── files/           # 本地文件副本
│   └── meta/            # 源数据元信息
├── wiki/                 # 处理后知识条目
│   ├── domains/         # 领域组织页面
│   ├── summaries/       # 源级摘要
│   └── concepts/        # 跨源概念
└── state/               # 状态管理
    ├── manifest.json    # 跟踪源和页面清单
    └── log.jsonl        # 操作日志
```

---

## 安装与启用

### 前置条件

- Aeloon >= 0.1.0
- Wiki 插件为内置插件，无需额外安装

### 启用插件

在 `~/.aeloon/config.toml` 中添加：

```toml
[plugins]
wiki = { enabled = true }
```

或使用插件专属配置：

```toml
[wiki]
enabled = true
repoRoot = "~/my-wiki"           # 知识库根目录
autoQueryEnabled = true          # 启用自动查询增强
supportedFormats = ["pdf", "docx", "md", "txt", "csv"]
```

重启 Aeloon 后插件自动加载。

---

## 命令参考

### 知识库管理

#### `/wiki init [path]` —— 初始化知识库

创建新的知识库目录结构。如果不指定路径，使用配置中的 `repoRoot` 或默认路径。

```
/wiki init                    # 使用默认路径
/wiki init ~/my-knowledge     # 指定自定义路径
/wiki init /workspace/wiki    # 使用工作区路径
```

#### `/wiki status` —— 查看知识库状态

显示当前知识库的统计信息和配置状态。

```
/wiki status
```

输出示例：
```
## Wiki Status

- repo_root: `/home/user/.aeloon/plugin_storage/aeloon.wiki/repo`
- initialized: yes
- use_mode: prefer-local
- raw_sources: 12
- domains: 3
- summaries: 8
- concepts: 5
```

#### `/wiki remove --confirm` —— 删除知识库

**警告**：此操作会永久删除整个知识库！

```
/wiki remove                  # 显示确认提示
/wiki remove --confirm        # 确认删除
```

---

### 内容采集

#### `/wiki <URL|文本>` —— 采集 URL 或文本中的引用

直接从 URL 或自由文本中采集内容。支持自动识别 URL、arXiv 引用。

```
/wiki https://example.com/article

/wiki 请分析这篇论文 https://arxiv.org/abs/2401.12345

/wiki 参考这些资料：
- https://docs.python.org/3/tutorial/
- https://arxiv.org/abs/2305.12345
```

**注意**：URL 采集在后台运行，完成后自动发送结果。

#### `/wiki add <路径|文本>` —— 添加本地文件

采集本地文件到知识库。

```
/wiki add ~/Documents/paper.pdf
/wiki add /workspace/notes.md
/wiki add ./data/report.csv

/wiki add 请分析这些文件：
- ~/docs/specs.pdf
- ~/docs/design.md
```

**支持格式**：PDF、DOCX、Markdown、TXT、CSV

---

### 摘要处理

#### `/wiki digest` —— 重新处理原始内容

对已采集但未处理的原始内容重新运行摘要生成。

```
/wiki digest
```

输出示例：
```
| Source | Artifacts | Summary |
|--------|-----------|---------|
| paper.pdf | domain:ai, summary:paper | summary:paper |
| article.md | concept:neural-networks | - |
```

---

### 查询与检索

#### `/wiki list` —— 列出所有内容

显示已跟踪的原始源和生成的 Wiki 条目。

```
/wiki list
```

输出示例：
```
## Wiki List

### Raw Sources
- `paper.pdf` [digested]
- `https://example.com/article` [pending]
- `notes.md` [digested]

### Wiki Entries
- `summary:paper` -> `wiki/summaries/paper.md`
- `concept:neural-networks` -> `wiki/concepts/neural-networks.md`
- `domain:ai` -> `wiki/domains/ai.md`
```

#### `/wiki get <条目>` —— 查看具体条目

显示指定 Wiki 条目的完整内容。

```
/wiki get summary:paper
/wiki get concept:neural-networks
/wiki get domain:ai
```

#### `/wiki map [条目]` —— 生成关系图谱

以 Mermaid 图表形式展示 Wiki 条目之间的关系。

```
/wiki map                     # 完整知识图谱
/wiki map domain:ai          # 特定领域的关系图
/wiki map summary:paper      # 特定摘要的关联图
```

---

### 使用模式控制

#### `/wiki use <模式>` —— 控制 Wiki 在对话中的使用

设置当前会话的 Wiki 增强模式：

```
/wiki use off                # 关闭 Wiki 增强
/wiki use prefer-local       # 优先使用本地知识（默认）
/wiki use local-only         # 仅使用本地知识
/wiki use status             # 查看当前模式
```

| 模式 | 说明 |
|------|------|
| `off` | 对话中完全不使用 Wiki 增强 |
| `prefer-local` | 先尝试从 Wiki 获取知识，缺失时使用 LLM 常识 |
| `local-only` | 仅从 Wiki 获取知识，明确告知知识缺失 |

#### `/wiki attach <on|off|status>` —— 自动附件采集

控制是否自动采集会话中的文件附件：

```
/wiki attach on              # 开启自动采集
/wiki attach off             # 关闭自动采集
/wiki attach status          # 查看当前状态
```

开启后，会话中收到的 PDF、文档等附件会自动导入 Wiki 并生成摘要。

---

### 后台任务

#### `/wiki jobs` —— 查看后台任务

显示当前会话正在运行的 Wiki 后台任务。

```
/wiki jobs
```

输出示例：
```
Wiki background task is running.
- command: `https://arxiv.org/abs/2401.12345`
- elapsed_seconds: 45
```

---

## 使用模式

### 模式对比

| 模式 | 触发条件 | 知识缺失时行为 |
|------|----------|----------------|
| `off` | 不触发 | - |
| `prefer-local` | 对话中自动触发 | 回退到 LLM 常识 |
| `local-only` | 仅在知识查询时触发 | 明确告知知识缺失 |

### 知识查询识别

当 `local-only` 模式开启时，插件会识别以下类型的知识查询：

- 包含 `?` 的疑问句
- 以 `what`、`why`、`how`、`compare`、`explain`、`summarize`、`tell me` 开头的问题

---

## 配置选项

### 完整配置示例

```toml
[wiki]
enabled = true
repoRoot = "~/wiki"
autoQueryEnabled = true
supportedFormats = ["pdf", "docx", "md", "txt", "csv"]
```

### 配置项说明

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `enabled` | boolean | `false` | 是否启用 Wiki 插件 |
| `repoRoot` | string | `""` | 知识库根目录路径，空则使用插件存储目录 |
| `autoQueryEnabled` | boolean | `true` | 是否启用自动查询增强 |
| `supportedFormats` | string[] | `["pdf", "docx", "md", "txt", "csv"]` | 支持的文件格式 |

---

## 使用工作流

### 工作流 1：快速建立个人知识库

```bash
# 1. 初始化知识库
/wiki init ~/my-knowledge

# 2. 采集常用参考资料
/wiki https://docs.python.org/3/tutorial/
/wiki add ~/Documents/cheatsheet.pdf
/wiki https://arxiv.org/abs/2305.12345

# 3. 查看采集状态
/wiki status
/wiki list

# 4. 使用知识增强对话
/wiki use prefer-local
# 现在每次对话都会自动检索相关知识
```

### 工作流 2：研究项目知识管理

```bash
# 1. 为项目创建专用知识库
/wiki init ./project-wiki

# 2. 批量导入相关论文和文档
/wiki add ./papers/*.pdf
/wiki add ./notes/*.md

# 3. 确保所有内容已处理
/wiki digest

# 4. 查看知识图谱
/wiki map

# 5. 严格使用本地知识进行研究
/wiki use local-only
# 询问与论文相关的问题
```

### 工作流 3：自动附件采集

```bash
# 1. 开启自动附件采集
/wiki attach on

# 2. 发送文件到会话中（如通过 Telegram）
# 文件会自动导入 Wiki 并生成摘要

# 3. 查看导入结果
/wiki list

# 4. 查看生成的摘要
/wiki get summary:document-name
```

---

## 架构设计

### 服务架构

```
┌─────────────────────────────────────────┐
│ 命令层 (Command Layer)                  │
│  /wiki 命令路由和参数解析                 │
├─────────────────────────────────────────┤
│ 服务层 (Service Layer)                  │
│  - RepoService: 知识库结构管理           │
│  - ManifestService: 源和条目跟踪         │
│  - IngestService: 内容采集处理           │
│  - DigestService: 摘要生成               │
│  - QueryService: 知识查询检索            │
│  - UsageModeStore: 会话使用模式          │
├─────────────────────────────────────────┤
│ 中间件层 (Middleware Layer)             │
│  WikiQueryMiddleware: 对话增强注入       │
├─────────────────────────────────────────┤
│ 存储层 (Storage Layer)                  │
│  原始内容 / 结构化条目 / 元数据           │
└─────────────────────────────────────────┘
```

### 数据流

```
外部内容
    ↓
[IngestService] 采集 → raw/
    ↓
[DigestService] 摘要 → wiki/
    ↓
[QueryService] 索引 → 可查询
    ↓
[WikiQueryMiddleware] 增强 → 对话上下文
```

### 核心服务职责

| 服务 | 职责 |
|------|------|
| **RepoService** | 管理知识库目录结构，提供路径解析和状态查询 |
| **ManifestService** | 维护 `manifest.json`，跟踪所有源和生成的页面 |
| **IngestService** | 处理 URL 下载、文件复制、格式识别、重复检测 |
| **DigestService** | 调用 LLM 分析原始内容，生成领域/摘要/概念页面 |
| **QueryService** | 提供条目检索、关系图谱、证据格式化 |
| **WikiQueryMiddleware** | 拦截 LLM 调用，根据查询自动注入相关知识 |

### 中间件工作方式

1. **消息捕获**：通过 `MESSAGE_RECEIVED` Hook 捕获会话上下文
2. **查询识别**：从用户消息中提取最新查询文本
3. **模式判断**：根据当前会话的使用模式决定是否增强
4. **知识检索**：调用 QueryService 搜索相关证据
5. **上下文注入**：将证据块注入系统消息，供 LLM 使用

### 证据块格式

当 Wiki 找到相关知识时，会注入如下格式的上下文：

```markdown
## Wiki Evidence

### [条目标题]
- entry: `entry-id`
- score: 85

摘要内容...

### Related
- `related-entry-1`: 描述
- `related-entry-2`: 描述
```

---

## 模板规范

Wiki 使用 `WIKI_HARNESS.md` 作为知识库使用规范，所有生成的页面遵循以下约定：

- `raw/` 是输入目录，不作为答案表面
- `wiki/summaries/` 包含源级摘要
- `wiki/concepts/` 包含跨源概念
- `wiki/domains/` 包含领域组织页面
- 摘要和概念页面声明 `primary_domain`，可声明额外的 `domain_refs`
- `state/manifest.json` 是跟踪源和派生页面的唯一真相源
