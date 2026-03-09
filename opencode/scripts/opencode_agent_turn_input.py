#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
from typing import Any

from opencode_task_cluster import (
    ALLOWED_REPLY_POLICY_KEYS,
    ALLOWED_TASK_CLUSTER_KEYS,
    DEFAULT_REPLY_POLICY,
    normalize_reply_policy,
    normalize_task_cluster,
)

# Hard boundary for the agent-consumption layer:
# - allowed: send/skip recommendation, update classification, compact facts, cadence,
#   explicit lightweight runtime signal metadata, and origin-preserving routing hints;
# - caution: expose only fact fields that a live main-session agent may or may not mention;
# - disallowed: rendered chat text, strategy trees, next-step plans, or rewritten delivery.
# This adapter exists to prepare agent input, not to become a narrative/strategy engine.
ALLOWED_TOP_LEVEL_KEYS = frozenset({
    "shouldSend",
    "action",
    "updateType",
    "priority",
    "style",
    "reason",
    "narrativeOwner",
    "mentionFields",
    "facts",
    "cadence",
    "routing",
    "taskCluster",
    "replyPolicy",
    "runtimeSignal",
})
ALLOWED_FACT_KEYS = frozenset({"status", "phase", "latestMeaningfulPreview"})
ALLOWED_CADENCE_KEYS = frozenset({
    "decision",
    "noChange",
    "consecutiveNoChangeCount",
    "lastVisibleUpdateAt",
})
ALLOWED_ROUTING_KEYS = frozenset({"originSession", "originTarget", "mustPreserveOrigin"})
ALLOWED_RUNTIME_SIGNAL_KEYS = frozenset({
    "signalKind",
    "recommendedNextAction",
    "opencodeSessionId",
    "taskClusterKey",
    "reasonCategory",
})

DEFAULT_SIGNAL_FACT_FIELDS = ("status", "phase")


def load_json(path: Path):
    return json.loads(path.read_text())


def update_type(turn_result):
    fact = turn_result.get("factSkeleton") or {}
    cadence = turn_result.get("cadence") or {}
    status = fact.get("status")
    should_send = bool(turn_result.get("shouldSend"))

    if not should_send:
        return "silent"
    if status == "blocked":
        return "blocked"
    if status == "failed":
        return "failed"
    if status == "completed":
        return "completed"
    if cadence.get("noChange"):
        return "heartbeat"
    return "progress"


def priority_for(kind: str) -> str:
    if kind in {"blocked", "failed"}:
        return "high"
    if kind in {"completed", "progress"}:
        return "normal"
    return "low"


def style_for(kind: str) -> str:
    return {
        "progress": "brief_progress",
        "heartbeat": "brief_heartbeat",
        "blocked": "brief_blocker",
        "failed": "brief_failure",
        "completed": "brief_completion",
        "silent": "silent",
    }[kind]


def mention_fields_for(kind: str) -> list[str]:
    if kind == "silent":
        return []
    return list(DEFAULT_SIGNAL_FACT_FIELDS)


def compact_facts(turn_result, fields: list[str]) -> dict:
    fact = turn_result.get("factSkeleton") or {}
    out = {}
    for field in fields:
        value = fact.get(field)
        if value is not None:
            out[field] = value
    return out


def signal_kind_for(update_kind: str) -> str:
    if update_kind in {"progress", "heartbeat"}:
        return "progress"
    if update_kind in {"blocked", "failed", "completed", "silent"}:
        return update_kind
    return "progress"


def reason_category_for(turn_result: dict, kind: str) -> str:
    fact = turn_result.get("factSkeleton") or {}
    cadence = turn_result.get("cadence") or {}
    reason = str(fact.get("reason") or cadence.get("reason") or "").strip().lower()

    if kind == "blocked":
        return "blocked"
    if kind == "failed":
        return "failed"
    if kind == "completed":
        return "completed"
    if reason.startswith("status="):
        return "status_changed"
    if reason == "state_changed":
        return "state_changed"
    if "no_change" in reason or cadence.get("noChange"):
        return "no_change"
    if reason == "recent_visible_update_exists":
        return "recent_visible_update_exists"
    return reason or "unspecified"


def build_runtime_signal(turn_result: dict, *, kind: str, task_cluster: dict[str, Any]) -> dict[str, Any]:
    should_send = bool(turn_result.get("shouldSend"))
    recommended_next_action = "inspect_once_current_state" if should_send else "stay_silent"
    return {
        "signalKind": signal_kind_for(kind),
        "recommendedNextAction": recommended_next_action,
        "opencodeSessionId": turn_result.get("opencodeSessionId"),
        "taskClusterKey": task_cluster.get("key"),
        "reasonCategory": reason_category_for(turn_result, kind),
    }


def normalize_runtime_signal(runtime_signal: Any, *, agent_input: dict | None = None) -> dict[str, Any]:
    base = runtime_signal if isinstance(runtime_signal, dict) else {}
    task_cluster = normalize_task_cluster((agent_input or {}).get("taskCluster"))
    should_send = bool((agent_input or {}).get("shouldSend"))
    update_kind = str((agent_input or {}).get("updateType") or "progress")
    recommended_next_action = (
        "inspect_once_current_state"
        if should_send and update_kind != "silent"
        else "stay_silent"
    )
    reason = str((agent_input or {}).get("reason") or "").strip().lower()
    normalized = {
        "signalKind": base.get("signalKind") or signal_kind_for(update_kind),
        "recommendedNextAction": base.get("recommendedNextAction") or recommended_next_action,
        "opencodeSessionId": base.get("opencodeSessionId"),
        "taskClusterKey": base.get("taskClusterKey") or task_cluster.get("key"),
        "reasonCategory": base.get("reasonCategory") or reason or "unspecified",
    }
    return {
        key: (value if value is not None else None)
        for key, value in normalized.items()
    }


