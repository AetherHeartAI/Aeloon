---
name: openviking-memory
description: Use when OpenViking is enabled as an additive memory provider. Treat OpenViking recall as supplemental context layered on top of local prompt memory and transcript recall.
---

# OpenViking Memory

- Treat OpenViking recall as supplemental context, not a replacement for local prompt memory.
- Keep using `memory/MEMORY.md` and `memory/USER.md` for always-on durable facts.
- Prefer `session_search` for past conversation recall and OpenViking for semantic/provider recall.
- If the user asks about migration or setup, explain the layered model clearly.
