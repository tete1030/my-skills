# Execution Model

## Principle

Use a **main-session-centered** execution model:

- the main session is the default decision center;
- the main session is the default visible progress surface;
- background components provide input and assistance, not the primary user experience.

## Layers

### Main Session Agent
Responsible for:
- understanding user goals;
- making key decisions;
- reporting visible progress;
- integrating discussion with execution;
- deciding whether to continue, correct, pause, stop, or escalate.

### Trigger Layer
Responsible for:
- timed triggers;
- event triggers;
- OpenCode API state changes;
- turning raw observations into compact, structured deltas.

### State Store
Responsible for:
- continuity anchors;
- deduplication;
- no-change compression;
- recent decisions and visible-update pacing.

### Optional Subagent / Background Worker
Responsible for:
- heavy execution;
- long-running operations;
- complex analysis.

These units may do work, but they should not become the main narrative or decision owner.

## Default mode

```json
{
  "executionMode": "main_session_centered"
}
```
