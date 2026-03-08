# State Flow

## Shared state

Recommended fields:

- `executionMode`
- `status`
- `phase`
- `lastSeenMessageId`
- `lastCompletedMessageId`
- `lastTodoDigest`
- `lastUpdatedMs`
- `lastDecision`
- `lastVisibleUpdateAt`
- `consecutiveNoChangeCount`
- `lastNotifiedState`

## Timed triggers

Timed triggers should create a decision opportunity even when there is no explicit change.

Timed triggers should not blindly dump a visible message every time. They should instead:
- feed compact state into the main session;
- let the main session decide whether visible output is needed.

## Event triggers

Event triggers should capture important changes quickly, such as:
- completion;
- failure;
- blocked permission/question;
- clear phase movement;
- important checkpoint movement.

## No-change policy

No-change still enters the decision layer.

Recommended presentation policy:
- short-interval no-change: usually silent;
- accumulated no-change: compress into one short visible update;
- no-change that still causes an action: make that action visible.
