#!/usr/bin/env python3
import argparse
import hashlib
import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Callable

SCRIPT_DIR = Path(__file__).resolve().parent

from opencode_delivery_handoff import (
    assert_handoff_boundary,
    decode_system_event_text,
    extract_agent_input_from_system_event_envelope,
    extract_runtime_signal_from_system_event_envelope,
)

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
STABLE_IDEMPOTENCY_FACT_KEYS = ("status", "phase")
STABLE_IDEMPOTENCY_ROUTING_KEYS = ("originSession", "originTarget")
STABLE_IDEMPOTENCY_RUNTIME_SIGNAL_KEYS = (
    "action",
    "opencodeSessionId",
)
STABLE_IDEMPOTENCY_TASK_CLUSTER_KEYS = ("key", "clusterStateRank", "detailRank")


def load_json_input(value: str):
    if value == "-":
        return json.loads(sys.stdin.read())
    return json.loads(Path(value).read_text())


def build_agent_message(system_event_text: str, *, handoff: dict | None = None) -> str:
    envelope = decode_system_event_text(system_event_text)
    runtime_signal = extract_runtime_signal_from_system_event_envelope(envelope)
    handoff_input = assert_handoff_boundary(dict(handoff)) if handoff is not None else None
    agent_input = handoff_input or extract_agent_input_from_system_event_envelope(envelope) or {}
    task_cluster = agent_input.get("taskCluster") or {}
    reply_policy = agent_input.get("replyPolicy") or {}

    session_hint = runtime_signal.get("opencodeSessionId") or "the referenced OpenCode session"
    preamble = ["Internal runtime signal for the current conversation."]
    if runtime_signal.get("action") == "inspect_once_current_state":
        preamble.append(
            f"Inspect {session_hint} once, then base any visible reply on that inspected current state rather than this event text."
        )
        preamble.append("Prefer rehydration.currentState and rehydration.sinceLatestUserInput from that inspect.")
        preamble.append("If inspect alone still leaves a real gap, proactively run one targeted inspect-history drill-down (usually --recent-index 0/1/2, or --message-id when the inspection already points to one).")
        preamble.append("Use that drill-down for both relevant older history and 'what happened between inspect points?' questions, especially recent shell/tool output or stdout tail lines.")
        preamble.append("Do not fetch broad history by default; only do the narrow lookup needed to answer.")
        preamble.append("Do not start or attach a watcher, and do not keep polling from this session.")
    if task_cluster.get("key") and reply_policy.get("replyDefault") == "send_if_not_cluster_superseded":
        preamble.append("Reply visibly only if the inspected current state adds net-new user-visible progress for this task cluster.")
        preamble.append("A newer user input inside the OpenCode session does not reset same-cluster reply allowances in this chat.")
        preamble.append("Small exception: when rehydration.sinceLatestUserInput.assistantMessageCount == 0 and the inspected state is still running with meaningful progress, you may send one short visible progress reply for this task cluster.")
        preamble.append("Across one same-cluster chain, prefer at most one visible running/progress reply and at most one visible terminal completion/failure reply.")
        preamble.append("Do not suppress the first same-cluster terminal completion/failure reply just because an earlier progress reply was already sent.")
        preamble.append("If this chat already received a visible same-cluster terminal/status reply, later same-cluster terminal/status updates are NO_REPLY unless the earlier reply was clearly wrong.")
        preamble.append("After that first visible same-cluster progress reply, later non-terminal equal, older, weaker, duplicate, or superseded inspected states are NO_REPLY.")
        preamble.append("When suppressing, output the single token NO_REPLY and nothing else—no explanation, prefix, suffix, bullets, or code fences.")

    return "\n".join([
        *preamble,
        "",
        "<opencodeEvent>",
        system_event_text,
        "</opencodeEvent>",
    ])


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


