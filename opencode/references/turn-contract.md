# Turn Contract

The happy path is:

1. `turn` produces a small, mechanical result.
2. `agent-turn-input` optionally adapts that result into compact main-agent guidance.
3. The main-session agent writes the final user-facing explanation.

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

## Main-agent consumption order

1. `shouldSend`
2. `delivery` / `routing`
3. facts
4. cadence

Default mapping:

- `shouldSend=false` -> usually stay silent
- `shouldSend=true` + running + changed -> brief progress update
- `shouldSend=true` + running + no-change -> brief heartbeat
- `shouldSend=true` + blocked/failed/completed -> explicit status update

## Debug path

Use `explain-turn` when debugging why a turn surfaced or stayed quiet:

```bash
python3 scripts/opencodectl.py explain-turn --input <turn-result.json>
```

If you need lower-level scripts, treat them as implementation detail, not the routine control surface.
