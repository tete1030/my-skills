# Control Surface

## Principle

The skill should not force the agent to remember many low-level scripts and their boundaries.

Use a **single exposed control surface** for routine operation, while keeping lower-level scripts as internal implementation detail.

## Exposed entrypoint

Use:

```bash
python3 scripts/opencodectl.py ...
```

## Happy path

For normal operation, prefer **one command**:

```bash
python3 scripts/opencodectl.py turn \
  --base-url <url> \
  --session-id <session-id> \
  --state <state.json> \
  [--control <control.json>] \
  [--origin-session <session>] \
  [--origin-target <target>] \
  [--write] \
  [--payload-out <payload.json>] \
  [--include-payload]
```

This is the default high-level path for a single main-session turn:
- apply control input if present;
- observe remote OpenCode state;
- decide whether a visible update is warranted;
- emit a structured turn result containing fact skeleton fields, cadence, and delivery metadata.

Happy-path consumers should read fields such as:
- `factSkeleton.status`
- `factSkeleton.phase`
- `factSkeleton.latestMeaningfulPreview`
- `factSkeleton.reason`
- `shouldSend`
- `delivery`
- `cadence`

The main-session agent should turn those facts into the final chat message.
If a compact adapter is useful, transform the turn result into agent-facing recommendation/input metadata rather than back into rendered prose.

Delivery metadata should point back to the original task-initiating session.

Use lower-level commands only when debugging or testing a narrower layer.


## Supported commands

### Initialize state

```bash
python3 scripts/opencodectl.py state-init --state <state.json>
```

### Show current state

```bash
python3 scripts/opencodectl.py state-show --state <state.json>
```

### Run one local cycle

```bash
python3 scripts/opencodectl.py cycle \
  --state <state.json> \
  [--control <control.json>] \
  [--observation <observation.json>] \
  [--write]
```

### Build a remote snapshot

```bash
python3 scripts/opencodectl.py snapshot \
  --base-url <url> \
  --session-id <session-id>
```

### Run one remote cycle

```bash
python3 scripts/opencodectl.py remote-cycle \
  --base-url <url> \
  --session-id <session-id> \
  --state <state.json> \
  [--control <control.json>] \
  [--write]
```

### Replay a multi-step local scenario

```bash
python3 scripts/opencodectl.py scenario \
  --state <state.json> \
  --scenario <scenario.json> \
  [--write]
```


### Alias for the happy-path turn

```bash
python3 scripts/opencodectl.py session-turn \
  --base-url <url> \
  --session-id <session-id> \
  --state <state.json> \
  [--control <control.json>] \
  [--origin-session <session>] \
  [--origin-target <target>] \
  [--write] \
  [--payload-out <payload.json>] \
  [--include-payload]
```

`session-turn` is an explicit alias for the same happy-path structured turn workflow.
Use `--control` when the current chat turn also changes execution policy or other control state.

### Explain one turn result

```bash
python3 scripts/opencodectl.py explain-turn \
  --input <session-turn-result.json>
```

Use this when debugging why a turn emitted or skipped a visible update, or when you want a compact summary of the fact skeleton plus cadence.

### Build compact main-agent input from one turn result

```bash
python3 scripts/opencodectl.py agent-turn-input \
  --input <session-turn-result.json>
```

Use this when the main-session agent wants a compact recommendation/input object for send/skip, update type, brevity, and routing preservation without turning the script layer back into a message renderer.

## Internal scripts

The following scripts remain valid internal implementation pieces, but the skill should avoid treating them as the primary user-facing control surface:

- `opencode_control_state.py`
- `opencode_decision_gate.py`
- `opencode_cycle.py`
- `opencode_api_client.py`
- `opencode_snapshot.py`
- `opencode_remote_cycle.py`

Use them for debugging, targeted prototyping, or later refactoring — not as the first interface the skill agent must memorize.
