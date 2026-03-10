---
name: opencode
description: Manage OpenCode work from an OpenClaw conversation: start a new OpenCode task in a workspace, list or inspect existing OpenCode sessions, continue an existing session, and attach/list/stop/detach local watchers that route progress back to the originating OpenClaw session. Prefer `scripts/opencode_manager.py` for normal conversation-driven usage; use `scripts/opencodectl.py turn/watch/...` only for runtime wiring or debugging. Also use when interpreting injected OpenCode runtime/system-event updates so they are treated as internal progress inputs, not echoed mechanically to the user.
---

# Opencode

Default stance: for ordinary user requests, `scripts/opencode_manager.py` is the control surface. Treat everything else as lower-priority runtime wiring or debugging.

## Hot path: what matters right now

### 1) Keep the objects separate

- **OpenCode session**: the remote coding task/session (`opencodeSessionId`) running in an OpenCode workspace.
- **OpenClaw session**: the chat/session that should receive progress or results (`openclawSessionKey`).
- **Watcher**: the local runtime process that observes one OpenCode session and routes progress back into one OpenClaw session.

Do not confuse “current OpenClaw conversation” with “current OpenCode work session”.
Keep field names explicit:

- OpenCode side: `opencodeSessionId`, `opencodeWorkspace`
- OpenClaw side: `openclawSessionKey`, `openclawDeliveryTarget`

### 2) Respect the workspace-path boundary

Treat `opencodeWorkspace` as an OpenCode-side / remote workspace identifier by default.
Do **not** `ls`, `stat`, `pwd`, or otherwise preflight that path on the current host just to start, continue, list, or inspect remote OpenCode work.
Pass the workspace path through to the manager/API unless the task explicitly requires host-side filesystem access or validation.

### 3) Use the manager commands exactly as implemented

Normal usage commands:

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

### 4) Quick chooser

- Fresh work in a workspace -> `start`
- Need to find an existing session first -> `list-sessions`
- Need current state of one existing session -> `inspect` (now returns a compact `rehydration` block for takeover/current-state rebuild)
- Need to send more work into an existing session -> `continue`
- Need watcher routing back to this OpenClaw session -> `attach` or `continue --ensure-watcher` (`attach` now also returns the same immediate inspection/rehydration payload)
- Need to see watcher bindings -> `list-watchers`
- Need to stop monitoring only -> `stop-watcher`
- Need to remove the OpenClaw binding -> `detach`

Key CLI facts that matter in the hot path:

- `continue` uses `--follow-up-prompt`
- `continue` can ensure routing with `--ensure-watcher`
- `detach` removes the watcher binding without deleting the OpenCode session

### 5) The manager handoff contract is authoritative

After `start` or `continue`, read the returned contract instead of re-deriving behavior from prose.
The manager result can include:

`progressSource`, `agentShouldPoll`, `recommendedNextAction`, `turnShouldEnd`, `completionCheckOwner`, `disallowImmediateCompletionCheck`, `recommendedUserVisibleAction`, `userFacingAck`.

Hot-path interpretation:

- If `progressSource=watcher`, the watcher now owns progress delivery.
- If `turnShouldEnd=true`, end the current turn after the user-visible acknowledgment.
- If `disallowImmediateCompletionCheck=true`, do not do a same-turn completion check.
- If `completionCheckOwner=watcher_runtime_updates`, completion checking belongs to watcher-driven runtime updates, not this turn.
- A normal live handoff is `recommendedNextAction=wait_for_runtime_updates` plus `recommendedUserVisibleAction=acknowledge_handoff_then_end_turn`.

This is the main anti-sprawl rule: prefer the manager contract over repeated hand-written polling guidance.

### 6) Do not become a second watcher

`inspect` is for one-off understanding, not for waiting loops.
Allowed cases are narrow:

- initial takeover / understanding of an existing session
- explicit user request to check status
- one decision point before a follow-up prompt
- watcher/runtime anomaly diagnosis

Once a live watcher handoff is active, do **not** run `sleep + inspect`, repeated `inspect`, or “just one more inspect” completion checks.
No second watcher. No inspect loop.

### 7) Runtime updates are signals, not chat replies

Watcher-delivered runtime updates are internal progress inputs for the main OpenClaw agent.
The user does **not** see the injected payload.
Do not echo raw `systemEvent`, JSON, headers, tags, or watcher wording.

Use this rule set:

- Read `runtimeSignal` before anything else.
- Prefer `runtimeSignal.signalKind`, `runtimeSignal.recommendedNextAction`, `runtimeSignal.opencodeSessionId`, `taskCluster`, `status`, `phase`, `reason`, and cadence over old preview text.
- If `runtimeSignal.recommendedNextAction=inspect_once_current_state`, do **one** `python3 scripts/opencode_manager.py inspect ...` for that `opencodeSessionId`, then speak from the inspected current state.
- After that one inspect, do **not** keep polling unless the user explicitly asks or you are diagnosing watcher/runtime issues.
- For one task cluster, prefer at most **one progress update while work is moving** and **one final completion/status update** when the outcome is clear.
- Same-state / repeated `completed` / low-value updates should usually stay silent.

When a visible reply is needed, order it like this:

1. what was just done
2. what evidence was seen
3. what that means for the user/task

### 8) Noise handling

`ignored=true` plugin events may appear in raw ledgers. Treat them as debug noise unless another meaningful signal confirms they matter.
Prefer this reading order:

1. `latestMeaningfulPreview`
2. `recentEventSummary`
3. current `status` and `phase`
4. raw `eventLedger` only for debugging or ambiguity resolution

## Lower-priority reference / debugging

Use `scripts/opencodectl.py` only when wiring or debugging the runtime path itself.
That includes:

- `turn`
- `delivery-handoff`
- `openclaw-agent-call`
- `watch`
- `agent-turn-input`
- `explain-turn`

Happy-path lower-level chain:

`turn -> delivery-handoff -> openclaw-agent-call -> openclaw gateway call agent(sessionKey=originSession) -> main-session agent decides visible reply`

For repeated long-run watching, prefer the thin wrapper:

```bash
python3 scripts/opencode_watch_runtime.py --name default
```

Runtime profile layout:

- config: `.local/opencode/watch/<name>/config.json`
- state: `.local/opencode/watch/<name>/state.json`
- log: `.local/opencode/watch/<name>/watch.log`

## Not hot path: deeper docs

Read only when needed:

1. `references/runtime-loop.md`
2. `references/turn-contract.md`
3. `references/delivery-handoff.md`
4. `references/api-surface.md`

Repository-level design and historical iteration docs live outside the skill hot path under `design/opencode/` and `design/opencode/archive/`.

## Keep / avoid

Keep:

- `opencode_manager.py` as the everyday control surface
- exact subcommand names
- explicit separation between OpenCode sessions, OpenClaw sessions, and watcher bindings
- the manager handoff contract as the authoritative control signal
- natural user-facing replies based on current runtime facts

Avoid:

- using lower-level runtime scripts as the default workflow
- confusing the current chat session with the current OpenCode session
- inventing manager aliases
- echoing transport/debug payloads mechanically
- letting plugin noise outweigh meaningful progress
- treating watcher output as the final conversation narrative
