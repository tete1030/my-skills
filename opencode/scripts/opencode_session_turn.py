#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
from pathlib import Path

PY = sys.executable
SCRIPT_DIR = Path(__file__).resolve().parent


def run_capture(script_name: str, args: list[str]) -> str:
    script = SCRIPT_DIR / script_name
    proc = subprocess.run([PY, str(script), *args], capture_output=True, text=True)
    if proc.returncode != 0:
        if proc.stdout:
            print(proc.stdout, end="", file=sys.stderr)
        if proc.stderr:
            print(proc.stderr, end="", file=sys.stderr)
        raise SystemExit(proc.returncode)
    return proc.stdout


def load_json(path: str | None):
    if not path:
        return None
    return json.loads(Path(path).read_text())


def short(text, n=120):
    if not text:
        return None
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def latest_meaningful_preview(payload):
    snapshot = payload.get("snapshot") or {}
    latest = snapshot.get("latestMessage") or {}
    preview = (
        snapshot.get("latestAssistantTextPreview")
        or snapshot.get("latestTextPreview")
        or latest.get("message.lastTextPreview")
        or latest.get("textPreview")
    )
    return short(preview)


def build_fact_skeleton(payload):
    observation = payload.get("observation") or {}
    after = payload.get("after") or {}
    decision = payload.get("decision") or {}
    return {
        "status": observation.get("status") or after.get("status") or "unknown",
        "phase": observation.get("phase") or after.get("phase"),
        "latestMeaningfulPreview": latest_meaningful_preview(payload),
        "reason": decision.get("reason"),
    }


def build_cadence(payload):
    observation = payload.get("observation") or {}
    after = payload.get("after") or {}
    decision = payload.get("decision") or {}
    return {
        "decision": decision.get("decision"),
        "noChange": observation.get("noChange"),
        "consecutiveNoChangeCount": after.get("consecutiveNoChangeCount"),
        "lastVisibleUpdateAt": after.get("lastVisibleUpdateAt"),
    }


def build_turn_result(payload, control=None, origin_session=None, origin_target=None, include_payload=False):
    fact_skeleton = build_fact_skeleton(payload)
    cadence = build_cadence(payload)
    delivery = {
        "originSession": origin_session,
        "originTarget": origin_target,
    }
    should_send = cadence.get("decision") == "visible_update"
    result = {
        "factSkeleton": fact_skeleton,
        "shouldSend": should_send,
        "delivery": delivery,
        "cadence": cadence,
        "control": control,
    }
    if include_payload:
        result["payload"] = payload
    return result


def main() -> None:
    p = argparse.ArgumentParser(description="Run one main-session turn and emit a structured fact skeleton with cadence and delivery metadata.")
    p.add_argument("--base-url", required=True)
    p.add_argument("--session-id", required=True)
    p.add_argument("--state", required=True)
    p.add_argument("--control")
    p.add_argument("--origin-session")
    p.add_argument("--origin-target")
    p.add_argument("--token")
    p.add_argument("--timeout", type=int, default=20)
    p.add_argument("--message-limit", type=int, default=10)
    p.add_argument("--no-change-visible-after-min", type=int, default=30)
    p.add_argument("--write", action="store_true")
    p.add_argument("--payload-out")
    p.add_argument("--include-payload", action="store_true")
    args = p.parse_args()

    cycle_args = [
        "--base-url", args.base_url,
        "--session-id", args.session_id,
        "--state", args.state,
        "--timeout", str(args.timeout),
        "--message-limit", str(args.message_limit),
        "--no-change-visible-after-min", str(args.no_change_visible_after_min),
    ]
    if args.control:
        cycle_args += ["--control", args.control]
    if args.token:
        cycle_args += ["--token", args.token]
    if args.write:
        cycle_args.append("--write")

    cycle_stdout = run_capture("opencode_remote_cycle.py", cycle_args)
    payload = json.loads(cycle_stdout)
    control = load_json(args.control)

    if args.payload_out:
        Path(args.payload_out).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    print(json.dumps(
        build_turn_result(
            payload,
            control=control,
            origin_session=args.origin_session,
            origin_target=args.origin_target,
            include_payload=args.include_payload,
        ),
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
