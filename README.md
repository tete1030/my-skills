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
- `python3 opencode/scripts/opencode_manager.py list-watchers ...`
- `python3 opencode/scripts/opencode_manager.py stop-watcher ...`
- `python3 opencode/scripts/opencode_manager.py detach ...`

Key points:

- `continue` uses `--follow-up-prompt` and can ensure watcher routing with `--ensure-watcher`.
- `start` and `continue` return the handoff contract fields `progressSource`, `agentShouldPoll`, `recommendedNextAction`, `turnShouldEnd`, `completionCheckOwner`, `disallowImmediateCompletionCheck`, `recommendedUserVisibleAction`, and `userFacingAck`.
- When `progressSource=watcher`, the watcher is the progress source and `completionCheckOwner=watcher_runtime_updates` means the current turn should not do the completion check.
- `detach` removes the watcher binding without deleting the OpenCode session and returns `detachStatus` (`detached_now`, `already_detached`, `not_found`).
- Manager registry + watcher runtime files stay local-only under `.local/opencode-manager/`.
- Manager-facing JSON/config fields keep the naming split explicit:
  - OpenCode: `opencodeSessionId`, `opencodeWorkspace`
  - OpenClaw: `openclawSessionKey`, `openclawDeliveryTarget`

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
