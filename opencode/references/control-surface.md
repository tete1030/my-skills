# Control Surface

## Principle

The skill should not force the agent to remember many low-level scripts and their boundaries.

Use a **single exposed control surface** for routine operation, while keeping lower-level scripts as internal implementation detail.

## Exposed entrypoint

Use:

```bash
python3 scripts/opencodectl.py ...
```

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
  [--write]
```

### Replay a multi-step local scenario

```bash
python3 scripts/opencodectl.py scenario \
  --state <state.json> \
  --scenario <scenario.json> \
  [--write]
```

## Internal scripts

The following scripts remain valid internal implementation pieces, but the skill should avoid treating them as the primary user-facing control surface:

- `opencode_control_state.py`
- `opencode_decision_gate.py`
- `opencode_cycle.py`
- `opencode_api_client.py`
- `opencode_snapshot.py`
- `opencode_remote_cycle.py`

Use them for debugging, targeted prototyping, or later refactoring—not as the first interface the skill agent must memorize.
