---
name: opencode
description: Design, iterate, and operate an OpenCode execution/monitoring workflow for OpenClaw using a main-session-centered model with visible progress, trigger handling, control-input interpretation, and shared state continuity. Use when building or refining the OpenCode skill, control flow, state model, or experimentation workflow.
---

# Opencode

Keep the main session as the primary decision surface and the primary user-visible progress surface.

## Core rules

- Treat timed triggers and event triggers as **input layers**, not as the main execution owner.
- Treat user chat input as **high-priority control input**.
- Prefer `executionMode = main_session_centered` unless a later design revision explicitly changes it.
- Keep environment-specific paths, hostnames, credentials, and local lab details **out of the skill package and out of committed docs**.

## Working layout

- `references/execution-model.md` — system model and layer responsibilities.
- `references/control-inputs.md` — how to interpret user input as control state.
- `references/state-flow.md` — shared state, trigger flow, and no-change handling.
- `scripts/opencode_control_state.py` — local helper for iterating on state/control merges.

## How to use this skill while iterating

1. Read `references/execution-model.md` for the overall model.
2. Read `references/control-inputs.md` when changing how user instructions affect execution.
3. Read `references/state-flow.md` when changing trigger/state/no-change behavior.
4. Use `scripts/opencode_control_state.py` to prototype control-state evolution locally.

## Packaging guidance

Only keep runtime-relevant instructions and reusable resources inside the skill.

Do **not** put high-level iteration history, archive proposals, or environment-specific experiment notes inside the skill package. Keep those at the repo level.
