#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from opencode_task_cluster import normalize_task_cluster, task_cluster_is_superseded

PY = sys.executable
SCRIPT_DIR = Path(__file__).resolve().parent
OPENCODECTL = SCRIPT_DIR / "opencodectl.py"
WATCH_STATE_KEY = "watchRunner"
TERMINAL_OR_IDLE_STATUSES = {"completed", "failed", "blocked", "deviated", "stalled", "idle"}
PRIORITY_RANK = {"low": 0, "normal": 1, "high": 2}
CRITICAL_UPDATE_TYPES = {"completed", "failed", "blocked"}


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


def task_cluster_head_for_key(watch_state: dict[str, Any], task_cluster: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_task_cluster(task_cluster)
    cluster_heads = watch_state.get("clusterHeads") if isinstance(watch_state.get("clusterHeads"), dict) else {}
    return normalize_task_cluster(cluster_heads.get(normalized.get("key")))


def update_cluster_heads(watch_state: dict[str, Any], task_cluster: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_task_cluster(task_cluster)
    if not normalized.get("key"):
        return dict(watch_state.get("clusterHeads") or {})
    cluster_heads = dict(watch_state.get("clusterHeads") or {})
    current_head = normalize_task_cluster(cluster_heads.get(normalized["key"]))
    if not current_head.get("key") or task_cluster_is_superseded(current_head, normalized):
        cluster_heads[normalized["key"]] = normalized
    return cluster_heads


def watch_activity_signature(turn: dict[str, Any], agent_call: dict[str, Any]) -> str:
    fact = turn.get("factSkeleton") if isinstance(turn.get("factSkeleton"), dict) else {}
    cadence = turn.get("cadence") if isinstance(turn.get("cadence"), dict) else {}
    task_cluster = normalize_task_cluster(turn.get("taskCluster"))
    signature_payload = {
        "status": fact.get("status"),
        "phase": fact.get("phase"),
        "decision": cadence.get("decision"),
        "actionKey": action_key_from_agent_call(agent_call),
        "routeStatus": agent_call.get("routeStatus"),
        "deliveryAction": agent_call.get("deliveryAction"),
        "taskClusterKey": task_cluster.get("key"),
        "clusterStateRank": task_cluster.get("clusterStateRank"),
        "detailRank": task_cluster.get("detailRank"),
        "sourceUpdateMs": task_cluster.get("sourceUpdateMs"),
    }
    return json.dumps(signature_payload, ensure_ascii=False, sort_keys=True)


def idle_timeout_reason(turn: dict[str, Any]) -> str | None:
    fact = turn.get("factSkeleton") if isinstance(turn.get("factSkeleton"), dict) else {}
    status = str(fact.get("status") or "").strip().lower()
    if status in TERMINAL_OR_IDLE_STATUSES:
        return f"terminal_status:{status}"
    return None


def notification_meta_for(handoff: dict[str, Any], turn: dict[str, Any], task_cluster: dict[str, Any]) -> dict[str, Any]:
    fact = turn.get("factSkeleton") if isinstance(turn.get("factSkeleton"), dict) else {}
    update_type = str(handoff.get("updateType") or "progress").strip().lower()
    priority = str(handoff.get("priority") or "low").strip().lower()
    preview = str(fact.get("latestMeaningfulPreview") or "").strip()
    phase = str(fact.get("phase") or "").strip()
    status = str(fact.get("status") or "").strip().lower()
    summary = str(task_cluster.get("summary") or "").strip()
    searchable_parts = [status, phase, preview, summary]
    searchable_text = "\n".join(part for part in searchable_parts if part)
    is_critical = update_type in CRITICAL_UPDATE_TYPES or priority == "high"
    return {
        "updateType": update_type,
        "priority": priority if priority in PRIORITY_RANK else "low",
        "searchableText": searchable_text,
        "critical": is_critical,
    }



def priority_allows(meta: dict[str, Any], *, min_priority: str) -> bool:
    actual = PRIORITY_RANK.get(meta.get("priority") or "low", 0)
    required = PRIORITY_RANK.get(min_priority, 0)
    return actual >= required



def keyword_allows(meta: dict[str, Any], *, keywords: list[str]) -> bool:
    if not keywords:
        return True
    haystack = str(meta.get("searchableText") or "").lower()
    if not haystack:
        return False
    return any(keyword in haystack for keyword in keywords)



def rate_limit_allows(watch_state: dict[str, Any], *, min_interval_sec: int) -> tuple[bool, float | None]:
    if min_interval_sec <= 0:
        return True, None
    last_visible = parse_iso_timestamp(watch_state.get("lastVisibleNotificationAt") or watch_state.get("lastExecutedAt"))
    if last_visible is None:
        return True, None
    elapsed = (now_utc() - last_visible).total_seconds()
    return elapsed >= min_interval_sec, elapsed



def decide_watch_action(
    agent_call: dict[str, Any],
    watch_state: dict[str, Any],
    *,
    live: bool,
    task_cluster: dict[str, Any] | None = None,
    handoff: dict[str, Any] | None = None,
    turn: dict[str, Any] | None = None,
    notify_min_interval_sec: int = 0,
    notify_min_priority: str = "low",
    notify_keywords: list[str] | None = None,
    notify_filter_critical: bool = False,
) -> dict[str, Any]:
    delivery_action = agent_call.get("deliveryAction")
    route_status = agent_call.get("routeStatus")
    action_key = action_key_from_agent_call(agent_call)
    normalized_cluster = normalize_task_cluster(task_cluster)
    notify_keywords = [str(item).strip().lower() for item in (notify_keywords or []) if str(item).strip()]

    base = {
        "mode": "live" if live else "dry-run",
        "duplicateSuppressed": False,
        "supersededSuppressed": False,
        "rateLimited": False,
        "priorityFiltered": False,
        "keywordFiltered": False,
        "notificationMeta": None,
        "actionKey": action_key,
    }

    if delivery_action != "inject" or route_status != "ready":
        return {
            **base,
            "operation": "skip",
            "shouldExecute": False,
            "reason": agent_call.get("reason") or "not_ready",
        }

    if live and action_key and watch_state.get("lastExecutedActionKey") == action_key:
        return {
            **base,
            "mode": "live",
            "operation": "skip_duplicate",
            "shouldExecute": False,
            "duplicateSuppressed": True,
            "reason": "duplicate_action_key",
        }

    if live and normalized_cluster.get("key"):
        current_head = task_cluster_head_for_key(watch_state, normalized_cluster)
        if current_head.get("key") and task_cluster_is_superseded(normalized_cluster, current_head):
            return {
                **base,
                "mode": "live",
                "operation": "skip_superseded",
                "shouldExecute": False,
                "supersededSuppressed": True,
                "reason": "superseded_task_cluster_update",
            }

    meta = notification_meta_for(handoff or {}, turn or {}, normalized_cluster)
    if notify_filter_critical or not meta["critical"]:
        if not priority_allows(meta, min_priority=notify_min_priority):
            return {
                **base,
                "mode": "live" if live else "dry-run",
                "operation": "skip_filtered_priority",
                "shouldExecute": False,
                "priorityFiltered": True,
                "notificationMeta": meta,
                "reason": f"priority<{notify_min_priority}",
            }
        if notify_keywords and not keyword_allows(meta, keywords=notify_keywords):
            return {
                **base,
                "mode": "live" if live else "dry-run",
                "operation": "skip_filtered_keywords",
                "shouldExecute": False,
                "keywordFiltered": True,
                "notificationMeta": meta,
                "reason": "keyword_filter_miss",
            }
        allowed_by_interval, elapsed = rate_limit_allows(watch_state, min_interval_sec=notify_min_interval_sec)
        if not allowed_by_interval:
            return {
                **base,
                "mode": "live" if live else "dry-run",
                "operation": "skip_rate_limited",
                "shouldExecute": False,
                "rateLimited": True,
                "notificationMeta": {**meta, "elapsedSec": elapsed, "minIntervalSec": notify_min_interval_sec},
                "reason": f"notify_min_interval<{notify_min_interval_sec}s",
            }

    return {
        **base,
        "mode": "live" if live else "dry-run",
        "operation": "execute" if live else "plan",
        "shouldExecute": bool(live),
        "notificationMeta": meta,
        "reason": "ready_inject_live" if live else "ready_inject_dry_run",
    }


def update_watch_state(
    state_path: Path,
    *,
    session_id: str,
    watch_action: dict[str, Any],
    agent_call: dict[str, Any],
    turn: dict[str, Any],
    task_cluster: dict[str, Any] | None = None,
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
    normalized_cluster = normalize_task_cluster(task_cluster if task_cluster is not None else turn.get("taskCluster"))
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
        "lastSupersededSuppressed": bool(watch_action.get("supersededSuppressed")),
        "lastRateLimited": bool(watch_action.get("rateLimited")),
        "lastPriorityFiltered": bool(watch_action.get("priorityFiltered")),
        "lastKeywordFiltered": bool(watch_action.get("keywordFiltered")),
        "lastNotificationMeta": watch_action.get("notificationMeta"),
        "lastFactStatus": status,
        "lastFactPhase": fact.get("phase"),
        "lastPreview": fact.get("latestMeaningfulPreview"),
        "lastCadenceDecision": cadence.get("decision"),
        "lastNoChange": cadence.get("noChange"),
        "lastTaskClusterKey": normalized_cluster.get("key"),
        "idleTimeoutSec": idle_timeout_sec,
        "lastActivitySignature": activity_signature,
    })

    running_progress_observation = state_doc.get("runningProgressObservation") if isinstance(state_doc.get("runningProgressObservation"), dict) else None
    if running_progress_observation:
        watch_state["lastRunningProgressObservation"] = running_progress_observation
    else:
        watch_state.pop("lastRunningProgressObservation", None)

    transport_error_hints = state_doc.get("transportErrorHints") if isinstance(state_doc.get("transportErrorHints"), list) else None
    if transport_error_hints:
        watch_state["lastTransportErrorHints"] = transport_error_hints
    else:
        watch_state.pop("lastTransportErrorHints", None)

    if activity_changed or not watch_state.get("lastActivityAt"):
        watch_state["lastActivityAt"] = current_time

    if watch_action["operation"] == "plan":
        watch_state["lastPlannedActionKey"] = watch_action.get("actionKey")
        watch_state["lastPlannedAt"] = current_time
    if watch_action["operation"] == "execute":
        watch_state["lastExecutedActionKey"] = watch_action.get("actionKey")
        watch_state["lastExecutedAt"] = current_time
        watch_state["lastVisibleNotificationAt"] = current_time
        watch_state["suppressedNotificationCount"] = 0
        watch_state["clusterHeads"] = update_cluster_heads(watch_state, normalized_cluster)
    elif watch_action["operation"] in {"skip_rate_limited", "skip_filtered_priority", "skip_filtered_keywords"}:
        watch_state["suppressedNotificationCount"] = int(watch_state.get("suppressedNotificationCount", 0)) + 1
        watch_state["lastSuppressedNotificationAt"] = current_time

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


def turn_for_handoff(turn: dict[str, Any], *, opencode_session_id: str | None = None) -> dict[str, Any]:
    handoff = {
        key: value
        for key, value in turn.items()
        if key in {"factSkeleton", "shouldSend", "delivery", "cadence", "taskCluster"}
    }
    if opencode_session_id:
        handoff["opencodeSessionId"] = opencode_session_id
    return handoff


def run_single_step(args: argparse.Namespace) -> dict[str, Any]:
    state_path = Path(args.state)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    turn = run_opencodectl(build_turn_command(args))

    handoff_args = ["delivery-handoff", "--input", "-"]
    if args.live:
        handoff_args.append("--live-ready")
    handoff = run_opencodectl(
        handoff_args,
        stdin_text=json.dumps(turn_for_handoff(turn, opencode_session_id=args.session_id), ensure_ascii=False),
    )

    agent_call = run_opencodectl(
        ["openclaw-agent-call", "--input", "-"],
        stdin_text=json.dumps(handoff, ensure_ascii=False),
    )

    pre_state = load_json_file(state_path)
    watch_state = dict(pre_state.get(WATCH_STATE_KEY) or {})
    watch_action = decide_watch_action(
        agent_call,
        watch_state,
        live=args.live,
        task_cluster=handoff.get("taskCluster"),
        handoff=handoff,
        turn=turn,
        notify_min_interval_sec=args.notify_min_interval_sec,
        notify_min_priority=args.notify_min_priority,
        notify_keywords=args.notify_keyword,
        notify_filter_critical=args.notify_filter_critical,
    )

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
        task_cluster=handoff.get("taskCluster"),
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
        "turn": turn_for_handoff(turn, opencode_session_id=args.session_id),
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
    p.add_argument("--notify-min-interval-sec", type=int, default=0, help="minimum interval for non-critical visible notifications; critical updates bypass this throttle unless --notify-filter-critical is set")
    p.add_argument("--notify-min-priority", choices=sorted(PRIORITY_RANK), default="low", help="minimum non-critical notification priority to deliver")
    p.add_argument("--notify-keyword", action="append", default=[], help="case-insensitive keyword filter for non-critical notifications; may be repeated")
    p.add_argument("--notify-filter-critical", action="store_true", help="apply notify interval/priority/keyword filters to critical updates too; default preserves critical notifications")
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
