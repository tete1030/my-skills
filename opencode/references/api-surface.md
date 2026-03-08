# OpenCode API Surface (working notes)

This reference tracks the currently assumed OpenCode API surface used by the skill prototypes.

## Known read / observe endpoints

- `/doc`
- `/session`
- `/session/{id}/message`
- `/session/status`
- `/pty`
- `/event`
- `/global/event`
- `/permission`
- `/question`

## Usage principle

Do not guess write/control endpoints unless they are verified.

For now, the skill should treat the OpenCode API layer primarily as:
- an observation source;
- a delta source;
- a future integration boundary for control operations.

## Current prototype direction

Use the API layer to build compact snapshots that feed the main-session decision loop.
