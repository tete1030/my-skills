#!/usr/bin/env python3
import argparse
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
    command += ["--timeout", str(args.timeout), "--message-limit", str(args.message_limit)]
    return run_json("opencode_snapshot.py", command)


def cmd_remote_cycle(args) -> int:
    command = [
        "--base-url", args.base_url,
        "--session-id", args.session_id,
        "--state", args.state,
        "--message-limit", str(args.message_limit),
        "--no-change-visible-after-min", str(args.no_change_visible_after_min),
    ]
    if args.control:
        command += ["--control", args.control]
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




def cmd_session_turn(args) -> int:
    command = [
        "--base-url", args.base_url,
        "--session-id", args.session_id,
        "--state", args.state,
        "--timeout", str(args.timeout),
        "--message-limit", str(args.message_limit),
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
    if args.include_payload:
        command.append("--include-payload")
    return run_json("opencode_session_turn.py", command)


def cmd_explain_turn(args) -> int:
    command = ["--input", args.input]
    return run_json("opencode_explain_turn.py", command)


def cmd_agent_turn_input(args) -> int:
    command = ["--input", args.input]
    return run_json("opencode_agent_turn_input.py", command)


def cmd_delivery_handoff(args) -> int:
    command = ["--input", args.input]
    if args.live_ready:
        command.append("--live-ready")
    return run_json("opencode_delivery_handoff.py", command)


def cmd_openclaw_agent_call(args) -> int:
    command = ["--input", args.input, "--timeout-ms", str(args.timeout_ms)]
    if args.execute:
        command.append("--execute")
    if args.allow_handoff_dry_run:
        command.append("--allow-handoff-dry-run")
    if args.expect_final:
        command.append("--expect-final")
    return run_json("opencode_openclaw_agent_call.py", command)


def cmd_watch(args) -> int:
    command = [
        "--base-url", args.base_url,
        "--session-id", args.session_id,
        "--state", args.state,
        "--timeout", str(args.timeout),
        "--message-limit", str(args.message_limit),
        "--no-change-visible-after-min", str(args.no_change_visible_after_min),
        "--interval-sec", str(args.interval_sec),
    ]
    if args.origin_session:
        command += ["--origin-session", args.origin_session]
    if args.origin_target:
        command += ["--origin-target", args.origin_target]
    if args.token:
        command += ["--token", args.token]
    if args.loop:
        command.append("--loop")
    if args.live:
        command.append("--live")

    script = SCRIPT_DIR / "opencode_watch_runner.py"
    proc = subprocess.run([PY, str(script), *command])
    return proc.returncode


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Unified control surface for the opencode skill prototypes. Happy-path turn output is structured facts plus cadence and delivery metadata."
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
    p_snap.add_argument("--message-limit", type=int, default=10)
    p_snap.set_defaults(func=cmd_snapshot)

    p_rc = sub.add_parser("remote-cycle", help="Fetch remote state and run one decision cycle.")
    p_rc.add_argument("--base-url", required=True)
    p_rc.add_argument("--session-id", required=True)
    p_rc.add_argument("--state", required=True)
    p_rc.add_argument("--control")
    p_rc.add_argument("--token")
    p_rc.add_argument("--timeout", type=int, default=20)
    p_rc.add_argument("--message-limit", type=int, default=10)
    p_rc.add_argument("--no-change-visible-after-min", type=int, default=30)
    p_rc.add_argument("--write", action="store_true")
    p_rc.set_defaults(func=cmd_remote_cycle)

    p_sc = sub.add_parser("scenario", help="Replay a multi-step local scenario through the decision loop.")
    p_sc.add_argument("--state", required=True)
    p_sc.add_argument("--scenario", required=True)
    p_sc.add_argument("--no-change-visible-after-min", type=int, default=30)
    p_sc.add_argument("--write", action="store_true")
    p_sc.set_defaults(func=cmd_scenario)



    p_turn = sub.add_parser("turn", help="Preferred happy path: run one main-session turn and emit structured facts plus cadence and delivery metadata.")
    p_turn.add_argument("--base-url", required=True)
    p_turn.add_argument("--session-id", required=True)
    p_turn.add_argument("--state", required=True)
    p_turn.add_argument("--control")
    p_turn.add_argument("--origin-session")
    p_turn.add_argument("--origin-target")
    p_turn.add_argument("--token")
    p_turn.add_argument("--timeout", type=int, default=20)
    p_turn.add_argument("--message-limit", type=int, default=10)
    p_turn.add_argument("--no-change-visible-after-min", type=int, default=30)
    p_turn.add_argument("--write", action="store_true")
    p_turn.add_argument("--payload-out")
    p_turn.add_argument("--include-payload", action="store_true")
    p_turn.set_defaults(func=cmd_session_turn)

    p_st = sub.add_parser("session-turn", help="Explicit name for the same happy-path structured turn workflow.")
    p_st.add_argument("--base-url", required=True)
    p_st.add_argument("--session-id", required=True)
    p_st.add_argument("--state", required=True)
    p_st.add_argument("--control")
    p_st.add_argument("--origin-session")
    p_st.add_argument("--origin-target")
    p_st.add_argument("--token")
    p_st.add_argument("--timeout", type=int, default=20)
    p_st.add_argument("--message-limit", type=int, default=10)
    p_st.add_argument("--no-change-visible-after-min", type=int, default=30)
    p_st.add_argument("--write", action="store_true")
    p_st.add_argument("--payload-out")
    p_st.add_argument("--include-payload", action="store_true")
    p_st.set_defaults(func=cmd_session_turn)

    p_et = sub.add_parser("explain-turn", help="Explain a structured turn result in compact debug form.")
    p_et.add_argument("--input", required=True)
    p_et.set_defaults(func=cmd_explain_turn)

    p_ati = sub.add_parser("agent-turn-input", help="Transform a structured turn result into compact main-agent input without rendering chat prose.")
    p_ati.add_argument("--input", required=True)
    p_ati.set_defaults(func=cmd_agent_turn_input)

    p_dh = sub.add_parser("delivery-handoff", help="Resolve a structured turn result (preferred) or compact agent input into an origin-session delivery handoff without authoring user-facing text.")
    p_dh.add_argument("--input", required=True, help="JSON file path or '-' for stdin")
    p_dh.add_argument("--live-ready", action="store_true", help="mark the handoff as non-dry-run metadata only; this still does not inject or send messages")
    p_dh.set_defaults(func=cmd_delivery_handoff)

    p_oac = sub.add_parser("openclaw-agent-call", help="Build or execute a safe openclaw gateway call agent command from a delivery-handoff result. Dry-run by default.")
    p_oac.add_argument("--input", required=True, help="delivery-handoff JSON path or '-' for stdin")
    p_oac.add_argument("--execute", action="store_true")
    p_oac.add_argument("--allow-handoff-dry-run", action="store_true")
    p_oac.add_argument("--expect-final", action="store_true")
    p_oac.add_argument("--timeout-ms", type=int, default=10000)
    p_oac.set_defaults(func=cmd_openclaw_agent_call)

    p_watch = sub.add_parser("watch", help="Run the tiny single-session watcher MVP over turn -> delivery-handoff -> openclaw-agent-call.")
    p_watch.add_argument("--base-url", required=True)
    p_watch.add_argument("--session-id", required=True)
    p_watch.add_argument("--state", required=True)
    p_watch.add_argument("--origin-session")
    p_watch.add_argument("--origin-target")
    p_watch.add_argument("--token")
    p_watch.add_argument("--timeout", type=int, default=20)
    p_watch.add_argument("--message-limit", type=int, default=10)
    p_watch.add_argument("--no-change-visible-after-min", type=int, default=30)
    p_watch.add_argument("--interval-sec", type=int, default=60)
    p_watch.add_argument("--loop", action="store_true")
    p_watch.add_argument("--live", action="store_true")
    p_watch.set_defaults(func=cmd_watch)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
