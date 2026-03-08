#!/usr/bin/env python3
import argparse
import json
from pathlib import Path


def load_json(path: Path):
    return json.loads(path.read_text())


def get(obj, *keys, default=None):
    cur = obj
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def explain_structured_turn(data):
    fact = data.get("factSkeleton") or {}
    cadence = data.get("cadence") or {}
    delivery = data.get("delivery") or {}
    return {
        "decision": cadence.get("decision"),
        "reason": fact.get("reason") or cadence.get("reason"),
        "status": fact.get("status"),
        "phase": fact.get("phase"),
        "latestMeaningfulPreview": fact.get("latestMeaningfulPreview"),
        "noChange": cadence.get("noChange"),
        "consecutiveNoChangeCount": cadence.get("consecutiveNoChangeCount"),
        "originSession": delivery.get("originSession"),
        "originTarget": delivery.get("originTarget"),
        "shouldSend": data.get("shouldSend"),
        "renderedUpdateFallback": get(data, "fallback", "renderedUpdate"),
    }


def explain_legacy_turn(data):
    payload = data.get("payload") if isinstance(data, dict) and "payload" in data else data
    decision = get(payload, "decision", default={}) or {}
    observation = get(payload, "observation", default={}) or {}
    snapshot = get(payload, "snapshot", default={}) or {}
    after = get(payload, "after", default={}) or {}
    latest = snapshot.get("latestMessage") or {}

    return {
        "decision": decision.get("decision"),
        "reason": decision.get("reason"),
        "status": observation.get("status") or after.get("status"),
        "phase": observation.get("phase") or after.get("phase"),
        "latestMeaningfulPreview": snapshot.get("latestAssistantTextPreview") or snapshot.get("latestTextPreview") or latest.get("message.lastTextPreview"),
        "noChange": observation.get("noChange"),
        "consecutiveNoChangeCount": after.get("consecutiveNoChangeCount"),
        "lastSeenMessageId": observation.get("lastSeenMessageId") or after.get("lastSeenMessageId"),
        "lastTodoDigest": observation.get("lastTodoDigest") or after.get("lastTodoDigest"),
        "originSession": get(data, "delivery", "originSession"),
        "originTarget": get(data, "delivery", "originTarget"),
        "shouldSend": get(data, "delivery", "shouldSend"),
        "renderedUpdateFallback": data.get("update") if isinstance(data, dict) else None,
    }


def main():
    p = argparse.ArgumentParser(description="Explain the structured turn output in a compact debug-friendly form.")
    p.add_argument("--input", required=True)
    args = p.parse_args()

    data = load_json(Path(args.input))
    if isinstance(data, dict) and "factSkeleton" in data:
        out = explain_structured_turn(data)
    else:
        out = explain_legacy_turn(data)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
