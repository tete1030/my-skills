# Reporting Policy

## Goal

Mechanical layers should produce concise **facts plus cadence**, not own the final user-facing narrative.

The main-session agent should read the turn result, decide whether to speak, and write the final explanation in the live conversation context.

## Principles

- Prefer compact fact skeletons over prewritten chat prose.
- Keep `shouldSend` separate from the final wording.
- Preserve delivery routing metadata so explanation goes back to the originating session.
- Expose cadence/no-change state so the main agent can stay quiet or be brief on purpose.
- Avoid replaying raw API payloads into chat.
- If a helper is used, make it emit recommendation/input metadata for the main agent rather than rendered message text.

## Happy-path output

The normal turn output should emphasize:
- `factSkeleton.status`
- `factSkeleton.phase`
- `factSkeleton.latestMeaningfulPreview`
- `factSkeleton.reason`
- `shouldSend`
- `delivery`
- `cadence`

This is enough for the main-session agent to decide:
- whether to send anything now;
- how much detail to include;
- whether the update is routine, corrective, or escalatory.

## How the main agent should use it

Consume the fields in this order:
1. `shouldSend`
2. `delivery`
3. `factSkeleton`
4. `cadence`

That keeps send/skip, routing, facts, and explanation style separate.

### Running / phase moved
Use the fact skeleton to say what changed and what the agent is doing now.

### No-change visible heartbeat
Use cadence fields to keep the update short and avoid sounding like a black-box timer.

### Blocked
Explain what is blocking and whether user input, approval, or intervention is likely needed.

### Failed
Explain that the task appears failed and what inspection or recovery should happen next.

### Completed
Explain that the task appears complete and summarize the latest meaningful preview if it helps.

