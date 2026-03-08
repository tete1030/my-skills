#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

from opencode_agent_turn_input import assert_agent_input_boundary
from opencode_delivery_handoff import (
    assert_system_event_envelope_boundary,
    assert_system_event_payload_boundary,
    decode_system_event_text,
)

ALLOWED_RUNTIME_TOP_LEVEL_KEYS = frozenset({"agentInput", "runtimeConsumption"})
ALLOWED_RUNTIME_CONSUMPTION_KEYS = frozenset({
    "kind",
    "deliveryKind",
    "consumeAction",
    "reason",
    "decisionOwner",
    "narrativeOwner",
    "preserveOrigin",
    "expectedSession",
    "sessionCheck",
    "deliveryPolicy",
})


def load_json(path: Path):
    return json.loads(path.read_text())



def extract_system_event_payload(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("origin-session-consume expects a JSON object input")

    if data.get("kind") == "systemEvent":
        payload = data
    elif isinstance(data.get("payload"), dict) and data["payload"].get("kind") == "systemEvent":
        payload = data["payload"]
    else:
        delivery = data.get("openclawDelivery") or {}
        template = delivery.get("systemEventTemplate") or {}
        payload = template.get("payload")

    if not isinstance(payload, dict):
        raise ValueError("origin-session-consume could not find a systemEvent payload in the input")

    return assert_system_event_payload_boundary(payload)



def session_check_for(agent_input: dict, expected_session: str | None):
    routing = agent_input.get("routing") or {}
    origin_session = routing.get("originSession")

    if not origin_session:
        return "missing_origin_session", "origin_session_required", "hold"
    if not expected_session:
        return "not_checked", "recognized_origin_session_handoff", "offer_decision"
    if origin_session != expected_session:
        return "mismatch", "expected_origin_session_mismatch", "hold"
    return "matched", "recognized_origin_session_handoff", "offer_decision"



def assert_runtime_consumption_boundary(result: dict) -> dict:
    keys = set(result)
    if keys != ALLOWED_RUNTIME_TOP_LEVEL_KEYS:
        raise ValueError(
            "origin-session-consume boundary violation: unexpected top-level keys "
            f"{sorted(keys - ALLOWED_RUNTIME_TOP_LEVEL_KEYS)}"
        )

    assert_agent_input_boundary(dict(result["agentInput"]))

    runtime_keys = set(result["runtimeConsumption"])
    if runtime_keys != ALLOWED_RUNTIME_CONSUMPTION_KEYS:
        raise ValueError(
            "origin-session-consume boundary violation: unexpected runtimeConsumption keys "
            f"{sorted(runtime_keys - ALLOWED_RUNTIME_CONSUMPTION_KEYS)}"
        )

    delivery_policy = result["runtimeConsumption"]["deliveryPolicy"]
    assert_system_event_envelope_boundary(
        {
            "kind": "opencode_origin_session_handoff",
            "version": "v1",
            "agentInput": result["agentInput"],
            "deliveryPolicy": delivery_policy,
        }
    )
    return result



def build_runtime_consumption(envelope: dict, expected_session: str | None = None) -> dict:
    safe_envelope = assert_system_event_envelope_boundary(dict(envelope))
    agent_input = safe_envelope["agentInput"]
    session_check, reason, consume_action = session_check_for(agent_input, expected_session)

    result = {
        "agentInput": agent_input,
        "runtimeConsumption": {
            "kind": "opencode_origin_session_runtime_consumption_v1",
            "deliveryKind": "systemEvent",
            "consumeAction": consume_action,
            "reason": reason,
            "decisionOwner": "main_session_agent",
            "narrativeOwner": "main_session_agent",
            "preserveOrigin": bool((agent_input.get("routing") or {}).get("mustPreserveOrigin")),
            "expectedSession": expected_session,
            "sessionCheck": session_check,
            "deliveryPolicy": safe_envelope["deliveryPolicy"],
        },
    }
    return assert_runtime_consumption_boundary(result)



def build_runtime_consumption_from_payload(payload: dict, expected_session: str | None = None) -> dict:
    safe_payload = assert_system_event_payload_boundary(dict(payload))
    envelope = decode_system_event_text(safe_payload["text"])
    return build_runtime_consumption(envelope, expected_session=expected_session)



def main():
    p = argparse.ArgumentParser(
        description="Recognize an opencode origin-session systemEvent and transform it into runtime intake for the main-session agent without rendering chat prose."
    )
    p.add_argument("--input", required=True)
    p.add_argument("--expected-session", help="optional current/origin session key to enforce exact origin-session consumption")
    args = p.parse_args()

    data = load_json(Path(args.input))
    payload = extract_system_event_payload(data)
    out = build_runtime_consumption_from_payload(payload, expected_session=args.expected_session)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
