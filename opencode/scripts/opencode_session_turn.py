#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
from pathlib import Path

from opencode_task_cluster import ALLOWED_TASK_CLUSTER_KEYS, build_task_cluster

PY = sys.executable
SCRIPT_DIR = Path(__file__).resolve().parent

# Hard boundary for the happy-path turn envelope:
# - allowed: mechanical facts, default send/skip recommendation, origin-preserving delivery,
#   and cadence metadata;
# - caution: control input may influence the decision pass but should not be echoed back as
#   part of the agent-consumption happy path;
# - disallowed: rendered user-facing prose, strategy/narrative plans, or helper-context routing.
ALLOWED_TURN_KEYS = frozenset({"opencodeSessionId", "factSkeleton", "shouldSend", "delivery", "cadence", "taskCluster"})
DEBUG_ONLY_TURN_KEYS = frozenset({"payload"})
ALLOWED_FACT_SKELETON_KEYS = frozenset({"status", "phase", "latestMeaningfulPreview", "reason"})
ALLOWED_DELIVERY_KEYS = frozenset({"originSession", "originTarget"})
ALLOWED_CADENCE_KEYS = frozenset({
    "decision",
    "noChange",
    "consecutiveNoChangeCount",
    "lastVisibleUpdateAt",
})
TERMINAL_TASK_CLUSTER_STATUSES = frozenset({"completed", "failed", "blocked", "deviated", "stalled"})


def run_capture(script_name: str, args: list[str]) -> str:
    script = SCRIPT_DIR / script_name
    proc = subprocess.run([PY, str(script), *args], capture_output=True, text=True)
    if proc.returncode != 0:
        if proc.stdout:
            print(proc.stdout, end="", file=sys.stderr)
        if proc.stderr:
            print(proc.stderr, end="", file=sys.stderr)
        raise SystemExit(proc.returncode)
    return proc.stdout


def short(text, n=200):
    if not text:
        return None
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def latest_meaningful_preview(payload):
    snapshot = payload.get("snapshot") or {}
    latest = snapshot.get("latestMessage") or {}
    preview = (
        snapshot.get("accumulatedEventSummary")
        or snapshot.get("latestAssistantTextPreview")
        or snapshot.get("latestTextPreview")
        or latest.get("message.lastTextPreview")
        or latest.get("textPreview")
    )
    return short(preview)


def latest_assistant_message_text_preview(snapshot):
    latest = snapshot.get("latestMessage") or {}
    latest_role = latest.get("role") or latest.get("message.role")
    if latest_role != "assistant":
        return None
    terminal_marker = (
        latest.get("message.stopReason")
        or latest.get("finish")
        or ("step-finish" if latest.get("type") == "step-finish" else None)
    )
    if not terminal_marker:
        return None
    return short(
        latest.get("message.lastTextPreview")
        or latest.get("textPreview")
    )


def task_cluster_preview(payload):
    snapshot = payload.get("snapshot") or {}
    observation = payload.get("observation") or {}
    after = payload.get("after") or {}
    status = str(observation.get("status") or after.get("status") or "").strip().lower()
    if status in TERMINAL_TASK_CLUSTER_STATUSES:
        return latest_assistant_message_text_preview(snapshot)
    return latest_meaningful_preview(payload)


def build_fact_skeleton(payload):
    observation = payload.get("observation") or {}
    after = payload.get("after") or {}
    decision = payload.get("decision") or {}
    return {
        "status": observation.get("status") or after.get("status") or "unknown",
        "phase": observation.get("phase") or after.get("phase"),
        "latestMeaningfulPreview": latest_meaningful_preview(payload),
        "reason": decision.get("reason"),
    }


def build_cadence(payload):
    observation = payload.get("observation") or {}
    after = payload.get("after") or {}
    decision = payload.get("decision") or {}
    return {
        "decision": decision.get("decision"),
        "noChange": observation.get("noChange"),
        "consecutiveNoChangeCount": after.get("consecutiveNoChangeCount"),
        "lastVisibleUpdateAt": after.get("lastVisibleUpdateAt"),
    }


