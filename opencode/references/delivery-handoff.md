# Delivery Handoff

Use this when the main agent wants an **origin-session systemEvent handoff** that points back to the original task-initiating OpenClaw session without turning the script layer into a renderer.

## Command

Preferred input:

```bash
python3 scripts/opencodectl.py delivery-handoff --input <turn-result.json>
```

Legacy optional input:

```bash
python3 scripts/opencodectl.py delivery-handoff --input <agent-turn-input.json>
```

Default behavior is **dry-run metadata only**.
This command does **not** inject or send anything.
It only resolves whether the turn can be represented as a structured `systemEvent` envelope for the original OpenClaw session.

Optional metadata-only switch:

```bash
python3 scripts/opencodectl.py delivery-handoff \
  --input <turn-result.json> \
  --live-ready
```

`--live-ready` only flips the `dryRun` flag in the output.
It still does **not** inject anything.
Use it only when a downstream orchestrator already knows how to inject a `systemEvent` into the originating session.

## Input expectation

`delivery-handoff` prefers the output of:

```bash
python3 scripts/opencodectl.py turn ...
```

That means the primary input is already constrained to:

- send/skip recommendation
- compact fact skeleton
- cadence
- origin-preserving delivery

Internally, `delivery-handoff` compacts that turn result into the same small main-agent input shape that `agent-turn-input` exposes.
If you already have a legacy `agent-turn-input` object, it is still accepted.

## Output shape

It emits the compact main-agent input and adds one field:

- `openclawDelivery`

So the full output shape remains:

- `shouldSend`
- `action`
- `updateType`
- `priority`
- `style`
- `reason`
- `narrativeOwner`
- `mentionFields`
- `facts`
- `cadence`
- `routing`
- `openclawDelivery`

### `openclawDelivery`

Allowed fields:

- `kind`
- `dryRun`
- `deliveryAction`
- `routeStatus`
- `reason`
- `resolutionSource`
- `preserveOrigin`
- `requiresNarrative`
- `primaryDelivery`
- `systemEventTemplate`

### `deliveryAction`

- `inject` — safe origin-session systemEvent template resolved and this turn should surface
- `hold` — this turn should surface, but origin-session injection is unresolved or conflicting
- `skip` — this turn should stay silent

### `routeStatus`

- `ready` — origin session is explicit and safe for systemEvent injection
- `missing_origin_session` — the originating OpenClaw session key is missing, so the primary path cannot be built
- `conflict` — both origin session and origin target resolve, but they disagree; do not silently rewrite

### `systemEventTemplate`

When `routeStatus=ready`, the adapter emits the **primary** origin-session injection template:

```json
{
  "sessionKey": "origin-session-example",
  "payload": {
    "kind": "systemEvent",
    "text": "OPENCODE_ORIGIN_SESSION_SYSTEM_EVENT_V1\n{ ... }"
  }
}
```

The `text` is a structured JSON envelope prefixed with a stable header line.
It carries:

- the compact main-agent input
- the explicit delivery policy (`primary=origin_session_system_event`)

This is intentionally **not user-facing prose**.
The originating OpenClaw session can hand the decoded compact object straight to the main-session agent, which still decides whether/how to explain it to the user.


## Resolution rules

1. Require `routing.originSession` for the primary path.
2. Use `routing.originTarget` only as a cross-check, not as the primary delivery route.
3. If both resolve and they disagree, emit `routeStatus=conflict` and `deliveryAction=hold`.
4. Never replace origin routing with the current helper/debug context.
5. Never downgrade the primary path from origin-session injection to direct user-message sending inside this layer.


## Boundary

This layer may:

- resolve origin routing into an origin-session `systemEvent` template
- preserve the original raw routing fields
- compact the turn result into the small main-agent input shape
- detect routing conflicts and refuse silent rewrites
- stay dry-run by default

This layer must not:

- generate final user-facing prose
- decide the actual message wording
- call `message.send`
- silently route to the current lab/debug session
- hide origin conflicts by picking one route automatically
