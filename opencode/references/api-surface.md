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

## Payload notes

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
