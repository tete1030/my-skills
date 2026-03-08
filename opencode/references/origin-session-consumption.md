# Origin-Session Consumption

Use this after `delivery-handoff` has already produced and injected a structured origin-session `systemEvent`.

This reference defines the **next runtime intake layer** for the originating OpenClaw session:

- recognize the event as an opencode-origin handoff
- validate that the payload is still mechanical
- preserve the original session routing explicitly
- hand compact facts/cadence/routing to the main-session agent
- keep the final explanation owned by the main-session agent, not by the script layer

## Command

```bash
python3 scripts/opencodectl.py origin-session-consume --input <system-event.json>
```

Optional strict session check:

```bash
python3 scripts/opencodectl.py origin-session-consume \
  --input <system-event.json> \
  --expected-session <origin-session-key>
```

The command accepts either:

- a raw `systemEvent` payload
- an object with `payload.kind=systemEvent`
- a `delivery-handoff` result containing `openclawDelivery.systemEventTemplate.payload`

## Recognition rule

The originating session should only treat an incoming system event as an opencode handoff when all of the following are true:

1. payload `kind` is exactly `systemEvent`
2. payload `text` begins with `OPENCODE_ORIGIN_SESSION_SYSTEM_EVENT_V1`
3. the JSON body decodes into the expected envelope shape
4. the envelope `kind` is `opencode_origin_session_handoff`
5. the envelope `version` is `v1`

If any of those checks fail, this path should not reinterpret the event as opencode runtime input.

## Output shape

`origin-session-consume` returns two fields:

- `agentInput`
- `runtimeConsumption`

### `agentInput`

This is the validated compact object already prepared for the main-session agent:

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

The consumer does **not** rewrite or expand it into prose.

### `runtimeConsumption`

Allowed fields:

- `kind`
- `deliveryKind`
- `consumeAction`
- `reason`
- `decisionOwner`
- `narrativeOwner`
- `preserveOrigin`
- `expectedSession`
- `sessionCheck`
- `deliveryPolicy`

### `consumeAction`

- `offer_decision` — the originating session may offer this compact input to the main-session agent for a visible/silent decision
- `hold` — the event was recognized, but origin-session checks failed and it must not be silently rerouted or consumed as normal

### `sessionCheck`

- `matched` — `routing.originSession` matches `--expected-session`
- `not_checked` — no explicit session was supplied, but the handoff was otherwise recognized
- `mismatch` — `routing.originSession` and `--expected-session` disagree; do not silently reroute
- `missing_origin_session` — the compact payload lacks an origin session and should be held

## Intended runtime behavior

The intended runtime chain is now:

`turn -> agent-turn-input -> delivery-handoff -> inject systemEvent into origin session -> origin-session-consume -> main-session agent decides visible reply`

That means:

- the script layer still emits facts + cadence + routing
- the runtime intake layer only recognizes/unwraps/validates
- the main-session agent still owns the final explanation or silence

## Boundary

This layer may:

- recognize the stable opencode handoff header
- decode the structured JSON envelope
- verify optional expected-session matching
- preserve the original compact routing metadata
- expose a mechanical `consumeAction` to the main-session runtime

This layer must not:

- generate user-facing prose
- silently replace the origin session with the current helper/debug session
- reinterpret the event as a direct send instruction
- downgrade the main-session agent into a passive renderer of script-authored text
