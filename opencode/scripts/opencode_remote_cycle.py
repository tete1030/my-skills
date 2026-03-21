#!/usr/bin/env python3
import argparse
import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from opencode_api_client import OpenCodeClient
from opencode_cycle import load_state, write_json, now_iso, append_history, decide, apply_control
from opencode_snapshot import analyze_running_progress, build_compact_snapshot, summarize_transport_errors


ACTIVE_TODO_STATUSES = {"in_progress", "active", "running", "current"}
PENDING_TODO_STATUSES = {"pending", "todo", "queued", "next", "open"}



def stable_digest(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()



def load_json(path: str | None):
    if not path:
        return None
    return json.loads(Path(path).read_text())



def todo_pending_state(todo) -> bool:
    if isinstance(todo, dict):
        if todo.get("hasPendingWork") is not None:
            return bool(todo.get("hasPendingWork"))
        current = todo.get("current")
        if isinstance(current, dict) and current.get("content"):
            return True
        next_item = todo.get("next")
        if isinstance(next_item, dict) and next_item.get("content"):
            return True
        items = todo.get("items")
        if isinstance(items, list):
            return any(
                isinstance(item, dict) and item.get("status") in (ACTIVE_TODO_STATUSES | PENDING_TODO_STATUSES)
                for item in items
            )
    if isinstance(todo, list):
        return any(
            isinstance(item, dict) and str(item.get("status") or "").lower() in (ACTIVE_TODO_STATUSES | PENDING_TODO_STATUSES)
            for item in todo
        )
    return False



def derive_phase(todo, fallback=None):
    if isinstance(todo, dict):
        if todo.get("phase"):
            return todo.get("phase")
        current = todo.get("current")
        if isinstance(current, dict) and current.get("content"):
            return current.get("content")
        next_item = todo.get("next")
        if isinstance(next_item, dict) and next_item.get("content"):
            return next_item.get("content")
        latest_completed = todo.get("latestCompleted")
        if isinstance(latest_completed, dict) and latest_completed.get("content"):
            return latest_completed.get("content")
        for key in ["title", "name", "current"]:
            if key in todo and todo.get(key):
                return todo.get(key)
    if isinstance(todo, list):
        for item in todo:
            if isinstance(item, dict):
                status = str(item.get("status", "")).lower()
                if status in ACTIVE_TODO_STATUSES:
                    return item.get("title") or item.get("content") or fallback
        for item in todo:
            if isinstance(item, dict):
                status = str(item.get("status", "")).lower()
                if status in PENDING_TODO_STATUSES:
                    return item.get("title") or item.get("content") or fallback
        for item in reversed(todo):
            if isinstance(item, dict):
                return item.get("title") or item.get("content") or fallback
    return fallback



def non_empty(value) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, tuple, dict, str, bytes)):
        return len(value) > 0
    return True



def blocked_prompt_count(snapshot) -> int:
    count = snapshot.get("blockedPromptCount")
    if isinstance(count, int):
        return max(count, 0)
    pending_prompts = snapshot.get("pendingPrompts")
    if isinstance(pending_prompts, list):
        return len(pending_prompts)
    return -1


USAGE_LIMIT_HINT_TERMS = (
    "usage limit",
    "rate limit",
    "quota",
    "insufficient_quota",
    "too many requests",
    "429",
)


def session_status(snapshot):
    value = snapshot.get("sessionStatus")
    return value if isinstance(value, dict) else {}


def is_usage_limit_message(message) -> bool:
    normalized = str(message or "").strip().lower()
    if not normalized:
        return False
    return any(term in normalized for term in USAGE_LIMIT_HINT_TERMS)


def derive_status(snapshot, previous_status="idle"):
    latest = snapshot.get("latestMessage") or {}
    permission = snapshot.get("permission")
    question = snapshot.get("question")
    errors = snapshot.get("errors") or {}
    todo = snapshot.get("todo")

    prompt_count = blocked_prompt_count(snapshot)
    if prompt_count > 0:
        return "blocked"
    if prompt_count < 0 and (non_empty(permission) or non_empty(question)):
        return "blocked"

    raw_status = str(latest.get("status") or "").lower()
    if latest.get("message.aborted") or latest.get("message.errorName"):
        return "failed"
    if raw_status in {"failed", "error"}:
        return "failed"

    status_payload = session_status(snapshot)
    status_type = str(status_payload.get("type") or status_payload.get("status") or "").strip().lower()
    status_message = str(status_payload.get("message") or status_payload.get("reason") or "").strip()
    if status_type in {"failed", "error"}:
        return "failed"
    if status_type == "retry" and status_message and is_usage_limit_message(status_message):
        return "blocked"

    role = str(latest.get("role") or "").lower()
    finish = str(latest.get("finish") or latest.get("message.stopReason") or "").lower()
    has_text = bool(latest.get("hasText") or latest.get("message.lastTextPreview"))
    has_tool_calls = bool(latest.get("hasToolCalls"))
    tool_statuses = {str(status).lower() for status in (latest.get("toolStatuses") or [])}

    if role == "user":
        return "running"

    has_terminal_completion = bool(
        raw_status in {"completed", "done", "stopped", "success", "succeeded"}
        or finish == "stop"
        or latest.get("completed")
        or latest.get("completedAt")
    )
    if has_terminal_completion:
        return "completed"

    if todo_pending_state(todo):
        return "running"

    if latest.get("completed") and has_text:
        return "completed"

    if latest.get("completed") and has_tool_calls and not has_text and (not tool_statuses or tool_statuses <= {"completed", "done", "finished", "succeeded", "success", "ok"}):
        return "completed"

    if errors and previous_status not in {"completed", "failed", "blocked", "stalled", "deviated"}:
        return previous_status or "running"

    return "running"



