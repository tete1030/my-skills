#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

from opencode_agent_turn_input import (
    ALLOWED_CADENCE_KEYS,
    ALLOWED_FACT_KEYS,
    ALLOWED_ROUTING_KEYS,
    ALLOWED_TOP_LEVEL_KEYS as ALLOWED_AGENT_INPUT_KEYS,
    assert_agent_input_boundary,
    build_agent_turn_input,
)
from opencode_task_cluster import ALLOWED_REPLY_POLICY_KEYS, ALLOWED_TASK_CLUSTER_KEYS

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
    "primaryDelivery",
    "systemEventTemplate",
})
ALLOWED_SYSTEM_EVENT_TEMPLATE_KEYS = frozenset({"sessionKey", "payload"})
ALLOWED_SYSTEM_EVENT_PAYLOAD_KEYS = frozenset({"kind", "text"})
ALLOWED_SYSTEM_EVENT_ENVELOPE_KEYS = frozenset({"kind", "version", "agentInput", "deliveryPolicy", "consumptionPolicy"})
ALLOWED_DELIVERY_POLICY_KEYS = frozenset({"primary"})
ALLOWED_CONSUMPTION_POLICY_KEYS = frozenset({"treatAs", "ifVisible", "avoid"})
SYSTEM_EVENT_TEXT_HEADER = "OPENCODE_ORIGIN_SESSION_SYSTEM_EVENT_V1"
ROUTE_SENTINELS = {"topic", "thread"}
SESSION_TARGET_KEYS = ("group", "chat", "user", "dm", "target")


def load_json_input(value: str):
    if value == "-":
        return json.loads(sys.stdin.read())
    return json.loads(Path(value).read_text())


