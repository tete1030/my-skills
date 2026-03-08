---
name: opencode
description: Design, iterate, and operate an OpenCode execution/monitoring workflow for OpenClaw using a main-session-centered model with visible progress, trigger handling, control-input interpretation, shared state continuity, cadence-controlled no-change updates, and structured turn outputs that separate mechanical facts from final user-facing explanation. Use when building or refining the OpenCode skill, its state model, trigger flow, visible progress policy, delivery routing, or experimentation workflow.
---

# Opencode

Keep the main session as the primary decision surface and the primary user-visible progress surface.

## Core rules

- Treat timed triggers and event triggers as **input layers**, not as the main execution owner.
- Treat user chat input as **high-priority control input**.
- Prefer `executionMode = main_session_centered` unless a later design revision explicitly changes it.
- Treat `opencodectl.py turn` as the **happy-path structured turn output**.
- Let mechanical/script layers emit facts, cadence, and routing metadata; let the main-session agent write the final explanation.
- Keep environment-specific paths, hostnames, credentials, and local lab details **out of the skill package and out of committed docs**.

## Read order

1. Read `references/execution-model.md` for the overall architecture.
2. Read `references/control-inputs.md` when changing how user instructions affect execution.
3. Read `references/state-flow.md` when changing trigger/state/no-change behavior.
4. Read `references/control-surface.md` for the unified script entrypoint and the intended exposed control surface.
5. Read `references/turn-output.md` when adjusting the boundary between structured turn facts and final main-session explanation.
6. Read `references/agent-consumption.md` when adjusting how the main-session agent should consume a turn result and choose final user-facing output.
7. Read `references/reporting-policy.md` when adjusting how decision results should guide visible updates in the main session.
8. Read `references/delivery-routing.md` when adjusting where updates should be delivered.

## Use this skill for four kinds of work

### 1. Design work
Use this skill to refine:
- layer responsibilities;
- control-state modeling;
- trigger/data-flow behavior;
- visible progress policy;
- no-change handling;
- structured turn output boundaries.

### 2. Runtime behavior definition
Use this skill to decide:
- what must happen in the main session;
- what can run in the background;
- when to produce visible updates;
- when to stay silent;
- when to escalate;
- which parts are mechanical facts versus final explanation.

### 3. Prototype logic locally
Default to the unified entrypoint:
- `scripts/opencodectl.py`

Treat `opencodectl.py turn` as the **primary happy path** for real operation.
Use its optional `--control` input when the same chat turn also updates execution policy or control state. That control should affect the decision pass itself, not just the final result envelope.
When available, pass origin delivery metadata so updates are routed back to the original task-initiating session rather than the current execution context.
Use lower-level commands only when debugging or refining internals. For turn-level debugging, prefer `opencodectl.py explain-turn`; for the main-session consumer boundary, prefer `opencodectl.py agent-turn-input`; and use raw payload output only in explicit debug flows.

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

### Step 5: Emit structured turn facts deliberately
When a turn completes, prefer a structured result that surfaces:
- normalized status;
- current phase;
- latest meaningful preview;
- cadence / send-or-skip reason;
- originating-session delivery metadata.

The main-session agent should use that structure to write the final user-facing explanation.
When a compact consumption hint is useful, transform the turn result into an agent-facing recommendation/input object rather than back into rendered chat prose.
Do **not** make the renderer the primary owner of the conversation narrative.

### Step 6: Keep heavy work as assistance, not as the narrative owner
Background workers or subagents may perform heavy work, but:
- they should not become the primary decision center;
- they should not own the user-facing narrative;
- important results should flow back into the main session quickly.

### Step 7: Keep the skill package clean
Put only runtime-relevant reusable material in the skill package:
- `SKILL.md`
- `references/`
- `scripts/`
- `assets/` when needed

Keep higher-level design docs, iteration archives, and environment-specific experiment notes outside the skill package.

## Current packaged resources

### Primary exposed surface
- `references/control-surface.md` — unified control surface and command patterns, with `turn` as the primary happy path.
- `references/turn-output.md` — structured turn envelope and the boundary between mechanical facts and final explanation.
- `scripts/opencodectl.py` — unified operational entrypoint for the happy-path turn workflow plus lower-level state/cycle/debug commands.

### Supporting references
- `references/execution-model.md` — system model and layer responsibilities.
- `references/control-inputs.md` — how to interpret user input as control state.
- `references/state-flow.md` — shared state, trigger flow, and no-change handling.
- `references/api-surface.md` — current known OpenCode API surface used by the prototypes.
- `references/agent-consumption.md` — how the main-session agent should consume turn results, respect `shouldSend`, and preserve delivery semantics without turning scripts into narrators.
- `references/reporting-policy.md` — how structured turn results should guide concise visible main-session updates.
- `references/delivery-routing.md` — how originating-session delivery should be modeled and preserved.

### Internal implementation scripts
- `scripts/opencode_control_state.py` — local helper for iterating on state/control merges.
- `scripts/opencode_decision_gate.py` — local helper for prototyping visible-update gating and no-change cadence behavior.
- `scripts/opencode_cycle.py` — single-cycle prototype that merges control input, observation input, and visible-update decisions into one experiment flow.
- `scripts/opencode_api_client.py` — minimal OpenCode API client for session/status/todo/question/permission access.
- `scripts/opencode_snapshot.py` — compact snapshot builder that turns remote OpenCode state into main-session-consumable input.
- `scripts/opencode_remote_cycle.py` — fetch remote OpenCode state, derive a normalized observation, and run one decision cycle against local shared state.
- `scripts/opencode_scenario.py` — replay a multi-step local scenario through the decision loop for experiment design and regression checks.
- `scripts/opencode_session_turn.py` — combine remote-cycle output into one higher-level turn result with fact skeleton, cadence, and delivery metadata.
- `scripts/opencode_explain_turn.py` — summarize why a turn emitted or skipped a visible update and expose the structured turn facts for debugging.
- `scripts/opencode_agent_turn_input.py` — transform a structured turn result into compact main-agent input/recommendation without rendering final chat prose.

## Packaging guidance

Only keep runtime-relevant instructions and reusable resources inside the skill.

Do **not** put high-level iteration history, archive proposals, or environment-specific experiment notes inside the skill package.
