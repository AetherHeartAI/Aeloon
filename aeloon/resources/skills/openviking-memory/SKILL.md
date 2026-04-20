---
name: openviking-memory
description: Reference guidance for OpenViking setup and layered-memory migration in Aeloon. Runtime usage should rely on provider-native viking_* tools, not this skill.
---

# OpenViking Setup And Migration

- OpenViking is additive in Aeloon. Prompt memory in `memory/MEMORY.md` and `memory/USER.md` stays active, and transcript recall still belongs to `session_search`.
- When the OpenViking provider is enabled, the primary runtime surface is the provider-native tool set:
  - `viking_search`
  - `viking_read`
  - `viking_browse`
  - `viking_remember`
  - `viking_add_resource`
- Use this skill only for setup, migration, or explanation tasks. Do not treat it as the main control plane for OpenViking operations.
- If the user asks how the layers fit together, explain them in this order:
  - prompt memory for small durable facts
  - session archive for transcript recall
  - OpenViking for additive semantic/provider-native knowledge