def coerce_agent_input(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("delivery-handoff expects a JSON object input")
    if "factSkeleton" in data:
        return build_agent_turn_input(data)
    return assert_agent_input_boundary(dict(data))


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


def build_system_event_envelope(agent_input: dict):
    base = assert_agent_input_boundary(dict(agent_input))
    envelope = {
        "kind": "opencode_origin_session_handoff",
        "version": "v1",
        "agentInput": base,
        "deliveryPolicy": {
            "primary": "origin_session_system_event",
        },
        "consumptionPolicy": {
            "treatAs": "internal_runtime_input",
            "ifVisible": "continue_current_conversation_naturally",
            "avoid": [
                "handoff_mechanics",
                "routing_details",
                "transport_details",
                "prompt_mechanics",
            ],
        },
    }
    return assert_system_event_envelope_boundary(envelope)


def assert_system_event_envelope_boundary(envelope: dict) -> dict:
    keys = set(envelope)
    if keys != ALLOWED_SYSTEM_EVENT_ENVELOPE_KEYS:
        raise ValueError(
            f"delivery-handoff boundary violation: unexpected system event envelope keys {sorted(keys - ALLOWED_SYSTEM_EVENT_ENVELOPE_KEYS)}"
        )

    delivery_policy_keys = set(envelope["deliveryPolicy"])
    if delivery_policy_keys != ALLOWED_DELIVERY_POLICY_KEYS:
        raise ValueError(
            "delivery-handoff boundary violation: unexpected deliveryPolicy keys "
            f"{sorted(delivery_policy_keys - ALLOWED_DELIVERY_POLICY_KEYS)}"
        )

    consumption_policy_keys = set(envelope["consumptionPolicy"])
    if consumption_policy_keys != ALLOWED_CONSUMPTION_POLICY_KEYS:
        raise ValueError(
            "delivery-handoff boundary violation: unexpected consumptionPolicy keys "
            f"{sorted(consumption_policy_keys - ALLOWED_CONSUMPTION_POLICY_KEYS)}"
        )
    avoid = envelope["consumptionPolicy"].get("avoid")
    if not isinstance(avoid, list) or not avoid or any(not isinstance(item, str) or not item for item in avoid):
        raise ValueError("delivery-handoff boundary violation: consumptionPolicy.avoid must be a non-empty list of strings")

    assert_agent_input_boundary(dict(envelope["agentInput"]))
    return envelope


def assert_system_event_payload_boundary(payload: dict) -> dict:
    payload_keys = set(payload)
    if payload_keys != ALLOWED_SYSTEM_EVENT_PAYLOAD_KEYS:
        raise ValueError(
            "delivery-handoff boundary violation: unexpected system event payload keys "
            f"{sorted(payload_keys - ALLOWED_SYSTEM_EVENT_PAYLOAD_KEYS)}"
        )
    if payload.get("kind") != "systemEvent":
        raise ValueError("delivery-handoff boundary violation: system event payload kind must be 'systemEvent'")
    if not isinstance(payload.get("text"), str) or not payload["text"]:
        raise ValueError("delivery-handoff boundary violation: system event payload text must be non-empty")
    return payload


def encode_system_event_text(envelope: dict) -> str:
    safe_envelope = assert_system_event_envelope_boundary(dict(envelope))
    return SYSTEM_EVENT_TEXT_HEADER + "\n" + json.dumps(safe_envelope, ensure_ascii=False, indent=2)


def decode_system_event_text(text: str) -> dict:
    if not isinstance(text, str) or not text.startswith(SYSTEM_EVENT_TEXT_HEADER + "\n"):
        raise ValueError("delivery-handoff boundary violation: unrecognized system event text header")
    payload = text.split("\n", 1)[1]
    try:
        envelope = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError("delivery-handoff boundary violation: invalid system event JSON payload") from exc
    return assert_system_event_envelope_boundary(envelope)


def resolve_origin_session_injection(routing: dict):
    origin_session = routing.get("originSession")
    from_session = parse_origin_session(origin_session)
    from_target = parse_origin_target(routing.get("originTarget"))

    if not origin_session or not isinstance(origin_session, str):
        return {
            "routeStatus": "missing_origin_session",
            "reason": "origin_session_required",
            "resolutionSource": None,
            "sessionKey": None,
        }

    if from_session and from_target and not same_route(from_session, from_target):
        return {
            "routeStatus": "conflict",
            "reason": "origin_route_conflict",
            "resolutionSource": None,
            "sessionKey": None,
        }

    return {
        "routeStatus": "ready",
        "reason": "resolved_from_origin_session",
        "resolutionSource": "originSession",
        "sessionKey": origin_session,
    }


def build_system_event_template(session_key: str, text: str):
    return {
        "sessionKey": session_key,
        "payload": {
            "kind": "systemEvent",
            "text": text,
        },
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

    task_cluster_keys = set(result["taskCluster"])
    if task_cluster_keys != ALLOWED_TASK_CLUSTER_KEYS:
        raise ValueError(f"delivery-handoff boundary violation: unexpected taskCluster keys {sorted(task_cluster_keys - ALLOWED_TASK_CLUSTER_KEYS)}")

    reply_policy_keys = set(result["replyPolicy"])
    if reply_policy_keys != ALLOWED_REPLY_POLICY_KEYS:
        raise ValueError(f"delivery-handoff boundary violation: unexpected replyPolicy keys {sorted(reply_policy_keys - ALLOWED_REPLY_POLICY_KEYS)}")

    delivery_keys = set(result["openclawDelivery"])
    if delivery_keys != ALLOWED_OPENCLAW_DELIVERY_KEYS:
        raise ValueError(f"delivery-handoff boundary violation: unexpected openclawDelivery keys {sorted(delivery_keys - ALLOWED_OPENCLAW_DELIVERY_KEYS)}")

    system_event = result["openclawDelivery"]["systemEventTemplate"]
    if system_event is not None:
        template_keys = set(system_event)
        if template_keys != ALLOWED_SYSTEM_EVENT_TEMPLATE_KEYS:
            raise ValueError(
                "delivery-handoff boundary violation: unexpected systemEventTemplate keys "
                f"{sorted(template_keys - ALLOWED_SYSTEM_EVENT_TEMPLATE_KEYS)}"
            )
        assert_system_event_payload_boundary(system_event["payload"])

    return result


def build_delivery_handoff(data: dict, dry_run: bool = True):
    base = coerce_agent_input(data)
    routing = base.get("routing") or {}
    route = resolve_origin_session_injection(routing)
    should_send = bool(base.get("shouldSend"))

    system_event_template = None
    if should_send and route["routeStatus"] == "ready":
        envelope = build_system_event_envelope(base)
        text = encode_system_event_text(envelope)
        system_event_template = build_system_event_template(route["sessionKey"], text)
        delivery_action = "inject"
        reason = route["reason"]
    elif should_send:
        delivery_action = "hold"
        reason = route["reason"]
    else:
        delivery_action = "skip"
        reason = "should_not_send"

    result = {
        **base,
        "openclawDelivery": {
            "kind": "openclaw_origin_session_system_event_handoff_v1",
            "dryRun": bool(dry_run),
            "deliveryAction": delivery_action,
            "routeStatus": route["routeStatus"],
            "reason": reason,
            "resolutionSource": route["resolutionSource"],
            "preserveOrigin": bool(routing.get("mustPreserveOrigin")),
            "requiresNarrative": should_send,
            "primaryDelivery": "origin_session_system_event",
            "systemEventTemplate": system_event_template,
        },
    }
    return assert_handoff_boundary(result)


def main():
    p = argparse.ArgumentParser(
        description="Resolve a structured turn result (preferred) or compact agent input into an origin-session delivery handoff without rendering chat text or sending messages."
    )
    p.add_argument("--input", required=True, help="JSON file path or '-' for stdin")
    p.add_argument("--live-ready", action="store_true", help="mark the handoff as non-dry-run metadata only; this command never sends messages")
    args = p.parse_args()

    data = load_json_input(args.input)
    out = build_delivery_handoff(data, dry_run=not args.live_ready)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
