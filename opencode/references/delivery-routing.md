# Delivery Routing

## Principle

Progress or result delivery should target the **original task-initiating session**, not whichever lab, debug, or control context happened to execute the turn.

## Why this matters

Execution context and delivery context are not always the same:

- a local lab adapter may run the experiment;
- a debug shell may trigger the turn;
- a helper session may inspect the result.

None of those should silently become the delivery target.

## Recommended model

Treat delivery as explicit metadata attached to a turn result:

```json
{
  "originSession": "<session-key-or-id>",
  "originTarget": "<chat target if applicable>",
  "shouldSend": true,
  "message": "<rendered update>"
}
```

## Rules

- `originSession` identifies the originating OpenClaw session when known.
- `originTarget` identifies the outbound chat target when direct target routing is needed.
- `shouldSend` is derived from the decision/rendering layer, not forced by the transport layer.
- The transport layer should send only when `shouldSend == true`.
- If execution happens in a lab/debug context, keep that separate from delivery metadata.

## Current prototype scope

Current prototypes attach delivery metadata to the turn result.
They do **not** yet perform the final OpenClaw-native send themselves.
