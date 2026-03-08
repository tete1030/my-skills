#!/usr/bin/env python3
import argparse
import json
from pathlib import Path


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
        return ["status", "phase"]
    if kind in {"blocked", "failed", "completed", "progress"}:
        return ["status", "phase", "latestMeaningfulPreview"]
    return []


def compact_facts(turn_result, fields: list[str]) -> dict:
    fact = turn_result.get("factSkeleton") or {}
    out = {}
    for field in fields:
        value = fact.get(field)
        if value is not None:
            out[field] = value
    return out


def build_agent_turn_input(turn_result):
    fact = turn_result.get("factSkeleton") or {}
    cadence = turn_result.get("cadence") or {}
    delivery = turn_result.get("delivery") or {}
    should_send = bool(turn_result.get("shouldSend"))
    kind = update_type(turn_result)
    mention_fields = mention_fields_for(kind)

    return {
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
