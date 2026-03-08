# Delivery Handoff

Use this after `agent-turn-input` when the main agent wants an **origin-session systemEvent handoff** that points back to the original task-initiating OpenClaw session without turning the script layer into a renderer.

## Command

```bash
python3 scripts/opencodectl.py delivery-handoff --input <agent-turn-input.json>
```

Default behavior is **dry-run metadata only**.
This command does **not** inject or send anything.
It only resolves whether the turn can be represented as a structured `systemEvent` envelope for the original OpenClaw session.

Optional metadata-only switch:

```bash
python3 scripts/opencodectl.py delivery-handoff \
  --input <agent-turn-input.json> \
  --live-ready
```

`--live-ready` only flips the `dryRun` flag in the output.
It still does **not** inject anything.
Use it only when a downstream orchestrator already knows how to inject a `systemEvent` into the originating session.

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
- `primaryDelivery`
- `cronFallback`
- `systemEventTemplate`
- `watchdogCronTemplate`

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

- the compact `agent-turn-input`
- the explicit delivery policy (`primary=origin_session_system_event`)
- the explicit statement that cron is only `watchdog_only`

This is intentionally **not user-facing prose**.
The originating OpenClaw session consumes it as a system event, then the main-session agent decides whether/how to explain it to the user.

### `watchdogCronTemplate`

When `routeStatus=ready`, the adapter also emits a **fallback-only** cron-shaped template:

```json
{
  "sessionTarget": "main",
  "sessionKey": "origin-session-example",
  "payload": {
    "kind": "systemEvent",
    "text": "OPENCODE_ORIGIN_SESSION_SYSTEM_EVENT_V1\n{ ... }"
  }
}
```

Treat this as a watchdog/safety-net template only.
It exists so a separate scheduler can re-inject the same structured handoff if the primary direct injection path is unavailable.
It is **not** the primary consumption path.

## Resolution rules

1. Require `routing.originSession` for the primary path.
2. Use `routing.originTarget` only as a cross-check, not as the primary delivery route.
3. If both resolve and they disagree, emit `routeStatus=conflict` and `deliveryAction=hold`.
4. Never replace origin routing with the current helper/debug context.
5. Never downgrade the primary path from origin-session injection to direct user-message sending inside this layer.

## Cron role

Cron is relegated to **fallback/watchdog** use:

- acceptable: low-frequency re-injection of the same structured system event when the direct injector is unavailable
- acceptable: watchdog checks that verify the origin session still receives decision opportunities
- not acceptable: making cron the normal, primary consumer of turn output
- not acceptable: turning cron payload text into user-facing narrative prose

The primary model remains:

`turn -> agent-turn-input -> delivery-handoff -> inject structured systemEvent into originating session -> origin-session-consume -> main-session agent decides visible reply`

## Boundary

This layer may:

- resolve origin routing into an origin-session `systemEvent` template
- preserve the original raw routing fields
- detect routing conflicts and refuse silent rewrites
- emit a watchdog-only cron fallback template using the same structured payload
- stay dry-run by default

This layer must not:

- generate final user-facing prose
- decide the actual message wording
- call `message.send`
- silently route to the current lab/debug session
- hide origin conflicts by picking one route automatically
- promote cron from watchdog to primary delivery path
