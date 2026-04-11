---
name: openviking-memory
description: Use when the active memory backend is `openviking` and recalled OpenViking results are already injected into the prompt. Treat OpenViking recall as the source of long-term memory for the current turn, stay in OpenViking-backed memory mode, and ignore legacy file-memory artifacts unless the user explicitly asks about migration or file-backed memory.
---

# OpenViking Memory

- Treat the `# OpenViking Recall` section as the authoritative long-term memory context for the current turn.
- Stay in OpenViking-backed memory mode. Do not switch the agent back to legacy file-backed memory behavior unless the user explicitly asks for migration or legacy memory handling.
- Use recalled OpenViking items to answer questions, maintain continuity, and ground follow-up decisions.
- Ignore legacy file-memory artifacts in the workspace by default.
- If the user explicitly asks about migration or file-backed memory, compare the legacy workspace artifacts with the current OpenViking-backed behavior before recommending changes.
