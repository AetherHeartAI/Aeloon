---
name: openviking-memory
description: OpenViking-backed memory recall and archival guidance.
always: false
---

# OpenViking Memory

- OpenViking recall is injected into the prompt automatically when the `openviking` memory backend is active.
- Do not switch back to legacy file-backed memory behavior in this mode.
- Treat recalled OpenViking items as the source of long-term context for the current turn.
- If memory-related files from an older file-backed setup still exist in the workspace, ignore them unless the user explicitly asks about migration.
