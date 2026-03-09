# Turn Contract

The happy path is:

1. `turn` produces a small, mechanical result.
2. `delivery-handoff` resolves origin routing and packages a structured origin-session handoff directly from that turn result.
3. `openclaw-agent-call` turns that handoff into `openclaw gateway call agent --params { sessionKey, message, deliver }`.
4. The main-session agent in the originating session consumes that handoff and writes the final user-facing explanation.

`agent-turn-input` still exists, but only as an optional compact helper when you want to inspect the main-agent recommendation object directly.
It is not required on the main path.

## Primary command

```bash
python3 scripts/opencodectl.py turn \
  --base-url <url> \
  --session-id <session-id> \
  --state <state.json> \
  [--control <control.json>] \
  [--origin-session <session>] \
  [--origin-target <target>] \
  [--write]
```

Use `session-turn` only as an explicit alias.
Use lower-level commands only for debugging or focused internal work.

## `turn` boundary

The happy-path `turn` envelope should stay small and auditable.

Allowed top-level fields:

- `factSkeleton`
- `shouldSend`
- `delivery`
- `cadence`

Debug-only field:

- `payload`

Disallowed in the happy path:

- rendered chat prose
- narrative strategy or next-step plans
- helper/lab routing masquerading as delivery
- raw payload dumps by default

### `factSkeleton`

Compact mechanical facts for the main-session agent:

- `status`
- `phase`
- `latestMeaningfulPreview`
- `reason`

### `shouldSend`

Default send/skip recommendation from cadence.
It is guidance, not authored prose.

### `delivery`

Origin-preserving routing metadata:

- `originSession`
- `originTarget`

### `cadence`

Mechanical visibility state:

- `decision`
- `noChange`
- `consecutiveNoChangeCount`
- `lastVisibleUpdateAt`

## `agent-turn-input` boundary

Use this only when the main-session agent wants a compact recommendation object without rendering chat text.
It is an optional adapter, not a required hop in the happy path.

```bash
python3 scripts/opencodectl.py agent-turn-input --input <turn-result.json>
```

Allowed output shape:

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

This layer may:

- preserve send/skip recommendation
- classify the update (`progress`, `heartbeat`, `blocked`, `failed`, `completed`, `silent`)
- expose compact fact fields already present in `factSkeleton`
- preserve origin routing

This layer must not:

- generate polished reply text
- add plans, strategies, or escalation trees
- rewrite routing away from the originating destination
- become the narrative owner

## `delivery-handoff` boundary

Use this when the next layer needs a safe origin-session handoff without making the script layer the narrative owner.
Preferred input is the raw `turn` result; legacy `agent-turn-input` also works.

```bash
python3 scripts/opencodectl.py delivery-handoff --input <turn-result.json>
```

Allowed behavior:

- accept a raw `turn` result and internally compact it into main-agent input
- accept a legacy `agent-turn-input` object without changing its behavior
- add `openclawDelivery`
- resolve origin routing into a mechanical origin-session handoff template when safe
- hold on missing/conflicting origin routing instead of silently rewriting
- stay dry-run by default

## `openclaw-agent-call` bridge

Use this to turn a ready `delivery-handoff` result into the current practical OpenClaw transport call.

```bash
python3 scripts/opencodectl.py openclaw-agent-call --input <delivery-handoff.json>
```

Allowed behavior:

- require `deliveryAction=inject`
- require `routeStatus=ready`
- require `sessionKey` preservation from `routing.originSession`
- forward the structured handoff as the `message` body of `openclaw gateway call agent`
- set `deliver=true` so the originating session stays the narrative surface
- stay dry-run by default unless explicitly executed

This layer must not:

- generate final reply text
- call `message.send`
- prefer helper/debug context over origin routing
- silently choose one route when `originSession` and `originTarget` disagree
- let cron become the primary consumer of turn output

## Main-agent consumption order

1. `shouldSend`
2. `routing`
3. `openclawDelivery` when origin-session injection closure is needed
4. facts
5. cadence

Consumption rules:

- The injected payload is **internal runtime context**. The user does **not** see it.
- Translate the facts into normal user language; do not restate headers, event tags, JSON, or watcher/debug phrasing.
- Do not mirror every runtime event with a visible chat reply.
- For the same task cluster, prefer one useful progress update and one final completion/status update.
- Repeated `completed`, same-state, or no-new-user-value updates should usually stay silent.
- If you do reply, structure it as: (1) what was just done, (2) what evidence was seen, (3) what that means for the user/task.

Default mapping:

- `shouldSend=false` -> usually stay silent
- `shouldSend=true` + running + changed -> brief progress update if it adds user value
- `shouldSend=true` + running + no-change -> usually stay silent unless cadence specifically calls for a helpful heartbeat
- `shouldSend=true` + blocked/failed/completed -> explicit status update, but avoid repeated completion replies for the same outcome

Primary delivery path:

`turn -> delivery-handoff -> openclaw-agent-call -> openclaw gateway call agent(sessionKey=originSession) -> main-session agent decides visible reply`

The transported message already carries the compact mechanical handoff object.
It also carries a small consumption policy so the originating main-session agent treats it as internal runtime input and, when replying visibly, continues the task conversation naturally instead of narrating transport mechanics.
No separate script consumer is required on the happy path.


## Debug path

Use `explain-turn` when debugging why a turn surfaced or stayed quiet:

```bash
python3 scripts/opencodectl.py explain-turn --input <turn-result.json>
```

If you need lower-level scripts, treat them as implementation detail, not the routine control surface.
