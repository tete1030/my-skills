---
name: opencode
description: Manage OpenCode work from an OpenClaw conversation: start a new OpenCode task in a workspace, list or inspect existing OpenCode sessions, continue an existing session, and attach/list/stop/detach local watchers that route progress back to the originating OpenClaw session. Prefer `scripts/opencode_manager.py` for normal conversation-driven usage; use `scripts/opencodectl.py turn/watch/...` only for runtime wiring or debugging. Also use when interpreting injected OpenCode runtime/system-event updates so they are treated as internal progress inputs, not echoed mechanically to the user.
---

# Opencode

Use this skill in two modes:

1. **Normal conversation-driven usage:** use `scripts/opencode_manager.py`.
2. **Runtime wiring / debugging:** use `scripts/opencodectl.py` and the references.

Do not make the agent memorize a broad internal script zoo. For ordinary user requests, the manager is the control surface.

## Distinguish the objects first

Keep these three things separate:

- **OpenCode session**: the remote coding task/session (`opencodeSessionId`) running in an OpenCode workspace.
- **OpenClaw session**: the chat/session that should receive progress or results (`openclawSessionKey`, sometimes called origin session).
- **Watcher**: a local runtime process that observes one OpenCode session and can inject structured progress back into one OpenClaw session.

A user may stay in the **same OpenClaw chat** while you create, inspect, continue, stop, or reattach **different OpenCode sessions**.
Do not confuse “current OpenClaw conversation” with “current OpenCode work session”.

Field naming is intentional:

- OpenCode side: `opencodeSessionId`, `opencodeWorkspace`
- OpenClaw side: `openclawSessionKey`, `openclawDeliveryTarget`

## Exact command surface

For normal usage, use the manager subcommands exactly as implemented:

- `start`
- `attach`
- `continue`
- `list-sessions`
- `inspect`
- `list-watchers`
- `stop-watcher`
- `detach`

Do **not** invent aliases like `create`, `list`, `start-watch`, `stop-watch`, or `detach-watch`.
If you need flags, run `python3 scripts/opencode_manager.py <subcommand> --help`.

## When to use which command

### Start a new OpenCode task in a workspace

Use `start` when the user wants OpenCode to begin fresh work in a workspace.
This creates a new OpenCode session, sends the first prompt, and attaches a watcher.

```bash
python3 scripts/opencode_manager.py start \
  --opencode-base-url <url> \
  --opencode-workspace <workspace> \
  --openclaw-session-key <origin-session-key> \
  --first-prompt '<task prompt>'
```

Add `--watch-live` only when you are ready for live progress injection.
Without it, keep things dry-run/safe where applicable.

### List existing OpenCode sessions in a workspace

Use `list-sessions` when you need to find the right existing task before continuing or attaching.
Use this first if the user says “check my OpenCode work in this repo/workspace” or “what session is already running?”.

```bash
python3 scripts/opencode_manager.py list-sessions \
  --opencode-base-url <url> \
  --opencode-workspace <workspace>
```

### Inspect one OpenCode session

Use `inspect` when you already know the `opencodeSessionId` and need normalized status, recent work, and recent event summary.
This is the right command for “what is this session doing now?”

```bash
python3 scripts/opencode_manager.py inspect \
  --opencode-base-url <url> \
  --opencode-session-id <session-id>
```

### Continue an existing OpenCode session

Use `continue` when the task should keep going in an existing OpenCode session instead of starting fresh.
This sends `--follow-up-prompt` to that existing session.

```bash
python3 scripts/opencode_manager.py continue \
  --opencode-base-url <url> \
  --opencode-session-id <session-id> \
  --follow-up-prompt '<follow-up prompt>'
```

Add `--ensure-watcher` when you also need to make sure that session has an active watcher bound to the intended OpenClaw session.
If you use `--ensure-watcher`, also supply `--openclaw-session-key` (and optionally `--openclaw-delivery-target`) so the binding is explicit.

### Attach a watcher to an existing OpenCode session

Use `attach` when the OpenCode session already exists but is not yet being watched for this OpenClaw conversation.
Typical cases:

- the session predates the current chat request
- the watcher was never started
- the watcher was stopped and needs to be restored
- you want progress routed to a specific OpenClaw session

```bash
python3 scripts/opencode_manager.py attach \
  --opencode-base-url <url> \
  --opencode-session-id <session-id> \
  --openclaw-session-key <origin-session-key>
```

### List watcher bindings

Use `list-watchers` when you need to know which OpenCode sessions currently have active local watcher processes.
This is the safest first step if you are unsure whether monitoring is already attached.

```bash
python3 scripts/opencode_manager.py list-watchers
```

### Stop a watcher

Use `stop-watcher` when you want to stop the currently running watcher process cleanly **without deleting the OpenCode session**.
The remote OpenCode task/session remains.

```bash
python3 scripts/opencode_manager.py stop-watcher --opencode-session-id <session-id>
```

### Detach a watcher binding

Use `detach` when you want to remove the active OpenClaw watcher binding for an OpenCode session **without deleting the OpenCode session**.
In practice this is the “this OpenCode session should no longer be attached to this OpenClaw flow” command.

```bash
python3 scripts/opencode_manager.py detach --opencode-session-id <session-id>
```

## Default operating recipe

For a normal user request, use this sequence:

1. **Need fresh work?** -> `start`
2. **Need to find an old session first?** -> `list-sessions`
3. **Need to understand one existing session?** -> `inspect`
4. **Need to keep an old session going?** -> `continue`
5. **Need progress routed back here?** -> `attach` or `continue --ensure-watcher`
6. **Need to see current watcher bindings?** -> `list-watchers`
7. **Need to stop monitoring only?** -> `stop-watcher`
8. **Need to remove the OpenClaw binding?** -> `detach`

