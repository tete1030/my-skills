# Delivery Handoff

Use this after `agent-turn-input` when the main agent wants an **OpenClaw-native routing template** that points back to the original destination without turning the script layer into a renderer.

## Command

```bash
python3 scripts/opencodectl.py delivery-handoff --input <agent-turn-input.json>
```

Default behavior is **dry-run metadata only**.
This command does **not** send a message.
It only resolves origin routing into a safe `message.send` template when possible.

Optional metadata-only switch:

```bash
python3 scripts/opencodectl.py delivery-handoff \
  --input <agent-turn-input.json> \
  --live-ready
```

`--live-ready` only flips the `dryRun` flag in the output.
It still does **not** send anything.
Use it only when a downstream orchestrator already has the final user-facing text and is explicitly allowed to send.

## Input expectation

`delivery-handoff` expects the output of:

```bash
python3 scripts/opencodectl.py agent-turn-input --input <turn-result.json>
```

That means the input is already constrained to:

- send/skip recommendation
- update classification
- compact facts
- cadence
- origin-preserving routing

## Output shape

It preserves the `agent-turn-input` object and adds one field:

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
- `toolRequestTemplate`

### `deliveryAction`

- `handoff` — safe route resolved and this turn should surface
- `hold` — this turn should surface, but origin routing is unresolved or conflicting
- `skip` — this turn should stay silent

### `routeStatus`

- `ready` — route resolved into an OpenClaw-native `message.send` template
- `unresolved` — neither `originTarget` nor `originSession` could be resolved
- `conflict` — both resolved, but they disagree; do not silently rewrite

### `toolRequestTemplate`

When `routeStatus=ready`, the adapter emits a **template** for the OpenClaw `message.send` tool:

```json
{
  "tool": "message.send",
  "action": "send",
  "channel": "telegram",
  "target": "-1003607560565",
  "threadId": "3348"
}
```

This is intentionally **not executable yet** because the script layer does not author final chat text.
The main-session agent or an explicit downstream orchestrator must still provide the final message body.

## Resolution rules

1. Prefer `routing.originTarget` when it can be parsed.
2. Fall back to `routing.originSession` when `originTarget` is absent or unparsable.
3. If both resolve and they disagree, emit `routeStatus=conflict` and `deliveryAction=hold`.
4. Never replace origin routing with the current helper/debug context.

## Boundary

This layer may:

- resolve origin routing into OpenClaw-native delivery parameters
- preserve the original raw routing fields
- detect routing conflicts and refuse silent rewrites
- stay dry-run by default

This layer must not:

- generate final user-facing prose
- decide the actual message wording
- call `message.send` itself
- silently route to the current lab/debug session
- hide origin conflicts by picking one route automatically
