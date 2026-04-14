---
name: session-search
description: Recall past conversations through the session_search tool instead of grepping transcript files.
always: false
---

# Session Search

Use `session_search` when the user refers to earlier work, past decisions, previous bugs, or anything that seems like cross-session recall.

## Prefer This Over Grep

- browse recent sessions by calling `session_search` without a query
- search by topic with OR-joined keywords for broader recall
- treat the returned summaries as the primary recall surface
- do not read `memory/HISTORY.md` for normal cross-session recall
- only inspect `HISTORY.md` when the user explicitly asks for that file or you are debugging archive behavior

## Good Triggers

- "what were we doing last time?"
- "remember when we fixed this?"
- "how did we solve the docker issue?"
- "did we already discuss this project?"
