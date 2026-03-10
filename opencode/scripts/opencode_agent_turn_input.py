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
#   a tiny runtime wake/inspect token, and origin-preserving routing hints;
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
    "action",
    "opencodeSessionId",
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


def build_runtime_signal(turn_result: dict) -> dict[str, Any]:
    should_send = bool(turn_result.get("shouldSend"))
    return {
        "action": "inspect_once_current_state" if should_send else "stay_silent",
        "opencodeSessionId": turn_result.get("opencodeSessionId"),
    }


def normalize_runtime_signal(runtime_signal: Any, *, agent_input: dict | None = None) -> dict[str, Any]:
    base = runtime_signal if isinstance(runtime_signal, dict) else {}
    should_send = bool((agent_input or {}).get("shouldSend"))
    update_kind = str((agent_input or {}).get("updateType") or "progress")
    action = (
        "inspect_once_current_state"
        if should_send and update_kind != "silent"
        else "stay_silent"
    )
    normalized = {
        "action": base.get("action") or base.get("recommendedNextAction") or action,
        "opencodeSessionId": base.get("opencodeSessionId"),
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
        "runtimeSignal": build_runtime_signal(turn_result),
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