def build_snapshot(client: OpenCodeClient, session_id: str, message_limit: int = 10, directory: str | None = None):
    snapshot, _errors = build_compact_snapshot(
        client,
        session_id,
        message_limit=message_limit,
        directory=directory,
        workspace=directory,
    )
    return snapshot



def snapshot_to_observation(snapshot, state):
    latest = snapshot.get("latestMessage") or {}
    latest_id = latest.get("id") or state.get("lastSeenMessageId")
    todo = snapshot.get("todo")
    todo_digest = stable_digest(todo) if todo is not None else state.get("lastTodoDigest")
    status = derive_status(snapshot, previous_status=state.get("status"))
    blocked_phase = snapshot.get("blockedPhase")
    session_status_payload = session_status(snapshot)
    session_status_message = str(session_status_payload.get("message") or session_status_payload.get("reason") or "").strip()
    session_status_type = str(session_status_payload.get("type") or session_status_payload.get("status") or "").strip().lower()
    if not blocked_phase and status == "blocked" and session_status_type == "retry":
        blocked_phase = "model usage limit retry" if is_usage_limit_message(session_status_message) else "provider retry"
    phase = blocked_phase if status == "blocked" and blocked_phase else derive_phase(todo, fallback=state.get("phase"))
    blocked_prompt_key = snapshot.get("blockedPromptKey")
    blocked_prompt_count_value = blocked_prompt_count(snapshot)
    if blocked_prompt_count_value < 0:
        blocked_prompt_count_value = None
    blocked_summary = snapshot.get("blockedSummary") or (session_status_message if status == "blocked" and session_status_message else None)
    last_updated_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    progress_observation = analyze_running_progress(snapshot, current_status=status, now_ms=last_updated_ms)
    transport_error_hints = summarize_transport_errors(snapshot.get("errors"))
    changed = False
    if latest_id and latest_id != state.get("lastSeenMessageId"):
        changed = True
    if todo_digest and todo_digest != state.get("lastTodoDigest"):
        changed = True
    if phase != state.get("phase"):
        changed = True
    if status != state.get("status"):
        changed = True
    if blocked_prompt_key != state.get("lastBlockedPromptKey"):
        changed = True
    if blocked_prompt_count_value != state.get("blockedPromptCount"):
        changed = True
    if blocked_summary != state.get("blockedSummary"):
        changed = True

    observation = {
        "status": status,
        "phase": phase,
        "lastSeenMessageId": latest_id,
        "lastCompletedMessageId": latest_id if status == "completed" else state.get("lastCompletedMessageId"),
        "lastTodoDigest": todo_digest,
        "lastBlockedPromptKey": blocked_prompt_key,
        "blockedPromptCount": blocked_prompt_count_value,
        "blockedSummary": blocked_summary,
        "lastUpdatedMs": last_updated_ms,
        "noChange": not changed,
        "visibleUpdate": False,
        "runningProgressObservation": progress_observation,
        "transportErrorHints": transport_error_hints,
    }
    return observation



def main():
    p = argparse.ArgumentParser(description="Fetch remote OpenCode state and run one compact main-session decision cycle.")
    p.add_argument("--base-url", required=True)
    p.add_argument("--session-id", required=True)
    p.add_argument("--state", required=True)
    p.add_argument("--control")
    p.add_argument("--token")
    p.add_argument("--timeout", type=int, default=20)
    p.add_argument("--message-limit", type=int, default=10)
    p.add_argument("--no-change-visible-after-min", type=int, default=30)
    p.add_argument("--write", action="store_true")
    args = p.parse_args()

    state_path = Path(args.state)
    before = load_state(state_path)
    effective_before = deepcopy(before)
    control = load_json(args.control)
    if control:
        apply_control(effective_before, control)
    after = deepcopy(effective_before)

    client = OpenCodeClient(base_url=args.base_url, token=args.token, timeout=args.timeout)
    snapshot = build_snapshot(client, args.session_id, message_limit=args.message_limit)
    observation = snapshot_to_observation(snapshot, effective_before)
    decision = decide(effective_before, observation, no_change_visible_after_min=args.no_change_visible_after_min)

    after.update({k: v for k, v in observation.items() if k != "visibleUpdate"})
    after["lastDecision"] = decision["decision"]
    if observation.get("noChange"):
        after["consecutiveNoChangeCount"] = int(effective_before.get("consecutiveNoChangeCount", 0)) + 1
    else:
        after["consecutiveNoChangeCount"] = 0
    if decision["decision"] == "visible_update":
        after["lastVisibleUpdateAt"] = now_iso()
    append_history(after, "remote_cycle", {
        "sessionId": args.session_id,
        "decision": decision,
        "observation": observation,
    })

    if args.write:
        write_json(state_path, after)

    print(json.dumps({
        "control": control,
        "snapshot": snapshot,
        "observation": observation,
        "decision": decision,
        "before": before,
        "effectiveBefore": effective_before,
        "after": after,
        "wrote": bool(args.write),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
