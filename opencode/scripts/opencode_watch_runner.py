#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PY = sys.executable
SCRIPT_DIR = Path(__file__).resolve().parent
OPENCODECTL = SCRIPT_DIR / "opencodectl.py"
WATCH_STATE_KEY = "watchRunner"
TERMINAL_OR_IDLE_STATUSES = {"completed", "failed", "blocked", "deviated", "stalled", "idle"}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat()


def parse_iso_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def load_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"state file must contain a JSON object: {path}")
    return data


def write_json_file(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def run_opencodectl(args: list[str], stdin_text: str | None = None) -> dict[str, Any]:
    proc = subprocess.run(
        [PY, str(OPENCODECTL), *args],
        input=stdin_text,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        detail = []
        if proc.stdout:
            detail.append(f"stdout:\n{proc.stdout}")
        if proc.stderr:
            detail.append(f"stderr:\n{proc.stderr}")
        joined = "\n".join(detail)
        raise RuntimeError(f"opencodectl {' '.join(args)} failed with code {proc.returncode}\n{joined}".rstrip())
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"opencodectl {' '.join(args)} returned non-JSON output") from exc


def action_key_from_agent_call(agent_call: dict[str, Any]) -> str | None:
    params = agent_call.get("gatewayParams")
    if isinstance(params, dict):
        key = params.get("idempotencyKey")
        if isinstance(key, str) and key:
            return key
    return None


def watch_activity_signature(turn: dict[str, Any], agent_call: dict[str, Any]) -> str:
    fact = turn.get("factSkeleton") if isinstance(turn.get("factSkeleton"), dict) else {}
    cadence = turn.get("cadence") if isinstance(turn.get("cadence"), dict) else {}
    signature_payload = {
        "status": fact.get("status"),
        "phase": fact.get("phase"),
        "preview": fact.get("latestMeaningfulPreview"),
        "decision": cadence.get("decision"),
        "actionKey": action_key_from_agent_call(agent_call),
        "routeStatus": agent_call.get("routeStatus"),
        "deliveryAction": agent_call.get("deliveryAction"),
    }
    return json.dumps(signature_payload, ensure_ascii=False, sort_keys=True)


def idle_timeout_reason(turn: dict[str, Any]) -> str | None:
    fact = turn.get("factSkeleton") if isinstance(turn.get("factSkeleton"), dict) else {}
    status = str(fact.get("status") or "").strip().lower()
    if status in TERMINAL_OR_IDLE_STATUSES:
        return f"terminal_status:{status}"
    return None


def decide_watch_action(agent_call: dict[str, Any], watch_state: dict[str, Any], *, live: bool) -> dict[str, Any]:
    delivery_action = agent_call.get("deliveryAction")
    route_status = agent_call.get("routeStatus")
    action_key = action_key_from_agent_call(agent_call)

    if delivery_action != "inject" or route_status != "ready":
        return {
            "mode": "live" if live else "dry-run",
            "operation": "skip",
            "shouldExecute": False,
            "duplicateSuppressed": False,
            "actionKey": action_key,
            "reason": agent_call.get("reason") or "not_ready",
        }

    if live and action_key and watch_state.get("lastExecutedActionKey") == action_key:
        return {
            "mode": "live",
            "operation": "skip_duplicate",
            "shouldExecute": False,
            "duplicateSuppressed": True,
            "actionKey": action_key,
            "reason": "duplicate_action_key",
        }

    return {
        "mode": "live" if live else "dry-run",
        "operation": "execute" if live else "plan",
        "shouldExecute": bool(live),
        "duplicateSuppressed": False,
        "actionKey": action_key,
        "reason": "ready_inject_live" if live else "ready_inject_dry_run",
    }


def update_watch_state(
    state_path: Path,
    *,
    session_id: str,
    watch_action: dict[str, Any],
    agent_call: dict[str, Any],
    turn: dict[str, Any],
    idle_timeout_sec: int = 0,
) -> dict[str, Any]:
    current_time = now_iso()
    state_doc = load_json_file(state_path)
    watch_state = dict(state_doc.get(WATCH_STATE_KEY) or {})
    previous_signature = watch_state.get("lastActivitySignature")
    activity_signature = watch_activity_signature(turn, agent_call)
    activity_changed = activity_signature != previous_signature

    fact = turn.get("factSkeleton") if isinstance(turn.get("factSkeleton"), dict) else {}
    cadence = turn.get("cadence") if isinstance(turn.get("cadence"), dict) else {}
    status = fact.get("status")
    idle_reason = idle_timeout_reason(turn)

    watch_state.update({
        "sessionId": session_id,
        "lastRunAt": current_time,
        "lastMode": watch_action["mode"],
        "lastOperation": watch_action["operation"],
        "lastReason": watch_action["reason"],
        "lastActionKey": watch_action.get("actionKey"),
        "lastRouteStatus": agent_call.get("routeStatus"),
        "lastDeliveryAction": agent_call.get("deliveryAction"),
        "lastDuplicateSuppressed": bool(watch_action.get("duplicateSuppressed")),
        "lastFactStatus": status,
        "lastFactPhase": fact.get("phase"),
        "lastPreview": fact.get("latestMeaningfulPreview"),
        "lastCadenceDecision": cadence.get("decision"),
        "lastNoChange": cadence.get("noChange"),
        "idleTimeoutSec": idle_timeout_sec,
        "lastActivitySignature": activity_signature,
    })

    if activity_changed or not watch_state.get("lastActivityAt"):
        watch_state["lastActivityAt"] = current_time

    if watch_action["operation"] == "plan":
        watch_state["lastPlannedActionKey"] = watch_action.get("actionKey")
        watch_state["lastPlannedAt"] = current_time
    if watch_action["operation"] == "execute":
        watch_state["lastExecutedActionKey"] = watch_action.get("actionKey")
        watch_state["lastExecutedAt"] = current_time

    if idle_reason:
        previous_idle_reason = watch_state.get("idleEligibleReason")
        if activity_changed or previous_idle_reason != idle_reason or not watch_state.get("idleEligibleSince"):
            watch_state["idleEligibleSince"] = current_time
        watch_state["idleEligibleReason"] = idle_reason
    else:
        watch_state.pop("idleEligibleSince", None)
        watch_state.pop("idleEligibleReason", None)

    state_doc[WATCH_STATE_KEY] = watch_state
    write_json_file(state_path, state_doc)
    return watch_state


def maybe_record_watch_exit(state_path: Path, *, reason: str) -> dict[str, Any]:
    state_doc = load_json_file(state_path)
    watch_state = dict(state_doc.get(WATCH_STATE_KEY) or {})
    watch_state["lastExitReason"] = reason
    watch_state["lastExitedAt"] = now_iso()
    state_doc[WATCH_STATE_KEY] = watch_state
    write_json_file(state_path, state_doc)
    return watch_state


def should_stop_for_idle_timeout(watch_state: dict[str, Any], *, idle_timeout_sec: int) -> bool:
    if idle_timeout_sec <= 0:
        return False
    idle_eligible_since = parse_iso_timestamp(watch_state.get("idleEligibleSince"))
    last_activity_at = parse_iso_timestamp(watch_state.get("lastActivityAt"))
    if not idle_eligible_since or not last_activity_at:
        return False
    cutoff = now_utc().timestamp() - idle_timeout_sec
    return idle_eligible_since.timestamp() <= cutoff and last_activity_at.timestamp() <= cutoff


def build_turn_command(args: argparse.Namespace) -> list[str]:
    command = [
        "turn",
        "--base-url", args.base_url,
        "--session-id", args.session_id,
        "--state", args.state,
        "--timeout", str(args.timeout),
        "--message-limit", str(args.message_limit),
        "--no-change-visible-after-min", str(args.no_change_visible_after_min),
        "--write",
    ]
    if args.origin_session:
        command += ["--origin-session", args.origin_session]
    if args.origin_target:
        command += ["--origin-target", args.origin_target]
    if args.token:
        command += ["--token", args.token]
    return command


def turn_for_handoff(turn: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in turn.items()
        if key in {"factSkeleton", "shouldSend", "delivery", "cadence"}
    }


def run_single_step(args: argparse.Namespace) -> dict[str, Any]:
    state_path = Path(args.state)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    turn = run_opencodectl(build_turn_command(args))

    handoff_args = ["delivery-handoff", "--input", "-"]
    if args.live:
        handoff_args.append("--live-ready")
    handoff = run_opencodectl(handoff_args, stdin_text=json.dumps(turn_for_handoff(turn), ensure_ascii=False))

    agent_call = run_opencodectl(
        ["openclaw-agent-call", "--input", "-"],
        stdin_text=json.dumps(handoff, ensure_ascii=False),
    )

    pre_state = load_json_file(state_path)
    watch_state = dict(pre_state.get(WATCH_STATE_KEY) or {})
    watch_action = decide_watch_action(agent_call, watch_state, live=args.live)

    final_agent_call = agent_call
    if watch_action["shouldExecute"]:
        final_agent_call = run_opencodectl(
            ["openclaw-agent-call", "--input", "-", "--execute"],
            stdin_text=json.dumps(handoff, ensure_ascii=False),
        )

    updated_watch_state = update_watch_state(
        state_path,
        session_id=args.session_id,
        watch_action=watch_action,
        agent_call=final_agent_call,
        turn=turn,
        idle_timeout_sec=args.idle_timeout_sec,
    )

    stop_reason = None
    if should_stop_for_idle_timeout(updated_watch_state, idle_timeout_sec=args.idle_timeout_sec):
        stop_reason = f"idle_timeout:{updated_watch_state.get('idleEligibleReason') or 'elapsed'}"
        updated_watch_state = maybe_record_watch_exit(state_path, reason=stop_reason)

    return {
        "kind": "opencode_watch_runner_step_v1",
        "sessionId": args.session_id,
        "state": args.state,
        "mode": watch_action["mode"],
        "watchAction": watch_action,
        "turn": turn_for_handoff(turn),
        "handoff": handoff,
        "agentCall": final_agent_call,
        "watchState": updated_watch_state,
        "shouldStop": bool(stop_reason),
        "stopReason": stop_reason,
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Run a tiny single-session OpenCode watcher over the existing turn -> delivery-handoff -> openclaw-agent-call chain. Defaults to one step; add --loop for fixed-interval polling."
    )
    p.add_argument("--base-url", required=True)
    p.add_argument("--session-id", required=True)
    p.add_argument("--state", required=True)
    p.add_argument("--origin-session")
    p.add_argument("--origin-target")
    p.add_argument("--token")
    p.add_argument("--timeout", type=int, default=20)
    p.add_argument("--message-limit", type=int, default=10)
    p.add_argument("--no-change-visible-after-min", type=int, default=30)
    p.add_argument("--interval-sec", type=int, default=60)
    p.add_argument("--idle-timeout-sec", type=int, default=0, help="exit after terminal/idle status stays unchanged for this many seconds; 0 disables idle exit")
    p.add_argument("--loop", action="store_true")
    p.add_argument("--live", action="store_true", help="execute the ready handoff after duplicate suppression; default is dry-run planning only")
    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        while True:
            result = run_single_step(args)
            print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
            if result.get("shouldStop"):
                return 0
            if not args.loop:
                return 0
            time.sleep(args.interval_sec)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
