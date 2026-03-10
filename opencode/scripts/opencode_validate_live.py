#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from opencode_delivery_handoff import SYSTEM_EVENT_TEXT_HEADER

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
MANAGER = SCRIPT_DIR / "opencode_manager.py"
DEFAULT_CONFIG_PATH = REPO_ROOT / ".local" / "opencode-validation" / "config.json"
DEFAULT_MANAGER_DEFAULTS_ENV = REPO_ROOT / ".local" / "opencode-manager" / "local-defaults.env"
DEFAULT_ARTIFACT_ROOT = Path("/tmp/opencode-validation")
DEFAULT_POLL_INTERVAL_SEC = 2.0
DEFAULT_HISTORY_MESSAGE_LIMIT = 12
DEFAULT_WAIT_FOR_EXECUTION_SEC = 60
DEFAULT_WAIT_FOR_STOP_SEC = 30
DEFAULT_INSPECT_RETRY_SEC = 5
DEFAULT_INSPECT_ATTEMPTS = 4
TERMINAL_STATUSES = {"completed", "failed", "cancelled", "blocked"}
CORE_CHECK_NAMES = (
    "start_watcher_live",
    "continue_watcher_reuse",
    "live_handoff_executed",
    "minimal_runtime_handoff_shape",
    "inspect_current_state",
    "session_completed_terminal",
    "workspace_start_artifact_content",
    "workspace_continue_artifact_content",
    "attach_rehydration",
    "inspect_history",
)


class ValidationError(RuntimeError):
    pass


@dataclass
class CommandResult:
    label: str
    argv: list[str]
    returncode: int
    stdout_path: Path
    stderr_path: Path
    parsed_path: Path | None
    meta_path: Path
    data: dict[str, Any]
    duration_sec: float



def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()



def slug_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")



def ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path



def save_json_file(path: Path, payload: Any) -> None:
    ensure_directory(path.parent)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")



def load_json_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))



def normalize_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None



def parse_env_value(raw: str) -> str:
    value = raw.strip()
    if not value:
        return ""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        try:
            parsed = ast.literal_eval(value)
        except Exception:
            return value[1:-1]
        return parsed if isinstance(parsed, str) else str(parsed)
    if " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    return value



