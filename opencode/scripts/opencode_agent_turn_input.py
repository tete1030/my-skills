#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

# Hard boundary for the agent-consumption layer:
# - allowed: send/skip recommendation, update classification, compact facts, cadence,
#   and origin-preserving routing hints;
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
})
ALLOWED_FACT_KEYS = frozenset({"status", "phase", "latestMeaningfulPreview"})
ALLOWED_CADENCE_KEYS = frozenset({
    "decision",
    "noChange",
    "consecutiveNoChangeCount",
    "lastVisibleUpdateAt",
})
ALLOWED_ROUTING_KEYS = frozenset({"originSession", "originTarget", "mustPreserveOrigin"})

HEARTBEAT_FACT_FIELDS = ("status", "phase")
DEFAULT_VISIBLE_FACT_FIELDS = ("status", "phase", "latestMeaningfulPreview")


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
    if kind == "heartbeat":
        return list(HEARTBEAT_FACT_FIELDS)
    if kind in {"blocked", "failed", "completed", "progress"}:
        return list(DEFAULT_VISIBLE_FACT_FIELDS)
    return []


def compact_facts(turn_result, fields: list[str]) -> dict:
    fact = turn_result.get("factSkeleton") or {}
    out = {}
    for field in fields:
        value = fact.get(field)
        if value is not None:
            out[field] = value
    return out


def assert_agent_input_boundary(agent_input: dict) -> dict:
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

    return agent_input


def build_agent_turn_input(turn_result):
    fact = turn_result.get("factSkeleton") or {}
    cadence = turn_result.get("cadence") or {}
    delivery = turn_result.get("delivery") or {}
    should_send = bool(turn_result.get("shouldSend"))
    kind = update_type(turn_result)
    mention_fields = mention_fields_for(kind)

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
