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
    }



def main():
    p = argparse.ArgumentParser(description="Explain the structured turn output in a compact debug-friendly form.")
    p.add_argument("--input", required=True)
    args = p.parse_args()

    data = load_json(Path(args.input))
    if not (isinstance(data, dict) and "factSkeleton" in data):
        raise SystemExit("explain-turn now supports structured turn results only")
    out = explain_structured_turn(data)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
