# OpenViking Memory Skill Cleanup and Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the duplicate `openviking-memory` skill file, rewrite the canonical skill so it better matches `skill-creator` guidance without changing runtime behavior, and add validation coverage that keeps the skill folder clean going forward.

**Architecture:** Keep the runtime contract unchanged. `OpenVikingMemoryBackend` must continue injecting the skill by the exact name `openviking-memory`, and `SkillsLoader` must continue loading the canonical `SKILL.md` file only. The change is structural and editorial: delete the stray `SKILL 2.md`, tighten the canonical frontmatter and body so the description carries the trigger conditions, and validate the folder with the existing skill validator plus a targeted OpenViking backend prompt test.

**Tech Stack:** Markdown, YAML frontmatter, Aeloon `SkillsLoader`, `quick_validate.py`, `pytest`

---

## File Map

- Delete: `aeloon/resources/skills/openviking-memory/SKILL 2.md`
- Modify: `aeloon/resources/skills/openviking-memory/SKILL.md`
- Modify: `tests/test_skill_creator_scripts.py`
- Verify only: `aeloon/memory/backends/openviking.py`
- Verify only: `aeloon/core/agent/skills.py`
- Verify only: `tests/test_openviking_memory_backend.py`

## Detailed Todo List

### Phase 0: Preflight And Scope Lock

- [x] Re-read `aeloon/resources/skills/openviking-memory/SKILL.md` and confirm the current user-facing behavior that must be preserved.
- [x] Confirm that `aeloon/resources/skills/openviking-memory/SKILL 2.md` is a literal duplicate and not a separately referenced artifact.
- [x] Re-check `aeloon/core/agent/skills.py` and confirm that the runtime only loads `SKILL.md` from a skill root.
- [x] Re-check `aeloon/memory/backends/openviking.py` and confirm that the backend still injects `always_skill_names=["openviking-memory"]`.
- [x] Re-check `aeloon/resources/skills/skill-creator/scripts/quick_validate.py` and confirm the current allowed root-level skill contents.
- [x] Freeze the non-goals for the change:
- [x] Do not rename the skill.
- [x] Do not change Python runtime behavior.
- [x] Do not add extra resource folders or extra root-level files unless they are required by validation.

### Phase 1: Add Safety Coverage Before Editing

- [x] Open `tests/test_skill_creator_scripts.py` and find the existing `quick_validate` tests.
- [x] Add a new regression test asserting that `aeloon/resources/skills/openviking-memory` validates successfully as a skill folder.
- [x] Run `pytest tests/test_skill_creator_scripts.py -k openviking_memory -v`.
- [x] Capture the current failure mode and verify that it points to the duplicate root file.
- [x] Run `pytest tests/test_openviking_memory_backend.py::test_openviking_prepare_turn_injects_recall_and_hides_file_skill -v`.
- [x] Record that the runtime contract is green before any skill text changes.

### Phase 2: Clean The Skill Directory

- [x] Delete `aeloon/resources/skills/openviking-memory/SKILL 2.md`.
- [x] List the contents of `aeloon/resources/skills/openviking-memory/` and verify that only the canonical `SKILL.md` remains at the root.
- [x] Confirm that the skill directory still satisfies the local repository convention of one root `SKILL.md` plus optional resource directories only.

### Phase 3: Normalize The Canonical Skill Frontmatter

- [x] Replace the current frontmatter in `aeloon/resources/skills/openviking-memory/SKILL.md`.
- [x] Keep `name: openviking-memory` unchanged.
- [x] Rewrite `description` so it carries trigger conditions, not just a label.
- [x] Remove `always: false` because it is not needed for behavior and adds no routing value here.
- [x] Verify the new description explicitly mentions:
- [x] active backend is `openviking`
- [x] recall is already injected into the prompt
- [x] legacy file-memory artifacts should be ignored unless the user asks

### Phase 4: Normalize The Skill Body

- [x] Rewrite the body into short imperative bullets that are easy for an agent to follow.
- [x] Keep the body single-file and concise; do not split into `references/` unless the rewritten body becomes too large.
- [x] Preserve the existing functional guidance:
- [x] OpenViking recall is authoritative for the turn.
- [x] Do not switch back to file-backed memory behavior by default.
- [x] Ignore legacy `memory/` artifacts unless migration is explicitly requested.
- [x] Add one explicit instruction covering what to do if the user asks about migration or legacy file-backed memory.
- [x] Re-read the finished body and remove any explanatory fluff that does not change agent behavior.

### Phase 5: Validate The Skill Itself

