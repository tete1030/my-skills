---
name: opencode
description: Orchestrate and supervise OpenCode sessions in OpenClaw with a main-session-centered execution model, visible progress updates, trigger handling, and structured state flow. Use when designing, iterating, or operating the OpenCode execution/monitoring skill and its related workflow.
---

# Opencode

Keep the main session as the primary decision surface and user-visible progress surface.

## Structure

- Put core agent instructions in this `SKILL.md`.
- Put runtime-relevant detailed material in `references/`.
- Put reusable scripts in `scripts/`.
- Keep high-level design docs and iteration notes outside the skill package, under the repo-level `design/` directory.

## Current direction

- Treat timed triggers and event triggers as structured input layers.
- Treat user chat input as high-priority control input.
- Prefer `executionMode = main_session_centered` unless a later design revision changes it.
