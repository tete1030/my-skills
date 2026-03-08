#!/usr/bin/env python3
import argparse
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_STATE = {
    "executionMode": "main_session_centered",
    "executionCenter": "main_session",
    "progressVisibility": "visible",
    "discussionCoupling": "tight",
    "status": "idle",
    "phase": None,
    "lastSeenMessageId": None,
    "lastCompletedMessageId": None,
    "lastTodoDigest": None,
    "lastUpdatedMs": None,
    "lastDecision": None,
    "lastVisibleUpdateAt": None,
    "consecutiveNoChangeCount": 0,
    "lastNotifiedState": None,
    "history": [],
}

ALLOWED_CONTROL_KEYS = {
    "executionMode",
    "executionCenter",
    "progressVisibility",
    "discussionCoupling",
    "status",
    "phase",
    "lastDecision",
}

ALLOWED_OBSERVATION_KEYS = {
    "status",
    "phase",
    "lastSeenMessageId",
    "lastCompletedMessageId",
    "lastTodoDigest",
    "lastUpdatedMs",
    "lastNotifiedState",
    "noChange",
    "visibleUpdate",
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def load_json(path: Path):
    return json.loads(path.read_text())


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def load_state(path: Path):
    if not path.exists():
        return deepcopy(DEFAULT_STATE)
    data = load_json(path)
    merged = deepcopy(DEFAULT_STATE)
    merged.update(data)
    return merged


def append_history(state, kind, payload):
    state.setdefault("history", []).append({
        "ts": now_iso(),
        "kind": kind,
        "payload": payload,
    })


def apply_control(state, control):
    unknown = sorted(set(control) - ALLOWED_CONTROL_KEYS)
    if unknown:
        raise ValueError(f"unsupported control keys: {', '.join(unknown)}")
    state.update(control)
    append_history(state, "control", control)


def apply_observation(state, obs):
    unknown = sorted(set(obs) - ALLOWED_OBSERVATION_KEYS)
    if unknown:
        raise ValueError(f"unsupported observation keys: {', '.join(unknown)}")
    no_change = bool(obs.pop("noChange", False))
    visible_update = bool(obs.pop("visibleUpdate", False))
    state.update(obs)
    if no_change:
        state["consecutiveNoChangeCount"] = int(state.get("consecutiveNoChangeCount", 0)) + 1
    else:
        state["consecutiveNoChangeCount"] = 0
    if visible_update:
        state["lastVisibleUpdateAt"] = now_iso()
    append_history(state, "observation", {**obs, "noChange": no_change, "visibleUpdate": visible_update})
    return no_change


def decide(state, incoming_observation, no_change_visible_after_min=30):
    current_status = state.get("status")
    current_phase = state.get("phase")
    obs_status = incoming_observation.get("status", current_status)
    obs_phase = incoming_observation.get("phase", current_phase)
    no_change = bool(incoming_observation.get("noChange", False))
    last_visible = parse_ts(state.get("lastVisibleUpdateAt"))

    if obs_status in {"completed", "failed", "blocked", "deviated", "stalled"}:
        return {"decision": "visible_update", "reason": f"status={obs_status}"}
    if obs_status != current_status or obs_phase != current_phase:
        return {"decision": "visible_update", "reason": "state_changed"}
    if no_change:
        if last_visible is None:
            return {"decision": "silent_noop", "reason": "no_change_initial_window"}
        age_sec = (datetime.now(timezone.utc) - last_visible).total_seconds()
        if age_sec >= no_change_visible_after_min * 60:
            return {"decision": "visible_update", "reason": f"no_change_age>={no_change_visible_after_min}m"}
        return {"decision": "silent_noop", "reason": "recent_visible_update_exists"}
    return {"decision": "silent_noop", "reason": "no_visible_condition_met"}


def main():
    p = argparse.ArgumentParser(description="Run one compact opencode control/observation/decision cycle.")
    p.add_argument("--state", required=True)
    p.add_argument("--control")
    p.add_argument("--observation")
    p.add_argument("--no-change-visible-after-min", type=int, default=30)
    p.add_argument("--write", action="store_true", help="persist updated state back to --state")
    args = p.parse_args()

    state_path = Path(args.state)
    before = load_state(state_path)
    after = deepcopy(before)

    control = load_json(Path(args.control)) if args.control else None
    observation = load_json(Path(args.observation)) if args.observation else None

    if control:
        apply_control(after, control)
    if observation:
        apply_observation(after, dict(observation))

    decision = decide(before, observation or {}, no_change_visible_after_min=args.no_change_visible_after_min)
    after["lastDecision"] = decision["decision"]
    if decision["decision"] == "visible_update":
        after["lastVisibleUpdateAt"] = now_iso()

    if args.write:
        write_json(state_path, after)

    print(json.dumps({
        "control": control,
        "observation": observation,
        "decision": decision,
        "before": before,
        "after": after,
        "wrote": bool(args.write),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
