# Turn Contract

The happy path is:

1. `turn` produces a small, mechanical result.
2. `delivery-handoff` resolves origin routing and packages a structured origin-session `systemEvent` handoff directly from that turn result.
3. The main-session agent consumes that handoff and writes the final user-facing explanation.

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

Use this when the next layer needs a safe origin-session `systemEvent` handoff without making the script layer the narrative owner.
Preferred input is the raw `turn` result; legacy `agent-turn-input` also works.

```bash
python3 scripts/opencodectl.py delivery-handoff --input <turn-result.json>
```

Allowed behavior:

- accept a raw `turn` result and internally compact it into main-agent input
- accept a legacy `agent-turn-input` object without changing its behavior
- add `openclawDelivery`
- resolve origin routing into an origin-session `systemEvent` template when safe
- hold on missing/conflicting origin routing instead of silently rewriting
- stay dry-run by default

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

Default mapping:

- `shouldSend=false` -> usually stay silent
- `shouldSend=true` + running + changed -> brief progress update
- `shouldSend=true` + running + no-change -> brief heartbeat
- `shouldSend=true` + blocked/failed/completed -> explicit status update

Primary delivery path:

`turn -> delivery-handoff -> inject structured systemEvent into originating session -> main-session agent decides visible reply`

The injected `systemEvent` already carries the compact mechanical handoff object.
No separate script consumer is required on the happy path.


## Debug path

Use `explain-turn` when debugging why a turn surfaced or stayed quiet:

```bash
python3 scripts/opencodectl.py explain-turn --input <turn-result.json>
```

If you need lower-level scripts, treat them as implementation detail, not the routine control surface.
