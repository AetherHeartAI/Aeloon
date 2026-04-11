<p align="right">
<b>English</b> | <a href="./README-wiki.md">中文</a>
</p>
# Wiki Plugin

Local Wiki and knowledge base management plugin — supports content ingestion, automatic summarization, and conversation enhancement.

## Table of Contents

1. [Overview](#overview)
2. [Installation & Activation](#installation--activation)
3. [Command Reference](#command-reference)
4. [Usage Modes](#usage-modes)
5. [Configuration Options](#configuration-options)
6. [Usage Workflows](#usage-workflows)
7. [Architecture Design](#architecture-design)

---

## Overview

The Wiki plugin is a **hybrid plugin** that provides local knowledge base management capabilities for your Aeloon assistant. It allows you to ingest external documents, web pages, papers, and other resources into your local system, automatically generates structured Wiki entries, and provides intelligent knowledge enhancement during conversations.

### Core Capabilities

| Capability | Description |
|------------|-------------|
| **Content Ingestion** | Supports URLs, arXiv papers, local files (PDF, DOCX, Markdown, TXT, CSV) |
| **Intelligent Summarization** | Uses LLM to analyze content and generate structured knowledge entries |
| **Knowledge Linking** | Automatically builds three-level knowledge graph: domains, summaries, and concepts |
| **Conversation Enhancement** | Automatically retrieves relevant knowledge based on user queries and injects into conversation context |
| **Background Tasks** | Long-running ingestion and processing tasks execute asynchronously |

### Knowledge Base Structure

```
wiki_root/
├── WIKI_HARNESS.md       # Knowledge base usage guidelines
├── raw/                  # Raw content storage
│   ├── links/           # URL link raw content
│   ├── files/           # Local file copies
│   └── meta/            # Source metadata
├── wiki/                 # Processed knowledge entries
│   ├── domains/         # Domain organization pages
│   ├── summaries/       # Source-level summaries
│   └── concepts/        # Cross-source concepts
└── state/               # State management
    ├── manifest.json    # Tracking manifest for sources and pages
    └── log.jsonl        # Operation logs
```

---

## Installation & Activation

### Prerequisites

- Aeloon >= 0.1.0
- Wiki plugin is built-in, no additional installation required

### Enable Plugin

Add to `~/.aeloon/config.toml`:

```toml
[plugins]
wiki = { enabled = true }
```

Or use plugin-specific configuration:

```toml
[wiki]
enabled = true
repoRoot = "~/my-wiki"           # Knowledge base root directory
autoQueryEnabled = true          # Enable automatic query enhancement
supportedFormats = ["pdf", "docx", "md", "txt", "csv"]
```

Restart Aeloon to automatically load the plugin.

---

## Command Reference

### Knowledge Base Management

#### `/wiki init [path]` — Initialize Knowledge Base

Create a new knowledge base directory structure. If no path is specified, uses the configured `repoRoot` or default path.

```
/wiki init                    # Use default path
/wiki init ~/my-knowledge     # Specify custom path
/wiki init /workspace/wiki    # Use workspace path
```

#### `/wiki status` — View Knowledge Base Status

Display statistics and configuration status of the current knowledge base.

```
/wiki status
```

Example output:
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

#### `/wiki remove --confirm` — Delete Knowledge Base

**Warning**: This action permanently deletes the entire knowledge base!

```
/wiki remove                  # Show confirmation prompt
/wiki remove --confirm        # Confirm deletion
```

---

### Content Ingestion

#### `/wiki <URL|text>` — Ingest URL or Text References

Directly ingest content from URLs or free text. Supports automatic URL recognition, arXiv references.

```
/wiki https://example.com/article

/wiki Please analyze this paper https://arxiv.org/abs/2401.12345

/wiki Reference these materials:
- https://docs.python.org/3/tutorial/
- https://arxiv.org/abs/2305.12345
```

**Note**: URL ingestion runs in the background and automatically sends results when complete.

#### `/wiki add <path|text>` — Add Local Files

Ingest local files into the knowledge base.

```
/wiki add ~/Documents/paper.pdf
/wiki add /workspace/notes.md
/wiki add ./data/report.csv

/wiki add Please analyze these files:
- ~/docs/specs.pdf
- ~/docs/design.md
```

**Supported Formats**: PDF, DOCX, Markdown, TXT, CSV

---

### Summarization Processing

#### `/wiki digest` — Re-process Raw Content

Re-run summarization generation on ingested but unprocessed raw content.

```
/wiki digest
```

Example output:
```
| Source | Artifacts | Summary |
|--------|-----------|---------|
| paper.pdf | domain:ai, summary:paper | summary:paper |
| article.md | concept:neural-networks | - |
```

---

### Query & Retrieval

#### `/wiki list` — List All Content

Display tracked raw sources and generated Wiki entries.

```
/wiki list
```

Example output:
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

#### `/wiki get <entry>` — View Specific Entry

Display the complete content of a specified Wiki entry.

```
/wiki get summary:paper
/wiki get concept:neural-networks
/wiki get domain:ai
```

#### `/wiki map [entry]` — Generate Relationship Graph

Display relationships between Wiki entries as a Mermaid diagram.

```
/wiki map                     # Complete knowledge graph
/wiki map domain:ai          # Relationship graph for specific domain
/wiki map summary:paper      # Relationship graph for specific summary
```

---

### Usage Mode Control

#### `/wiki use <mode>` — Control Wiki Usage in Conversation

Set the current session's Wiki enhancement mode:

```
/wiki use off                # Disable Wiki enhancement
/wiki use prefer-local       # Prefer local knowledge (default)
/wiki use local-only         # Use local knowledge only
/wiki use status             # View current mode
```

| Mode | Description |
|------|-------------|
| `off` | Do not use Wiki enhancement in conversation at all |
| `prefer-local` | Try to get knowledge from Wiki first, fall back to LLM common knowledge if missing |
| `local-only` | Only get knowledge from Wiki, explicitly report knowledge gap |

#### `/wiki attach <on|off|status>` — Automatic Attachment Ingestion

Control whether to automatically ingest file attachments in sessions:

```
/wiki attach on              # Enable automatic ingestion
/wiki attach off             # Disable automatic ingestion
/wiki attach status          # View current status
```

When enabled, PDFs, documents, and other attachments received in sessions are automatically imported into Wiki and summarized.

---

### Background Tasks

#### `/wiki jobs` — View Background Tasks

Display currently running Wiki background tasks for this session.

```
/wiki jobs
```

Example output:
```
Wiki background task is running.
- command: `https://arxiv.org/abs/2401.12345`
- elapsed_seconds: 45
```

---

## Usage Modes

### Mode Comparison

| Mode | Trigger Condition | Behavior When Knowledge Missing |
|------|-------------------|--------------------------------|
| `off` | Not triggered | - |
| `prefer-local` | Auto-triggered in conversation | Fall back to LLM common knowledge |
| `local-only` | Only triggered on knowledge queries | Explicitly report knowledge gap |

### Knowledge Query Recognition

When `local-only` mode is enabled, the plugin recognizes the following types of knowledge queries:

- Questions containing `?`
- Questions starting with `what`, `why`, `how`, `compare`, `explain`, `summarize`, `tell me`

---

## Configuration Options

### Complete Configuration Example

```toml
[wiki]
enabled = true
repoRoot = "~/wiki"
autoQueryEnabled = true
supportedFormats = ["pdf", "docx", "md", "txt", "csv"]
```

### Configuration Options Reference

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `enabled` | boolean | `false` | Whether to enable Wiki plugin |
| `repoRoot` | string | `""` | Knowledge base root directory path, empty means use plugin storage directory |
| `autoQueryEnabled` | boolean | `true` | Whether to enable automatic query enhancement |
| `supportedFormats` | string[] | `["pdf", "docx", "md", "txt", "csv"]` | Supported file formats |

---

## Usage Workflows

### Workflow 1: Quick Personal Knowledge Base Setup

```bash
# 1. Initialize knowledge base
/wiki init ~/my-knowledge

# 2. Ingest common reference materials
/wiki https://docs.python.org/3/tutorial/
/wiki add ~/Documents/cheatsheet.pdf
/wiki https://arxiv.org/abs/2305.12345

# 3. Check ingestion status
/wiki status
/wiki list

# 4. Use knowledge-enhanced conversation
/wiki use prefer-local
# Now every conversation will automatically retrieve relevant knowledge
```

### Workflow 2: Research Project Knowledge Management

```bash
# 1. Create dedicated knowledge base for project
/wiki init ./project-wiki

# 2. Batch import related papers and documents
/wiki add ./papers/*.pdf
/wiki add ./notes/*.md

# 3. Ensure all content is processed
/wiki digest

# 4. View knowledge graph
/wiki map

# 5. Strictly use local knowledge for research
/wiki use local-only
# Ask questions related to papers
```

### Workflow 3: Automatic Attachment Ingestion

```bash
# 1. Enable automatic attachment ingestion
/wiki attach on

# 2. Send files to session (e.g., via Telegram)
# Files are automatically imported into Wiki and summarized

# 3. View import results
/wiki list

# 4. View generated summaries
/wiki get summary:document-name
```

---

## Architecture Design

### Service Architecture

```
┌─────────────────────────────────────────┐
│ Command Layer                           │
│  /wiki command routing and parameter parsing
├─────────────────────────────────────────┤
│ Service Layer                           │
│  - RepoService: Knowledge base structure management
│  - ManifestService: Source and entry tracking
│  - IngestService: Content ingestion processing
│  - DigestService: Summarization generation
│  - QueryService: Knowledge query retrieval
│  - UsageModeStore: Session usage mode
├─────────────────────────────────────────┤
│ Middleware Layer                        │
│  WikiQueryMiddleware: Conversation enhancement injection
├─────────────────────────────────────────┤
│ Storage Layer                           │
│  Raw content / Structured entries / Metadata
└─────────────────────────────────────────┘
```

### Data Flow

```
External Content
    ↓
[IngestService] Ingest → raw/
    ↓
[DigestService] Summarize → wiki/
    ↓
[QueryService] Index → Queryable
    ↓
[WikiQueryMiddleware] Enhance → Conversation Context
```

### Core Service Responsibilities

| Service | Responsibility |
|---------|----------------|
| **RepoService** | Manage knowledge base directory structure, provide path resolution and status queries |
| **ManifestService** | Maintain `manifest.json`, track all sources and generated pages |
| **IngestService** | Handle URL downloads, file copying, format recognition, duplicate detection |
| **DigestService** | Call LLM to analyze raw content, generate domain/summary/concept pages |
| **QueryService** | Provide entry retrieval, relationship graphs, evidence formatting |
| **WikiQueryMiddleware** | Intercept LLM calls, automatically inject relevant knowledge based on queries |

### Middleware Operation

1. **Message Capture**: Capture session context through `MESSAGE_RECEIVED` Hook
2. **Query Recognition**: Extract latest query text from user messages
3. **Mode Judgment**: Decide whether to enhance based on current session usage mode
4. **Knowledge Retrieval**: Call QueryService to search for relevant evidence
5. **Context Injection**: Inject evidence blocks into system messages for LLM use

### Evidence Block Format

When Wiki finds relevant knowledge, it injects context in the following format:

```markdown
## Wiki Evidence

### [Entry Title]
- entry: `entry-id`
- score: 85

Summary content...

### Related
- `related-entry-1`: description
- `related-entry-2`: description
```

---

## Template Specification

Wiki uses `WIKI_HARNESS.md` as the knowledge base usage guideline. All generated pages follow these conventions:

- `raw/` is the input directory, not surfaced as answers
- `wiki/summaries/` contains source-level summaries
- `wiki/concepts/` contains cross-source concepts
- `wiki/domains/` contains domain organization pages
- Summary and concept pages declare `primary_domain`, can declare additional `domain_refs`
- `state/manifest.json` is the single source of truth for tracking sources and derived pages
