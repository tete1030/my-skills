---
name: opencode
description: Manage OpenCode work from an OpenClaw conversation: start a new OpenCode task in a workspace, list or inspect existing OpenCode sessions, drill into one recent message when needed, continue an existing session, and attach/list/stop/detach local watchers that route progress back to the originating OpenClaw session. Prefer `scripts/opencode_manager.py` for normal conversation-driven usage. For agent-driven `start` / `continue`, watcher routing is the default and progress should return to the originating OpenClaw session unless the caller explicitly opts out with `--no-watcher` or is doing runtime/debug work. Also use when interpreting injected OpenCode runtime/system-event updates so they are treated as internal progress inputs, not echoed mechanically to the user. When OpenCode should stop, always use the real `stop-session` command / abort API; stopping the OpenCode session should not also stop the watcher unless the user explicitly asks to stop monitoring or detach it.
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
- `stop-session`
- `list-sessions`
- `inspect`
- `inspect-history`
- `list-watchers`
- `stop-watcher`
- `detach`

Do **not** invent aliases like `create`, `list`, `start-watch`, `stop-watch`, or `detach-watch`.
If you need flags, run `python3 scripts/opencode_manager.py <subcommand> --help`.

### 4) Quick chooser

- Fresh work in a workspace -> `start` (normal path: this ensures a watcher for the provided OpenClaw session by default; use `--no-watcher` only when the caller explicitly wants no routed progress or is doing narrow runtime/debug work)
- Need to find an existing session first -> `list-sessions`
- Need current state of one existing session -> `inspect` (now returns a compact `rehydration` block for takeover/current-state rebuild)
- Need more detail on one recent message/event after inspect/attach, including recent shell output or what happened between inspect points -> `inspect-history` (`--recent-index 0` = latest, then 1/2 if needed, or use `--message-id`)
- Need to send more work into an existing session -> `continue` by default (normal path ensures watcher routing automatically; use `--no-watcher` only if the user explicitly wants no watcher / no routed progress, or you are doing narrow runtime debugging)
- Need to really stop the OpenCode run itself -> `stop-session` (real abort API, not a pause-like follow-up prompt; keep any watcher attached unless the user explicitly asks to stop monitoring too; if `--opencode-workspace` is supplied it must match the session's actual directory)
- Need watcher routing back to this OpenClaw session for an existing session -> `attach` or `continue` (`attach` now also returns the same immediate inspection/rehydration payload; `--ensure-watcher` remains accepted as an explicit compatibility alias, but default usage should not require it)
- Need to see watcher bindings -> `list-watchers`
- Need to stop monitoring only -> `stop-watcher` (only when the user explicitly asks to stop the watcher)
- Need to remove the OpenClaw binding -> `detach` (only when the user explicitly asks to detach / stop monitoring)

Hard rule: if you want OpenCode to stop, use `stop-session`. Do not send a natural-language follow-up prompt like “please stop”, “pause here”, or “hold off for now” and assume that means the run is really stopped. Use the actual stop command / abort API.

Important caveat: upstream abort acceptance is not the same as a verified hard stop. In current live behavior, `/session/{id}/abort` can return success and clear the busy flag while the underlying tool/shell work still continues and later completes normally. Treat `stop-session` as “abort + verify”, not “abort call returned true so the session is definitely dead”. The manager should report the result as verified, unverified, or likely failed, and an explicit workspace mismatch must be rejected before abort rather than silently proceeding.

Default lifecycle rule: OpenCode session lifecycle and watcher lifecycle are separate. Starting / continuing work should normally ensure a watcher is present for the originating OpenClaw session by default; use `--no-watcher` only for explicit opt-out/debug intent. Stopping work should normally stop only the OpenCode session; use `stop-watcher` or `detach` only when the user explicitly asks to stop monitoring.

Watcher notification shaping is available when token churn matters: use `--notify-min-interval-sec` to rate-limit non-critical updates, `--notify-min-priority` to suppress low-priority non-critical updates, and repeated `--notify-keyword` filters to forward only matching non-critical updates. Critical updates (failed / blocked / completed) bypass these non-critical filters by default.

Key CLI facts that matter in the hot path:

- `start` accepts `--first-prompt` or the safer `--first-prompt-file` (`-` = stdin); prefer file/stdin when the prompt is long, multiline, or shell-sensitive, and watcher setup is part of the default `start` path unless `--no-watcher` is set
- `start` and `continue` both accept optional OpenCode run overrides: `--opencode-agent`, `--opencode-model <providerID/modelID>` (for example `openai/gpt-5`), and `--opencode-variant`; if omitted, leave the current OpenCode defaults unchanged
- `continue` accepts `--follow-up-prompt` or the safer `--follow-up-prompt-file` (`-` = stdin)
- for normal agent usage, `continue` should preserve watcher routing by default; use `--no-watcher` only for explicit no-watcher/debug intent (`--ensure-watcher` remains accepted as a compatibility alias)
- `stop-session` calls the verified real abort API instead of faking a stop with another prompt, and by default should leave the watcher running so it can observe/report the terminal state
- if the goal is to stop, never substitute a “please stop/pause” follow-up prompt for `stop-session`; prompts can still leave internal continuation behavior, while the real stop command cleanly aborts the run
- manager session objects now return `opencodeUiUrl`; the usable UI URL format is `<base-url>/<base64url(workspace-no-padding)>/session/<sessionId>`, not `<base-url>/session/<sessionId>`
- `detach` removes the watcher binding without deleting the OpenCode session

### 5) The manager handoff contract is authoritative

After `start` or `continue`, read the returned contract instead of re-deriving behavior from prose.
The manager result now stays small:

`handoffMode`, `agentAction`, `userFacingAck`.

Hot-path interpretation:

- `agentAction=acknowledge_and_end_turn` means acknowledge once and stop there for this turn.
- `handoffMode=watcher_live` means the watcher now owns future progress delivery and same-turn completion checks should not be re-added by the agent.
- `handoffMode=watcher_not_live`, `watcher_missing`, or `no_watcher` mean there is no live watcher handoff; any later status check happens only in a future explicit turn.

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
- Treat `runtimeSignal` as a wake/inspect token, not as a state summary; the live state comes from `inspect`, `attach` rehydration, and targeted `inspect-history` drill-down when needed.
- If `runtimeSignal.action=inspect_once_current_state`, do **one** `python3 scripts/opencode_manager.py inspect ...` for that `opencodeSessionId`, then speak from the inspected current state.
- If that inspect still leaves a real gap, proactively do **one narrow** `inspect-history` lookup yourself—usually `--recent-index 0`, then `1`/`2` if needed, or `--message-id` when the inspection already points to the exact message.
- Use `inspect-history` both for older relevant history and for “what happened between inspect points?” questions such as recent shell/tool output, stdout tail lines, or read/write/patch details.
- After that one inspect (plus at most the narrow gap-filling drill-down above), do **not** keep polling unless the user explicitly asks or you are diagnosing watcher/runtime issues.
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
