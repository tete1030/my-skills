# OpenCode API Surface

This reference only tracks the remote API assumptions that matter to the current prototypes.
Do not guess write/control endpoints unless they are verified.

## Known observe/read endpoints

- `/doc`
- `/session`
- `/session/{id}/message`
- `/session/{id}/todo`
- `/session/status`
- `/pty`
- `/event`
- `/global/event`
- `/permission`
- `/question`

## Verified control endpoint

- `POST /session/{id}/abort?workspace=<workspace>` -> abort request surface for the current OpenCode run

### Abort caveat (live behavior)

- Current live probes show that `POST /session/{id}/abort?workspace=<workspace>` can return success and clear the workspace busy entry while the underlying tool/shell execution still continues and later completes normally.
- Therefore, do **not** treat abort acceptance or busy-entry disappearance alone as proof of a hard stop.
- Manager/tooling should verify terminal message state after abort, and may need to report `stopLikelyFailed` / unverified outcomes instead of claiming success.

## Verified UI route format

- Usable session UI URL: `/<base64url(workspace-no-padding)>/session/<sessionId>`
- Example workspace `/mnt/vault/teslausb-video-sum` -> `L21udC92YXVsdC90ZXNsYXVzYi12aWRlby1zdW0`
- Do **not** assume `/session/<sessionId>` is the correct browser URL by itself

## Payload notes

### `/session/status`

Observed shape is a workspace-scoped map of busy sessions, for example:

```json
{
  "ses_...": {"type": "busy"}
}
```

When an externally running session is aborted, the corresponding busy entry disappears; there was no separate durable `paused` state observed from this endpoint during the probe.


### `/session`

Useful fields observed in session summaries:

- `id`
- `slug`
- `title`
- `directory`
- `version`
- `time.created`
- `time.updated`
- optional `permission`

### `/session/{id}/message`

Returns a list of messages.
Normalize from `info.*` and `parts[]`, not from a synthetic top-level `message.content` shape.

Useful fields/variants observed:

- `info.id`
- `info.role`
- `info.time.created`
- optional `info.time.completed`
- optional `info.finish`
- optional `info.error.name` / `info.error.data.message`
  - observed when a run was externally stopped via `POST /session/{id}/abort`
  - early abort can leave `parts: []` while still setting `info.error.name = MessageAbortedError`
- `parts[]`
  - `text`
  - `tool`
  - `reasoning`
  - `step-start`
  - `step-finish`

### `/session/{id}/todo`

Observed todo item fields:

- `content`
- `status` (`completed`, `in_progress`, `pending`)
- `priority`

Preferred phase derivation:

1. active / in-progress item
2. next pending item
3. latest completed item

Do not fall back to the first todo item when everything is already completed.

## Normalized snapshot guidance

A `null` phase is not always a bug.
Even when todo is empty, snapshots should still preserve useful state.

Recommended normalized fields:

- `latestMessage.id`
- `latestMessage.role`
- `latestMessage.status`
- `latestMessage.finish`
- `latestMessage.message.lastTextPreview`
- `latestTextPreview`
- `latestAssistantTextPreview`
- `todo.phase`
- `todo.current`
- `todo.next`
- `todo.latestCompleted`
- `todo.hasPendingWork`
