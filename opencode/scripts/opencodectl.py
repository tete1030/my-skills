#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PY = sys.executable


def run_json(script_name: str, args: list[str]) -> int:
    script = SCRIPT_DIR / script_name
    proc = subprocess.run([PY, str(script), *args], capture_output=True, text=True)
    if proc.returncode != 0:
        if proc.stdout:
            print(proc.stdout, end="", file=sys.stderr)
        if proc.stderr:
            print(proc.stderr, end="", file=sys.stderr)
        return proc.returncode
    if proc.stdout:
        print(proc.stdout, end="")
    return 0


def cmd_state_init(args) -> int:
    return run_json("opencode_control_state.py", ["init", "--state", args.state])


def cmd_state_show(args) -> int:
    return run_json("opencode_control_state.py", ["show", "--state", args.state])


def cmd_cycle(args) -> int:
    command = ["--state", args.state]
    if args.control:
        command += ["--control", args.control]
    if args.observation:
        command += ["--observation", args.observation]
    command += ["--no-change-visible-after-min", str(args.no_change_visible_after_min)]
    if args.write:
        command.append("--write")
    return run_json("opencode_cycle.py", command)


def cmd_snapshot(args) -> int:
    command = ["--base-url", args.base_url, "--session-id", args.session_id]
    if args.token:
        command += ["--token", args.token]
    command += ["--timeout", str(args.timeout)]
    return run_json("opencode_snapshot.py", command)


def cmd_remote_cycle(args) -> int:
    command = [
        "--base-url", args.base_url,
        "--session-id", args.session_id,
        "--state", args.state,
        "--no-change-visible-after-min", str(args.no_change_visible_after_min),
    ]
    if args.control:
        command += ["--control", args.control]
    if args.origin_session:
        command += ["--origin-session", args.origin_session]
    if args.origin_target:
        command += ["--origin-target", args.origin_target]
    if args.token:
        command += ["--token", args.token]
    command += ["--timeout", str(args.timeout)]
    if args.write:
        command.append("--write")
    return run_json("opencode_remote_cycle.py", command)



def cmd_scenario(args) -> int:
    command = [
        "--state", args.state,
        "--scenario", args.scenario,
        "--no-change-visible-after-min", str(args.no_change_visible_after_min),
    ]
    if args.write:
        command.append("--write")
    return run_json("opencode_scenario.py", command)


def cmd_render_update(args) -> int:
    command = ["--input", args.input]
    if args.quiet_when_empty:
        command.append("--quiet-when-empty")
    return run_json("opencode_render_update.py", command)


def cmd_session_turn(args) -> int:
    command = [
        "--base-url", args.base_url,
        "--session-id", args.session_id,
        "--state", args.state,
        "--timeout", str(args.timeout),
        "--no-change-visible-after-min", str(args.no_change_visible_after_min),
    ]
    if args.control:
        command += ["--control", args.control]
    if args.origin_session:
        command += ["--origin-session", args.origin_session]
    if args.origin_target:
        command += ["--origin-target", args.origin_target]
    if args.token:
        command += ["--token", args.token]
    if args.write:
        command.append("--write")
    if args.payload_out:
        command += ["--payload-out", args.payload_out]
    if args.update_out:
        command += ["--update-out", args.update_out]
    if args.quiet_when_empty:
        command.append("--quiet-when-empty")
    return run_json("opencode_session_turn.py", command)


