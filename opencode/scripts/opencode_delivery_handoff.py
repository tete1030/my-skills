#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

from opencode_agent_turn_input import (
    ALLOWED_CADENCE_KEYS,
    ALLOWED_FACT_KEYS,
    ALLOWED_ROUTING_KEYS,
    ALLOWED_TOP_LEVEL_KEYS as ALLOWED_AGENT_INPUT_KEYS,
    assert_agent_input_boundary,
)

ALLOWED_HANDOFF_TOP_LEVEL_KEYS = frozenset(set(ALLOWED_AGENT_INPUT_KEYS) | {"openclawDelivery"})
ALLOWED_OPENCLAW_DELIVERY_KEYS = frozenset({
    "kind",
    "dryRun",
    "deliveryAction",
    "routeStatus",
    "reason",
    "resolutionSource",
    "preserveOrigin",
    "requiresNarrative",
    "toolRequestTemplate",
})
ALLOWED_TOOL_REQUEST_TEMPLATE_KEYS = frozenset({"tool", "action", "channel", "target", "threadId"})
ROUTE_SENTINELS = {"topic", "thread"}
SESSION_TARGET_KEYS = ("group", "chat", "user", "dm", "target")


def load_json(path: Path):
    return json.loads(path.read_text())



def parse_origin_target(value: str | None):
    if not value or not isinstance(value, str):
        return None
    parts = [part for part in value.split(":")]
    if len(parts) < 2:
        return None

    channel = parts[0].strip()
    rest = parts[1:]
    if not channel or not rest:
        return None

    thread_id = None
    if len(rest) >= 3 and rest[-2] in ROUTE_SENTINELS and rest[-1]:
        thread_id = rest[-1]
        rest = rest[:-2]

    target = ":".join(rest).strip()
    if not target:
        return None

    return {
        "channel": channel,
        "target": target,
        "threadId": thread_id,
    }



def parse_origin_session(value: str | None):
    if not value or not isinstance(value, str):
        return None
    parts = [part for part in value.split(":")]
    if len(parts) < 4 or parts[0] != "agent":
        return None

    channel = parts[2].strip()
    tokens = parts[3:]
    pairs = {}
    for i in range(0, len(tokens) - 1, 2):
        key = tokens[i]
        val = tokens[i + 1]
        if key and val:
            pairs[key] = val

    target = None
    for key in SESSION_TARGET_KEYS:
        if pairs.get(key):
            target = pairs[key]
            break

    if not channel or not target:
        return None

    return {
        "channel": channel,
        "target": target,
        "threadId": pairs.get("topic") or pairs.get("thread"),
    }



def same_route(left, right) -> bool:
    if not left or not right:
        return False
    return (
        left.get("channel"),
        left.get("target"),
        left.get("threadId"),
    ) == (
        right.get("channel"),
        right.get("target"),
        right.get("threadId"),
    )



def resolve_openclaw_route(routing: dict):
    from_target = parse_origin_target(routing.get("originTarget"))
    from_session = parse_origin_session(routing.get("originSession"))

    if from_target and from_session and not same_route(from_target, from_session):
        return {
            "routeStatus": "conflict",
            "reason": "origin_route_conflict",
            "resolutionSource": None,
            "toolRequestTemplate": None,
        }

    resolved = from_target or from_session
    resolution_source = None
    reason = "origin_route_unresolved"
    if from_target:
        resolution_source = "originTarget"
        reason = "resolved_from_origin_target"
    elif from_session:
        resolution_source = "originSession"
        reason = "resolved_from_origin_session"

    tool_request = None
    if resolved:
        tool_request = {
            "tool": "message.send",
            "action": "send",
            "channel": resolved.get("channel"),
            "target": resolved.get("target"),
            "threadId": resolved.get("threadId"),
        }

    return {
        "routeStatus": "ready" if resolved else "unresolved",
        "reason": reason,
        "resolutionSource": resolution_source,
        "toolRequestTemplate": tool_request,
    }



