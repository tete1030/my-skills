# my-skills

OpenClaw skills and related design docs.

## Layout by priority

- **Hot path now:** `opencode/SKILL.md` plus `python3 opencode/scripts/opencode_manager.py --help`
- **Lower-priority reference:** this README and `opencode/references/`
- **Not hot path:** `design/opencode/` and `design/opencode/archive/`

The skill package stays intentionally isolated from design/iteration material so runtime guidance does not carry the full design history.

## OpenCode manager quick reference

Use the manager entrypoint for the current session/watcher workflow:

- `python3 opencode/scripts/opencode_manager.py start ...`
- `python3 opencode/scripts/opencode_manager.py attach ...`
- `python3 opencode/scripts/opencode_manager.py continue ...`
- `python3 opencode/scripts/opencode_manager.py list-sessions ...`
- `python3 opencode/scripts/opencode_manager.py inspect ...`
- `python3 opencode/scripts/opencode_manager.py inspect-history ...`
- `python3 opencode/scripts/opencode_manager.py stop-session ...`
- `python3 opencode/scripts/opencode_manager.py list-watchers ...`
- `python3 opencode/scripts/opencode_manager.py stop-watcher ...`
- `python3 opencode/scripts/opencode_manager.py detach ...`

Key points:

- `start` accepts `--first-prompt` or the safer `--first-prompt-file` (`-` = stdin); prefer file/stdin for long or shell-sensitive prompts.
- `start` and `continue` both accept optional prompt/run overrides: `--opencode-agent`, `--opencode-model <providerID/modelID>` (for example `openai/gpt-5`), and `--opencode-variant`. If omitted, the current OpenCode defaults stay unchanged.
- `start` now ensures watcher setup by default for normal conversation-driven usage; use `--no-watcher` only when you explicitly want no routed progress/debug mode.
- `continue` accepts `--follow-up-prompt` or the safer `--follow-up-prompt-file` (`-` = stdin); for normal agent usage it now ensures watcher routing by default so later progress returns to the originating OpenClaw session. Use `--no-watcher` to opt out; `--ensure-watcher` remains accepted as a compatibility alias.
- Watchers now support notification shaping knobs for token control: `--notify-min-interval-sec` (rate-limit non-critical updates), `--notify-min-priority low|normal|high`, and repeated `--notify-keyword <term>` filters. Critical updates (failed/blocked/completed) bypass these non-critical filters by default.
- `stop-session` is the manager-level real stop surface and uses the verified OpenCode abort API instead of a pause-like follow-up prompt; it should stop only the OpenCode session and keep any watcher attached unless the user explicitly asks to stop monitoring or detach the watcher too.
- `stop-session` rejects an explicit `--opencode-workspace` mismatch before aborting, so a scope mistake cannot silently hit the wrong workspace/session pairing.
- `stop-session` now performs post-abort verification as well, because current live behavior shows that abort acceptance / busy-clearing alone does not guarantee the underlying OpenCode tool run actually stopped; results are classified as verified, unverified, or likely failed instead of being reported as success by default.
- `start`, `attach`, `continue`, `inspect`, and `list-sessions` now return `opencodeUiUrl`; the usable UI URL format is `<base-url>/<base64url(workspace-no-padding)>/session/<sessionId>` rather than `<base-url>/session/<sessionId>`.
- `start` and `continue` return the slim handoff contract fields `handoffMode`, `agentAction`, and `userFacingAck`.
- `inspect` now includes a compact `rehydration` block with current-state rebuild data: `currentState`, `latestUserIntent`, `recentCompletedWork`, `recentNotableEvents`, `watcherState`, `snapshotCoverage`, and `followUpHints` so takeover after compact/reset stays explicit about the observed window and when to do a narrow history drill-down.
- `inspect-history` is the explicit drill-down surface for one recent message: select by `--message-id`, `--recent-index` (`0` = latest, then `1`/`2` if needed), or `--latest` and it returns compact text/tool details including read/write/patch targets when inferable plus shell/stdout tail lines for “what happened between inspect points?” checks.
- `attach` now returns the same current-state inspection payload immediately, so attaching to an existing session gives instant takeover context instead of only watcher metadata.
- `agentAction=acknowledge_and_end_turn` means the current manager turn should stop after one acknowledgment.
- When `handoffMode=watcher_live`, the watcher is now the authoritative future progress source; acknowledge once and end the current turn.
- `detach` removes the watcher binding without deleting the OpenCode session and returns `detachStatus` (`detached_now`, `already_detached`, `not_found`).
- Manager registry + watcher runtime files stay local-only under `.local/opencode-manager/`.
- Manager-facing JSON/config fields keep the naming split explicit:
  - OpenCode: `opencodeSessionId`, `opencodeWorkspace`
  - OpenClaw: `openclawSessionKey`, `openclawDeliveryTarget`

Safe shell pattern for long prompts:

```bash
cat <<'EOF' | python3 opencode/scripts/opencode_manager.py continue \
  --opencode-base-url http://127.0.0.1:4096 \
  --opencode-session-id ses_demo \
  --follow-up-prompt-file -
Please continue this task and keep literal text like `video-sum run` intact.
EOF
```

## Thin watcher runtime

For repeated long-run watcher use, keep real runtime config local-only under `.local/` and use the thin wrapper instead of retyping the full `opencodectl.py watch ...` command each time.

1. Create the local runtime directory and copy the tracked example config:
   - `mkdir -p .local/opencode/watch/default`
   - `cp opencode/examples/watch-runtime.example.json .local/opencode/watch/default/config.json`
2. Fill in your local session/routing values in that ignored file.
3. Start the watcher:
   - `python3 opencode/scripts/opencode_watch_runtime.py --name default`

Default convention for a named runtime profile is:

- config: `.local/opencode/watch/<name>/config.json`
- state: `.local/opencode/watch/<name>/state.json`
- log: `.local/opencode/watch/<name>/watch.log`

Use `--once` for a single step, or `--live` / `--dry-run` to override the config's live mode for a run.
