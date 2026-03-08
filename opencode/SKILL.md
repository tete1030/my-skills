---
name: opencode
description: Design and operate an OpenCode→OpenClaw loop with a main-session-centered model. Prefer `scripts/opencodectl.py turn` for the happy path, `agent-turn-input` for compact main-agent guidance, and `delivery-handoff` for origin-session systemEvent handoffs with cron only as watchdog fallback. Use when refining turn boundaries, cadence, routing, or the structured fact output consumed by the main session.
---

# Opencode

Keep this skill centered on one idea:

- `turn` emits mechanical facts, cadence, and routing.
- The main-session agent decides whether to speak and writes the final explanation.
- `delivery-handoff` prepares structured origin-session `systemEvent` injection, not user-facing chat text.

## Core rules

- Keep the **main session** as the decision owner and visible narrative owner.
- Treat timed/event triggers as **inputs**, not as the conversation owner.
- Treat user chat messages as **high-priority control input** when they change goals, constraints, pause/resume state, or visibility expectations.
- Prefer `python3 scripts/opencodectl.py turn ...` for normal operation.
- Use `python3 scripts/opencodectl.py agent-turn-input --input <turn.json>` only when the main agent wants a compact recommendation object.
- Use `python3 scripts/opencodectl.py delivery-handoff --input <agent-turn-input.json>` when the next layer needs an origin-session `systemEvent` template.
- Let scripts emit **facts + cadence + origin routing**.
- Do **not** let scripts emit final chat prose, plans, strategy trees, or rewritten delivery.
- Keep origin-session preservation explicit.
- Treat cron as **watchdog/fallback only**, not as the normal consumption path.
- Keep environment-specific details out of the skill package.

## Read order

1. `references/runtime-loop.md`
2. `references/turn-contract.md`
3. `references/delivery-handoff.md` when you need the origin-session systemEvent closure after `agent-turn-input`
4. `references/api-surface.md` only when changing snapshot/API assumptions

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

Use `delivery-handoff` when the next layer needs a structured origin-session `systemEvent` envelope that preserves the original task-initiating session and still does **not** author chat text:

```bash
python3 scripts/opencodectl.py delivery-handoff --input <agent-turn-input.json>
```

The intended consumption order is:

`turn -> agent-turn-input -> delivery-handoff -> inject structured systemEvent into origin session -> main-session agent decides visible reply`

Cron may reuse the same structured payload only as a watchdog/safety net.
It is not the primary consumer.

Use `explain-turn` or lower-level commands only for debugging.
Do not make the agent memorize the lower-level scripts for routine use.

## Keep / avoid

Keep:
- `turn`
- `agent-turn-input`
- `delivery-handoff` for origin-session systemEvent templates
- `explain-turn` for debugging
- `api-surface` when integration assumptions change

Avoid:
- parallel/manual-heavy operating recipes as the default path
- overlapping references that restate the same boundary in different words
- preserving old fallback guidance when it blurs the happy path
- treating cron as the primary consumer of turn output
- environment-specific notes or lab details inside the committed skill