- [x] Run `python aeloon/resources/skills/skill-creator/scripts/quick_validate.py aeloon/resources/skills/openviking-memory`.
- [x] Verify the validator returns `Skill is valid!`.
- [x] Re-run `pytest tests/test_skill_creator_scripts.py -k openviking_memory -v`.
- [x] Confirm the new validator regression test now passes.

### Phase 6: Run Runtime Regression Checks

- [x] Re-run `pytest tests/test_openviking_memory_backend.py::test_openviking_prepare_turn_injects_recall_and_hides_file_skill -v`.
- [x] Confirm the prompt still contains `### Skill: openviking-memory`.
- [x] Confirm the prompt still hides the legacy `memory` skill.
- [x] Run the combined targeted suite:
- [x] `pytest tests/test_skill_creator_scripts.py tests/test_openviking_memory_backend.py -k 'openviking_memory or injects_recall_and_hides_file_skill' -v`
- [x] Confirm all selected tests pass.

### Phase 7: Final Review And Commit Prep

- [x] Review the final diff and confirm it is limited to:
- [x] skill directory cleanup
- [x] canonical `SKILL.md` normalization
- [x] validator regression coverage
- [x] Confirm no Python runtime files changed unexpectedly.
- [x] Confirm `aeloon/resources/skills/openviking-memory/` has no extra root-level files.
- [ ] Stage the intended files only.
- [ ] Create the commit using `git commit -m "fix(skills): normalize openviking-memory skill"`.
- [x] If `SKILL 2.md` was untracked, confirm it is gone from the working tree even if it does not appear as a staged deletion.

## Runtime Contract To Preserve

Do not rename the skill folder or the skill name. The backend hardcodes the injection contract:

```python
# aeloon/memory/backends/openviking.py
return PreparedMemoryContext(
    history_start_index=state["archivedThrough"],
    system_sections=[self._build_recall_section(result)],
    runtime_lines=[
        "Memory backend: openviking",
        f"OpenViking storage: {self.storage_root}",
    ],
    always_skill_names=["openviking-memory"],
)
```

The loader also only discovers `SKILL.md` at the skill root:

```python
# aeloon/core/agent/skills.py
for skill_dir in self.builtin_skills.iterdir():
    if skill_dir.is_dir():
        skill_file = skill_dir / "SKILL.md"
        if skill_file.exists():
            skills.append(...)
```

This means:

- keep `name: openviking-memory`
- keep the folder name `openviking-memory`
- do not move the skill into a different directory
- do not change Python runtime code unless you discover a contradiction during implementation

## Important Constraint From The Local Validator

Do not add `agents/`, `README.md`, or any other root-level extras to this skill folder in this change. The repository-local validator currently allows only:

- `SKILL.md`
- `scripts/`
- `references/`
- `assets/`

The relevant rule is:

```python
# aeloon/resources/skills/skill-creator/scripts/quick_validate.py
for child in skill_path.iterdir():
    if child.name == "SKILL.md":
        continue
    if child.is_dir() and child.name in ALLOWED_RESOURCE_DIRS:
        continue
    return (
        False,
        f"Unexpected file or directory in skill root: {child.name}. "
        "Only SKILL.md, scripts/, references/, and assets/ are allowed.",
    )
```

That is why `SKILL 2.md` must be removed, and why this plan does not introduce `agents/openai.yaml` even though generic skill-creation guidance sometimes recommends it.

### Task 1: Add A Failing Regression Test For Skill Folder Validity

**Files:**
- Modify: `tests/test_skill_creator_scripts.py`
- Verify: `aeloon/resources/skills/skill-creator/scripts/quick_validate.py`

- [ ] **Step 1: Write the failing test**
- [x] **Step 1: Write the failing test**

Add this test near the other `quick_validate` coverage:

```python
def test_validate_skill_accepts_openviking_memory() -> None:
    valid, message = quick_validate.validate_skill(
        Path("aeloon/resources/skills/openviking-memory").resolve()
    )

    assert valid, message
```

- [x] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_skill_creator_scripts.py -k openviking_memory -v`

Expected: FAIL with a message mentioning `Unexpected file or directory in skill root: SKILL 2.md`

- [x] **Step 3: Record the baseline runtime contract before editing the skill text**

Run: `pytest tests/test_openviking_memory_backend.py::test_openviking_prepare_turn_injects_recall_and_hides_file_skill -v`

Expected: PASS

Why this matters: the later `SKILL.md` rewrite must not change the runtime wiring that already works.

### Task 2: Delete The Duplicate File And Rewrite The Canonical Skill

**Files:**
- Delete: `aeloon/resources/skills/openviking-memory/SKILL 2.md`
- Modify: `aeloon/resources/skills/openviking-memory/SKILL.md`

- [x] **Step 1: Delete the duplicate root file**

Run: `rm 'aeloon/resources/skills/openviking-memory/SKILL 2.md'`

Expected: the skill folder contains only `SKILL.md`

- [x] **Step 2: Replace the canonical `SKILL.md` with a minimal, trigger-friendly version**

Use this exact content:

```md
---
name: openviking-memory
description: Use when the active memory backend is `openviking` and recalled OpenViking results are already injected into the prompt. Treat OpenViking recall as the source of long-term memory for the current turn, stay in OpenViking-backed memory mode, and ignore legacy file-memory artifacts unless the user explicitly asks about migration or file-backed memory.
---

