# Delivery Routing

## Principle

Progress or result delivery should target the **original task-initiating session**, not whichever lab, debug, or control context happened to execute the turn.

## Why this matters

Execution context and delivery context are not always the same:

- a local adapter may run the experiment;
- a debug shell may trigger the turn;
- a helper session may inspect the result.

None of those should silently become the delivery target.

## Recommended model

Treat delivery as explicit metadata attached to a structured turn result:

```json
{
  "factSkeleton": {
    "status": "running",
    "phase": "Collect verification status",
    "latestMeaningfulPreview": "Released v0.3.4 successfully.",
    "reason": "state_changed"
  },
  "shouldSend": true,
  "delivery": {
    "originSession": "<session-key-or-id>",
    "originTarget": "<chat-target-if-needed>"
  }
}
```

## Rules

- `delivery.originSession` identifies the originating OpenClaw session when known.
- `delivery.originTarget` identifies the outbound chat target when direct target routing is needed.
- `shouldSend` is derived from the cadence/decision layer, not forced by the transport layer.
- The transport or main-session layer should send only when `shouldSend == true`.
- If execution happens in a lab/debug context, keep that separate from delivery metadata.
- Any agent-facing adapter/helper must preserve `originSession` / `originTarget` explicitly instead of replacing them with its own runtime context.
- The main-session agent should write the final explanation; routing metadata only says **where** it should go.

## Current prototype scope

Current prototypes attach delivery metadata to the turn result.
They do **not** yet perform the final OpenClaw-native send themselves.
