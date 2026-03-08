#!/usr/bin/env python3
import argparse
import hashlib
import json
import shlex
import subprocess
from pathlib import Path
from typing import Callable

from opencode_delivery_handoff import assert_handoff_boundary, decode_system_event_text

ALLOWED_AGENT_CALL_KEYS = frozenset({
    "kind",
    "dryRun",
    "handoffDryRun",
    "deliveryAction",
    "routeStatus",
    "reason",
    "sessionKey",
    "gatewayMethod",
    "gatewayParams",
    "argv",
    "shellCommand",
    "executed",
    "execution",
})
ALLOWED_EXECUTION_KEYS = frozenset({"returncode", "stdout", "stderr"})
AGENT_CALL_KIND = "openclaw_gateway_agent_call_v1"


def load_json(path: Path):
    return json.loads(path.read_text())


def build_agent_message(system_event_text: str) -> str:
    decode_system_event_text(system_event_text)
    return (
        "OpenClaw internal handoff for the originating session.\n"
        "Treat the structured payload below as mechanical runtime input.\n"
        "Preserve the origin routing and own any visible user-facing explanation.\n\n"
        + system_event_text
    )


def assert_agent_call_boundary(result: dict) -> dict:
    keys = set(result)
    if keys != ALLOWED_AGENT_CALL_KEYS:
        raise ValueError(
            "openclaw-agent-call boundary violation: unexpected top-level keys "
            f"{sorted(keys - ALLOWED_AGENT_CALL_KEYS)}"
        )

    execution = result["execution"]
    if execution is not None:
        execution_keys = set(execution)
        if execution_keys != ALLOWED_EXECUTION_KEYS:
            raise ValueError(
                "openclaw-agent-call boundary violation: unexpected execution keys "
                f"{sorted(execution_keys - ALLOWED_EXECUTION_KEYS)}"
            )

    return result


def build_idempotency_key(session_key: str, system_event_text: str) -> str:
    digest = hashlib.sha256(f"{session_key}\n{system_event_text}".encode("utf-8")).hexdigest()
    return f"opencode-origin-handoff-{digest[:32]}"


def build_gateway_agent_call(
    handoff: dict,
    *,
    timeout_ms: int = 10_000,
    expect_final: bool = False,
) -> dict:
    result = assert_handoff_boundary(dict(handoff))
    delivery = result["openclawDelivery"]
    delivery_action = delivery["deliveryAction"]
    route_status = delivery["routeStatus"]
    handoff_dry_run = bool(delivery["dryRun"])

    if delivery_action != "inject" or route_status != "ready":
        plan = {
            "kind": AGENT_CALL_KIND,
            "dryRun": True,
            "handoffDryRun": handoff_dry_run,
            "deliveryAction": delivery_action,
            "routeStatus": route_status,
            "reason": delivery["reason"],
            "sessionKey": None,
            "gatewayMethod": None,
            "gatewayParams": None,
            "argv": None,
            "shellCommand": None,
            "executed": False,
            "execution": None,
        }
        return assert_agent_call_boundary(plan)

    template = delivery["systemEventTemplate"]
    if template is None:
        raise ValueError("openclaw-agent-call expected systemEventTemplate when deliveryAction=inject")

    session_key = template["sessionKey"]
    origin_session = result["routing"].get("originSession")
    if session_key != origin_session:
        raise ValueError("openclaw-agent-call refuses session rewrite: systemEventTemplate.sessionKey must equal routing.originSession")

    payload = template["payload"]
    system_event_text = payload["text"]
    envelope = decode_system_event_text(system_event_text)
    envelope_origin = envelope["agentInput"]["routing"].get("originSession")
    if envelope_origin != session_key:
        raise ValueError("openclaw-agent-call refuses session rewrite: envelope originSession must equal target sessionKey")

    gateway_params = {
        "sessionKey": session_key,
        "message": build_agent_message(system_event_text),
        "deliver": True,
        "idempotencyKey": build_idempotency_key(session_key, system_event_text),
    }
    argv = [
        "openclaw",
        "gateway",
        "call",
        "agent",
        "--json",
        "--timeout",
        str(timeout_ms),
        "--params",
        json.dumps(gateway_params, ensure_ascii=False),
    ]
    if expect_final:
        argv.append("--expect-final")

    plan = {
        "kind": AGENT_CALL_KIND,
        "dryRun": True,
        "handoffDryRun": handoff_dry_run,
        "deliveryAction": delivery_action,
        "routeStatus": route_status,
        "reason": delivery["reason"],
        "sessionKey": session_key,
        "gatewayMethod": "agent",
        "gatewayParams": gateway_params,
        "argv": argv,
        "shellCommand": shlex.join(argv),
        "executed": False,
        "execution": None,
    }
    return assert_agent_call_boundary(plan)


def execute_gateway_agent_call(
    plan: dict,
    *,
    allow_handoff_dry_run: bool = False,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict:
    checked = assert_agent_call_boundary(dict(plan))
    if checked["deliveryAction"] != "inject" or checked["routeStatus"] != "ready":
        raise ValueError("openclaw-agent-call can only execute ready inject plans")
    if checked["argv"] is None:
        raise ValueError("openclaw-agent-call requires argv to execute")
    if checked["handoffDryRun"] and not allow_handoff_dry_run:
        raise ValueError(
            "openclaw-agent-call refuses to execute a handoff marked dryRun=true; rerun delivery-handoff with --live-ready or pass --allow-handoff-dry-run"
        )

    proc = runner(checked["argv"], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            "openclaw-agent-call execution failed\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}"
        )

    executed = {
        **checked,
        "dryRun": False,
        "executed": True,
        "execution": {
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        },
    }
    return assert_agent_call_boundary(executed)


def main():
    p = argparse.ArgumentParser(
        description="Build or execute an OpenClaw CLI gateway agent call from a delivery-handoff result while preserving origin-session routing. Dry-run by default."
    )
    p.add_argument("--input", required=True, help="delivery-handoff JSON file")
    p.add_argument("--execute", action="store_true", help="execute the generated openclaw gateway call agent command")
    p.add_argument(
        "--allow-handoff-dry-run",
        action="store_true",
        help="allow execution even when the input handoff is marked dryRun=true",
    )
    p.add_argument("--expect-final", action="store_true", help="pass --expect-final to the gateway call")
    p.add_argument("--timeout-ms", type=int, default=10_000)
    args = p.parse_args()

    handoff = load_json(Path(args.input))
    plan = build_gateway_agent_call(handoff, timeout_ms=args.timeout_ms, expect_final=args.expect_final)
    if args.execute:
        plan = execute_gateway_agent_call(
            plan,
            allow_handoff_dry_run=args.allow_handoff_dry_run,
        )
    print(json.dumps(plan, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