def parse_simple_env_file(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = parse_env_value(value)
    return result



def pick_value(raw: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key not in raw:
            continue
        value = raw.get(key)
        if isinstance(value, str):
            value = value.strip()
        if value in (None, ""):
            continue
        return value
    return default



def pick_int(raw: dict[str, Any], *keys: str, default: int) -> int:
    value = pick_value(raw, *keys, default=None)
    if value is None:
        return default
    return int(value)



def normalize_validation_config(raw: dict[str, Any], *, source_path: Path) -> dict[str, Any]:
    base_url = normalize_optional_string(pick_value(raw, "baseUrl", "opencodeBaseUrl", "OPENCODE_BASE_URL"))
    workspace = normalize_optional_string(pick_value(raw, "workspace", "opencodeWorkspace", "OPENCODE_WORKSPACE"))
    openclaw_session_key = normalize_optional_string(
        pick_value(raw, "openclawSessionKey", "originSession", "OPENCLAW_SESSION_KEY")
    )
    openclaw_delivery_target = normalize_optional_string(
        pick_value(raw, "openclawDeliveryTarget", "originTarget", "OPENCLAW_DELIVERY_TARGET")
    )
    token = normalize_optional_string(pick_value(raw, "token", "opencodeToken", "OPENCODE_TOKEN"))
    token_env = normalize_optional_string(pick_value(raw, "tokenEnv", "opencodeTokenEnv", "OPENCODE_TOKEN_ENV"))

    missing = []
    if not base_url:
        missing.append("baseUrl / OPENCODE_BASE_URL")
    if not workspace:
        missing.append("workspace / OPENCODE_WORKSPACE")
    if not openclaw_session_key:
        missing.append("openclawSessionKey / OPENCLAW_SESSION_KEY")
    if missing:
        raise ValidationError(f"validation config is missing required fields from {source_path}: {', '.join(missing)}")

    watch_interval_sec = pick_int(raw, "watchIntervalSec", "OPENCODE_WATCH_INTERVAL_SEC", default=5)
    idle_timeout_sec = pick_int(raw, "idleTimeoutSec", "OPENCODE_IDLE_TIMEOUT_SEC", default=45)
    watch_message_limit = pick_int(raw, "watchMessageLimit", "OPENCODE_WATCH_MESSAGE_LIMIT", default=10)
    watch_timeout_sec = pick_int(raw, "watchTimeoutSec", "OPENCODE_WATCH_TIMEOUT_SEC", default=20)
    wait_for_execution_sec = pick_int(raw, "waitForExecutionSec", default=max(DEFAULT_WAIT_FOR_EXECUTION_SEC, watch_interval_sec * 8))
    wait_for_stop_sec = pick_int(raw, "waitForStopSec", default=DEFAULT_WAIT_FOR_STOP_SEC)
    inspect_retry_sec = pick_int(raw, "inspectRetrySec", default=DEFAULT_INSPECT_RETRY_SEC)
    history_message_limit = pick_int(raw, "historyMessageLimit", default=max(DEFAULT_HISTORY_MESSAGE_LIMIT, watch_message_limit))

    return {
        "sourcePath": str(source_path),
        "baseUrl": base_url,
        "workspace": workspace,
        "openclawSessionKey": openclaw_session_key,
        "openclawDeliveryTarget": openclaw_delivery_target,
        "token": token,
        "tokenEnv": token_env,
        "watchIntervalSec": watch_interval_sec,
        "idleTimeoutSec": idle_timeout_sec,
        "watchMessageLimit": watch_message_limit,
        "watchTimeoutSec": watch_timeout_sec,
        "waitForExecutionSec": wait_for_execution_sec,
        "waitForStopSec": wait_for_stop_sec,
        "inspectRetrySec": inspect_retry_sec,
        "historyMessageLimit": history_message_limit,
    }



def load_validation_config(config_path: Path) -> dict[str, Any]:
    requested_path = config_path.expanduser().resolve()
    default_config_path = DEFAULT_CONFIG_PATH.expanduser().resolve()
    defaults_env_path = DEFAULT_MANAGER_DEFAULTS_ENV.expanduser().resolve()

    if requested_path.exists():
        if requested_path.suffix.lower() == ".json":
            raw = load_json_file(requested_path)
            if not isinstance(raw, dict):
                raise ValidationError(f"validation config JSON must be an object: {requested_path}")
        else:
            raw = parse_simple_env_file(requested_path)
        source_path = requested_path
    elif requested_path == default_config_path and defaults_env_path.exists():
        raw = parse_simple_env_file(defaults_env_path)
        source_path = defaults_env_path
    else:
        raise ValidationError(
            f"validation config not found: {requested_path} (default fallback {defaults_env_path} is also unavailable)"
        )

    return normalize_validation_config(raw, source_path=source_path)



def redacted_config_snapshot(config: dict[str, Any], *, requested_config_path: Path) -> dict[str, Any]:
    return {
        "requestedConfigPath": str(requested_config_path),
        "resolvedSourcePath": config.get("sourcePath"),
        "baseUrl": config.get("baseUrl"),
        "workspace": config.get("workspace"),
        "openclawSessionKey": config.get("openclawSessionKey"),
        "openclawDeliveryTarget": config.get("openclawDeliveryTarget"),
        "tokenConfigured": bool(config.get("token")),
        "tokenEnv": config.get("tokenEnv"),
        "watchIntervalSec": config.get("watchIntervalSec"),
        "idleTimeoutSec": config.get("idleTimeoutSec"),
        "watchMessageLimit": config.get("watchMessageLimit"),
        "watchTimeoutSec": config.get("watchTimeoutSec"),
        "waitForExecutionSec": config.get("waitForExecutionSec"),
        "waitForStopSec": config.get("waitForStopSec"),
        "inspectRetrySec": config.get("inspectRetrySec"),
        "historyMessageLimit": config.get("historyMessageLimit"),
    }



def run_git(repo_root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ValidationError(f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout



def collect_git_info(repo_root: Path) -> dict[str, Any]:
    head = run_git(repo_root, "rev-parse", "HEAD").strip()
    short_head = run_git(repo_root, "rev-parse", "--short", "HEAD").strip()
    branch = run_git(repo_root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    status_lines = [line for line in run_git(repo_root, "status", "--short").splitlines() if line.strip()]
    origin_main_head = None
    try:
        origin_main_head = run_git(repo_root, "rev-parse", "origin/main").strip()
    except ValidationError:
        origin_main_head = None
    return {
        "head": head,
        "shortHead": short_head,
        "branch": branch,
        "originMainHead": origin_main_head,
        "headMatchesOriginMain": bool(origin_main_head and origin_main_head == head),
        "statusShort": status_lines,
        "clean": not status_lines,
    }



def build_artifact_dir(root: Path, *, short_head: str) -> Path:
    root = root.expanduser().resolve()
    ensure_directory(root)
    artifact_dir = root / f"opencode-live-validation-{slug_timestamp()}-{short_head}"
    ensure_directory(artifact_dir)
    ensure_directory(artifact_dir / "commands")
    return artifact_dir



def command_artifact_prefix(artifact_dir: Path, label: str) -> Path:
    return artifact_dir / "commands" / label



def redact_argv(argv: list[str]) -> list[str]:
    redacted: list[str] = []
    redact_next = False
    for item in argv:
        if redact_next:
            redacted.append("<redacted>")
            redact_next = False
            continue
        redacted.append(item)
        if item == "--opencode-token":
            redact_next = True
    return redacted



def build_manager_argv(
    config: dict[str, Any],
    *,
    registry_path: Path,
    command: str,
    extra_args: list[str] | None = None,
) -> list[str]:
    argv = [
        sys.executable,
        str(MANAGER),
        command,
    ]

    commands_with_runtime_connection = {"start", "attach", "continue", "list-sessions", "inspect", "inspect-history"}
    commands_with_registry = {"start", "attach", "continue", "inspect", "inspect-history", "list-watchers", "stop-watcher", "detach"}

    if command in commands_with_runtime_connection:
        argv += ["--opencode-base-url", str(config["baseUrl"])]
        if config.get("token"):
            argv += ["--opencode-token", str(config["token"])]
        elif config.get("tokenEnv"):
            argv += ["--opencode-token-env", str(config["tokenEnv"])]

    if command in commands_with_registry:
        argv += ["--registry-path", str(registry_path)]

    if extra_args:
        argv += [str(item) for item in extra_args]
    return argv



def manager_runtime_args(config: dict[str, Any]) -> list[str]:
    argv = [
        "--watch-live",
        "--watch-interval-sec",
        str(config["watchIntervalSec"]),
        "--idle-timeout-sec",
        str(config["idleTimeoutSec"]),
        "--watch-message-limit",
        str(config["watchMessageLimit"]),
        "--watch-timeout-sec",
        str(config["watchTimeoutSec"]),
    ]
    if config.get("openclawSessionKey"):
        argv += ["--openclaw-session-key", str(config["openclawSessionKey"])]
    if config.get("openclawDeliveryTarget"):
        argv += ["--openclaw-delivery-target", str(config["openclawDeliveryTarget"])]
    return argv



def run_json_command(label: str, argv: list[str], *, artifact_dir: Path, cwd: Path) -> CommandResult:
    prefix = command_artifact_prefix(artifact_dir, label)
    ensure_directory(prefix.parent)
    started = time.monotonic()
    proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False)
    duration_sec = time.monotonic() - started

    stdout_path = prefix.with_suffix(".stdout")
    stderr_path = prefix.with_suffix(".stderr")
    meta_path = prefix.with_suffix(".meta.json")
    stdout_path.write_text(proc.stdout, encoding="utf-8")
    stderr_path.write_text(proc.stderr, encoding="utf-8")

    parsed_path = None
    data = None
    parse_error = None
    if proc.stdout.strip():
        try:
            parsed = json.loads(proc.stdout)
            if isinstance(parsed, dict):
                data = parsed
                parsed_path = prefix.with_suffix(".parsed.json")
                save_json_file(parsed_path, parsed)
        except json.JSONDecodeError as exc:
            parse_error = str(exc)

    save_json_file(
        meta_path,
        {
            "label": label,
            "argv": argv,
            "argvRedacted": redact_argv(argv),
            "returncode": proc.returncode,
            "durationSec": round(duration_sec, 3),
            "stdoutPath": str(stdout_path),
            "stderrPath": str(stderr_path),
            "parsedPath": str(parsed_path) if parsed_path else None,
            "parseError": parse_error,
        },
    )

    if proc.returncode != 0:
        raise ValidationError(
            f"command {label} failed with code {proc.returncode}; see {stdout_path.name} / {stderr_path.name}"
        )
    if data is None:
        raise ValidationError(f"command {label} did not return a JSON object; see {stdout_path.name}")

    return CommandResult(
        label=label,
        argv=argv,
        returncode=proc.returncode,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        parsed_path=parsed_path,
        meta_path=meta_path,
        data=data,
        duration_sec=duration_sec,
    )



def read_json_object_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = load_json_file(path)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}



def collapse_duplicate_lines(text: str) -> str:
    lines = text.splitlines()
    collapsed: list[str] = []
    previous: str | None = None
    for line in lines:
        if line == previous:
            continue
        collapsed.append(line)
        previous = line
    normalized = "\n".join(collapsed)
    if text.endswith("\n") and normalized:
        normalized += "\n"
    return normalized



def extract_json_documents(text: str) -> list[Any]:
    decoder = json.JSONDecoder()
    docs: list[Any] = []
    index = 0
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        try:
            obj, next_index = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            newline = text.find("\n", index)
            if newline == -1:
                break
            index = newline + 1
            continue
        docs.append(obj)
        index = next_index
    return docs



def parse_system_event_payload(payload_text: str) -> dict[str, Any]:
    if not isinstance(payload_text, str) or not payload_text.startswith(SYSTEM_EVENT_TEXT_HEADER + "\n"):
        raise ValidationError("system event payload text is missing the OpenCode system-event header")
    payload = payload_text.split("\n", 1)[1]
    parsed = json.loads(payload)
    if not isinstance(parsed, dict):
        raise ValidationError("system event payload envelope must be a JSON object")
    return parsed



def summarize_watch_log(watch_log_path: Path) -> dict[str, Any]:
    if not watch_log_path.exists():
        raise ValidationError(f"watch log not found: {watch_log_path}")
    raw_text = watch_log_path.read_text(encoding="utf-8")
    normalized_text = collapse_duplicate_lines(raw_text)
    documents = extract_json_documents(normalized_text)
    steps = [doc for doc in documents if isinstance(doc, dict) and doc.get("kind") == "opencode_watch_runner_step_v1"]
    if not steps:
        raise ValidationError(f"watch log did not contain opencode_watch_runner_step_v1 documents: {watch_log_path}")

    last_step = steps[-1]
    handoff = last_step.get("handoff") if isinstance(last_step.get("handoff"), dict) else {}
    delivery = handoff.get("openclawDelivery") if isinstance(handoff.get("openclawDelivery"), dict) else {}
    system_event = delivery.get("systemEventTemplate") if isinstance(delivery.get("systemEventTemplate"), dict) else {}
    payload = system_event.get("payload") if isinstance(system_event.get("payload"), dict) else {}
    envelope = parse_system_event_payload(payload.get("text"))

    return {
        "documentCount": len(documents),
        "stepCount": len(steps),
        "handoffEnvelope": envelope,
        "lastStep": {
            "watchAction": last_step.get("watchAction"),
            "deliveryAction": delivery.get("deliveryAction"),
            "routeStatus": delivery.get("routeStatus"),
        },
    }



def wait_for_watch_execution(state_path: Path, *, timeout_sec: int, poll_interval_sec: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_sec
    last_state: dict[str, Any] = {}
    while time.monotonic() <= deadline:
        doc = read_json_object_if_exists(state_path)
        watch_state = doc.get("watchRunner") if isinstance(doc.get("watchRunner"), dict) else {}
        if watch_state.get("lastOperation") == "execute" and watch_state.get("lastExecutedActionKey"):
            return doc
        last_state = doc
        time.sleep(poll_interval_sec)
    raise ValidationError(
        f"watcher never recorded a live execute step within {timeout_sec}s: {state_path}; last state keys={list(last_state)}"
    )



def inspection_current_state(inspection_result: dict[str, Any]) -> dict[str, Any]:
    inspection = inspection_result.get("inspection") if isinstance(inspection_result.get("inspection"), dict) else {}
    rehydration = inspection.get("rehydration") if isinstance(inspection.get("rehydration"), dict) else {}
    current_state = rehydration.get("currentState") if isinstance(rehydration.get("currentState"), dict) else {}
    return current_state



def inspection_latest_message(inspection_result: dict[str, Any]) -> dict[str, Any]:
    inspection = inspection_result.get("inspection") if isinstance(inspection_result.get("inspection"), dict) else {}
    latest_message = inspection.get("latestMessage") if isinstance(inspection.get("latestMessage"), dict) else {}
    return latest_message



def validation_relative_root(run_id: str) -> str:
    return str(Path("validation-harness") / run_id)



def validation_relative_path(run_id: str, name: str) -> str:
    return str(Path(validation_relative_root(run_id)) / name)



def expected_start_text(run_id: str) -> str:
    return f"start ok {run_id}."



def expected_continue_text(run_id: str) -> str:
    return f"continue ok {run_id}."



def expected_start_artifact_payload(run_id: str) -> dict[str, Any]:
    return {"runId": run_id, "step": "start"}



def expected_continue_artifact_payload(run_id: str) -> dict[str, Any]:
    return {"runId": run_id, "step": "start", "continueSeen": True}



def wait_for_terminal_inspection(
    config: dict[str, Any],
    *,
    registry_path: Path,
    artifact_dir: Path,
    cwd: Path,
    session_id: str,
    workspace: str,
    timeout_sec: int,
    poll_interval_sec: float,
    label_prefix: str,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_sec
    last_result: dict[str, Any] | None = None
    attempt = 0
    while time.monotonic() <= deadline:
        attempt += 1
        result = run_json_command(
            f"{label_prefix}-{attempt:02d}",
            build_manager_argv(
                config,
                registry_path=registry_path,
                command="inspect",
                extra_args=[
                    "--opencode-session-id",
                    session_id,
                    "--opencode-workspace",
                    workspace,
                    "--watch-message-limit",
                    str(config["watchMessageLimit"]),
                    "--watch-timeout-sec",
                    str(config["watchTimeoutSec"]),
                ],
            ),
            artifact_dir=artifact_dir,
            cwd=cwd,
        )
        last_result = result.data
        current_state = inspection_current_state(result.data)
        status = str(current_state.get("status") or "").strip().lower()
        if status in TERMINAL_STATUSES:
            return result.data
        time.sleep(poll_interval_sec)

    raise ValidationError(f"inspect never reached a terminal status within {timeout_sec}s: {last_result}")



def wait_for_watcher_status(
    config: dict[str, Any],
    *,
    registry_path: Path,
    artifact_dir: Path,
    cwd: Path,
    watcher_id: str,
    expected_status: str,
    timeout_sec: int,
    poll_interval_sec: float,
    label_prefix: str,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_sec
    attempt = 0
    last_status = None
    while time.monotonic() <= deadline:
        attempt += 1
        result = run_json_command(
            f"{label_prefix}-{attempt:02d}",
            build_manager_argv(
                config,
                registry_path=registry_path,
                command="list-watchers",
                extra_args=["--include-exited"],
            ),
            artifact_dir=artifact_dir,
            cwd=cwd,
        )
        watchers = result.data.get("watchers") if isinstance(result.data.get("watchers"), list) else []
        for watcher in watchers:
            if isinstance(watcher, dict) and watcher.get("watcherId") == watcher_id:
                last_status = watcher.get("watcherStatus")
                if last_status == expected_status:
                    return result.data
                break
        time.sleep(poll_interval_sec)

    raise ValidationError(
        f"watcher {watcher_id} never reached status {expected_status!r} within {timeout_sec}s (last={last_status!r})"
    )



def bool_check(name: str, passed: bool, *, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "details": details or {},
    }



def build_verdict(checks: list[dict[str, Any]], *, preflight_ok: bool, has_error: bool = False) -> str:
    if not preflight_ok:
        return "failed"
    core_progress = any(bool(check.get("passed")) for check in checks if check.get("name") in CORE_CHECK_NAMES)
    if has_error:
        return "partly_ready" if core_progress else "failed"
    if checks and all(bool(check.get("passed")) for check in checks):
        return "ready"
    if core_progress:
        return "partly_ready"
    return "failed"



def build_start_prompt(run_id: str) -> str:
    root = validation_relative_root(run_id)
    start_path = validation_relative_path(run_id, "start.txt")
    artifact_path = validation_relative_path(run_id, "artifact.json")
    artifact_payload = json.dumps(expected_start_artifact_payload(run_id), ensure_ascii=False)
    return (
        "Live validation only. Work only inside the current workspace. "
        f"Create directory {root} if needed. "
        f"Write {start_path} with exactly: {expected_start_text(run_id)} "
        f"Write {artifact_path} with JSON exactly equal to: {artifact_payload} "
        "Finish with one short status line."
    )



def build_continue_prompt(run_id: str) -> str:
    root = validation_relative_root(run_id)
    continue_path = validation_relative_path(run_id, "continue.txt")
    artifact_path = validation_relative_path(run_id, "artifact.json")
    artifact_payload = json.dumps(expected_continue_artifact_payload(run_id), ensure_ascii=False)
    return (
        "Continue the same live validation. "
        f"In {root}, write continue.txt with exactly: {expected_continue_text(run_id)} "
        f"Replace {artifact_path} so its parsed JSON object is exactly: {artifact_payload} "
        "Finish with one short status line."
    )



def summarize_history_message(message: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "messageId": message.get("messageId"),
            "recentIndex": message.get("recentIndex"),
            "role": message.get("role"),
            "status": message.get("status"),
            "completedAt": message.get("completedAt"),
            "textPreview": message.get("textPreview"),
            "toolCallCount": message.get("toolCallCount"),
        }.items()
        if value is not None
    }



def scan_recent_history_messages(
    config: dict[str, Any],
    *,
    registry_path: Path,
    artifact_dir: Path,
    cwd: Path,
    session_id: str,
    workspace: str,
    max_messages: int = 6,
) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    scanned_indices: list[int] = []
    message_count_in_window = 0
    scan_limit = 0

    latest_result = run_json_command(
        "06-history-scan-00",
        build_manager_argv(
            config,
            registry_path=registry_path,
            command="inspect-history",
            extra_args=[
                "--opencode-session-id",
                session_id,
                "--opencode-workspace",
                workspace,
                "--history-message-limit",
                str(config["historyMessageLimit"]),
                "--watch-timeout-sec",
                str(config["watchTimeoutSec"]),
                "--recent-index",
                "0",
            ],
        ),
        artifact_dir=artifact_dir,
        cwd=cwd,
    )
    latest_history = latest_result.data.get("history") if isinstance(latest_result.data.get("history"), dict) else {}
    latest_message = latest_history.get("message") if isinstance(latest_history.get("message"), dict) else {}
    messages.append(latest_message)
    scanned_indices.append(0)
    selection = latest_history.get("selection") if isinstance(latest_history.get("selection"), dict) else {}
    message_count_in_window = int(selection.get("messageCountInWindow") or len(messages))
    scan_limit = min(max_messages, message_count_in_window)

    for recent_index in range(1, scan_limit):
        result = run_json_command(
            f"06-history-scan-{recent_index:02d}",
            build_manager_argv(
                config,
                registry_path=registry_path,
                command="inspect-history",
                extra_args=[
                    "--opencode-session-id",
                    session_id,
                    "--opencode-workspace",
                    workspace,
                    "--history-message-limit",
                    str(config["historyMessageLimit"]),
                    "--watch-timeout-sec",
                    str(config["watchTimeoutSec"]),
                    "--recent-index",
                    str(recent_index),
                ],
            ),
            artifact_dir=artifact_dir,
            cwd=cwd,
        )
        history = result.data.get("history") if isinstance(result.data.get("history"), dict) else {}
        message = history.get("message") if isinstance(history.get("message"), dict) else {}
        messages.append(message)
        scanned_indices.append(recent_index)

    return {
        "messages": messages,
        "scannedIndices": scanned_indices,
        "messageCountInWindow": message_count_in_window,
        "scanLimit": scan_limit,
    }



def normalize_tool_targets(tool_call: dict[str, Any]) -> list[str]:
    targets: list[str] = []
    for key in ("writeTargets", "patchTargets", "targets"):
        value = tool_call.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item not in targets:
                    targets.append(item)
    return targets



def extract_patch_added_text(patch_text: str) -> str | None:
    added_lines: list[str] = []
    for line in patch_text.splitlines():
        if line.startswith(("+++", "---", "@@")):
            continue
        if line.startswith("+"):
            added_lines.append(line[1:])
    if not added_lines:
        return None
    return "\n".join(added_lines)



def iter_tool_call_text_candidates(tool_call: dict[str, Any]):
    seen: set[tuple[str, str]] = set()
    for key in ("content", "newText", "patch"):
        value = tool_call.get(key)
        if isinstance(value, str) and value:
            token = (key, value)
            if token not in seen:
                seen.add(token)
                yield key, value
            if key == "patch":
                patch_added = extract_patch_added_text(value)
                if patch_added:
                    patch_token = ("patchAddedText", patch_added)
                    if patch_token not in seen:
                        seen.add(patch_token)
                        yield "patchAddedText", patch_added



def find_message_text_artifact(message: dict[str, Any], *, target_path: str, expected_text: str) -> dict[str, Any]:
    tool_calls = message.get("toolCalls") if isinstance(message.get("toolCalls"), list) else []
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            continue
        if target_path not in normalize_tool_targets(tool_call):
            continue
        for source, candidate in iter_tool_call_text_candidates(tool_call):
            normalized = candidate.rstrip("\n")
            if normalized == expected_text:
                return {
                    "matched": True,
                    "targetPath": target_path,
                    "toolName": tool_call.get("toolName"),
                    "action": tool_call.get("action"),
                    "contentSource": source,
                    "contentPreview": tool_call.get("contentPreview") or tool_call.get("newTextPreview") or tool_call.get("patchPreview"),
                }
    return {"matched": False, "targetPath": target_path, "expectedText": expected_text}



def find_message_json_artifact(message: dict[str, Any], *, target_path: str, expected_payload: dict[str, Any]) -> dict[str, Any]:
    tool_calls = message.get("toolCalls") if isinstance(message.get("toolCalls"), list) else []
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            continue
        if target_path not in normalize_tool_targets(tool_call):
            continue
        for source, candidate in iter_tool_call_text_candidates(tool_call):
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if parsed == expected_payload:
                return {
                    "matched": True,
                    "targetPath": target_path,
                    "toolName": tool_call.get("toolName"),
                    "action": tool_call.get("action"),
                    "contentSource": source,
                    "parsed": parsed,
                }
    return {"matched": False, "targetPath": target_path, "expectedPayload": expected_payload}



def message_completed(message: dict[str, Any]) -> bool:
    return (
        str(message.get("role") or "").strip().lower() == "assistant"
        and str(message.get("status") or "").strip().lower() == "completed"
        and bool(message.get("completedAt"))
    )



def evaluate_workspace_business_completion(run_id: str, history_messages: list[dict[str, Any]]) -> dict[str, Any]:
    assistant_messages = [
        message
        for message in history_messages
        if isinstance(message, dict)
        and str(message.get("role") or "").strip().lower() == "assistant"
        and str(message.get("status") or "").strip().lower() == "completed"
    ]
    assistant_messages.sort(key=lambda item: int(item.get("recentIndex") or 0), reverse=True)
    start_message = assistant_messages[0] if len(assistant_messages) >= 2 else None
    continue_message = assistant_messages[-1] if len(assistant_messages) >= 2 else None

    start_text = find_message_text_artifact(
        start_message or {},
        target_path=validation_relative_path(run_id, "start.txt"),
        expected_text=expected_start_text(run_id),
    )
    start_json = find_message_json_artifact(
        start_message or {},
        target_path=validation_relative_path(run_id, "artifact.json"),
        expected_payload=expected_start_artifact_payload(run_id),
    )
    continue_text = find_message_text_artifact(
        continue_message or {},
        target_path=validation_relative_path(run_id, "continue.txt"),
        expected_text=expected_continue_text(run_id),
    )
    continue_json = find_message_json_artifact(
        continue_message or {},
        target_path=validation_relative_path(run_id, "artifact.json"),
        expected_payload=expected_continue_artifact_payload(run_id),
    )

    start_result = {
        "message": summarize_history_message(start_message or {}),
        "messageCompleted": message_completed(start_message or {}),
        "startText": start_text,
        "artifactJson": start_json,
    }
    start_result["passed"] = bool(start_result["messageCompleted"] and start_text.get("matched") and start_json.get("matched"))

    continue_result = {
        "message": summarize_history_message(continue_message or {}),
        "messageCompleted": message_completed(continue_message or {}),
        "continueText": continue_text,
        "artifactJson": continue_json,
    }
    continue_result["passed"] = bool(
        continue_result["messageCompleted"] and continue_text.get("matched") and continue_json.get("matched")
    )

    return {
        "assistantTurnCount": len(assistant_messages),
        "assistantMessages": [summarize_history_message(message) for message in assistant_messages],
        "start": start_result,
        "continue": continue_result,
    }



def finalize_summary(summary: dict[str, Any], *, summary_path: Path, checks: list[dict[str, Any]], preflight_ok: bool) -> None:
    summary["checks"] = checks
    summary["finishedAt"] = now_iso()
    summary["verdict"] = build_verdict(checks, preflight_ok=preflight_ok, has_error=bool(summary.get("error")))
    save_json_file(summary_path, summary)



def best_effort_stop(
    config: dict[str, Any],
    *,
    registry_path: Path,
    artifact_dir: Path,
    cwd: Path,
    watcher_id: str,
    label: str,
) -> dict[str, Any]:
    try:
        result = run_json_command(
            label,
            build_manager_argv(
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
            cwd=cwd,
        )
        return {"ok": True, "watcherId": watcher_id, "result": result.data}
    except Exception as exc:
        return {"ok": False, "watcherId": watcher_id, "error": str(exc)}



def run_validation(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    repo_root = REPO_ROOT
    requested_config_path = Path(args.config).expanduser().resolve()
    config = load_validation_config(requested_config_path)
    git_info = collect_git_info(repo_root)
    artifact_dir = build_artifact_dir(Path(args.artifact_root), short_head=git_info["shortHead"])
    registry_path = artifact_dir / "registry.json"
    summary_path = artifact_dir / "summary.json"

    checks: list[dict[str, Any]] = []
    preflight_ok = True
    preflight_details = {
        "expectedHead": args.expected_head,
        "actualHead": git_info["head"],
        "actualShortHead": git_info["shortHead"],
        "originMainHead": git_info.get("originMainHead"),
        "headMatchesOriginMain": git_info.get("headMatchesOriginMain"),
        "clean": git_info["clean"],
        "statusShort": git_info["statusShort"],
    }
    if args.expected_head and git_info["head"] != args.expected_head:
        preflight_ok = False
        preflight_details["reason"] = "expected_head_mismatch"
    if args.require_clean and not git_info["clean"]:
        preflight_ok = False
        preflight_details["reason"] = preflight_details.get("reason") or "git_tree_dirty"
    checks.append(bool_check("git_preflight", preflight_ok, details=preflight_details))

    summary: dict[str, Any] = {
        "kind": "opencode_live_validation_summary_v1",
        "startedAt": now_iso(),
        "artifactDir": str(artifact_dir),
        "commandsDir": str(artifact_dir / "commands"),
        "config": redacted_config_snapshot(config, requested_config_path=requested_config_path),
        "git": git_info,
        "checks": checks,
        "scenario": {},
        "verdict": "failed",
    }
    save_json_file(artifact_dir / "config.redacted.json", summary["config"])
    save_json_file(artifact_dir / "git.json", git_info)
    save_json_file(summary_path, summary)

    if not preflight_ok:
        finalize_summary(summary, summary_path=summary_path, checks=checks, preflight_ok=preflight_ok)
        return summary, 2

    run_id = args.run_id or f"v{slug_timestamp().lower()}-{uuid.uuid4().hex[:6]}"
    scenario: dict[str, Any] = {
        "runId": run_id,
        "workspace": config["workspace"],
        "registryPath": str(registry_path),
    }
    summary["scenario"] = scenario
    save_json_file(summary_path, summary)

    session_id: str | None = None
    watcher_id: str | None = None
    attached_watcher_id: str | None = None

    try:
        start_result = run_json_command(
            "01-start",
            build_manager_argv(
                config,
                registry_path=registry_path,
                command="start",
                extra_args=[
                    "--opencode-workspace",
                    config["workspace"],
                    *manager_runtime_args(config),
                    "--title",
                    f"OpenCode live validation {run_id}",
                    "--first-prompt",
                    build_start_prompt(run_id),
                ],
            ),
            artifact_dir=artifact_dir,
            cwd=repo_root,
        )
        start_data = start_result.data
        session_summary = start_data.get("opencodeSession") if isinstance(start_data.get("opencodeSession"), dict) else {}
        start_watcher = start_data.get("watcher") if isinstance(start_data.get("watcher"), dict) else {}
        session_id = normalize_optional_string(session_summary.get("opencodeSessionId"))
        watcher_id = normalize_optional_string(start_watcher.get("watcherId"))
        watcher_state_path = Path(str(start_watcher.get("watcherStatePath") or ""))
        watcher_log_path = Path(str(start_watcher.get("watcherLogPath") or ""))
        scenario.update(
            {
                "opencodeSessionId": session_id,
                "startWatcherId": watcher_id,
                "watcherStatePath": str(watcher_state_path),
                "watcherLogPath": str(watcher_log_path),
            }
        )
        checks.append(
            bool_check(
                "start_watcher_live",
                bool(session_id)
                and bool(watcher_id)
                and start_data.get("handoffMode") == "watcher_live"
                and start_watcher.get("watcherStatus") == "running"
                and start_watcher.get("watchLive") is True,
                details={
                    "opencodeSessionId": session_id,
                    "watcherId": watcher_id,
                    "handoffMode": start_data.get("handoffMode"),
                    "watcherStatus": start_watcher.get("watcherStatus"),
                    "watchLive": start_watcher.get("watchLive"),
                },
            )
        )
        save_json_file(summary_path, summary)

        if not session_id or not watcher_id:
            raise ValidationError("start command did not return opencodeSessionId/watcherId")

        continue_result = run_json_command(
            "02-continue",
            build_manager_argv(
                config,
                registry_path=registry_path,
                command="continue",
                extra_args=[
                    "--opencode-session-id",
                    session_id,
                    "--opencode-workspace",
                    config["workspace"],
                    "--follow-up-prompt",
                    build_continue_prompt(run_id),
                    "--ensure-watcher",
                    *manager_runtime_args(config),
                ],
            ),
            artifact_dir=artifact_dir,
            cwd=repo_root,
        )
        continue_data = continue_result.data
        continue_watcher = continue_data.get("watcher") if isinstance(continue_data.get("watcher"), dict) else {}
        checks.append(
            bool_check(
                "continue_watcher_reuse",
                continue_data.get("watcherAlreadyRunning") is True
                and continue_data.get("handoffMode") == "watcher_live"
                and continue_watcher.get("watcherId") == watcher_id,
                details={
                    "watcherAlreadyRunning": continue_data.get("watcherAlreadyRunning"),
                    "handoffMode": continue_data.get("handoffMode"),
                    "continuedWatcherId": continue_watcher.get("watcherId"),
                    "expectedWatcherId": watcher_id,
                },
            )
        )
        save_json_file(summary_path, summary)

        executed_state_doc = wait_for_watch_execution(
            watcher_state_path,
            timeout_sec=config["waitForExecutionSec"],
            poll_interval_sec=args.poll_interval_sec,
        )
        watch_state = executed_state_doc.get("watchRunner") if isinstance(executed_state_doc.get("watchRunner"), dict) else {}
        checks.append(
            bool_check(
                "live_handoff_executed",
                bool(watch_state.get("lastExecutedActionKey"))
                and watch_state.get("lastOperation") == "execute"
                and watch_state.get("lastRouteStatus") == "ready"
                and watch_state.get("lastDeliveryAction") == "inject",
                details={
                    "lastExecutedActionKey": watch_state.get("lastExecutedActionKey"),
                    "lastOperation": watch_state.get("lastOperation"),
                    "lastRouteStatus": watch_state.get("lastRouteStatus"),
                    "lastDeliveryAction": watch_state.get("lastDeliveryAction"),
                    "lastFactStatus": watch_state.get("lastFactStatus"),
                    "lastFactPhase": watch_state.get("lastFactPhase"),
                },
            )
        )
        save_json_file(summary_path, summary)

        watch_log_summary = summarize_watch_log(watcher_log_path)
        envelope = watch_log_summary["handoffEnvelope"]
        forbidden_keys = [
            key
            for key in ["agentInput", "deliveryPolicy", "consumptionPolicy", "facts", "cadence", "taskCluster", "replyPolicy"]
            if key in envelope
        ]
        checks.append(
            bool_check(
                "minimal_runtime_handoff_shape",
                envelope.get("kind") == "opencode_origin_session_handoff"
                and envelope.get("version") == "v2"
                and envelope.get("runtimeSignal")
                == {"action": "inspect_once_current_state", "opencodeSessionId": session_id}
                and not forbidden_keys,
                details={
                    "kind": envelope.get("kind"),
                    "version": envelope.get("version"),
                    "runtimeSignal": envelope.get("runtimeSignal"),
                    "forbiddenKeysPresent": forbidden_keys,
                },
            )
        )
        scenario["watchLog"] = {
            "documentCount": watch_log_summary.get("documentCount"),
            "stepCount": watch_log_summary.get("stepCount"),
            "lastWatchAction": ((watch_log_summary.get("lastStep") or {}).get("watchAction") or {}).get("operation"),
        }
        save_json_file(summary_path, summary)

        completion_timeout_sec = max(config["waitForExecutionSec"], config["inspectRetrySec"] * max(args.inspect_attempts, 1))
        inspection_data = wait_for_terminal_inspection(
            config,
            registry_path=registry_path,
            artifact_dir=artifact_dir,
            cwd=repo_root,
            session_id=session_id,
            workspace=config["workspace"],
            timeout_sec=completion_timeout_sec,
            poll_interval_sec=args.poll_interval_sec,
            label_prefix="03-inspect-terminal",
        )
        current_state = inspection_current_state(inspection_data)
        latest_terminal_message = inspection_latest_message(inspection_data)
        checks.append(
            bool_check(
                "inspect_current_state",
                bool(current_state.get("status")) and current_state.get("status") in TERMINAL_STATUSES,
                details={
                    "status": current_state.get("status"),
                    "phase": current_state.get("phase"),
                    "latestMeaningfulPreview": current_state.get("latestMeaningfulPreview"),
                    "latestMessageRole": latest_terminal_message.get("role"),
                    "latestMessageStatus": latest_terminal_message.get("status"),
                },
            )
        )
        checks.append(
            bool_check(
                "session_completed_terminal",
                str(current_state.get("status") or "").strip().lower() == "completed"
                and str(latest_terminal_message.get("role") or "").strip().lower() == "assistant"
                and str(latest_terminal_message.get("status") or "").strip().lower() == "completed"
                and bool(latest_terminal_message.get("completedAt")),
                details={
                    "status": current_state.get("status"),
                    "phase": current_state.get("phase"),
                    "allTodosCompleted": current_state.get("allTodosCompleted"),
                    "hasPendingWork": current_state.get("hasPendingWork"),
                    "latestMessage": summarize_history_message(latest_terminal_message),
                },
            )
        )
        scenario["inspectionCurrentState"] = current_state
        scenario["terminalLatestMessage"] = summarize_history_message(latest_terminal_message)
        save_json_file(summary_path, summary)

        stop_result = run_json_command(
            "04-stop-watcher",
            build_manager_argv(
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
            cwd=repo_root,
        )
        stop_data = stop_result.data
        stopped_watchers = stop_data.get("watchers") if isinstance(stop_data.get("watchers"), list) else []
        stopped_summary = next((item for item in stopped_watchers if isinstance(item, dict) and item.get("watcherId") == watcher_id), {})
        checks.append(
            bool_check(
                "stop_initial_watcher",
                stop_data.get("stopped") is True
                and stop_data.get("watcherCount") == 1
                and stopped_summary.get("watcherStatus") == "exited",
                details={
                    "stopped": stop_data.get("stopped"),
                    "watcherCount": stop_data.get("watcherCount"),
                    "watcherStatus": stopped_summary.get("watcherStatus"),
                    "watchExitReason": stopped_summary.get("watchExitReason"),
                },
            )
        )
        wait_for_watcher_status(
            config,
            registry_path=registry_path,
            artifact_dir=artifact_dir,
            cwd=repo_root,
            watcher_id=watcher_id,
            expected_status="exited",
            timeout_sec=config["waitForStopSec"],
            poll_interval_sec=args.poll_interval_sec,
            label_prefix="04a-wait-stop",
        )
        save_json_file(summary_path, summary)

        attach_result = run_json_command(
            "05-attach",
            build_manager_argv(
                config,
                registry_path=registry_path,
                command="attach",
                extra_args=[
                    "--opencode-session-id",
                    session_id,
                    "--opencode-workspace",
                    config["workspace"],
                    *manager_runtime_args(config),
                ],
            ),
            artifact_dir=artifact_dir,
            cwd=repo_root,
        )
        attach_data = attach_result.data
        attached_watcher = attach_data.get("watcher") if isinstance(attach_data.get("watcher"), dict) else {}
        attached_watcher_id = normalize_optional_string(attached_watcher.get("watcherId"))
        attach_current_state = inspection_current_state(attach_data)
        attach_inspection = attach_data.get("inspection") if isinstance(attach_data.get("inspection"), dict) else {}
        attach_rehydration = attach_inspection.get("rehydration") if isinstance(attach_inspection.get("rehydration"), dict) else {}
        checks.append(
            bool_check(
                "attach_rehydration",
                bool(attached_watcher_id)
                and attached_watcher_id != watcher_id
                and attached_watcher.get("watchLive") is True
                and attach_rehydration.get("purpose") == "current_state_rebuild"
                and str(attach_current_state.get("status") or "").strip().lower() == "completed",
                details={
                    "attachedWatcherId": attached_watcher_id,
                    "priorWatcherId": watcher_id,
                    "watchLive": attached_watcher.get("watchLive"),
                    "rehydrationPurpose": attach_rehydration.get("purpose"),
                    "currentState": attach_current_state,
                },
            )
        )
        scenario["attachedWatcherId"] = attached_watcher_id
        scenario["attachedInspectionCurrentState"] = attach_current_state
        save_json_file(summary_path, summary)

        history_scan = scan_recent_history_messages(
            config,
            registry_path=registry_path,
            artifact_dir=artifact_dir,
            cwd=repo_root,
            session_id=session_id,
            workspace=config["workspace"],
        )
        history_messages = history_scan.get("messages") if isinstance(history_scan.get("messages"), list) else []
        latest_history_message = history_messages[0] if history_messages else {}
        latest_tool_calls = latest_history_message.get("toolCalls") if isinstance(latest_history_message.get("toolCalls"), list) else []
        business_completion = evaluate_workspace_business_completion(run_id, history_messages)
        checks.append(
            bool_check(
                "inspect_history",
                bool(latest_history_message.get("messageId")) and business_completion.get("assistantTurnCount", 0) >= 2,
                details={
                    "messageId": latest_history_message.get("messageId"),
                    "recentIndex": latest_history_message.get("recentIndex"),
                    "toolCallCount": len(latest_tool_calls),
                    "assistantTurnCount": business_completion.get("assistantTurnCount"),
                    "scannedIndices": history_scan.get("scannedIndices"),
                },
            )
        )
        checks.append(
            bool_check(
                "workspace_start_artifact_content",
                bool((business_completion.get("start") or {}).get("passed")),
                details=business_completion.get("start") or {},
            )
        )
        checks.append(
            bool_check(
                "workspace_continue_artifact_content",
                bool((business_completion.get("continue") or {}).get("passed")),
                details=business_completion.get("continue") or {},
            )
        )
        scenario["historyScan"] = {
            "messageCountInWindow": history_scan.get("messageCountInWindow"),
            "scanLimit": history_scan.get("scanLimit"),
            "scannedIndices": history_scan.get("scannedIndices"),
            "messages": [summarize_history_message(message) for message in history_messages],
        }
        scenario["businessCompletion"] = business_completion
        save_json_file(summary_path, summary)

        if attached_watcher_id:
            detach_result = run_json_command(
                "07-stop-attached-watcher",
                build_manager_argv(
                    config,
                    registry_path=registry_path,
                    command="stop-watcher",
                    extra_args=[
                        "--watcher-id",
                        attached_watcher_id,
                        "--stop-timeout-sec",
                        str(config["waitForStopSec"]),
                    ],
                ),
                artifact_dir=artifact_dir,
                cwd=repo_root,
            )
            detach_data = detach_result.data
            detached_watchers = detach_data.get("watchers") if isinstance(detach_data.get("watchers"), list) else []
            detached_summary = next(
                (item for item in detached_watchers if isinstance(item, dict) and item.get("watcherId") == attached_watcher_id),
                {},
            )
            checks.append(
                bool_check(
                    "stop_attached_watcher",
                    detach_data.get("stopped") is True
                    and detach_data.get("watcherCount") == 1
                    and detached_summary.get("watcherStatus") == "exited",
                    details={
                        "stopped": detach_data.get("stopped"),
                        "watcherCount": detach_data.get("watcherCount"),
                        "watcherStatus": detached_summary.get("watcherStatus"),
                        "watchExitReason": detached_summary.get("watchExitReason"),
                    },
                )
            )
            wait_for_watcher_status(
                config,
                registry_path=registry_path,
                artifact_dir=artifact_dir,
                cwd=repo_root,
                watcher_id=attached_watcher_id,
                expected_status="exited",
                timeout_sec=config["waitForStopSec"],
                poll_interval_sec=args.poll_interval_sec,
                label_prefix="07a-wait-stop",
            )
            save_json_file(summary_path, summary)

        final_watchers = run_json_command(
            "08-list-watchers-final",
            build_manager_argv(
                config,
                registry_path=registry_path,
                command="list-watchers",
                extra_args=["--include-exited"],
            ),
            artifact_dir=artifact_dir,
            cwd=repo_root,
        )
        scenario["finalWatchers"] = final_watchers.data.get("watchers")
        finalize_summary(summary, summary_path=summary_path, checks=checks, preflight_ok=preflight_ok)
        return summary, 0 if summary["verdict"] == "ready" else 1

    except Exception as exc:
        cleanup_actions = []
        seen_cleanup_ids: set[str] = set()
        for candidate in [attached_watcher_id, watcher_id]:
            if not candidate or candidate in seen_cleanup_ids:
                continue
            seen_cleanup_ids.add(candidate)
            cleanup_actions.append(
                best_effort_stop(
                    config,
                    registry_path=registry_path,
                    artifact_dir=artifact_dir,
                    cwd=repo_root,
                    watcher_id=candidate,
                    label=f"99-cleanup-stop-{len(cleanup_actions) + 1:02d}",
                )
            )
        summary["error"] = {
            "type": exc.__class__.__name__,
            "message": str(exc),
        }
        if cleanup_actions:
            summary["cleanup"] = cleanup_actions
        finalize_summary(summary, summary_path=summary_path, checks=checks, preflight_ok=preflight_ok)
        return summary, 1



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run an isolated end-to-end live validation of the OpenCode manager/watcher path. "
            "The harness creates a fresh artifact directory under /tmp, keeps watcher registry/runtime files there, "
            "records concise per-step JSON artifacts, and writes summary.json for machine-readable results."
        )
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="validation config JSON or env file (default: %(default)s)")
    parser.add_argument("--artifact-root", default=str(DEFAULT_ARTIFACT_ROOT), help="directory under which a fresh artifact dir is created")
    parser.add_argument("--expected-head", help="optional git HEAD sha that must match before live validation runs")
    parser.add_argument("--require-clean", action="store_true", help="fail preflight if git status is not clean")
    parser.add_argument("--run-id", help="optional stable run id used inside the OpenCode workspace")
    parser.add_argument("--poll-interval-sec", type=float, default=DEFAULT_POLL_INTERVAL_SEC)
    parser.add_argument("--inspect-attempts", type=int, default=DEFAULT_INSPECT_ATTEMPTS)
    return parser



def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    summary, exit_code = run_validation(args)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
