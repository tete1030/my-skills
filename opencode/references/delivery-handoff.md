# Delivery Handoff

Use this when the main agent wants an **origin-session handoff** that points back to the original task-initiating OpenClaw session without turning the script layer into a renderer.

The handoff remains a structured `systemEvent`-shaped envelope, but the **current practical delivery path** is:

`python watcher -> delivery-handoff -> openclaw-agent-call -> openclaw gateway call agent --params { sessionKey, message, deliver } -> originating session`

That means the skill still emits mechanical facts/cadence/routing plus a transport-ready envelope, while the delivery bridge uses the existing OpenClaw CLI/Gateway agent path to inject that handoff into the originating session.
The main-session agent still owns the final user-facing explanation.

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
This command does **not** inject or send anything by itself.
It only resolves whether the turn can be represented as a structured envelope for the original OpenClaw session.

The usual next step is the dedicated bridge helper:

```bash
python3 scripts/opencodectl.py openclaw-agent-call --input <delivery-handoff.json>
```

That helper also defaults to dry-run and prints the exact `openclaw gateway call agent` invocation shape it would use.

Optional metadata-only switch:

```bash
python3 scripts/opencodectl.py delivery-handoff \
  --input <turn-result.json> \
  --live-ready
```

`--live-ready` only flips the `dryRun` flag in the output.
It still does **not** inject anything.
Use it when a downstream bridge is allowed to execute the prepared delivery path.

Example live path with temp files:

```bash
python3 scripts/opencodectl.py delivery-handoff --input <turn-result.json> --live-ready > handoff.json
python3 scripts/opencodectl.py openclaw-agent-call --input handoff.json --execute
```

Tighter CLI path with no temp file:

```bash
python3 scripts/opencodectl.py delivery-handoff --input <turn-result.json> --live-ready \
  | python3 scripts/opencodectl.py openclaw-agent-call --input - --execute
```

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

- `inject` — safe origin-session handoff resolved and this turn should surface
- `hold` — this turn should surface, but origin-session delivery is unresolved or conflicting
- `skip` — this turn should stay silent

### `routeStatus`

- `ready` — origin session is explicit and safe for the primary `sessionKey`-based delivery path
- `missing_origin_session` — the originating OpenClaw session key is missing, so the primary path cannot be built
- `conflict` — both origin session and origin target resolve, but they disagree; do not silently rewrite

### `systemEventTemplate`

When `routeStatus=ready`, the adapter emits the mechanical handoff template that the bridge can turn into the **practical** origin-session delivery call:

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
- a compact consumption policy that tells the main-session agent to treat the payload as internal runtime input and, if it replies visibly, continue the conversation naturally instead of talking about handoff mechanics

This is intentionally **not user-facing prose**.
The bridge may forward it as the body of an `openclaw gateway call agent` request, but that does not change ownership: the originating main-session agent still decides whether/how to explain it to the user.

### Bridge to OpenClaw CLI

To turn a ready handoff into the current practical transport call:

```bash
python3 scripts/opencodectl.py openclaw-agent-call --input <delivery-handoff.json>
```

Default output is still dry-run.
It prints a structured plan containing the exact CLI argv / shell command for:

```bash
openclaw gateway call agent --json --params '{"sessionKey":"...","message":"...","deliver":true}'
```

Execution is explicit:

```bash
python3 scripts/opencodectl.py openclaw-agent-call --input <delivery-handoff.json> --execute
```

Safety rules for the bridge:

- require `deliveryAction=inject`
- require `routeStatus=ready`
- require `systemEventTemplate.sessionKey == routing.originSession`
- require the decoded envelope to preserve the same `originSession`
- refuse silent reroute
- stay dry-run unless explicitly asked to execute

## Resolution rules

1. Require `routing.originSession` for the primary path.
2. Use `routing.originTarget` only as a cross-check, not as the primary delivery route.
3. If both resolve and they disagree, emit `routeStatus=conflict` and `deliveryAction=hold`.
4. Never replace origin routing with the current helper/debug context.
5. Never downgrade the primary path from origin-session injection to direct user-message sending inside this layer.


## Boundary

This layer may:

- resolve origin routing into an origin-session mechanical handoff template
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
