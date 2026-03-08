# Agent Consumption Semantics

## Goal

Use the structured turn result as **agent input**, not as prewritten chat output.

Scripts should emit compact facts, cadence, and routing metadata.
The main-session agent should:
- decide whether to speak now;
- decide how brief or explicit to be;
- preserve originating-session delivery;
- write the final user-facing explanation in live context.

## Hard boundary for the agent-consumption layer

This layer exists to help a main-session agent consume one `turn` result safely.
It must not grow into a strategy planner or narrative renderer.

### Allowed scope

The adapter may:
- preserve the `shouldSend` default recommendation;
- classify the update shape (`progress`, `heartbeat`, `blocked`, `failed`, `completed`, `silent`);
- expose compact fact fields already present in `factSkeleton`;
- expose cadence fields that help the main agent stay brief or stay silent;
- preserve `originSession` / `originTarget` routing semantics.

### Caution scope

The adapter may provide recommendation metadata such as `style`, `priority`, or `mentionFields`, but only as compact hints.
Those hints should remain:
- mechanically derived from the structured turn result;
- small enough to audit quickly;
- optional for the main-session agent to follow verbatim.

If a fact is not already in the turn result, the adapter should not invent it.
If a conversational nuance depends on live context, the main-session agent should decide it at send time.

### Disallowed scope

The adapter should **not**:
- generate polished chat paragraphs, headlines, or canned replies;
- add strategy trees, next-step plans, or escalation scripts;
- rewrite delivery away from the originating session;
- echo raw payload dumps into the happy path;
- become the place where user-facing tone or narrative policy is implemented.

## Consumption order

1. Read `shouldSend` first.
2. Read `delivery` next so routing stays bound to the originating session.
3. Read `factSkeleton` for the actual facts worth surfacing.
4. Read `cadence` to understand whether this is a state change, heartbeat, or suppressed no-change turn.

## Field semantics

### `factSkeleton`

Treat this as the stable machine-to-agent fact payload.
It should stay compact and explanation-ready, but not become chat prose.

- `status`: normalized task state (`running`, `blocked`, `failed`, `completed`, etc.)
- `phase`: the current best phase/todo summary
- `latestMeaningfulPreview`: the most useful recent output fragment, if any
- `reason`: the decision/cadence reason that caused this turn to surface or stay quiet

Agent rule:
Use these fields as talking points, not as a finished message.

### `shouldSend`

Treat this as the default visible-update recommendation.

- `true`: the cadence layer believes the main session should usually receive a visible update now
- `false`: the main agent should usually stay silent unless broader conversation context overrides that

Agent rule:
`shouldSend` controls the default send/skip decision, but not the wording.
The main agent still owns the final conversational choice.

### `delivery`

Treat this as explicit routing metadata.

- `delivery.originSession`: original OpenClaw session that should receive the update when session-native delivery is used
- `delivery.originTarget`: original outbound chat target when direct target routing is needed

Agent rule:
Do not silently replace these with the current lab/debug/helper execution context.
If both are absent, only then fall back to the current session intentionally.

### `cadence`

Treat this as explanation-control metadata for the main-session agent.

Key fields:
- `decision`: mechanical send/skip outcome such as `visible_update` or `silent_noop`
- `noChange`: whether this turn observed no meaningful state change
- `consecutiveNoChangeCount`: how long the run has stayed in no-change territory
- `lastVisibleUpdateAt`: last time a visible update was emitted

Agent rule:
Use cadence to choose the update style:
- state changed -> short progress update
- no-change + visible update -> short heartbeat/update-in-place
- blocked/failed/completed -> explicit status update
- silent cadence -> usually say nothing

## Recommended agent mapping

Use this compact mental model:

- `shouldSend=false` -> default action: stay silent
- `shouldSend=true` + `status=running` + `noChange=false` -> progress update
- `shouldSend=true` + `status=running` + `noChange=true` -> heartbeat update
- `shouldSend=true` + `status=blocked` -> blocker update
- `shouldSend=true` + `status=failed` -> failure update
- `shouldSend=true` + `status=completed` -> completion update

The adapter/helper should expose this as structured recommendation metadata, not rendered prose.

## What the adapter should not do

Do **not**:
- generate polished chat paragraphs;
- hide originating-session delivery fields;
- collapse `shouldSend` into mandatory transport behavior;
- reintroduce raw payload dumps into the happy path.

## Minimal main-agent composition policy

When the agent does send a user-facing update:
- keep it brief by default;
- say what changed or what state now matters;
- mention the current phase when useful;
- mention `latestMeaningfulPreview` only when it adds concrete value;
- preserve the original destination from `delivery`.
