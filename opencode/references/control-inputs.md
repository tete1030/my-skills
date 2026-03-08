# Control Inputs

## Concept

User control input is an **internal abstraction**, not an extra UI.

The user continues to speak in normal chat. The system interprets certain messages as high-priority control state updates.

Examples:
- change the goal;
- add constraints;
- change reporting style;
- pause / resume / stop;
- require execution to stay visible in the main session;
- ask to remember a principle.

## Priority

User control input should outrank:
- background default behavior;
- ordinary timed-trigger heuristics;
- ordinary event-trigger heuristics.

## Modeling

Avoid multiple overlapping booleans when one higher-level mode expresses the idea more clearly.

Preferred simplified representation:

```json
{
  "executionMode": "main_session_centered"
}
```

Expanded orthogonal representation (optional later):

```json
{
  "executionCenter": "main_session",
  "progressVisibility": "visible",
  "discussionCoupling": "tight"
}
```

## Flow

1. User sends a message in chat.
2. Main session interprets it into structured control state.
3. State store persists the control state.
4. Later triggers and execution steps must obey the updated control state.
