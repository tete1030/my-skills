#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

import opencode_validate_live as validate_live

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
DEFAULT_ARTIFACT_ROOT = Path("/tmp/opencode-same-cluster-validation")


def same_cluster_output_path(run_id: str) -> str:
    return f".claw-validation/{run_id}.txt"


def is_exact_no_reply(reply_text: Any) -> bool:
    return str(reply_text or "").strip() == "NO_REPLY"


def synthetic_no_reply_window(occurrence: int, *, reason: str) -> dict[str, Any]:
    return {
        "occurrence": occurrence,
        "replyText": "NO_REPLY",
        "syntheticNoDelivery": True,
        "syntheticReason": reason,
    }


def is_well_formed_same_cluster_reply(reply_text: Any, *, session_id: str) -> bool:
    text = str(reply_text or "").strip()
    if is_exact_no_reply(text):
        return True
    if "NO_REPLY" in text:
        return False
    return validate_live.receiver_reply_looks_like_current_state(text, session_id=session_id)



def same_cluster_expected_lines(run_id: str) -> list[str]:
    return [
        f"alpha {run_id}",
        f"beta {run_id}",
        f"gamma {run_id}",
    ]



def build_same_cluster_prompt(run_id: str) -> str:
    output_path = same_cluster_output_path(run_id)
    first, second, third = same_cluster_expected_lines(run_id)
    return (
        "Same-cluster stress validation only. Work only inside the current workspace. "
        f"Using shell commands only, inspect {output_path}. "
        f"Append exactly one next missing line from this ordered list: {first} | {second} | {third}. "
        "If the file does not exist, create it. If all lines already exist, do not append anything. "
        "Do not create shell variables named status. "
        f"Then sleep 20 seconds and print the full contents of {output_path}. "
        "Finish with one short final line: done."
    )



def evaluate_same_cluster_windows(windows: list[dict[str, Any]], *, session_id: str) -> dict[str, Any]:
    if not windows:
        raise ValueError("same-cluster validation expected at least 1 receiver/synthetic window")

    replies = [str(window.get("replyText") or "").strip() for window in windows]
    first_reply = replies[0]
    later_replies = replies[1:]
    visible_later_replies = [reply for reply in later_replies if reply and not is_exact_no_reply(reply)]

    firstReplyAllowed = is_well_formed_same_cluster_reply(first_reply, session_id=session_id)
    laterRepliesWellFormed = all(
        is_well_formed_same_cluster_reply(reply, session_id=session_id)
        for reply in later_replies
    )
    laterAtMostOneVisible = len(visible_later_replies) <= 1

    synthetic_occurrences = [window.get("occurrence") for window in windows if window.get("syntheticNoDelivery")]

    return {
        "firstReplyAllowed": firstReplyAllowed,
        "laterRepliesWellFormed": laterRepliesWellFormed,
        "laterAtMostOneVisible": laterAtMostOneVisible,
        "allPassed": firstReplyAllowed and laterRepliesWellFormed and laterAtMostOneVisible,
        "details": {
            "firstReply": first_reply,
            "laterReplies": later_replies,
            "visibleLaterReplies": visible_later_replies,
            "windowCount": len(windows),
            "syntheticNoDeliveryOccurrences": synthetic_occurrences,
        },
    }



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Focused live validation for long same-cluster receiver chains across repeated identical user inputs."
    )
    parser.add_argument("--config", default=str(validate_live.DEFAULT_CONFIG_PATH), help="validation config JSON or env file")
    parser.add_argument("--artifact-root", default=str(DEFAULT_ARTIFACT_ROOT), help="artifact root directory")
    parser.add_argument("--run-id", help="optional fixed run id")
    parser.add_argument("--poll-interval-sec", type=float, default=validate_live.DEFAULT_POLL_INTERVAL_SEC)
    parser.add_argument("--receiver-wait-sec", type=int, default=max(120, validate_live.DEFAULT_RECEIVER_WAIT_SEC))
    return parser



