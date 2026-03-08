#!/usr/bin/env python3
import argparse
import json
from copy import deepcopy
from pathlib import Path

from opencode_cycle import load_state, write_json, apply_control, apply_observation, decide, now_iso, append_history


def load_json(path: Path):
    return json.loads(path.read_text())


def run_step(state, step, no_change_visible_after_min=30):
    before = deepcopy(state)
    control = step.get("control")
    observation = step.get("observation")
    label = step.get("label")

    if control:
        apply_control(state, deepcopy(control))
    if observation:
        apply_observation(state, deepcopy(observation))

    decision = decide(before, observation or {}, no_change_visible_after_min=no_change_visible_after_min)
    state["lastDecision"] = decision["decision"]
    if decision["decision"] == "visible_update":
        state["lastVisibleUpdateAt"] = now_iso()
    append_history(state, "scenario_step", {
        "label": label,
        "decision": decision,
        "control": control,
        "observation": observation,
    })
    return {
        "label": label,
        "control": control,
        "observation": observation,
        "decision": decision,
        "before": before,
        "after": deepcopy(state),
    }


def main():
    p = argparse.ArgumentParser(description="Replay a multi-step opencode scenario through the local cycle logic.")
    p.add_argument("--state", required=True)
    p.add_argument("--scenario", required=True)
    p.add_argument("--no-change-visible-after-min", type=int, default=30)
    p.add_argument("--write", action="store_true")
    args = p.parse_args()

    state_path = Path(args.state)
    scenario = load_json(Path(args.scenario))
    state = load_state(state_path)

    steps = scenario.get("steps") if isinstance(scenario, dict) else None
    if not isinstance(steps, list):
        raise SystemExit("scenario must be a JSON object with a 'steps' list")

    outputs = []
    for i, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            raise SystemExit(f"step {i} is not an object")
        if "label" not in step:
            step = {"label": f"step-{i}", **step}
        outputs.append(run_step(state, step, no_change_visible_after_min=args.no_change_visible_after_min))

    if args.write:
        write_json(state_path, state)

    print(json.dumps({
        "scenario": scenario.get("name") if isinstance(scenario, dict) else None,
        "steps": outputs,
        "finalState": state,
        "wrote": bool(args.write),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
