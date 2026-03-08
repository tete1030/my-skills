# Reporting Policy

## Goal

The system should not stop at state and decision logic. It should also produce concise, human-readable progress updates suitable for the main session.

## Principles

- Prefer short updates over long explanations.
- Only produce a visible update when the decision layer says one is warranted.
- Distinguish between:
  - phase/state movement;
  - blocked/failed/completed conditions;
  - accumulated no-change summaries.
- Avoid replaying raw API payloads into chat.

## Recommended output categories

### Running / phase moved
Explain briefly:
- what changed;
- current phase;
- short preview of latest meaningful text, if available.

### No-change visible heartbeat
Explain briefly:
- still running;
- no significant change;
- monitoring continues.

### Blocked
Explain clearly:
- what is blocking;
- whether user input or approval seems required.

### Failed
Explain clearly:
- task appears failed;
- agent should inspect or intervene.

### Completed
Explain clearly:
- task appears complete;
- summarize latest useful result if present.