def build_task_cluster_payload(payload):
    snapshot = payload.get("snapshot") or {}
    observation = payload.get("observation") or {}
    after = payload.get("after") or {}
    status = observation.get("status") or after.get("status") or "unknown"
    return build_task_cluster(
        snapshot.get("latestUserInputSummary"),
        task_cluster_preview(payload),
        status=status,
        source_update_ms=observation.get("lastUpdatedMs") or after.get("lastUpdatedMs"),
    )


def assert_turn_boundary(result: dict, include_payload: bool = False) -> dict:
    allowed_keys = set(ALLOWED_TURN_KEYS)
    if include_payload:
        allowed_keys |= DEBUG_ONLY_TURN_KEYS

    result_keys = set(result)
    if result_keys != allowed_keys:
        raise ValueError(f"turn boundary violation: unexpected top-level keys {sorted(result_keys - allowed_keys)}")

    fact_keys = set(result["factSkeleton"])
    if fact_keys != ALLOWED_FACT_SKELETON_KEYS:
        raise ValueError(f"turn boundary violation: unexpected factSkeleton keys {sorted(fact_keys - ALLOWED_FACT_SKELETON_KEYS)}")

    delivery_keys = set(result["delivery"])
    if delivery_keys != ALLOWED_DELIVERY_KEYS:
        raise ValueError(f"turn boundary violation: unexpected delivery keys {sorted(delivery_keys - ALLOWED_DELIVERY_KEYS)}")

    cadence_keys = set(result["cadence"])
    if cadence_keys != ALLOWED_CADENCE_KEYS:
        raise ValueError(f"turn boundary violation: unexpected cadence keys {sorted(cadence_keys - ALLOWED_CADENCE_KEYS)}")

    task_cluster_keys = set(result["taskCluster"])
    if task_cluster_keys != ALLOWED_TASK_CLUSTER_KEYS:
        raise ValueError(f"turn boundary violation: unexpected taskCluster keys {sorted(task_cluster_keys - ALLOWED_TASK_CLUSTER_KEYS)}")

    return result


def build_turn_result(payload, control=None, origin_session=None, origin_target=None, session_id=None, include_payload=False):
    fact_skeleton = build_fact_skeleton(payload)
    cadence = build_cadence(payload)
    delivery = {
        "originSession": origin_session,
        "originTarget": origin_target,
    }
    should_send = cadence.get("decision") == "visible_update"

    # `control` is intentionally consumed during the decision pass but omitted from the
    # happy-path turn envelope so the result stays mechanical and delivery-safe.
    result = {
        "opencodeSessionId": session_id,
        "factSkeleton": fact_skeleton,
        "shouldSend": should_send,
        "delivery": delivery,
        "cadence": cadence,
        "taskCluster": build_task_cluster_payload(payload),
    }
    if include_payload:
        result["payload"] = payload
    return assert_turn_boundary(result, include_payload=include_payload)


def main() -> None:
    p = argparse.ArgumentParser(description="Run one main-session turn and emit a structured fact skeleton with cadence and delivery metadata.")
    p.add_argument("--base-url", required=True)
    p.add_argument("--session-id", required=True)
    p.add_argument("--state", required=True)
    p.add_argument("--control")
    p.add_argument("--origin-session")
    p.add_argument("--origin-target")
    p.add_argument("--token")
    p.add_argument("--timeout", type=int, default=20)
    p.add_argument("--message-limit", type=int, default=10)
    p.add_argument("--no-change-visible-after-min", type=int, default=30)
    p.add_argument("--write", action="store_true")
    p.add_argument("--payload-out")
    p.add_argument("--include-payload", action="store_true")
    args = p.parse_args()

    cycle_args = [
        "--base-url", args.base_url,
        "--session-id", args.session_id,
        "--state", args.state,
        "--timeout", str(args.timeout),
        "--message-limit", str(args.message_limit),
        "--no-change-visible-after-min", str(args.no_change_visible_after_min),
    ]
    if args.control:
        cycle_args += ["--control", args.control]
    if args.token:
        cycle_args += ["--token", args.token]
    if args.write:
        cycle_args.append("--write")

    cycle_stdout = run_capture("opencode_remote_cycle.py", cycle_args)
    payload = json.loads(cycle_stdout)

    if args.payload_out:
        Path(args.payload_out).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    print(json.dumps(
        build_turn_result(
            payload,
            origin_session=args.origin_session,
            origin_target=args.origin_target,
            session_id=args.session_id,
            include_payload=args.include_payload,
        ),
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