def _stable_subset(source: dict, allowed_keys: tuple[str, ...]) -> dict:
    return {
        key: source[key]
        for key in allowed_keys
        if key in source and source[key] is not None
    }


def build_idempotency_basis(
    session_key: str,
    system_event_text: str,
    *,
    envelope: dict | None = None,
    handoff: dict | None = None,
) -> dict:
    safe_envelope = envelope or decode_system_event_text(system_event_text)
    handoff_input = assert_handoff_boundary(dict(handoff)) if handoff is not None else None
    agent_input = handoff_input or extract_agent_input_from_system_event_envelope(safe_envelope) or {}
    runtime_signal = extract_runtime_signal_from_system_event_envelope(safe_envelope)
    if handoff_input is not None:
        runtime_signal = handoff_input.get("runtimeSignal") or runtime_signal
    return {
        "kind": "opencode_origin_session_handoff_idempotency_v1",
        "sessionKey": session_key,
        "routing": _stable_subset(agent_input.get("routing") or {}, STABLE_IDEMPOTENCY_ROUTING_KEYS),
        "action": agent_input.get("action"),
        "updateType": agent_input.get("updateType"),
        "facts": _stable_subset(agent_input.get("facts") or {}, STABLE_IDEMPOTENCY_FACT_KEYS),
        "runtimeSignal": _stable_subset(runtime_signal or {}, STABLE_IDEMPOTENCY_RUNTIME_SIGNAL_KEYS),
        "taskCluster": _stable_subset(agent_input.get("taskCluster") or {}, STABLE_IDEMPOTENCY_TASK_CLUSTER_KEYS),
    }


def build_idempotency_key(
    session_key: str,
    system_event_text: str,
    *,
    envelope: dict | None = None,
    handoff: dict | None = None,
) -> str:
    basis = build_idempotency_basis(session_key, system_event_text, envelope=envelope, handoff=handoff)
    canonical = json.dumps(basis, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
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
    runtime_signal = extract_runtime_signal_from_system_event_envelope(envelope)
    expected_runtime_signal = _stable_subset(result.get("runtimeSignal") or {}, STABLE_IDEMPOTENCY_RUNTIME_SIGNAL_KEYS)
    if _stable_subset(runtime_signal, STABLE_IDEMPOTENCY_RUNTIME_SIGNAL_KEYS) != expected_runtime_signal:
        raise ValueError("openclaw-agent-call refuses runtime signal rewrite: system event payload must match handoff runtimeSignal")

    legacy_agent_input = extract_agent_input_from_system_event_envelope(envelope)
    if legacy_agent_input is not None:
        envelope_origin = legacy_agent_input["routing"].get("originSession")
        if envelope_origin != session_key:
            raise ValueError("openclaw-agent-call refuses session rewrite: envelope originSession must equal target sessionKey")

    gateway_params = {
        "sessionKey": session_key,
        "message": build_agent_message(system_event_text, handoff=result),
        "deliver": True,
        "idempotencyKey": build_idempotency_key(session_key, system_event_text, envelope=envelope, handoff=result),
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
    p.add_argument("--input", required=True, help="delivery-handoff JSON file or '-' for stdin")
    p.add_argument("--execute", action="store_true", help="execute the generated openclaw gateway call agent command")
    p.add_argument(
        "--allow-handoff-dry-run",
        action="store_true",
        help="allow execution even when the input handoff is marked dryRun=true",
    )
    p.add_argument("--expect-final", action="store_true", help="pass --expect-final to the gateway call")
    p.add_argument("--timeout-ms", type=int, default=10_000)
    args = p.parse_args()

    handoff = load_json_input(args.input)
    plan = build_gateway_agent_call(handoff, timeout_ms=args.timeout_ms, expect_final=args.expect_final)
    if args.execute:
        plan = execute_gateway_agent_call(
            plan,
            allow_handoff_dry_run=args.allow_handoff_dry_run,
        )
    print(json.dumps(plan, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
