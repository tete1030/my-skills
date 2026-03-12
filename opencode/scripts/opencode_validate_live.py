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

from opencode_api_client import OpenCodeClient
from opencode_delivery_handoff import SYSTEM_EVENT_TEXT_HEADER
from opencode_openclaw_agent_call import build_gateway_agent_call

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
DEFAULT_RECEIVER_WAIT_SEC = 60
DEFAULT_RECEIVER_QUIET_SEC = 8
DEFAULT_OPENCLAW_SESSIONS_INDEX = Path.home() / ".openclaw" / "agents" / "main" / "sessions" / "sessions.json"
TERMINAL_STATUSES = {"completed", "failed", "cancelled", "blocked"}
CORE_CHECK_NAMES = (
    "start_watcher_live",
    "continue_watcher_reuse",
    "live_handoff_executed",
    "minimal_runtime_handoff_shape",
    "inspect_current_state",
    "session_completed_terminal",
    "latest_completed_assistant_turn",
    "workspace_final_file_content",
    "attach_rehydration",
    "receiver_first_event_observed",
    "receiver_one_off_inspect",
    "receiver_visible_reply_from_current_state",
    "receiver_quiet_after_reply",
    "receiver_duplicate_no_visible_reply",
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



def save_text_file(path: Path, content: str) -> None:
    ensure_directory(path.parent)
    path.write_text(content, encoding="utf-8")



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
        "handoff": handoff,
        "lastStep": {
            "watchAction": last_step.get("watchAction"),
            "deliveryAction": delivery.get("deliveryAction"),
            "routeStatus": delivery.get("routeStatus"),
        },
    }



def load_openclaw_sessions_index(path: Path = DEFAULT_OPENCLAW_SESSIONS_INDEX) -> dict[str, Any]:
    if not path.exists():
        raise ValidationError(f"OpenClaw sessions index not found: {path}")
    payload = load_json_file(path)
    if not isinstance(payload, dict):
        raise ValidationError(f"OpenClaw sessions index must be a JSON object: {path}")
    return payload



def resolve_openclaw_session_file(
    session_key: str,
    *,
    index_path: Path = DEFAULT_OPENCLAW_SESSIONS_INDEX,
    timeout_sec: int = DEFAULT_RECEIVER_WAIT_SEC,
    poll_interval_sec: float = DEFAULT_POLL_INTERVAL_SEC,
) -> Path:
    deadline = time.monotonic() + timeout_sec
    last_keys: list[str] = []
    while time.monotonic() <= deadline:
        sessions_index = load_openclaw_sessions_index(index_path)
        last_keys = list(sessions_index)
        entry = sessions_index.get(session_key)
        if isinstance(entry, dict):
            session_file = normalize_optional_string(entry.get("sessionFile"))
            if session_file and Path(session_file).exists():
                return Path(session_file)
        time.sleep(poll_interval_sec)
    raise ValidationError(
        f"OpenClaw session file not resolved for {session_key!r} within {timeout_sec}s; known keys sample={last_keys[:6]}"
    )



