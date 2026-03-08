# Runtime Loop

Keep the runtime model small:

- the **main session** owns decisions and user-visible explanation;
- triggers provide **decision opportunities**;
- scripts provide **mechanical facts**;
- cadence decides whether this turn should surface or stay silent;
- delivery metadata points back to the original task-initiating destination.

## Default model

```json
{
  "executionMode": "main_session_centered"
}
```

Use this unless there is a strong reason to change the design.

## Control input

User control input is an internal interpretation layer, not a separate UI.
Normal chat messages may update control state when they change:

- goals
- constraints
- pause / resume / stop state
- visibility expectations

That control should outrank ordinary timed/event-trigger defaults.

## Turn flow

1. A user message, timed trigger, or event trigger creates a decision opportunity.
2. Optional user control input is normalized into control state.
3. `turn` reads compact remote/local state instead of re-reading full history.
4. The cadence layer decides `visible_update` vs `silent_noop`.
5. The turn result carries fact skeleton + cadence + origin routing.
6. The main-session agent decides whether/how to explain it to the user.

## State expectations

The shared state only needs enough continuity to support deduplication and cadence. Typical fields:

- `executionMode`
- `status`
- `phase`
- `lastSeenMessageId`
- `lastCompletedMessageId`
- `lastTodoDigest`
- `lastUpdatedMs`
- `lastDecision`
- `lastVisibleUpdateAt`
- `consecutiveNoChangeCount`
- `lastNotifiedState`

## Cadence rules

No-change still goes through the decision layer.
The question is not "did nothing happen?" but "does the user need a visible update now?"

Recommended pattern:

- short-interval no-change -> usually silent
- accumulated no-change -> short heartbeat/update-in-place
- blocked / failed / completed -> explicit visible update
- clear state change -> short progress update

## Delivery rule

Execution context and delivery context are different things.
Always preserve origin routing in the structured turn result:

- `delivery.originSession`
- `delivery.originTarget`

Do not silently replace them with the current lab/debug/helper context.

## Background work

Subagents and background workers may do heavy work.
They do not own the conversation narrative.
Important results should flow back into the main session quickly.
