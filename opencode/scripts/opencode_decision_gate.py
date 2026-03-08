#!/usr/bin/env python3
import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEVERITY_ORDER = ["idle", "running", "completed", "failed", "blocked", "deviated", "stalled"]
SEVERITY_SCORE = {name: i for i, name in enumerate(SEVERITY_ORDER)}


def now_utc():
    return datetime.now(timezone.utc)


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def load_json(path: Path):
    return json.loads(path.read_text())


def recommend(state, obs, no_change_visible_after_min=30):
    current_status = state.get("status")
    next_status = obs.get("status", current_status)
    current_phase = state.get("phase")
    next_phase = obs.get("phase", current_phase)
    no_change = bool(obs.get("noChange", False))
    last_visible = parse_ts(state.get("lastVisibleUpdateAt"))
    consecutive = int(state.get("consecutiveNoChangeCount", 0))
    next_consecutive = consecutive + 1 if no_change else 0

    severe = next_status in {"completed", "failed", "blocked", "deviated", "stalled"}
    status_changed = next_status != current_status
    phase_changed = next_phase != current_phase

    if severe:
        return {
            "decision": "visible_update",
            "reason": f"status={next_status}",
            "nextConsecutiveNoChangeCount": next_consecutive,
        }

    if status_changed or phase_changed:
        return {
            "decision": "visible_update",
            "reason": "state_changed",
            "nextConsecutiveNoChangeCount": next_consecutive,
        }

    if no_change:
        if last_visible is None:
            return {
                "decision": "silent_noop",
                "reason": "no_change_initial_window",
                "nextConsecutiveNoChangeCount": next_consecutive,
            }
        age = now_utc() - last_visible
        if age >= timedelta(minutes=no_change_visible_after_min):
            return {
                "decision": "visible_update",
                "reason": f"no_change_age>={no_change_visible_after_min}m",
                "nextConsecutiveNoChangeCount": next_consecutive,
            }
        return {
            "decision": "silent_noop",
            "reason": "recent_visible_update_exists",
            "nextConsecutiveNoChangeCount": next_consecutive,
        }

    return {
        "decision": "silent_noop",
        "reason": "no_visible_condition_met",
        "nextConsecutiveNoChangeCount": next_consecutive,
    }


def main():
    p = argparse.ArgumentParser(description="Prototype visible-update gating for the opencode skill.")
    p.add_argument("--state", required=True, help="path to current state json")
    p.add_argument("--observation", required=True, help="path to observation json")
    p.add_argument("--no-change-visible-after-min", type=int, default=30)
    args = p.parse_args()

    state = load_json(Path(args.state))
    obs = load_json(Path(args.observation))
    result = recommend(state, obs, no_change_visible_after_min=args.no_change_visible_after_min)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
