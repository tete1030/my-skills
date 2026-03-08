# Turn Output Boundary

## Principle

Treat `opencodectl.py turn` as a **mechanical turn summarizer**, not as the final narrator.

The happy path should return:
- a compact fact skeleton;
- cadence / send-or-skip metadata;
- explicit delivery routing metadata.

The main-session agent owns the final user-facing explanation.

## Happy-path envelope

Prefer a turn result shaped like:

```json
{
  "factSkeleton": {
    "status": "running",
    "phase": "Collect verification status",
    "latestMeaningfulPreview": "Released v0.3.4 successfully.",
    "reason": "state_changed"
  },
  "shouldSend": true,
  "delivery": {
    "originSession": "<origin-session>",
    "originTarget": "<origin-target>"
  },
  "cadence": {
    "decision": "visible_update",
    "noChange": false,
    "consecutiveNoChangeCount": 0,
    "lastVisibleUpdateAt": "2026-03-08T09:40:00+00:00"
  }
}
```

## Field meanings

### `factSkeleton`

Keep this compact and factual.
Treat it as the stable machine-to-agent fact payload, not as ready-to-send prose.

- `status`: normalized state such as `running`, `blocked`, `failed`, or `completed`
- `phase`: best current phase / todo summary when available
- `latestMeaningfulPreview`: short recent output worth surfacing to the main agent
- `reason`: why the cadence layer chose this outcome

### `shouldSend`

Interpret this as the default visible-update recommendation, not final prose and not transport-enforced delivery.

- `true` means the cadence layer believes the main session should usually get a visible update
- `false` means the main agent can usually stay silent unless broader context says otherwise

### `delivery`

Preserve the original task-initiating destination.

Do not silently replace it with whichever lab, debug shell, or helper session ran the turn.
Treat this as explicit routing metadata that the main-session agent or transport layer must preserve when deciding where the final explanation goes.

### `cadence`

Expose the decision mechanics that help the main agent decide how noisy or terse to be.

Useful fields include:
- `decision`
- `noChange`
- `consecutiveNoChangeCount`
- `lastVisibleUpdateAt`

These fields help the main agent decide whether the final explanation should be a progress update, a heartbeat, a blocker/failure notice, or silence.
When a compact adapter is helpful, map cadence into recommendation metadata rather than directly rendered prose.

## Optional debug-only fields

The happy path should not require raw payloads.
If deeper inspection is needed, include raw payload only in explicit debug flows, not by default in every turn result.

Likewise, fallback rendered text should only appear when a caller explicitly asks for it (for example via a debug or compatibility path).
