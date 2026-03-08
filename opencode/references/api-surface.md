# OpenCode API Surface (working notes)

This reference tracks the currently assumed OpenCode API surface used by the skill prototypes.

## Known read / observe endpoints

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

## Real payload shape notes from the read-only lab

### Session list

`/session` returns a list of session summaries. Useful fields observed in practice:

- `id`
- `slug`
- `title`
- `directory`
- `version`
- `time.created`
- `time.updated`
- optional `permission`

### Messages

`/session/{id}/message` returns a list of messages, not the older `{id, message: {content: ...}}` shape.

Observed message structure:

- `info.id` — canonical message id
- `info.role` — usually `user` or `assistant`
- `info.time.created`
- optional `info.time.completed`
- optional `info.finish` — e.g. `stop`, `tool-calls`
- `parts[]` — ordered message parts

Observed part variants:

- `text` with `text`
- `tool` with `tool` and `state.status` / `state.output`
- `reasoning`
- `step-start`
- `step-finish`

Implication: snapshot code should normalize from `info.*` and `parts[]`, not from a synthetic top-level `message.content` object.

### Todo

`/session/{id}/todo` currently returns a list of items shaped like:

- `content`
- `status` (`completed`, `in_progress`, `pending` observed)
- `priority`

Implication: phase derivation should prefer:
1. active / in-progress item;
2. next pending item;
3. latest completed item.

Do not fall back to the first todo item when everything is already completed.

## Usage principle

Do not guess write/control endpoints unless they are verified.

For now, the skill should treat the OpenCode API layer primarily as:
- an observation source;
- a delta source;
- a future integration boundary for control operations.

## Current prototype direction

Use the API layer to build compact snapshots that feed the main-session decision loop.

Recommended normalized snapshot fields:

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