# OpenViking Memory

- Treat the `# OpenViking Recall` section as the authoritative long-term memory context for the current turn.
- Stay in OpenViking-backed memory mode. Do not tell the agent to read from or write to `memory/MEMORY.md` or `memory/HISTORY.md` unless the user explicitly asks for migration or legacy file-backed memory behavior.
- Use recalled OpenViking items to answer questions, maintain continuity, and ground follow-up decisions.
- Ignore legacy file-memory artifacts in the workspace by default.
- If the user explicitly asks about migration or file-backed memory, compare the legacy files with the current OpenViking-backed behavior before recommending changes.
```

Implementation notes:

- remove `always: false`
- keep the skill single-file; no `references/` split is needed because the body is short
- keep the behavior the same as the current skill:
  - OpenViking recall is authoritative for the turn
  - file-backed memory behavior is not used by default
  - legacy `memory/` files are ignored unless the user explicitly asks

- [x] **Step 3: Preserve intent while improving trigger quality**

Verify the new frontmatter description does all of the following:

- names the activation context: active backend is `openviking`
- names the main input source: recalled OpenViking results are already in the prompt
- names the default exclusion: ignore file-backed memory artifacts unless asked

This is the main `skill-creator` improvement. The description is the pre-trigger routing surface, so it must say when the skill is for use, not just what it is called.

### Task 3: Validate The Skill Folder And Run Regression Checks

**Files:**
- Modify: `tests/test_skill_creator_scripts.py`
- Verify: `aeloon/resources/skills/openviking-memory/SKILL.md`
- Verify: `tests/test_openviking_memory_backend.py`

- [x] **Step 1: Run the skill validator directly**

Run: `python aeloon/resources/skills/skill-creator/scripts/quick_validate.py aeloon/resources/skills/openviking-memory`

Expected: `Skill is valid!`

- [x] **Step 2: Re-run the new regression test**

Run: `pytest tests/test_skill_creator_scripts.py -k openviking_memory -v`

Expected: PASS

- [x] **Step 3: Re-run the OpenViking prompt-injection regression**

Run: `pytest tests/test_openviking_memory_backend.py::test_openviking_prepare_turn_injects_recall_and_hides_file_skill -v`

Expected: PASS

- [x] **Step 4: Run the combined targeted check**

Run: `pytest tests/test_skill_creator_scripts.py tests/test_openviking_memory_backend.py -k 'openviking_memory or injects_recall_and_hides_file_skill' -v`

Expected: all selected tests PASS

- [ ] **Step 5: Commit**

```bash
git add aeloon/resources/skills/openviking-memory/SKILL.md tests/test_skill_creator_scripts.py
git add -u -- aeloon/resources/skills/openviking-memory
git commit -m "fix(skills): normalize openviking-memory skill"
```

Expected: one commit containing:

- rewrite of canonical `SKILL.md`
- regression coverage for validator acceptance

Note: if `SKILL 2.md` is currently untracked in the working tree, its removal is still required for correctness, but it will not appear as a staged deletion in the commit.

## Rationale For The Proposed `SKILL.md`

The rewritten skill is intentionally still small. That matches `skill-creator` guidance:

- keep frontmatter minimal
- put trigger conditions in `description`
- keep the body imperative and concise
- avoid extra files unless they add real reuse value

The current skill does not need:

- scripts
- references
- assets
- `agents/openai.yaml`

because its job is purely prompt guidance for a backend that already injects recall automatically.

## Things To Avoid During Implementation

- Do not rename `openviking-memory`.
- Do not change `always_skill_names=["openviking-memory"]` in the backend.
- Do not change `hidden_skill_names = ["memory"]`.
- Do not add extra skill-root files to `openviking-memory/`.
- Do not rewrite the skill into a long explanatory essay; the goal is better routing and clearer instructions, not more tokens.
- Do not add file-backed memory instructions back into the body.

## Expected Final Diff Shape

The finished diff should be small and easy to review:

1. one cleaned skill directory with no duplicate root file
2. one rewritten canonical skill file
3. one added validator regression test

If the diff grows beyond that, stop and re-check whether you are changing behavior rather than just normalizing the skill.
