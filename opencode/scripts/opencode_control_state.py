#!/usr/bin/env python3
import argparse
import json
import sys
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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path):
    return json.loads(path.read_text())


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def load_state(path: Path):
    if not path.exists():
        return deepcopy(DEFAULT_STATE)
    data = read_json(path)
    merged = deepcopy(DEFAULT_STATE)
    merged.update(data)
    return merged


def append_history(state, kind, payload):
    state.setdefault("history", []).append({
        "ts": now_iso(),
        "kind": kind,
        "payload": payload,
    })


def cmd_init(args):
    state = deepcopy(DEFAULT_STATE)
    write_json(Path(args.state), state)
    print(json.dumps(state, ensure_ascii=False, indent=2))


def cmd_apply_control(args):
    state_path = Path(args.state)
    state = load_state(state_path)
    control = read_json(Path(args.input))
    unknown = sorted(set(control) - ALLOWED_CONTROL_KEYS)
    if unknown:
        raise SystemExit(f"unsupported control keys: {', '.join(unknown)}")
    state.update(control)
    append_history(state, "control", control)
    write_json(state_path, state)
    print(json.dumps(state, ensure_ascii=False, indent=2))


def cmd_apply_observation(args):
    state_path = Path(args.state)
    state = load_state(state_path)
    obs = read_json(Path(args.input))
    unknown = sorted(set(obs) - ALLOWED_OBSERVATION_KEYS)
    if unknown:
        raise SystemExit(f"unsupported observation keys: {', '.join(unknown)}")

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
    write_json(state_path, state)
    print(json.dumps(state, ensure_ascii=False, indent=2))


def cmd_show(args):
    state = load_state(Path(args.state))
    print(json.dumps(state, ensure_ascii=False, indent=2))


def build_parser():
    p = argparse.ArgumentParser(description="Prototype control/state flow for the opencode skill.")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init")
    p_init.add_argument("--state", required=True)
    p_init.set_defaults(func=cmd_init)

    p_ctl = sub.add_parser("apply-control")
    p_ctl.add_argument("--state", required=True)
    p_ctl.add_argument("--input", required=True)
    p_ctl.set_defaults(func=cmd_apply_control)

    p_obs = sub.add_parser("apply-observation")
    p_obs.add_argument("--state", required=True)
    p_obs.add_argument("--input", required=True)
    p_obs.set_defaults(func=cmd_apply_observation)

    p_show = sub.add_parser("show")
    p_show.add_argument("--state", required=True)
    p_show.set_defaults(func=cmd_show)
    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