def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    config = validate_live.load_validation_config(Path(args.config))
    run_id = args.run_id or f"samecluster-{uuid.uuid4().hex[:8]}"
    artifact_dir = validate_live.ensure_directory(Path(args.artifact_root).expanduser().resolve() / run_id)
    registry_path = artifact_dir / "registry.json"
    receiver_session_file = validate_live.resolve_openclaw_session_file(config["openclawSessionKey"])
    baseline_line_count = len(validate_live.read_jsonl_objects(receiver_session_file))
    prompt = build_same_cluster_prompt(run_id)

    summary: dict[str, Any] = {
        "kind": "opencode_same_cluster_live_validation_v1",
        "runId": run_id,
        "artifactDir": str(artifact_dir),
        "config": validate_live.redacted_config_snapshot(config, requested_config_path=Path(args.config)),
        "prompt": prompt,
        "receiverSession": {
            "sessionKey": config["openclawSessionKey"],
            "sessionFile": str(receiver_session_file),
            "baselineLineCount": baseline_line_count,
        },
        "windows": [],
    }
    validate_live.save_json_file(artifact_dir / "summary.json", summary)

    session_id: str | None = None
    watcher_id: str | None = None

    try:
        start_result = validate_live.run_json_command(
            "01-start",
            validate_live.build_manager_argv(
                config,
                registry_path=registry_path,
                command="start",
                extra_args=[
                    "--opencode-workspace",
                    config["workspace"],
                    *validate_live.manager_runtime_args(config),
                    "--title",
                    f"Same cluster stress {run_id}",
                    "--first-prompt",
                    prompt,
                ],
            ),
            artifact_dir=artifact_dir,
            cwd=REPO_ROOT,
        )
        start_data = start_result.data
        session_id = validate_live.normalize_optional_string((start_data.get("opencodeSession") or {}).get("opencodeSessionId"))
        watcher_id = validate_live.normalize_optional_string((start_data.get("watcher") or {}).get("watcherId"))
        if not session_id or not watcher_id:
            raise validate_live.ValidationError("same-cluster validation start did not return opencodeSessionId/watcherId")
        summary["opencodeSessionId"] = session_id
        summary["watcherId"] = watcher_id
        validate_live.save_json_file(artifact_dir / "summary.json", summary)

        for occurrence in (1, 2, 3):
            if occurrence > 1:
                validate_live.run_json_command(
                    f"{occurrence:02d}-continue",
                    validate_live.build_manager_argv(
                        config,
                        registry_path=registry_path,
                        command="continue",
                        extra_args=[
                            "--opencode-session-id",
                            session_id,
                            "--opencode-workspace",
                            config["workspace"],
                            "--follow-up-prompt",
                            prompt,
                            "--ensure-watcher",
                            *validate_live.manager_runtime_args(config),
                        ],
                    ),
                    artifact_dir=artifact_dir,
                    cwd=REPO_ROOT,
                )

            try:
                window, _ = validate_live.wait_for_event_window(
                    receiver_session_file,
                    baseline_line_count=baseline_line_count,
                    session_id=session_id,
                    occurrence=occurrence,
                    timeout_sec=args.receiver_wait_sec,
                    poll_interval_sec=args.poll_interval_sec,
                )
            except validate_live.ValidationError:
                if occurrence == 1:
                    raise
                summary["windowsMissing"] = summary.get("windowsMissing", []) + [occurrence]
                synthetic_window = synthetic_no_reply_window(
                    occurrence,
                    reason="no_receiver_window_within_timeout",
                )
                summary["windows"].append(synthetic_window)
                validate_live.save_json_file(artifact_dir / f"receiver-window-{occurrence}.json", synthetic_window)
                validate_live.save_json_file(artifact_dir / "summary.json", summary)
                continue

            summary["windows"].append(validate_live.summarize_receiver_window(window))
            validate_live.save_json_file(artifact_dir / f"receiver-window-{occurrence}.json", summary["windows"][-1])
            validate_live.save_json_file(artifact_dir / "summary.json", summary)

        inspection = validate_live.wait_for_terminal_inspection(
            config,
            registry_path=registry_path,
            artifact_dir=artifact_dir,
            cwd=REPO_ROOT,
            session_id=session_id,
            workspace=config["workspace"],
            timeout_sec=max(config["waitForExecutionSec"], args.receiver_wait_sec * 2),
            poll_interval_sec=args.poll_interval_sec,
            label_prefix="04-final-inspect",
        )
        summary["finalInspection"] = validate_live.inspection_current_state(inspection)
        summary["checks"] = evaluate_same_cluster_windows(summary["windows"], session_id=session_id)
        validate_live.save_json_file(artifact_dir / "summary.json", summary)
    finally:
        if watcher_id:
            try:
                stop_result = validate_live.run_json_command(
                    "99-stop-watcher",
                    validate_live.build_manager_argv(
                        config,
                        registry_path=registry_path,
                        command="stop-watcher",
                        extra_args=[
                            "--watcher-id",
                            watcher_id,
                            "--stop-timeout-sec",
                            str(config["waitForStopSec"]),
                        ],
                    ),
                    artifact_dir=artifact_dir,
                    cwd=REPO_ROOT,
                )
                summary["stopWatcher"] = stop_result.data
                validate_live.save_json_file(artifact_dir / "summary.json", summary)
            except Exception as exc:  # pragma: no cover - cleanup best effort
                summary["stopWatcherError"] = str(exc)
                validate_live.save_json_file(artifact_dir / "summary.json", summary)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get("checks", {}).get("allPassed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
