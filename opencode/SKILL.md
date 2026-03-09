---
name: opencode
description: Design and operate an OpenCode→OpenClaw loop with a main-session-centered model. Prefer `scripts/opencodectl.py turn` for the happy path, `delivery-handoff` for origin-session handoffs, `openclaw-agent-call` for the current `openclaw gateway call agent` bridge, and keep `agent-turn-input` only as an optional compact helper. Use when refining turn boundaries, cadence, routing, or the structured fact output consumed by the main session.
---

# Opencode

Keep this skill centered on one idea:

- `turn` emits mechanical facts, cadence, and routing.
- The main-session agent decides whether to speak and writes the final explanation.
- `delivery-handoff` prepares the structured origin-session handoff, not user-facing chat text.
- `openclaw-agent-call` turns that handoff into the current practical `openclaw gateway call agent` injection path using `sessionKey`.
- The transported handoff already carries the compact decision input the originating main-session agent needs; no extra script layer is required to "consume" it.

## Core rules

- Keep the **main session** as the decision owner and visible narrative owner.
- Treat timed/event triggers as **inputs**, not as the conversation owner.
- Treat user chat messages as **high-priority control input** when they change goals, constraints, pause/resume state, or visibility expectations.
- Prefer `python3 scripts/opencodectl.py turn ...` for normal operation.
- Prefer `python3 scripts/opencodectl.py delivery-handoff --input <turn.json>` when the next layer needs an origin-session handoff.
- Prefer `python3 scripts/opencodectl.py openclaw-agent-call --input <delivery-handoff.json>` when the next layer needs the concrete `openclaw gateway call agent` shape.
- Use `python3 scripts/opencodectl.py agent-turn-input --input <turn.json>` only when the main agent wants to inspect the compact recommendation object directly.
- Let scripts emit **facts + cadence + origin routing**.
- Do **not** let scripts emit final chat prose, plans, strategy trees, or rewritten delivery.
- Keep origin-session preservation explicit.
- Keep environment-specific details out of the skill package.

## Read order

1. `references/runtime-loop.md`
2. `references/turn-contract.md`
3. `references/delivery-handoff.md` when you need the origin-session delivery handoff or the `openclaw gateway call agent` bridge shape
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

Use `delivery-handoff` when the next layer needs a structured origin-session handoff that preserves the original task-initiating session and still does **not** author chat text:

```bash
python3 scripts/opencodectl.py delivery-handoff --input <turn-result.json>
```

`delivery-handoff` also accepts a legacy `agent-turn-input` payload, but the preferred path is to hand it the raw `turn` result.
It will compact that into the same mechanical main-agent input internally.

Use `openclaw-agent-call` when you want the concrete OpenClaw CLI bridge from that handoff into the originating session:

```bash
python3 scripts/opencodectl.py openclaw-agent-call --input <delivery-handoff.json>
```

For a tighter CLI chain, both `delivery-handoff` and `openclaw-agent-call` accept `--input -`, so you can pipe them without temp files:

```bash
python3 scripts/opencodectl.py delivery-handoff --input <turn-result.json> --live-ready \
  | python3 scripts/opencodectl.py openclaw-agent-call --input - --execute
```

Default behavior is dry-run planning.
Add `--execute` only when the handoff is already marked live-ready and you want to run the generated `openclaw gateway call agent` invocation.

Use `agent-turn-input` only when you want to inspect that compact recommendation object by itself:

```bash
python3 scripts/opencodectl.py agent-turn-input --input <turn-result.json>
```

The intended consumption order is:

`turn -> delivery-handoff -> openclaw-agent-call -> openclaw gateway call agent(sessionKey=originSession) -> main-session agent decides visible reply`

For the deliberately tiny single-session watcher MVP, `watch` is still the underlying loop, but repeated long-run operation should prefer the thin runtime wrapper so local config, state, and logs stay in one ignored runtime directory.

Tracked example config:

```bash
mkdir -p ../.local/opencode/watch/default
cp examples/watch-runtime.example.json ../.local/opencode/watch/default/config.json
```

Long-run entrypoint:

```bash
python3 scripts/opencode_watch_runtime.py --name default
```

Default convention for a named runtime profile:

- config: `.local/opencode/watch/<name>/config.json`
- state: `.local/opencode/watch/<name>/state.json`
- log: `.local/opencode/watch/<name>/watch.log`

Use `--once` for a single dry-run-style step, or `--live` / `--dry-run` to override the config for that run.

If you need the raw lower-level command directly, `watch` still works the same way:

```bash
python3 scripts/opencodectl.py watch \
  --base-url <url> \
  --session-id <session-id> \
  --state .local/opencode-watch-state.json \
  --origin-session <origin-session> \
  --origin-target <origin-target>
```

Add `--live` to actually execute the ready `openclaw gateway call agent` handoff.
Add `--loop --interval-sec 60` for fixed-interval polling.
Default mode is one dry-run step, which is the safest way to verify the session id, origin routing, and duplicate suppression state before turning it live.

Cron may reuse the same structured payload only as a watchdog/safety net.
It is not the primary consumer.

Use `explain-turn` or lower-level commands only for debugging.
Do not make the agent memorize the lower-level scripts for routine use.

## Keep / avoid

Keep:
- `turn`
- `delivery-handoff` for origin-session handoffs
- `openclaw-agent-call` for the concrete OpenClaw CLI bridge
- `agent-turn-input` as an optional helper / debug surface
- `explain-turn` for debugging
- `api-surface` when integration assumptions change

Avoid:
- `origin-session-consume` as a required happy-path layer
- parallel/manual-heavy operating recipes as the default path
- overlapping references that restate the same boundary in different words
- preserving old fallback guidance when it blurs the happy path
- treating cron as the primary consumer of turn output
- environment-specific notes or lab details inside the committed skill