def assert_handoff_boundary(result: dict) -> dict:
    keys = set(result)
    if keys != ALLOWED_HANDOFF_TOP_LEVEL_KEYS:
        raise ValueError(f"delivery-handoff boundary violation: unexpected top-level keys {sorted(keys - ALLOWED_HANDOFF_TOP_LEVEL_KEYS)}")

    fact_keys = set(result["facts"])
    if fact_keys - ALLOWED_FACT_KEYS:
        raise ValueError(f"delivery-handoff boundary violation: unexpected fact keys {sorted(fact_keys - ALLOWED_FACT_KEYS)}")

    mention_fields = set(result["mentionFields"])
    if mention_fields - ALLOWED_FACT_KEYS:
        raise ValueError(f"delivery-handoff boundary violation: unexpected mention fields {sorted(mention_fields - ALLOWED_FACT_KEYS)}")

    cadence_keys = set(result["cadence"])
    if cadence_keys != ALLOWED_CADENCE_KEYS:
        raise ValueError(f"delivery-handoff boundary violation: unexpected cadence keys {sorted(cadence_keys - ALLOWED_CADENCE_KEYS)}")

    routing_keys = set(result["routing"])
    if routing_keys != ALLOWED_ROUTING_KEYS:
        raise ValueError(f"delivery-handoff boundary violation: unexpected routing keys {sorted(routing_keys - ALLOWED_ROUTING_KEYS)}")

    delivery_keys = set(result["openclawDelivery"])
    if delivery_keys != ALLOWED_OPENCLAW_DELIVERY_KEYS:
        raise ValueError(f"delivery-handoff boundary violation: unexpected openclawDelivery keys {sorted(delivery_keys - ALLOWED_OPENCLAW_DELIVERY_KEYS)}")

    tool_request = result["openclawDelivery"]["toolRequestTemplate"]
    if tool_request is not None:
        template_keys = set(tool_request)
        if template_keys != ALLOWED_TOOL_REQUEST_TEMPLATE_KEYS:
            raise ValueError(
                f"delivery-handoff boundary violation: unexpected toolRequestTemplate keys {sorted(template_keys - ALLOWED_TOOL_REQUEST_TEMPLATE_KEYS)}"
            )

    return result



def build_delivery_handoff(agent_input: dict, dry_run: bool = True):
    base = assert_agent_input_boundary(dict(agent_input))
    routing = base.get("routing") or {}
    route = resolve_openclaw_route(routing)
    should_send = bool(base.get("shouldSend"))

    if should_send:
        delivery_action = "handoff" if route["routeStatus"] == "ready" else "hold"
        reason = route["reason"]
        tool_request = route["toolRequestTemplate"] if route["routeStatus"] == "ready" else None
    else:
        delivery_action = "skip"
        reason = "should_not_send"
        tool_request = None

    result = {
        **base,
        "openclawDelivery": {
            "kind": "openclaw_message_send_handoff_v1",
            "dryRun": bool(dry_run),
            "deliveryAction": delivery_action,
            "routeStatus": route["routeStatus"],
            "reason": reason,
            "resolutionSource": route["resolutionSource"],
            "preserveOrigin": bool(routing.get("mustPreserveOrigin")),
            "requiresNarrative": should_send,
            "toolRequestTemplate": tool_request,
        },
    }
    return assert_handoff_boundary(result)



def main():
    p = argparse.ArgumentParser(
        description="Resolve compact agent-turn input into an OpenClaw-native delivery handoff without rendering chat text or sending messages."
    )
    p.add_argument("--input", required=True)
    p.add_argument("--live-ready", action="store_true", help="mark the handoff as non-dry-run metadata only; this command never sends messages")
    args = p.parse_args()

    data = load_json(Path(args.input))
    out = build_delivery_handoff(data, dry_run=not args.live_ready)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
