---
name: opencode
description: Design and operate an OpenCode→OpenClaw loop with a main-session-centered model. Prefer `scripts/opencodectl.py turn` for the happy path and `agent-turn-input` for the compact main-agent handoff. Use when refining turn boundaries, cadence, routing, or the structured fact output consumed by the main session.
---

# Opencode

Keep this skill centered on one idea:

- `turn` emits mechanical facts, cadence, and routing.
- The main-session agent decides whether to speak and writes the final explanation.

## Core rules

- Keep the **main session** as the decision owner and visible narrative owner.
- Treat timed/event triggers as **inputs**, not as the conversation owner.
- Treat user chat messages as **high-priority control input** when they change goals, constraints, pause/resume state, or visibility expectations.
- Prefer `python3 scripts/opencodectl.py turn ...` for normal operation.
- Use `python3 scripts/opencodectl.py agent-turn-input --input <turn.json>` only when the main agent wants a compact recommendation object.
- Let scripts emit **facts + cadence + origin routing**.
- Do **not** let scripts emit final chat prose, plans, strategy trees, or rewritten delivery.
- Keep environment-specific details out of the skill package.

## Read order

1. `references/runtime-loop.md`
2. `references/turn-contract.md`
3. `references/api-surface.md` only when changing snapshot/API assumptions

## Happy path

Primary command:

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

Use `--control` when the same chat turn also changes execution policy or control state.
That control should influence the decision pass itself, not become narrative output.

Use `agent-turn-input` when the main-session agent wants a compact send/skip + update-type + routing-preservation object without turning the script layer into a renderer:

```bash
python3 scripts/opencodectl.py agent-turn-input --input <turn-result.json>
```

Use `explain-turn` or lower-level commands only for debugging.
Do not make the agent memorize the lower-level scripts for routine use.

## Keep / avoid

Keep:
- `turn`
- `agent-turn-input`
- `explain-turn` for debugging
- `api-surface` when integration assumptions change

Avoid:
- parallel/manual-heavy operating recipes as the default path
- overlapping references that restate the same boundary in different words
- preserving old fallback guidance when it blurs the happy path
- environment-specific notes or lab details inside the committed skill