Prefer this manager flow unless you are actively building or debugging the runtime chain itself.

## Polling boundary: do not become a second watcher

Use `inspect` and related manager reads as **one-off understanding tools**, not as a waiting loop.

Allowed `inspect` cases are narrow:

- initial takeover / understanding of an existing OpenCode session
- an explicit user request to check current state
- a one-off decision point before sending a follow-up prompt
- watcher anomaly diagnosis

After `start` or `continue --ensure-watcher`, once the watcher is attached and you have the initial context you need, **stop active progress polling**.
During normal running, watcher updates are the progress source.
Manager results now make this explicit with `progressSource`, `agentShouldPoll`, `recommendedNextAction`, and `userFacingAck`.
If `progressSource=watcher` and `agentShouldPoll=false`, acknowledge that OpenCode work has been handed off to the watcher, then end the turn.
A typical live handoff uses `recommendedNextAction=wait_for_runtime_updates`.
Do **not** use `sleep + inspect` or repeated `inspect` calls to wait for completion, silence, or stalls.

Anti-patterns:

- `sleep 20 && python3 scripts/opencode_manager.py inspect ...`
- `while ...; do python3 scripts/opencode_manager.py inspect ...; done`
- “just one more inspect to confirm completion” after watcher-driven progress is already active

## How to interpret runtime/event updates

Watcher-delivered runtime updates are **internal progress inputs** for the main OpenClaw agent, not prewritten chat replies.
Treat them like structured background-worker callbacks.

Important: the user does **not** see the injected runtime payload.
They only see your visible reply in chat.
Do not assume shared visibility of `systemEvent` text, JSON, headers, event tags, or watcher/debug wording.

Rules:

- `systemEvent` / `OPENCODE_ORIGIN_SESSION_SYSTEM_EVENT_V1` payloads are transport envelopes, not user-facing text.
- Treat watcher-delivered runtime updates as **lightweight signals**. The signal is a trigger to inspect current state once, not rich content to paraphrase.
- Prefer `runtimeSignal.signalKind`, `runtimeSignal.recommendedNextAction`, `runtimeSignal.opencodeSessionId`, `taskCluster`, `status`, `phase`, `reason`, and cadence over any old preview text.
- When `runtimeSignal.recommendedNextAction=inspect_once_current_state`, do **one** `python3 scripts/opencode_manager.py inspect ...` against that `opencodeSessionId`, then speak from the current inspected state.
- Do **not** restate the signal payload itself to the user.
- After that one inspect, do **not** continue polling unless the user explicitly asks, a blocker/high-priority exception requires follow-up, or you are diagnosing watcher/runtime issues.
- Translate runtime facts into user language; do **not** echo raw JSON, transport headers, event tags, or mechanical watcher wording.
- Do **not** reply with “received runtime update” or narrate watcher plumbing unless the user explicitly asks about the plumbing.
- Do **not** restate progress event-by-event.
- For one task cluster, prefer **one progress update while work is moving** and **one final completion/status update** when the outcome is clear.
- Repeated `completed` / same-state / no-new-user-value updates should usually stay silent.
- If no visible reply is needed, stay silent; the runtime input still matters internally.

When a visible reply is needed, structure it in this order:

1. what was just done
2. what evidence was seen
3. what that means for the user/task

### Noise handling

`ignored=true` plugin events may still appear in raw ledgers for debugging.
Treat them as low-priority noise unless another meaningful signal confirms they matter.
They should not dominate your interpretation of session state.

Prefer this order when understanding what happened:

1. `latestMeaningfulPreview`
2. `recentEventSummary` / accumulated meaningful event summary
3. current `status` and `phase`
4. raw `eventLedger` only for debugging or ambiguity resolution

Do not turn every tool call or plugin blip into a visible chat update.
User-facing replies should summarize task state and user impact, not the transport trace.

## Use lower-level runtime tools only when needed

Use `scripts/opencodectl.py` only when you are wiring or debugging the runtime path itself.
That includes:

- `turn`
- `delivery-handoff`
- `openclaw-agent-call`
- `watch`
- `agent-turn-input`
- `explain-turn`

Happy-path lower-level chain:

`turn -> delivery-handoff -> openclaw-agent-call -> openclaw gateway call agent(sessionKey=originSession) -> main-session agent decides visible reply`

For repeated long-run watching, prefer the thin runtime wrapper:

```bash
python3 scripts/opencode_watch_runtime.py --name default
```

Tracked example config:

```bash
mkdir -p ../.local/opencode/watch/default
cp examples/watch-runtime.example.json ../.local/opencode/watch/default/config.json
```

Default runtime profile layout:

- config: `.local/opencode/watch/<name>/config.json`
- state: `.local/opencode/watch/<name>/state.json`
- log: `.local/opencode/watch/<name>/watch.log`

## Read order when you need deeper details

1. `references/runtime-loop.md`
2. `references/turn-contract.md`
3. `references/delivery-handoff.md` when you need the injected origin-session handoff semantics
4. `references/api-surface.md` only when checking remote API assumptions

## Keep / avoid

Keep:

- `opencode_manager.py` as the everyday control surface
- exact subcommand names
- explicit separation between OpenCode sessions, OpenClaw sessions, and watcher bindings
- natural user-facing replies based on runtime facts
- raw event ledgers only as debug material

Avoid:

- using lower-level runtime scripts as the default user-facing workflow
- confusing the current chat session with the current OpenCode session
- inventing manager subcommand aliases
- echoing transport headers, JSON, or runtime task updates mechanically
- letting `ignored=true` plugin noise outweigh meaningful progress signals
- treating watcher output as the final conversation narrative
