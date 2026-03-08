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

- `status`: normalized state such as `running`, `blocked`, `failed`, or `completed`
- `phase`: best current phase / todo summary when available
- `latestMeaningfulPreview`: short recent output worth surfacing to the main agent
- `reason`: why the cadence layer chose this outcome

### `shouldSend`

Interpret this as routing intent, not final prose.

- `true` means the cadence layer believes the main session should usually get a visible update
- `false` means the main agent can usually stay silent unless broader context says otherwise

### `delivery`

Preserve the original task-initiating destination.

Do not silently replace it with whichever lab, debug shell, or helper session ran the turn.

### `cadence`

Expose the decision mechanics that help the main agent decide how noisy or terse to be.

Useful fields include:
- `decision`
- `noChange`
- `consecutiveNoChangeCount`
- `lastVisibleUpdateAt`

## Render fallback

`render-update` remains available, but only as a fallback/debug helper.

Use it when:
- debugging the turn result;
- producing a temporary generic sentence for inspection;
- maintaining compatibility with older experiments.

Do **not** treat fallback rendered text as the authoritative main-session explanation.