def cmd_explain_turn(args) -> int:
    command = ["--input", args.input]
    return run_json("opencode_explain_turn.py", command)

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Unified control surface for the opencode skill prototypes."
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("state-init", help="Initialize local shared state.")
    p_init.add_argument("--state", required=True)
    p_init.set_defaults(func=cmd_state_init)

    p_show = sub.add_parser("state-show", help="Show local shared state.")
    p_show.add_argument("--state", required=True)
    p_show.set_defaults(func=cmd_state_show)

    p_cycle = sub.add_parser("cycle", help="Run one local control + observation + decision cycle.")
    p_cycle.add_argument("--state", required=True)
    p_cycle.add_argument("--control")
    p_cycle.add_argument("--observation")
    p_cycle.add_argument("--no-change-visible-after-min", type=int, default=30)
    p_cycle.add_argument("--write", action="store_true")
    p_cycle.set_defaults(func=cmd_cycle)

    p_snap = sub.add_parser("snapshot", help="Fetch a compact remote OpenCode snapshot.")
    p_snap.add_argument("--base-url", required=True)
    p_snap.add_argument("--session-id", required=True)
    p_snap.add_argument("--token")
    p_snap.add_argument("--timeout", type=int, default=20)
    p_snap.set_defaults(func=cmd_snapshot)

    p_rc = sub.add_parser("remote-cycle", help="Fetch remote state and run one decision cycle.")
    p_rc.add_argument("--base-url", required=True)
    p_rc.add_argument("--session-id", required=True)
    p_rc.add_argument("--state", required=True)
    p_rc.add_argument("--control")
    p_rc.add_argument("--origin-session")
    p_rc.add_argument("--origin-target")
    p_rc.add_argument("--token")
    p_rc.add_argument("--timeout", type=int, default=20)
    p_rc.add_argument("--no-change-visible-after-min", type=int, default=30)
    p_rc.add_argument("--write", action="store_true")
    p_rc.set_defaults(func=cmd_remote_cycle)

    p_sc = sub.add_parser("scenario", help="Replay a multi-step local scenario through the decision loop.")
    p_sc.add_argument("--state", required=True)
    p_sc.add_argument("--scenario", required=True)
    p_sc.add_argument("--no-change-visible-after-min", type=int, default=30)
    p_sc.add_argument("--write", action="store_true")
    p_sc.set_defaults(func=cmd_scenario)

    p_ru = sub.add_parser("render-update", help="Render a concise main-session progress update from a cycle payload.")
    p_ru.add_argument("--input", required=True)
    p_ru.add_argument("--quiet-when-empty", action="store_true")
    p_ru.set_defaults(func=cmd_render_update)

    p_turn = sub.add_parser("turn", help="Preferred happy path: run one main-session turn (control + remote observation + rendered update).")
    p_turn.add_argument("--base-url", required=True)
    p_turn.add_argument("--session-id", required=True)
    p_turn.add_argument("--state", required=True)
    p_turn.add_argument("--control")
    p_turn.add_argument("--origin-session")
    p_turn.add_argument("--origin-target")
    p_turn.add_argument("--token")
    p_turn.add_argument("--timeout", type=int, default=20)
    p_turn.add_argument("--no-change-visible-after-min", type=int, default=30)
    p_turn.add_argument("--write", action="store_true")
    p_turn.add_argument("--payload-out")
    p_turn.add_argument("--update-out")
    p_turn.add_argument("--quiet-when-empty", action="store_true")
    p_turn.set_defaults(func=cmd_session_turn)

    p_st = sub.add_parser("session-turn", help="Explicit name for the same happy-path turn workflow.")
    p_st.add_argument("--base-url", required=True)
    p_st.add_argument("--session-id", required=True)
    p_st.add_argument("--state", required=True)
    p_st.add_argument("--control")
    p_st.add_argument("--origin-session")
    p_st.add_argument("--origin-target")
    p_st.add_argument("--token")
    p_st.add_argument("--timeout", type=int, default=20)
    p_st.add_argument("--no-change-visible-after-min", type=int, default=30)
    p_st.add_argument("--write", action="store_true")
    p_st.add_argument("--payload-out")
    p_st.add_argument("--update-out")
    p_st.add_argument("--quiet-when-empty", action="store_true")
    p_st.set_defaults(func=cmd_session_turn)

    p_et = sub.add_parser("explain-turn", help="Explain why a session-turn did or did not emit a visible update.")
    p_et.add_argument("--input", required=True)
    p_et.set_defaults(func=cmd_explain_turn)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
