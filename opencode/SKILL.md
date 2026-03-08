---
name: opencode
description: Design, iterate, and operate an OpenCode execution/monitoring workflow for OpenClaw using a main-session-centered model with visible progress, trigger handling, control-input interpretation, shared state continuity, and cadence-controlled no-change updates. Use when building or refining the OpenCode skill, its state model, trigger flow, visible progress policy, or experimentation workflow.
---

# Opencode

Keep the main session as the primary decision surface and the primary user-visible progress surface.

## Core rules

- Treat timed triggers and event triggers as **input layers**, not as the main execution owner.
- Treat user chat input as **high-priority control input**.
- Prefer `executionMode = main_session_centered` unless a later design revision explicitly changes it.
- Keep environment-specific paths, hostnames, credentials, and local lab details **out of the skill package and out of committed docs**.

## Read order

1. Read `references/execution-model.md` for the overall architecture.
2. Read `references/control-inputs.md` when changing how user instructions affect execution.
3. Read `references/state-flow.md` when changing trigger/state/no-change behavior.
4. Read `references/control-surface.md` for the unified script entrypoint and the intended exposed control surface.

## Use this skill for four kinds of work

### 1. Design work
Use this skill to refine:
- layer responsibilities;
- control-state modeling;
- trigger/data-flow behavior;
- visible progress policy;
- no-change handling.

### 2. Runtime behavior definition
Use this skill to decide:
- what must happen in the main session;
- what can run in the background;
- when to produce visible updates;
- when to stay silent;
- when to escalate.

### 3. Prototype logic locally
Default to the unified entrypoint:
- `scripts/opencodectl.py`

Use lower-level scripts only when debugging or refining internals.

### 4. Experimentation support
Use this skill to prepare generic experiment flows and decision logic.
Do **not** store machine-specific lab details in the skill itself.

## Operating workflow

### Step 1: Start from the main session
When refining or operating the system, treat the main session as the default place where:
- goals are interpreted;
- user corrections are absorbed;
- key decisions are made;
- visible progress is reported.

### Step 2: Normalize user control input
Interpret user messages as control state when they change:
- goals;
- constraints;
- progress visibility expectations;
- pause/resume/stop behavior;
- discussion/execution coupling.

Prefer the simplified control representation unless there is a strong reason to expand it:

```json
{
  "executionMode": "main_session_centered"
}
```

### Step 3: Consume compact trigger input
Do not reread full transcripts by default.
Instead, consume compact deltas from:
- timed triggers;
- event triggers;
- recent status/checkpoint/todo changes;
- shared state anchors.

### Step 4: Decide visible behavior explicitly
For each decision opportunity, choose one of these broad outcomes:
- stay silent;
- emit a short visible progress update;
- take a visible corrective action;
- escalate to a heavier execution or analysis unit.

No-change still enters the decision layer.
The question is not “did nothing happen?” but “does the user need a visible update or action now?”

### Step 5: Keep heavy work as assistance, not as the narrative owner
Background workers or subagents may perform heavy work, but:
- they should not become the primary decision center;
- they should not own the user-facing narrative;
- important results should flow back into the main session quickly.

### Step 6: Keep the skill package clean
Put only runtime-relevant reusable material in the skill package:
- `SKILL.md`
- `references/`
- `scripts/`
- `assets/` when needed

Keep higher-level design docs, iteration archives, and environment-specific experiment notes outside the skill package.

## Current packaged resources

### Primary exposed surface
- `references/control-surface.md` — unified control surface and command patterns.
- `scripts/opencodectl.py` — unified operational entrypoint for state init/show, local cycle, remote snapshot, and remote cycle.

### Supporting references
- `references/execution-model.md` — system model and layer responsibilities.
- `references/control-inputs.md` — how to interpret user input as control state.
- `references/state-flow.md` — shared state, trigger flow, and no-change handling.
- `references/api-surface.md` — current known OpenCode API surface used by the prototypes.

### Internal implementation scripts
- `scripts/opencode_control_state.py` — local helper for iterating on state/control merges.
- `scripts/opencode_decision_gate.py` — local helper for prototyping visible-update gating and no-change cadence behavior.
- `scripts/opencode_cycle.py` — single-cycle prototype that merges control input, observation input, and visible-update decisions into one experiment flow.
- `scripts/opencode_api_client.py` — minimal OpenCode API client for session/status/todo/question/permission access.
- `scripts/opencode_snapshot.py` — compact snapshot builder that turns remote OpenCode state into main-session-consumable input.
- `scripts/opencode_remote_cycle.py` — fetch remote OpenCode state, derive a normalized observation, and run one decision cycle against local shared state.

## Packaging guidance

Only keep runtime-relevant instructions and reusable resources inside the skill.

Do **not** put high-level iteration history, archive proposals, or environment-specific experiment notes inside the skill package. Keep those at the repo level.
