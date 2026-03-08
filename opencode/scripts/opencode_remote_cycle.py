#!/usr/bin/env python3
import argparse
import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from opencode_api_client import OpenCodeClient
from opencode_cycle import load_state, write_json, now_iso, append_history, decide
from opencode_snapshot import compact_latest_message


def stable_digest(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def derive_phase(todo, fallback=None):
    if isinstance(todo, list):
        for item in todo:
            if isinstance(item, dict):
                status = str(item.get("status", "")).lower()
                if status in {"in_progress", "active", "running", "current", "pending"}:
                    return item.get("title") or item.get("content") or fallback
        for item in todo:
            if isinstance(item, dict):
                return item.get("title") or item.get("content") or fallback
    if isinstance(todo, dict):
        for key in ["phase", "title", "name", "current"]:
            if key in todo and todo.get(key):
                return todo.get(key)
    return fallback


def non_empty(value) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, tuple, dict, str, bytes)):
        return len(value) > 0
    return True


def derive_status(snapshot, previous_status="idle"):
    latest = snapshot.get("latestMessage") or {}
    permission = snapshot.get("permission")
    question = snapshot.get("question")
    errors = snapshot.get("errors") or {}

    if non_empty(permission) or non_empty(question):
        return "blocked"

    raw_status = str(latest.get("status") or "").lower()
    if raw_status in {"failed", "error"}:
        return "failed"

    if latest.get("completed") or str(latest.get("finish") or "").lower() == "stop" or str(latest.get("message.stopReason") or "").lower() == "stop":
        return "completed"

    if errors and previous_status not in {"completed", "failed", "blocked", "stalled", "deviated"}:
        return previous_status or "running"

    return "running"


def build_snapshot(client: OpenCodeClient, session_id: str):
    errors = {}

    def attempt(name, fn):
        try:
            return fn()
        except Exception as e:
            errors[name] = str(e)
            return None

    latest_message = attempt('latest_message', lambda: client.latest_message(session_id))
    todo = attempt('todo', lambda: client.session_todo(session_id))
    status = attempt('status', client.session_status)
    permission = attempt('permission', client.permission)
    question = attempt('question', client.question)

    return {
        "sessionId": session_id,
        "latestMessage": compact_latest_message(latest_message),
        "todo": todo,
        "status": status,
        "permission": permission,
        "question": question,
        "errors": errors,
    }


def snapshot_to_observation(snapshot, state):
    latest = snapshot.get("latestMessage") or {}
    latest_id = latest.get("id") or state.get("lastSeenMessageId")
    todo = snapshot.get("todo")
    todo_digest = stable_digest(todo) if todo is not None else state.get("lastTodoDigest")
    phase = derive_phase(todo, fallback=state.get("phase"))
    status = derive_status(snapshot, previous_status=state.get("status"))
    changed = False
    if latest_id and latest_id != state.get("lastSeenMessageId"):
        changed = True
    if todo_digest and todo_digest != state.get("lastTodoDigest"):
        changed = True
    if phase != state.get("phase"):
        changed = True
    if status != state.get("status"):
        changed = True

    observation = {
        "status": status,
        "phase": phase,
        "lastSeenMessageId": latest_id,
        "lastCompletedMessageId": latest_id if status == "completed" else state.get("lastCompletedMessageId"),
        "lastTodoDigest": todo_digest,
        "lastUpdatedMs": int(datetime.now(timezone.utc).timestamp() * 1000),
        "noChange": not changed,
        "visibleUpdate": False,
    }
    return observation


def main():
    p = argparse.ArgumentParser(description="Fetch remote OpenCode state and run one compact main-session decision cycle.")
    p.add_argument("--base-url", required=True)
    p.add_argument("--session-id", required=True)
    p.add_argument("--state", required=True)
    p.add_argument("--token")
    p.add_argument("--timeout", type=int, default=20)
    p.add_argument("--no-change-visible-after-min", type=int, default=30)
    p.add_argument("--write", action="store_true")
    args = p.parse_args()

    state_path = Path(args.state)
    before = load_state(state_path)
    after = deepcopy(before)

    client = OpenCodeClient(base_url=args.base_url, token=args.token, timeout=args.timeout)
    snapshot = build_snapshot(client, args.session_id)
    observation = snapshot_to_observation(snapshot, before)
    decision = decide(before, observation, no_change_visible_after_min=args.no_change_visible_after_min)

    after.update({k: v for k, v in observation.items() if k != "visibleUpdate"})
    after["lastDecision"] = decision["decision"]
    if observation.get("noChange"):
        after["consecutiveNoChangeCount"] = int(before.get("consecutiveNoChangeCount", 0)) + 1
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
        "snapshot": snapshot,
        "observation": observation,
        "decision": decision,
        "before": before,
        "after": after,
        "wrote": bool(args.write),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