def normalize_agent_input(agent_input: dict) -> dict:
    normalized = dict(agent_input)
    normalized["taskCluster"] = normalize_task_cluster(normalized.get("taskCluster"))
    normalized["replyPolicy"] = normalize_reply_policy(normalized.get("replyPolicy"))
    normalized["runtimeSignal"] = normalize_runtime_signal(
        normalized.get("runtimeSignal"),
        agent_input=normalized,
    )
    return normalized


def assert_agent_input_boundary(agent_input: dict) -> dict:
    agent_input = normalize_agent_input(agent_input)
    keys = set(agent_input)
    if keys != ALLOWED_TOP_LEVEL_KEYS:
        raise ValueError(f"agent-turn-input boundary violation: unexpected top-level keys {sorted(keys - ALLOWED_TOP_LEVEL_KEYS)}")

    fact_keys = set(agent_input["facts"])
    if fact_keys - ALLOWED_FACT_KEYS:
        raise ValueError(f"agent-turn-input boundary violation: unexpected fact keys {sorted(fact_keys - ALLOWED_FACT_KEYS)}")

    mention_fields = set(agent_input["mentionFields"])
    if mention_fields - ALLOWED_FACT_KEYS:
        raise ValueError(f"agent-turn-input boundary violation: unexpected mention fields {sorted(mention_fields - ALLOWED_FACT_KEYS)}")

    cadence_keys = set(agent_input["cadence"])
    if cadence_keys != ALLOWED_CADENCE_KEYS:
        raise ValueError(f"agent-turn-input boundary violation: unexpected cadence keys {sorted(cadence_keys - ALLOWED_CADENCE_KEYS)}")

    routing_keys = set(agent_input["routing"])
    if routing_keys != ALLOWED_ROUTING_KEYS:
        raise ValueError(f"agent-turn-input boundary violation: unexpected routing keys {sorted(routing_keys - ALLOWED_ROUTING_KEYS)}")

    task_cluster_keys = set(agent_input["taskCluster"])
    if task_cluster_keys != ALLOWED_TASK_CLUSTER_KEYS:
        raise ValueError(f"agent-turn-input boundary violation: unexpected taskCluster keys {sorted(task_cluster_keys - ALLOWED_TASK_CLUSTER_KEYS)}")

    reply_policy_keys = set(agent_input["replyPolicy"])
    if reply_policy_keys != ALLOWED_REPLY_POLICY_KEYS:
        raise ValueError(f"agent-turn-input boundary violation: unexpected replyPolicy keys {sorted(reply_policy_keys - ALLOWED_REPLY_POLICY_KEYS)}")

    runtime_signal_keys = set(agent_input["runtimeSignal"])
    if runtime_signal_keys != ALLOWED_RUNTIME_SIGNAL_KEYS:
        raise ValueError(
            "agent-turn-input boundary violation: unexpected runtimeSignal keys "
            f"{sorted(runtime_signal_keys - ALLOWED_RUNTIME_SIGNAL_KEYS)}"
        )

    return agent_input


def build_agent_turn_input(turn_result):
    fact = turn_result.get("factSkeleton") or {}
    cadence = turn_result.get("cadence") or {}
    delivery = turn_result.get("delivery") or {}
    should_send = bool(turn_result.get("shouldSend"))
    kind = update_type(turn_result)
    mention_fields = mention_fields_for(kind)
    task_cluster = normalize_task_cluster(turn_result.get("taskCluster"))

    agent_input = {
        "shouldSend": should_send,
        "action": "send_update" if should_send else "stay_silent",
        "updateType": kind,
        "priority": priority_for(kind),
        "style": style_for(kind),
        "reason": fact.get("reason") or cadence.get("reason"),
        "narrativeOwner": "main_session_agent",
        "mentionFields": mention_fields,
        "facts": compact_facts(turn_result, mention_fields),
        "cadence": {
            "decision": cadence.get("decision"),
            "noChange": cadence.get("noChange"),
            "consecutiveNoChangeCount": cadence.get("consecutiveNoChangeCount"),
            "lastVisibleUpdateAt": cadence.get("lastVisibleUpdateAt"),
        },
        "routing": {
            "originSession": delivery.get("originSession"),
            "originTarget": delivery.get("originTarget"),
            "mustPreserveOrigin": bool(delivery.get("originSession") or delivery.get("originTarget")),
        },
        "taskCluster": task_cluster,
        "replyPolicy": dict(DEFAULT_REPLY_POLICY),
        "runtimeSignal": build_runtime_signal(turn_result, kind=kind, task_cluster=task_cluster),
    }
    return assert_agent_input_boundary(agent_input)


def main():
    p = argparse.ArgumentParser(description="Transform a structured turn result into compact main-agent input without rendering final chat prose.")
    p.add_argument("--input", required=True)
    args = p.parse_args()

    data = load_json(Path(args.input))
    if not (isinstance(data, dict) and "factSkeleton" in data):
        raise SystemExit("agent-turn-input expects a structured turn result")
    out = build_agent_turn_input(data)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
