# my-skills

OpenClaw skills and related design docs.

## Layout

- `opencode/` — the actual skill package under iteration
- `design/opencode/` — system design docs for the opencode skill/workflow
- `design/opencode/archive/` — historical proposal/iteration documents

## Notes

The skill package is intentionally isolated from the higher-level design docs so the skill can evolve without forcing all design/iteration material into the runtime skill payload.

## OpenCode Manager (Phase 1)

Use the manager entrypoint for the new Phase 1 session/watcher workflow:

- `python3 opencode/scripts/opencode_manager.py start ...`
- `python3 opencode/scripts/opencode_manager.py attach ...`
- `python3 opencode/scripts/opencode_manager.py list-sessions ...`
- `python3 opencode/scripts/opencode_manager.py inspect ...`
- `python3 opencode/scripts/opencode_manager.py list-watchers ...`

Manager registry + watcher runtime files stay local-only under `.local/opencode-manager/`.
Manager-facing JSON/config fields keep the naming split explicit:

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