def read_jsonl_objects(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise ValidationError(f"JSONL file not found: {path}")
    objects: list[dict[str, Any]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            item = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValidationError(f"invalid JSONL line {line_number} in {path}: {exc}") from exc
        if isinstance(item, dict):
            item["_lineNumber"] = line_number
            objects.append(item)
    return objects



def session_entry_role(entry: dict[str, Any]) -> str:
    message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
    return str(message.get("role") or "").strip().lower()



def session_entry_content_items(entry: dict[str, Any]) -> list[dict[str, Any]]:
    message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
    content = message.get("content") if isinstance(message.get("content"), list) else []
    return [item for item in content if isinstance(item, dict)]



def session_entry_text(entry: dict[str, Any]) -> str:
    texts = [item.get("text") for item in session_entry_content_items(entry) if item.get("type") == "text" and isinstance(item.get("text"), str)]
    return "\n".join(texts).strip()



def session_entry_tool_calls(entry: dict[str, Any]) -> list[dict[str, Any]]:
    tool_calls: list[dict[str, Any]] = []
    for item in session_entry_content_items(entry):
        if item.get("type") != "toolCall":
            continue
        arguments = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
        tool_calls.append(
            {
                "name": item.get("name"),
                "arguments": arguments,
                "argumentsText": json.dumps(arguments, ensure_ascii=False, sort_keys=True),
            }
        )
    return tool_calls



def entry_is_runtime_signal(entry: dict[str, Any], *, session_id: str) -> bool:
    return (
        session_entry_role(entry) == "user"
        and SYSTEM_EVENT_TEXT_HEADER in session_entry_text(entry)
        and session_id in session_entry_text(entry)
    )



def entry_is_terminal_assistant_text(entry: dict[str, Any]) -> bool:
    return session_entry_role(entry) == "assistant" and bool(session_entry_text(entry))



def entry_contains_session_inspect(entry: dict[str, Any], *, session_id: str) -> bool:
    for tool_call in session_entry_tool_calls(entry):
        arguments = tool_call.get("arguments") or {}
        command = str(arguments.get("command") or "")
        if session_id not in command and session_id not in str(tool_call.get("argumentsText") or ""):
            continue
        if "opencode_manager.py inspect" in command or "opencode_manager.py inspect-history" in command:
            return True
    return False



def extract_event_window(entries: list[dict[str, Any]], *, session_id: str, occurrence: int = 1) -> dict[str, Any] | None:
    matched_starts = [index for index, entry in enumerate(entries) if entry_is_runtime_signal(entry, session_id=session_id)]
    if len(matched_starts) < occurrence:
        return None
    start_index = matched_starts[occurrence - 1]
    next_start_index = matched_starts[occurrence] if len(matched_starts) > occurrence else len(entries)
    terminal_index = None
    for index in range(start_index + 1, next_start_index):
        if entry_is_terminal_assistant_text(entries[index]):
            terminal_index = index
            break
    if terminal_index is None:
        return None
    window_entries = entries[start_index : terminal_index + 1]
    return {
        "occurrence": occurrence,
        "startIndex": start_index,
        "terminalIndex": terminal_index,
        "startLine": entries[start_index].get("_lineNumber"),
        "terminalLine": entries[terminal_index].get("_lineNumber"),
        "entries": window_entries,
        "eventText": session_entry_text(entries[start_index]),
        "replyText": session_entry_text(entries[terminal_index]),
        "inspectEntryCount": sum(1 for entry in window_entries if entry_contains_session_inspect(entry, session_id=session_id)),
        "inspectEntries": [
            {
                "lineNumber": entry.get("_lineNumber"),
                "timestamp": entry.get("timestamp"),
                "toolCalls": session_entry_tool_calls(entry),
                "text": session_entry_text(entry),
            }
            for entry in window_entries
            if entry_contains_session_inspect(entry, session_id=session_id)
        ],
        "assistantTextEntries": [
            {
                "lineNumber": entry.get("_lineNumber"),
                "timestamp": entry.get("timestamp"),
                "text": session_entry_text(entry),
            }
            for entry in window_entries
            if entry_is_terminal_assistant_text(entry)
        ],
        "toolResultEntries": [
            {
                "lineNumber": entry.get("_lineNumber"),
                "timestamp": entry.get("timestamp"),
                "toolName": ((entry.get("message") or {}).get("toolName")),
                "text": session_entry_text(entry),
            }
            for entry in window_entries
            if session_entry_role(entry) == "toolresult"
        ],
    }



def wait_for_event_window(
    session_file: Path,
    *,
    baseline_line_count: int,
    session_id: str,
    occurrence: int,
    timeout_sec: int,
    poll_interval_sec: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    deadline = time.monotonic() + timeout_sec
    latest_entries: list[dict[str, Any]] = []
    while time.monotonic() <= deadline:
        all_entries = read_jsonl_objects(session_file)
        latest_entries = all_entries[baseline_line_count:]
        window = extract_event_window(latest_entries, session_id=session_id, occurrence=occurrence)
        if window is not None:
            return window, latest_entries
        time.sleep(poll_interval_sec)
    raise ValidationError(
        f"receiver session never completed event window occurrence={occurrence} for {session_id} within {timeout_sec}s"
    )



def wait_for_session_quiet(
    session_file: Path,
    *,
    minimum_line_count: int,
    quiet_sec: int,
    poll_interval_sec: float,
) -> list[dict[str, Any]]:
    time.sleep(max(quiet_sec, 0))
    entries = read_jsonl_objects(session_file)
    if len(entries) < minimum_line_count:
        raise ValidationError(
            f"receiver session shrank unexpectedly: expected at least {minimum_line_count} lines, got {len(entries)}"
        )
    return entries



def summarize_receiver_window(window: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "occurrence": window.get("occurrence"),
            "startLine": window.get("startLine"),
            "terminalLine": window.get("terminalLine"),
            "inspectEntryCount": window.get("inspectEntryCount"),
            "replyText": window.get("replyText"),
            "inspectEntries": window.get("inspectEntries"),
            "toolResultEntries": window.get("toolResultEntries"),
            "assistantTextEntries": window.get("assistantTextEntries"),
        }.items()
        if value is not None
    }



def receiver_reply_looks_like_current_state(reply_text: str, *, session_id: str) -> bool:
    text = (reply_text or "").strip()
    if not text or text == "NO_REPLY":
        return False
    forbidden = [
        "<opencodeEvent>",
        "</opencodeEvent>",
        SYSTEM_EVENT_TEXT_HEADER,
        "runtimeSignal",
        "inspect_once_current_state",
        session_id,
    ]
    return not any(token in text for token in forbidden)



def tool_result_is_opencode_inspect(window: dict[str, Any], *, session_id: str) -> bool:
    for entry in window.get("toolResultEntries") or []:
        text = str(entry.get("text") or "")
        if '"kind": "opencode_manager_inspect_v1"' not in text and '"kind":"opencode_manager_inspect_v1"' not in text:
            continue
        if session_id not in text:
            continue
        if '"currentStatus":' in text or '"currentState":' in text:
            return True
    return False



def run_gateway_agent_call(
    label: str,
    *,
    gateway_params: dict[str, Any],
    artifact_dir: Path,
    cwd: Path,
    timeout_ms: int = 10_000,
) -> CommandResult:
    return run_json_command(
        label,
        [
            "openclaw",
            "gateway",
            "call",
            "agent",
            "--json",
            "--timeout",
            str(timeout_ms),
            "--params",
            json.dumps(gateway_params, ensure_ascii=False),
        ],
        artifact_dir=artifact_dir,
        cwd=cwd,
    )



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
    return str(Path(".claw-validation"))



def validation_relative_path(run_id: str, name: str) -> str:
    return str(Path(validation_relative_root(run_id)) / name)



def validation_output_path(run_id: str) -> str:
    return validation_relative_path(run_id, f"{run_id}.txt")



def expected_start_text(run_id: str) -> str:
    return f"start ok {run_id}"



def expected_continue_text(run_id: str) -> str:
    return f"continue ok {run_id}"



def expected_final_output(run_id: str) -> str:
    return f"{expected_start_text(run_id)}\n{expected_continue_text(run_id)}"



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
    output_path = validation_output_path(run_id)
    return (
        "Live validation only. Work only inside the current workspace. "
        f"Using shell commands, create directory {validation_relative_root(run_id)} if needed. "
        f"Then write {output_path} with exactly one line: {expected_start_text(run_id)} "
        f"Then sleep 8 seconds and print the full contents of {output_path}. "
        "Finish with one short status line."
    )



def build_continue_prompt(run_id: str) -> str:
    output_path = validation_output_path(run_id)
    return (
        "Continue the same live validation. "
        f"Using shell commands only, append one second line to {output_path}: {expected_continue_text(run_id)} "
        f"Then sleep 8 seconds and print the full contents of {output_path}. "
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



def compact_text_preview(text: str, limit: int = 180) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"



def fetch_raw_session_messages(config: dict[str, Any], *, session_id: str, workspace: str, limit: int) -> list[dict[str, Any]]:
    client = OpenCodeClient(str(config["baseUrl"]), token=config.get("token"))
    data = client.session_messages(session_id, limit=limit, workspace=workspace)
    return data if isinstance(data, list) else []



def normalize_raw_session_messages(raw_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    total = len(raw_messages)
    for index, item in enumerate(raw_messages):
        if not isinstance(item, dict):
            continue
        info = item.get("info") if isinstance(item.get("info"), dict) else {}
        parts = item.get("parts") if isinstance(item.get("parts"), list) else []
        role = str(info.get("role") or "").strip().lower()
        time_info = info.get("time") if isinstance(info.get("time"), dict) else {}
        completed_at = time_info.get("completed")
        text_preview = None
        tool_calls: list[dict[str, Any]] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type") or "").strip().lower()
            if part_type == "text" and isinstance(part.get("text"), str) and part.get("text") and text_preview is None:
                text_preview = compact_text_preview(part["text"])
            if part_type != "tool":
                continue
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            input_data = state.get("input") if isinstance(state.get("input"), dict) else {}
            output = state.get("output") if isinstance(state.get("output"), str) else ""
            output_lines = output.splitlines() if output else []
            tool_calls.append(
                {
                    "toolName": part.get("tool"),
                    "toolStatus": state.get("status"),
                    "action": "shell" if str(part.get("tool") or "").strip().lower() == "bash" else "tool",
                    "commandPreview": input_data.get("command"),
                    "content": output,
                    "outputPreview": compact_text_preview(output) if output else None,
                    "outputTailLines": output_lines[-10:] if output_lines else None,
                }
            )
        normalized.append(
            {
                "messageId": info.get("id"),
                "recentIndex": total - index - 1,
                "role": role,
                "status": "completed" if completed_at else "running",
                "completedAt": datetime.fromtimestamp(completed_at / 1000, tz=timezone.utc).isoformat() if completed_at else None,
                "textPreview": text_preview,
                "toolCallCount": len(tool_calls),
                "toolCalls": tool_calls,
            }
        )
    return normalized



def iter_tool_call_output_candidates(tool_call: dict[str, Any]):
    seen: set[tuple[str, str]] = set()
    for key in ("content", "outputPreview", "commandPreview"):
        value = tool_call.get(key)
        if isinstance(value, str) and value:
            token = (key, value)
            if token not in seen:
                seen.add(token)
                yield key, value
    output_tail_lines = tool_call.get("outputTailLines")
    if isinstance(output_tail_lines, list):
        lines = [str(line) for line in output_tail_lines if isinstance(line, str) and line]
        if lines:
            joined = "\n".join(lines)
            token = ("outputTailLines", joined)
            if token not in seen:
                seen.add(token)
                yield "outputTailLines", joined



def message_completed(message: dict[str, Any]) -> bool:
    return (
        str(message.get("role") or "").strip().lower() == "assistant"
        and str(message.get("status") or "").strip().lower() == "completed"
        and bool(message.get("completedAt"))
    )



def recent_index_value(message: dict[str, Any]) -> int:
    return int(message.get("recentIndex") or 0)



def find_latest_completed_assistant_turn(history_messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    assistant_messages = [
        message
        for message in history_messages
        if isinstance(message, dict)
        and str(message.get("role") or "").strip().lower() == "assistant"
        and str(message.get("status") or "").strip().lower() == "completed"
    ]
    if not assistant_messages:
        return None
    assistant_messages.sort(key=recent_index_value)
    return assistant_messages[0]



def contains_expected_output(candidate: str, expected_output: str) -> bool:
    normalized = candidate.replace("\r\n", "\n").replace("\r", "\n")
    expected_lines = expected_output.splitlines()
    return all(line in normalized for line in expected_lines) and normalized.find(expected_lines[0]) <= normalized.rfind(expected_lines[-1])



def find_final_output_in_message(message: dict[str, Any], *, expected_output: str) -> dict[str, Any]:
    text_preview = message.get("textPreview")
    if isinstance(text_preview, str) and contains_expected_output(text_preview, expected_output):
        return {
            "matched": True,
            "toolName": None,
            "action": "text",
            "contentSource": "textPreview",
            "commandPreview": None,
            "outputTailLines": None,
        }

    tool_calls = message.get("toolCalls") if isinstance(message.get("toolCalls"), list) else []
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            continue
        for source, candidate in iter_tool_call_output_candidates(tool_call):
            if contains_expected_output(candidate, expected_output):
                return {
                    "matched": True,
                    "toolName": tool_call.get("toolName"),
                    "action": tool_call.get("action"),
                    "contentSource": source,
                    "commandPreview": tool_call.get("commandPreview"),
                    "outputTailLines": tool_call.get("outputTailLines"),
                }
    return {"matched": False, "expectedOutput": expected_output}



def evaluate_workspace_business_completion(run_id: str, history_messages: list[dict[str, Any]]) -> dict[str, Any]:
    assistant_messages = [
        message
        for message in history_messages
        if isinstance(message, dict)
        and str(message.get("role") or "").strip().lower() == "assistant"
        and str(message.get("status") or "").strip().lower() == "completed"
    ]
    assistant_messages.sort(key=recent_index_value)
    latest_completed_message = assistant_messages[0] if assistant_messages else None
    latest_completed_result = {
        "message": summarize_history_message(latest_completed_message or {}),
        "messageCompleted": message_completed(latest_completed_message or {}),
    }
    latest_completed_result["passed"] = bool(latest_completed_result["messageCompleted"])

    final_output = find_final_output_in_message(
        latest_completed_message or {},
        expected_output=expected_final_output(run_id),
    )
    final_output_result = {
        "message": summarize_history_message(latest_completed_message or {}),
        "expectedOutput": expected_final_output(run_id),
        "match": final_output,
    }
    final_output_result["passed"] = bool(latest_completed_result["messageCompleted"] and final_output.get("matched"))

    return {
        "assistantTurnCount": len(assistant_messages),
        "assistantMessages": [summarize_history_message(message) for message in assistant_messages],
        "latestCompletedAssistantTurn": latest_completed_result,
        "finalFileContent": final_output_result,
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

    receiver_session_file = resolve_openclaw_session_file(config["openclawSessionKey"])
    receiver_baseline_entries = read_jsonl_objects(receiver_session_file)
    scenario["receiverSession"] = {
        "sessionKey": config["openclawSessionKey"],
        "sessionFile": str(receiver_session_file),
        "baselineLineCount": len(receiver_baseline_entries),
    }
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

        first_receiver_window, _receiver_entries = wait_for_event_window(
            receiver_session_file,
            baseline_line_count=scenario["receiverSession"]["baselineLineCount"],
            session_id=session_id,
            occurrence=1,
            timeout_sec=DEFAULT_RECEIVER_WAIT_SEC,
            poll_interval_sec=args.poll_interval_sec,
        )
        first_reply_text = str(first_receiver_window.get("replyText") or "")
        first_reply_line = int(first_receiver_window.get("terminalLine") or 0)
        receiver_quiet_entries = wait_for_session_quiet(
            receiver_session_file,
            minimum_line_count=first_reply_line,
            quiet_sec=DEFAULT_RECEIVER_QUIET_SEC,
            poll_interval_sec=args.poll_interval_sec,
        )
        new_entries_after_first_reply = [entry for entry in receiver_quiet_entries if int(entry.get("_lineNumber") or 0) > first_reply_line]
        checks.append(
            bool_check(
                "receiver_first_event_observed",
                True,
                details=summarize_receiver_window(first_receiver_window),
            )
        )
        checks.append(
            bool_check(
                "receiver_one_off_inspect",
                first_receiver_window.get("inspectEntryCount") == 1
                and tool_result_is_opencode_inspect(first_receiver_window, session_id=session_id),
                details=summarize_receiver_window(first_receiver_window),
            )
        )
        checks.append(
            bool_check(
                "receiver_visible_reply_from_current_state",
                receiver_reply_looks_like_current_state(first_reply_text, session_id=session_id),
                details={
                    **summarize_receiver_window(first_receiver_window),
                    "replyText": first_reply_text,
                },
            )
        )
        checks.append(
            bool_check(
                "receiver_quiet_after_reply",
                not any(session_entry_role(entry) == "assistant" for entry in new_entries_after_first_reply),
                details={
                    "replyLine": first_reply_line,
                    "newAssistantEntriesAfterReply": [
                        {
                            "lineNumber": entry.get("_lineNumber"),
                            "role": session_entry_role(entry),
                            "text": session_entry_text(entry),
                            "toolCalls": session_entry_tool_calls(entry),
                        }
                        for entry in new_entries_after_first_reply
                        if session_entry_role(entry) == "assistant"
                    ],
                },
            )
        )
        scenario["receiverSession"]["firstEvent"] = summarize_receiver_window(first_receiver_window)
        save_json_file(artifact_dir / "receiver-first-window.json", summarize_receiver_window(first_receiver_window))
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

        handoff_for_receiver = watch_log_summary.get("handoff") if isinstance(watch_log_summary.get("handoff"), dict) else None
        if not handoff_for_receiver:
            raise ValidationError("watch log did not retain the executed handoff needed for receiver duplicate validation")
        duplicate_plan = build_gateway_agent_call(handoff_for_receiver, timeout_ms=10_000)
        duplicate_gateway_params = dict(duplicate_plan["gatewayParams"] or {})
        duplicate_gateway_params["idempotencyKey"] = f"{duplicate_gateway_params['idempotencyKey']}-replay-{uuid.uuid4().hex[:8]}"
        duplicate_baseline_entries = read_jsonl_objects(receiver_session_file)
        duplicate_call = run_gateway_agent_call(
            "04b-receiver-duplicate-call",
            gateway_params=duplicate_gateway_params,
            artifact_dir=artifact_dir,
            cwd=repo_root,
        )
        duplicate_receiver_window, _duplicate_entries = wait_for_event_window(
            receiver_session_file,
            baseline_line_count=len(duplicate_baseline_entries),
            session_id=session_id,
            occurrence=1,
            timeout_sec=DEFAULT_RECEIVER_WAIT_SEC,
            poll_interval_sec=args.poll_interval_sec,
        )
        duplicate_reply_line = int(duplicate_receiver_window.get("terminalLine") or 0)
        duplicate_quiet_entries = wait_for_session_quiet(
            receiver_session_file,
            minimum_line_count=duplicate_reply_line,
            quiet_sec=DEFAULT_RECEIVER_QUIET_SEC,
            poll_interval_sec=args.poll_interval_sec,
        )
        duplicate_after_reply = [entry for entry in duplicate_quiet_entries if int(entry.get("_lineNumber") or 0) > duplicate_reply_line]
        checks.append(
            bool_check(
                "receiver_duplicate_no_visible_reply",
                str(duplicate_receiver_window.get("replyText") or "").strip() == "NO_REPLY"
                and duplicate_receiver_window.get("inspectEntryCount") == 1
                and not any(session_entry_role(entry) == "assistant" for entry in duplicate_after_reply),
                details={
                    **summarize_receiver_window(duplicate_receiver_window),
                    "duplicateGatewayCall": {
                        "returncode": duplicate_call.returncode,
                        "stdoutPath": str(duplicate_call.stdout_path),
                        "stderrPath": str(duplicate_call.stderr_path),
                        "metaPath": str(duplicate_call.meta_path),
                    },
                    "newAssistantEntriesAfterDuplicateReply": [
                        {
                            "lineNumber": entry.get("_lineNumber"),
                            "role": session_entry_role(entry),
                            "text": session_entry_text(entry),
                            "toolCalls": session_entry_tool_calls(entry),
                        }
                        for entry in duplicate_after_reply
                        if session_entry_role(entry) == "assistant"
                    ],
                },
            )
        )
        scenario["receiverSession"]["duplicateEvent"] = summarize_receiver_window(duplicate_receiver_window)
        save_json_file(artifact_dir / "receiver-duplicate-window.json", summarize_receiver_window(duplicate_receiver_window))
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
        raw_history_messages = normalize_raw_session_messages(
            fetch_raw_session_messages(
                config,
                session_id=session_id,
                workspace=config["workspace"],
                limit=int(config["historyMessageLimit"]),
            )
        )
        business_completion = evaluate_workspace_business_completion(run_id, raw_history_messages)
        latest_completed_turn = business_completion.get("latestCompletedAssistantTurn") or {}
        final_file_content = business_completion.get("finalFileContent") or {}
        checks.append(
            bool_check(
                "inspect_history",
                bool(latest_history_message.get("messageId")) and bool(latest_completed_turn.get("passed")),
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
                "latest_completed_assistant_turn",
                bool(latest_completed_turn.get("passed")),
                details=latest_completed_turn,
            )
        )
        checks.append(
            bool_check(
                "workspace_final_file_content",
                bool(final_file_content.get("passed")),
                details=final_file_content,
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
