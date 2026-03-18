# Watcher permission-gap: minimal remediation plan

## Goal
Close the main gap between the current watcher and OpenCode UI behavior for permission/question prompts, without broad redesign.

## Confirmed current state in this repo

### What already exists
- The watcher is **poll-driven**: `watch-runtime -> opencodectl watch -> watch_runner -> turn -> remote_cycle -> snapshot`.
- `opencode_snapshot.py` already fetches:
  - `/session/{id}/message`
  - `/session/{id}/todo`
  - `/session/status`
  - `/permission`
  - `/question`
- `derive_status()` in `opencode_remote_cycle.py` marks the session as `blocked` whenever `permission` or `question` is non-empty.
- The rest of the pipeline already treats `blocked` as critical/visible.

### Main gaps
1. **Prompt identity is discarded.**
   - `/permission` and `/question` are only used as truthy/falsy blocked signals.
   - No stable prompt key is kept.
   - No linkage to `messageID` / `callID` is surfaced.

2. **Blocked state is not session-scoped enough.**
   - Snapshot fetches global `/permission` and `/question`, but current normalization does not clearly filter/annotate them against the watched session using session-tree semantics.
   - That means the watcher cannot confidently say *which* prompt belongs to *this* session when multiple sessions exist.

3. **Blocked updates are low-detail.**
   - `phase` is still mostly derived from todo state.
   - `latestMeaningfulPreview` is based on message/event summaries, not the live pending prompt.
   - Result: a permission/question can be detected, but the watcher cannot produce an operator-grade blocker summary like the UI can.

4. **No event-driven wake-up path.**
   - `/event` and `/global/event` are listed as known observe endpoints, but there is no SSE/event consumer in the watcher.
   - So prompt appearance/clearance is only noticed on the next poll.

5. **No actionable prompt handle for later control work.**
   - Even if write/control endpoints are added later, today’s watcher output does not preserve the identifiers needed to safely answer/approve the exact pending prompt.

## Smallest coherent fix (Phase 1)

### Decision
**Do not add auto-approval now.**

Reason:
- this repo has verified read endpoints for `/permission` and `/question`, but no verified permission/question write endpoint;
- auto-approval is risky even if a control endpoint later exists;
- the main gap is currently **detection + attribution + operator context**, not unattended action.

### Phase 1 scope
Keep the watcher polling architecture, but make blocked prompts first-class snapshot state.

### Phase 1 design
1. **Normalize pending prompts from REST bootstrap**
   - In `opencode_snapshot.py`, replace the current raw `permission` / `question` pass-through with a normalized prompt view:
     - `pendingPrompts[]`
     - `blockedSummary`
     - `blockedPhase`
     - `blockedPromptKey`
     - `blockedPromptCount`
   - `pendingPrompts[]` should preserve, when present:
     - `kind` = `permission` or `question`
     - prompt/session id fields from upstream payload
     - `messageID` / `messageId` if present
     - `callID` / `callId` if present
     - concise human summary/title/body preview
     - session-tree / parent-child linkage fields if present

2. **Scope prompts to the watched session before treating them as blockers**
   - Add a small selector in snapshot normalization:
     - prefer prompts explicitly tied to the watched `session_id`;
     - if upstream payload uses session-tree references, treat descendants/active branch prompts for this session as relevant;
     - if scoping is uncertain, keep raw prompt data in debug fields but do **not** silently attribute unrelated prompts to this session.
   - REST `/permission` + `/question` stays the source of truth for prompt presence.

3. **Create a stable blocker identity**
   - Compute `blockedPromptKey` from the best available identity tuple, in priority order:
     - prompt id
     - `(kind, session-id-or-tree-node, messageID, callID)`
     - fallback digest of normalized prompt payload
   - This is the key part that closes the current dedupe gap.

4. **Make blocked prompts change visible state**
   - In `opencode_remote_cycle.py`, incorporate blocked prompt identity into observation state so that:
     - a new permission replaces an old permission => `noChange = false`
     - question -> permission transition => state change
     - same blocked status but different prompt => still visible as a new blocker
   - Minimal way: persist `lastBlockedPromptKey` (and optionally `lastBlockedPromptDigest`) in state and compare it in `snapshot_to_observation()`.

5. **Surface blocker detail in phase/preview**
   - When blocked:
     - `phase` should come from `blockedPhase` instead of todo phase.
     - `latestMeaningfulPreview` should prefer `blockedSummary`.
   - This lets the existing `turn -> delivery-handoff -> main-session inspect` path produce useful operator-facing blocker notices without redesigning the handoff envelope.

6. **Expose prompt refs in inspect output**
   - `opencode_manager.py inspect` should show the current normalized blocker details:
     - prompt kind
     - summary
     - prompt key
     - messageID/callID if present
   - This gives the main session enough context to explain the block and prepares the system for later explicit approve/answer commands.

### Why this is enough for Phase 1
It fixes the important behavioral gap:
- watcher notices permission/question prompts as distinct blocker events;
- blocker updates are attributable and inspectable;
- main-session agent gets current-state wording closer to the UI;
- no risky control path is introduced yet.

## Optional later improvements (Phase 2+)

### Phase 2: SSE-assisted wake-up
Add a small event listener in `opencode_api_client.py` for `/event` or `/global/event`.

Use SSE only as a **wake signal**, not as the source of truth:
- on relevant prompt/session event, trigger immediate refresh;
- continue to re-read `/permission` and `/question` to build authoritative normalized prompt state.

Why later:
- this improves latency and reduces prompt blind windows,
- but it is not required to fix attribution/dedupe/actionability.

### Phase 3: explicit operator actions
Only after upstream control endpoints are verified:
- approve/deny permission explicitly;
- answer question explicitly;
- require exact prompt key match;
- log the acted-on prompt identity.

### Explicit non-goal for now
- **No automatic approval.**
- If approval support is later added, it should be explicit and operator-driven first.

## Safest implementation order
1. **Snapshot normalization first**
   - add prompt normalization and stable blocker identity.
2. **Observation/state change logic second**
   - treat prompt-key changes as real watcher changes.
3. **Inspect output third**
   - make blocker details visible to operators and the main session.
4. **Only then consider SSE wake-up**
   - optimize latency after semantics are correct.

This order keeps the change set small and testable.

## Files most likely to change

### Required for Phase 1
- `scripts/opencode_snapshot.py`
  - normalize `/permission` + `/question`
  - compute `pendingPrompts`, `blockedSummary`, `blockedPhase`, `blockedPromptKey`
- `scripts/opencode_remote_cycle.py`
  - derive blocked observation from normalized prompt state
  - persist/compare blocker identity in state
- `scripts/opencode_session_turn.py`
  - prefer blocker summary for `latestMeaningfulPreview` when blocked
- `scripts/opencode_manager.py`
  - expose normalized blocker details in inspect output

### Likely supporting updates
- `scripts/opencode_api_client.py`
  - optional helper methods for cleaner permission/question fetch normalization
  - later: SSE support
- `scripts/opencode_cycle.py`
  - only if state schema/defaults need an added blocker identity field

### Tests
- `tests/test_snapshot_normalization.py`
- `tests/test_turn_output.py`
- `tests/test_manager.py`
- `tests/test_api_client.py` (later if SSE helpers are added)

## Practical recommendation
Implement **Phase 1 only** next:
- keep polling;
- preserve prompt identity;
- scope prompts to the watched session/session-tree branch;
- make blocker changes visible even when overall status stays `blocked`;
- surface messageID/callID for operator context;
- do **not** add auto-approval.

That is the smallest coherent watcher improvement that materially closes the current permission/question gap.
