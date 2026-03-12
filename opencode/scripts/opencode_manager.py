#!/usr/bin/env python3
import argparse
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import fcntl

from opencode_api_client import OpenCodeClient, build_opencode_session_ui_url
from opencode_snapshot import (
    analyze_running_progress,
    build_compact_snapshot,
    build_event_record,
    clean_preview,
    compact_latest_message,
    shorten_path,
    summarize_transport_errors,
)

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
REPO_ROOT = SKILL_ROOT.parent
WATCH_RUNTIME = SCRIPT_DIR / "opencode_watch_runtime.py"
DEFAULT_REGISTRY_PATH = REPO_ROOT / ".local" / "opencode-manager" / "registry.json"
DEFAULT_WATCHER_ROOT = REPO_ROOT / ".local" / "opencode-manager" / "watchers"
DEFAULT_WATCH_INTERVAL_SEC = 60
DEFAULT_IDLE_TIMEOUT_SEC = 900
DEFAULT_MESSAGE_LIMIT = 10
DEFAULT_HISTORY_MESSAGE_LIMIT = 25
DEFAULT_TIMEOUT_SEC = 20
DEFAULT_STOP_TIMEOUT_SEC = 10
DEFAULT_HISTORY_ANCHOR_COUNT = 6
DEFAULT_TARGETED_HISTORY_RECENT_INDEXES = (0, 1, 2)
DETAIL_TEXT_LIMIT = 1200
DETAIL_TEXT_PREVIEW_LIMIT = 240
DETAIL_OUTPUT_TAIL_LINES = 4
DETAIL_OUTPUT_TAIL_LINE_LIMIT = 180
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
WATCH_RUNTIME_START_KIND = "opencode_watch_runtime_start_v1"
WATCHER_HANDOFF_ACK = "已交给 OpenCode，后续进展会由 watcher 继续回到当前 OpenClaw 会话。"
NON_LIVE_WATCHER_ACK = "OpenCode 已收到请求，但当前 watcher 未处于 live 回传模式；后续不会自动回到这个 OpenClaw 会话。"
MISSING_WATCHER_ACK = "OpenCode 已收到请求，但当前没有 live watcher 把后续进展回传到这个 OpenClaw 会话。"
NO_WATCHER_ACK = "OpenCode 已收到请求；当前没有 watcher 负责把后续进展回传到这个 OpenClaw 会话。"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)



def now_iso() -> str:
    return now_utc().isoformat()



def iso_from_epoch_ms(value: Any) -> str | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(numeric / 1000.0, tz=timezone.utc).isoformat()



def preview_text(value: Any, limit: int = 160) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1] + "…"



def resolve_opencode_token(opencode_token: str | None, opencode_token_env: str | None) -> str | None:
    if opencode_token and opencode_token_env:
        raise ValueError("set either --opencode-token or --opencode-token-env, not both")
    if opencode_token_env:
        env_value = os.environ.get(opencode_token_env)
        if not env_value:
            raise ValueError(f"environment variable named by --opencode-token-env is empty or unset: {opencode_token_env}")
        return env_value
    return opencode_token



def resolve_prompt_input(
    prompt_text: str | None,
    prompt_file: str | None,
    *,
    text_flag: str,
    file_flag: str,
) -> dict[str, Any]:
    if prompt_text is not None and prompt_file is not None:
        raise ValueError(f"set either {text_flag} or {file_flag}, not both")
    if prompt_text is not None:
        return {
            "text": prompt_text,
            "inputMethod": "text",
        }
    if prompt_file is None:
        raise ValueError(f"missing prompt input: set one of {text_flag} or {file_flag}")
    if prompt_file == "-":
        return {
            "text": sys.stdin.read(),
            "inputMethod": "stdin",
        }

    prompt_path = Path(prompt_file).expanduser().resolve()
    return {
        "text": prompt_path.read_text(encoding="utf-8"),
        "inputMethod": "file",
        "promptFile": str(prompt_path),
    }



def watcher_root_for_registry_path(registry_path: Path) -> Path:
    if registry_path.resolve() == DEFAULT_REGISTRY_PATH.resolve():
        return DEFAULT_WATCHER_ROOT
    return registry_path.parent / "watchers"


@contextmanager
def locked_registry(registry_path: Path) -> Iterator[tuple[dict[str, Any], Path]]:
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = registry_path.with_suffix(registry_path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        registry = load_registry(registry_path)
        try:
            yield registry, registry_path
        finally:
            save_registry(registry_path, registry)
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)



def load_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"JSON file must contain an object: {path}")
    return data



def save_json_object(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")



def load_registry(registry_path: Path) -> dict[str, Any]:
    registry = load_json_object(registry_path)
    registry.setdefault("kind", "opencode_manager_registry_v1")
    registry.setdefault("watchers", [])
    if not isinstance(registry.get("watchers"), list):
        raise ValueError(f"registry watchers must be a list: {registry_path}")
    return registry



def save_registry(registry_path: Path, registry: dict[str, Any]) -> None:
    registry["kind"] = "opencode_manager_registry_v1"
    registry["updatedAt"] = now_iso()
    save_json_object(registry_path, registry)



def process_is_alive(process_id: Any) -> bool:
    if not isinstance(process_id, int) or process_id <= 0:
        return False
    try:
        os.kill(process_id, 0)
        return True
    except OSError:
        return False



def parse_watch_runtime_process(command: str) -> dict[str, Any] | None:
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    if not parts:
        return None

    runtime_index = None
    for index, part in enumerate(parts):
        if part == str(WATCH_RUNTIME) or part.endswith(f"/{WATCH_RUNTIME.name}") or part == WATCH_RUNTIME.name:
            runtime_index = index
            break
    if runtime_index is None:
        return None

    config_path = None
    for index in range(runtime_index + 1, len(parts) - 1):
        if parts[index] == "--config":
            config_path = Path(parts[index + 1]).expanduser().resolve()
            break
    if config_path is None:
        return None

    return {
        "command": command,
        "configPath": str(config_path),
        "dryRun": "--dry-run" in parts,
        "once": "--once" in parts,
    }



def list_watch_runtime_processes() -> dict[str, dict[str, Any]]:
    proc = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return {}

    result: dict[str, dict[str, Any]] = {}
    for raw_line in proc.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        pid_text, _, command = line.partition(" ")
        if not command:
            continue
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        parsed = parse_watch_runtime_process(command)
        if not parsed:
            continue
        parsed["pid"] = pid
        result[parsed["configPath"]] = parsed
    return result



def watcher_paths_for_id(watcher_id: str, *, watcher_root: Path | None = None) -> dict[str, Path]:
    watcher_dir = (watcher_root or DEFAULT_WATCHER_ROOT) / watcher_id
    return {
        "watcherDir": watcher_dir,
        "watcherConfigPath": watcher_dir / "config.json",
        "watcherStatePath": watcher_dir / "state.json",
        "watcherLogPath": watcher_dir / "watch.log",
    }



def build_manager_watcher_config(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "opencodeBaseUrl": entry["opencodeBaseUrl"],
        "opencodeSessionId": entry["opencodeSessionId"],
        "opencodeWorkspace": entry["opencodeWorkspace"],
        "openclawSessionKey": entry["openclawSessionKey"],
        "openclawDeliveryTarget": entry.get("openclawDeliveryTarget"),
        "opencodeToken": entry.get("opencodeToken"),
        "opencodeTokenEnv": entry.get("opencodeTokenEnv"),
        "watchStatePath": entry["watcherStatePath"],
        "watchLogPath": entry["watcherLogPath"],
        "watchTimeoutSec": entry["watchTimeoutSec"],
        "watchMessageLimit": entry["watchMessageLimit"],
        "watchIntervalSec": entry["watchIntervalSec"],
        "watchLive": entry["watchLive"],
        "idleTimeoutSec": entry["idleTimeoutSec"],
        "notifyMinIntervalSec": entry.get("notifyMinIntervalSec", 0),
        "notifyMinPriority": entry.get("notifyMinPriority", "low"),
        "notifyKeywords": entry.get("notifyKeywords") or [],
        "notifyFilterCritical": bool(entry.get("notifyFilterCritical", False)),
    }



def read_watch_state(watcher_state_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    if not watcher_state_path.exists():
        return {}, {}
    watch_state_document = load_json_object(watcher_state_path)
    watch_state = watch_state_document.get("watchRunner") if isinstance(watch_state_document.get("watchRunner"), dict) else {}
    return watch_state_document, watch_state



def read_watch_log_banner(watcher_log_path: Path) -> dict[str, Any]:
    if not watcher_log_path.exists():
        return {}
    try:
        lines = watcher_log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    for line in reversed(lines[-200:]):
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("kind") == WATCH_RUNTIME_START_KIND:
            return payload
    return {}



def normalize_entry_paths(entry: dict[str, Any], *, registry_path: Path | None = None) -> dict[str, Any]:
    normalized = dict(entry)
    watcher_id = normalized.get("watcherId")
    if not isinstance(watcher_id, str) or not watcher_id:
        return normalized
    watcher_root = watcher_root_for_registry_path(registry_path) if registry_path else None
    paths = watcher_paths_for_id(watcher_id, watcher_root=watcher_root)
    normalized.setdefault("watcherConfigPath", str(paths["watcherConfigPath"]))
    normalized.setdefault("watcherStatePath", str(paths["watcherStatePath"]))
    normalized.setdefault("watcherLogPath", str(paths["watcherLogPath"]))
    return normalized



def build_recovered_entry_from_watcher_dir(
    watcher_dir: Path,
    *,
    runtime_processes: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    watcher_id = watcher_dir.name
    paths = watcher_paths_for_id(watcher_id, watcher_root=watcher_dir.parent)
    config_path = paths["watcherConfigPath"]
    if not config_path.exists():
        return None

    config = load_json_object(config_path)
    log_banner = read_watch_log_banner(paths["watcherLogPath"])
    runtime_info = (runtime_processes or {}).get(str(config_path.resolve()))

    entry = {
        "watcherId": watcher_id,
        "watcherStatus": "running" if runtime_info else "exited",
        "opencodeBaseUrl": config.get("opencodeBaseUrl") or config.get("base_url"),
        "opencodeSessionId": config.get("opencodeSessionId") or config.get("session_id"),
        "opencodeWorkspace": config.get("opencodeWorkspace") or config.get("workspace") or config.get("directory"),
        "openclawSessionKey": config.get("openclawSessionKey") or config.get("origin_session"),
        "openclawDeliveryTarget": config.get("openclawDeliveryTarget") or config.get("origin_target"),
        "opencodeToken": config.get("opencodeToken") or config.get("token"),
        "opencodeTokenEnv": config.get("opencodeTokenEnv") or config.get("token_env"),
        "watchLive": config.get("watchLive") if isinstance(config.get("watchLive"), bool) else config.get("live"),
        "watchIntervalSec": config.get("watchIntervalSec") or config.get("interval_sec"),
        "idleTimeoutSec": config.get("idleTimeoutSec") or config.get("idle_timeout_sec"),
        "notifyMinIntervalSec": config.get("notifyMinIntervalSec") or config.get("notify_min_interval_sec") or 0,
        "notifyMinPriority": config.get("notifyMinPriority") or config.get("notify_min_priority") or config.get("notifyMinSeverity") or config.get("notify_min_severity") or "low",
        "notifyKeywords": config.get("notifyKeywords") or config.get("notify_keywords") or [],
        "notifyFilterCritical": bool(config.get("notifyFilterCritical") or config.get("notify_filter_critical") or False),
        "watchMessageLimit": config.get("watchMessageLimit") or config.get("message_limit"),
        "watchTimeoutSec": config.get("watchTimeoutSec") or config.get("timeout"),
        "watchCreatedAt": log_banner.get("startedAt") or iso_from_epoch_ms(config_path.stat().st_mtime * 1000),
        "watchStartedAt": log_banner.get("startedAt"),
        "watchProcessId": runtime_info.get("pid") if runtime_info else None,
        "watchProcessAlive": bool(runtime_info),
        "watcherConfigPath": str(config_path),
        "watcherStatePath": str(paths["watcherStatePath"]),
        "watcherLogPath": str(paths["watcherLogPath"]),
    }
    return {key: value for key, value in entry.items() if value is not None}



def refresh_registry_entry(
    entry: dict[str, Any],
    *,
    registry_path: Path | None = None,
    runtime_processes: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    refreshed = normalize_entry_paths(entry, registry_path=registry_path)
    config_path = Path(refreshed["watcherConfigPath"]).expanduser().resolve()
    state_path = Path(refreshed["watcherStatePath"]).expanduser().resolve()
    log_path = Path(refreshed["watcherLogPath"]).expanduser().resolve()

    if config_path.exists():
        recovered_from_disk = build_recovered_entry_from_watcher_dir(config_path.parent, runtime_processes=runtime_processes)
        if recovered_from_disk:
            disk_merged = dict(recovered_from_disk)
            disk_merged.update(refreshed)
            refreshed = disk_merged

    _watch_state_document, watch_state = read_watch_state(state_path)
    log_banner = read_watch_log_banner(log_path)
    runtime_info = (runtime_processes or {}).get(str(config_path)) if runtime_processes is not None else list_watch_runtime_processes().get(str(config_path))
    stored_pid = refreshed.get("watchProcessId")
    stored_pid_alive = process_is_alive(stored_pid)

    if runtime_info:
        refreshed["watchProcessId"] = runtime_info.get("pid")
        refreshed["watchProcessAlive"] = True
        refreshed["watcherStatus"] = "running"
        refreshed.pop("watchExitedAt", None)
        if not refreshed.get("watchStartedAt"):
            refreshed["watchStartedAt"] = log_banner.get("startedAt") or now_iso()
    else:
        refreshed["watchProcessAlive"] = False
        if refreshed.get("watcherStatus") in {"running", "starting"}:
            refreshed["watcherStatus"] = "exited"
            refreshed.setdefault("watchExitedAt", watch_state.get("lastExitedAt") or now_iso())
        if stored_pid_alive:
            refreshed["watchExitReason"] = watch_state.get("lastExitReason") or refreshed.get("watchExitReason") or "stale_process_reference"
        else:
            refreshed["watchExitReason"] = watch_state.get("lastExitReason") or refreshed.get("watchExitReason") or "process_not_running"
        if watch_state.get("lastExitedAt"):
            refreshed["watchExitedAt"] = watch_state.get("lastExitedAt")

    if log_banner.get("startedAt") and not refreshed.get("watchStartedAt"):
        refreshed["watchStartedAt"] = log_banner.get("startedAt")

    if watch_state:
        refreshed["lastWatchRunAt"] = watch_state.get("lastRunAt")
        refreshed["lastWatchOperation"] = watch_state.get("lastOperation")
        refreshed["lastRouteStatus"] = watch_state.get("lastRouteStatus")
        refreshed["lastDeliveryAction"] = watch_state.get("lastDeliveryAction")
        refreshed["lastOpencodeStatus"] = watch_state.get("lastFactStatus")
        refreshed["lastOpencodePhase"] = watch_state.get("lastFactPhase")
        refreshed["lastPreview"] = watch_state.get("lastPreview")
        refreshed["lastActivityAt"] = watch_state.get("lastActivityAt")
        refreshed["idleEligibleSince"] = watch_state.get("idleEligibleSince")
        refreshed["lastRunningProgressObservation"] = watch_state.get("lastRunningProgressObservation")
        refreshed["lastTransportErrorHints"] = watch_state.get("lastTransportErrorHints")
        if watch_state.get("lastExitReason"):
            refreshed["watchExitReason"] = watch_state.get("lastExitReason")
        if watch_state.get("lastExitedAt"):
            refreshed["watchExitedAt"] = watch_state.get("lastExitedAt")

    return refreshed



def refresh_registry_entries(registry: dict[str, Any], *, registry_path: Path) -> dict[str, Any]:
    runtime_processes = list_watch_runtime_processes()
    watcher_root = watcher_root_for_registry_path(registry_path)

    merged_entries: list[dict[str, Any]] = []
    by_watcher_id: dict[str, dict[str, Any]] = {}
    watcher_order: list[str] = []

    for original in registry.get("watchers") or []:
        normalized = normalize_entry_paths(original, registry_path=registry_path)
        watcher_id = normalized.get("watcherId")
        if isinstance(watcher_id, str) and watcher_id:
            if watcher_id not in by_watcher_id:
                watcher_order.append(watcher_id)
                by_watcher_id[watcher_id] = normalized
            else:
                combined = dict(by_watcher_id[watcher_id])
                combined.update(normalized)
                by_watcher_id[watcher_id] = combined
        else:
            merged_entries.append(normalized)

    if watcher_root.exists():
        for watcher_dir in sorted(path for path in watcher_root.iterdir() if path.is_dir()):
            recovered = build_recovered_entry_from_watcher_dir(watcher_dir, runtime_processes=runtime_processes)
            if not recovered:
                continue
            watcher_id = recovered["watcherId"]
            if watcher_id not in by_watcher_id:
                watcher_order.append(watcher_id)
                by_watcher_id[watcher_id] = recovered
            else:
                combined = dict(recovered)
                combined.update(by_watcher_id[watcher_id])
                by_watcher_id[watcher_id] = combined

    for watcher_id in watcher_order:
        merged_entries.append(refresh_registry_entry(by_watcher_id[watcher_id], registry_path=registry_path, runtime_processes=runtime_processes))

    registry["watchers"] = merged_entries
    return registry



def find_active_watcher_entry(registry: dict[str, Any], opencode_session_id: str) -> dict[str, Any] | None:
    for entry in registry.get("watchers") or []:
        if entry.get("opencodeSessionId") == opencode_session_id and entry.get("watcherStatus") == "running":
            return entry
    return None



def find_latest_watcher_entry(registry: dict[str, Any], opencode_session_id: str) -> dict[str, Any] | None:
    latest: dict[str, Any] | None = None
    latest_key = ""
    for entry in registry.get("watchers") or []:
        if entry.get("opencodeSessionId") != opencode_session_id:
            continue
        sort_key = str(entry.get("watchCreatedAt") or entry.get("watchStartedAt") or entry.get("watcherId") or "")
        if latest is None or sort_key >= latest_key:
            latest = entry
            latest_key = sort_key
    return latest



def build_session_summary(
    session_data: dict[str, Any],
    *,
    opencode_base_url: str | None = None,
    watcher_entry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = session_data.get("summary") if isinstance(session_data.get("summary"), dict) else {}
    time_data = session_data.get("time") if isinstance(session_data.get("time"), dict) else {}
    opencode_session_id = session_data.get("id") or session_data.get("sessionID") or session_data.get("sessionId")
    opencode_workspace = session_data.get("directory")
    opencode_ui_url = None
    if opencode_base_url and isinstance(opencode_workspace, str) and opencode_workspace and isinstance(opencode_session_id, str) and opencode_session_id:
        opencode_ui_url = build_opencode_session_ui_url(opencode_base_url, opencode_workspace, opencode_session_id)
    result = {
        "opencodeSessionId": opencode_session_id,
        "slug": session_data.get("slug"),
        "title": session_data.get("title"),
        "opencodeWorkspace": opencode_workspace,
        "opencodeUiUrl": opencode_ui_url,
        "version": session_data.get("version"),
        "createdAt": iso_from_epoch_ms(time_data.get("created")),
        "updatedAt": iso_from_epoch_ms(time_data.get("updated")),
        "changeSummary": summary or None,
    }
    if watcher_entry:
        result["activeWatcherId"] = watcher_entry.get("watcherId")
        result["activeOpenclawSessionKey"] = watcher_entry.get("openclawSessionKey")
        result["activeWatcherStatus"] = watcher_entry.get("watcherStatus")
    return {key: value for key, value in result.items() if value is not None}



def value_non_empty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, tuple, dict, str, bytes)):
        return len(value) > 0
    return True



def derive_inspection_status(snapshot: dict[str, Any], latest_message: dict[str, Any], todo: dict[str, Any]) -> str:
    if value_non_empty(snapshot.get("permission")) or value_non_empty(snapshot.get("question")):
        return "blocked"

    raw_status = str(latest_message.get("status") or snapshot.get("status") or "").strip().lower()
    if latest_message.get("message.aborted") or latest_message.get("message.errorName"):
        return "failed"
    if raw_status in {"failed", "error", "cancelled", "canceled"}:
        return "failed"
    if todo.get("hasPendingWork"):
        return "running"
    if str(latest_message.get("role") or "").strip().lower() == "user":
        return "running"
    if raw_status:
        return raw_status
    if todo.get("allCompleted"):
        return "completed"
    return "unknown"



def classify_stop_verification(
    *,
    busy_entry: Any,
    snapshot: dict[str, Any] | None,
    abort_requested_at_ms: int | None,
) -> dict[str, Any]:
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    latest_message = snapshot.get("latestMessage") if isinstance(snapshot.get("latestMessage"), dict) else {}
    todo = snapshot.get("todo") if isinstance(snapshot.get("todo"), dict) else {}
    current_status = derive_inspection_status(snapshot, latest_message, todo)
    latest_completed_at = latest_message.get("completedAt")
    completed_after_abort = False
    if abort_requested_at_ms is not None:
        try:
            completed_after_abort = isinstance(latest_completed_at, (int, float)) and int(latest_completed_at) >= int(abort_requested_at_ms)
        except (TypeError, ValueError):
            completed_after_abort = False

    if busy_entry is not None:
        outcome = "still_busy_after_abort"
        verified = False
        likely_failed = False
    elif latest_message.get("message.aborted") or current_status == "failed":
        outcome = "aborted_terminal"
        verified = True
        likely_failed = False
    elif current_status == "completed":
        outcome = "completed_after_abort_request" if completed_after_abort else "completed_without_abort_marker"
        verified = False
        likely_failed = True
    elif current_status == "running":
        outcome = "busy_cleared_but_message_still_running"
        verified = False
        likely_failed = True
    else:
        outcome = "abort_acknowledged_but_unverified"
        verified = False
        likely_failed = False

    return {
        "outcome": outcome,
        "verified": verified,
        "stopLikelyFailed": likely_failed,
        "busyEntryPresent": busy_entry is not None,
        "inspectionStatus": current_status,
        "latestMessage": {
            key: value
            for key, value in {
                "id": latest_message.get("id"),
                "role": latest_message.get("role"),
                "status": latest_message.get("status"),
                "completedAt": iso_from_epoch_ms(latest_completed_at),
                "finish": latest_message.get("finish"),
                "errorName": latest_message.get("message.errorName"),
                "aborted": latest_message.get("message.aborted"),
                "textPreview": latest_message.get("textPreview"),
                "toolOutputPreview": latest_message.get("toolOutputPreview"),
            }.items()
            if value is not None
        },
    }



def verify_stop_session_attempt(
    client: OpenCodeClient,
    *,
    session_id: str,
    directory: str,
    abort_requested_at_ms: int,
    verify_wait_sec: float,
    verify_poll_sec: float,
    message_limit: int = DEFAULT_MESSAGE_LIMIT,
) -> dict[str, Any]:
    started_at = time.time()
    deadline = started_at + max(0.0, float(verify_wait_sec))
    poll_sec = max(0.05, float(verify_poll_sec))
    observations: list[dict[str, Any]] = []
    snapshot_errors: dict[str, Any] | None = None

    while True:
        busy_map = client.session_status(directory=directory)
        busy_entry = busy_map.get(session_id) if isinstance(busy_map, dict) else None
        snapshot, snapshot_errors = build_compact_snapshot(client, session_id, message_limit=message_limit)
        classified = classify_stop_verification(
            busy_entry=busy_entry,
            snapshot=snapshot,
            abort_requested_at_ms=abort_requested_at_ms,
        )
        observations.append(
            {
                "checkedAt": now_iso(),
                "busyEntryPresent": classified["busyEntryPresent"],
                "inspectionStatus": classified["inspectionStatus"],
                "outcome": classified["outcome"],
                "verified": classified["verified"],
                "stopLikelyFailed": classified["stopLikelyFailed"],
                "latestMessage": classified["latestMessage"],
            }
        )
        if classified["verified"] or classified["stopLikelyFailed"] or time.time() >= deadline:
            return {
                "mode": "post_abort_verification",
                "waitSec": max(0.0, float(verify_wait_sec)),
                "pollSec": poll_sec,
                "observationCount": len(observations),
                "outcome": classified["outcome"],
                "verified": classified["verified"],
                "stopLikelyFailed": classified["stopLikelyFailed"],
                "busyEntryPresent": classified["busyEntryPresent"],
                "inspectionStatus": classified["inspectionStatus"],
                "latestMessage": classified["latestMessage"],
                "snapshotErrors": snapshot_errors or None,
                "observations": observations,
            }
        remaining = deadline - time.time()
        if remaining <= 0:
            continue
        time.sleep(min(poll_sec, remaining))



def build_recent_event_label(event: dict[str, Any]) -> str:
    kind = event.get("kind")
    if kind == "tool" and event.get("toolName"):
        return f"tool[{event['toolName']}]"
    return {
        "user_input": "user",
        "text": "text",
        "read": "read",
        "prune": "prune",
        "tool": "tool",
    }.get(kind, kind or "event")



def visible_snapshot_events(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    events = snapshot.get("eventLedger") if isinstance(snapshot.get("eventLedger"), list) else []
    return [event for event in events if isinstance(event, dict) and not event.get("ignored")]



def build_notable_event_items(events: list[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]] | None:
    notable = []
    for event in events[-limit:]:
        summary = preview_text(event.get("summary"), limit=120)
        if not summary:
            continue
        item = {
            "kind": event.get("kind"),
            "label": build_recent_event_label(event),
            "summary": summary,
            "messageId": event.get("messageId"),
            "createdAt": iso_from_epoch_ms(event.get("created")),
        }
        notable.append({key: value for key, value in item.items() if value is not None})
    return notable or None



def build_recent_notable_events(snapshot: dict[str, Any], *, limit: int = 5) -> list[dict[str, Any]] | None:
    visible = visible_snapshot_events(snapshot)
    if not visible:
        return None
    return build_notable_event_items(visible, limit=limit)



def build_completed_event_items(events: list[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]] | None:
    completed: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def append_item(source: str, summary: Any, **extra: Any) -> None:
        normalized = preview_text(summary, limit=140)
        if not normalized:
            return
        signature = (source, normalized)
        if signature in seen:
            return
        seen.add(signature)
        item = {"source": source, "summary": normalized, **extra}
        completed.append({key: value for key, value in item.items() if value is not None})

    completed_tool_statuses = {"completed", "done", "finished", "succeeded", "success", "ok"}
    for event in events:
        kind = event.get("kind")
        if kind in {"user_input", "read", "prune"}:
            continue
        if kind == "tool":
            tool_status = str(event.get("toolStatus") or "").strip().lower()
            if tool_status and tool_status not in completed_tool_statuses:
                continue
            summary_text = str(event.get("summary") or "").strip().lower()
            if summary_text in completed_tool_statuses:
                continue
        append_item(
            "event",
            event.get("summary"),
            kind=kind,
            label=build_recent_event_label(event),
            messageId=event.get("messageId"),
            createdAt=iso_from_epoch_ms(event.get("created")),
        )

    return completed[-limit:] or None



def build_recent_completed_work(snapshot: dict[str, Any], todo: dict[str, Any], *, limit: int = 5) -> list[dict[str, Any]] | None:
    completed: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def append_item(source: str, summary: Any, **extra: Any) -> None:
        normalized = preview_text(summary, limit=140)
        if not normalized:
            return
        signature = (source, normalized)
        if signature in seen:
            return
        seen.add(signature)
        item = {"source": source, "summary": normalized, **extra}
        completed.append({key: value for key, value in item.items() if value is not None})

    for item in (todo.get("items") or [])[-limit:]:
        if isinstance(item, dict) and str(item.get("status") or "").strip().lower() == "completed":
            append_item("todo", item.get("content"))

    for item in build_completed_event_items(visible_snapshot_events(snapshot), limit=limit) or []:
        append_item(item.get("source") or "event", item.get("summary"), **{
            key: value
            for key, value in item.items()
            if key not in {"source", "summary"}
        })

    return completed[-limit:] or None



def build_since_latest_user_input(snapshot: dict[str, Any], *, limit: int = 4) -> dict[str, Any] | None:
    visible = visible_snapshot_events(snapshot)
    if not visible:
        return None

    latest_user_message_id = snapshot.get("latestUserInputMessageId")
    anchor_index = None
    fallback_index = None
    for index in range(len(visible) - 1, -1, -1):
        event = visible[index]
        if event.get("kind") != "user_input":
            continue
        if fallback_index is None:
            fallback_index = index
        if latest_user_message_id and event.get("messageId") == latest_user_message_id:
            anchor_index = index
            break
    if anchor_index is None:
        anchor_index = fallback_index
    if anchor_index is None:
        return None

    anchor = visible[anchor_index]
    delta_events = visible[anchor_index + 1 :]
    unique_message_ids: list[str] = []
    unique_assistant_message_ids: list[str] = []
    seen_message_ids: set[str] = set()
    seen_assistant_message_ids: set[str] = set()
    latest_assistant_text = None
    latest_assistant_message_id = None

    for event in delta_events:
        message_id = event.get("messageId")
        if isinstance(message_id, str) and message_id and message_id not in seen_message_ids:
            seen_message_ids.add(message_id)
            unique_message_ids.append(message_id)
        role = str(event.get("role") or ("user" if event.get("kind") == "user_input" else "assistant")).strip().lower()
        if role == "assistant" and isinstance(message_id, str) and message_id and message_id not in seen_assistant_message_ids:
            seen_assistant_message_ids.add(message_id)
            unique_assistant_message_ids.append(message_id)
        if role == "assistant" and event.get("kind") == "text":
            latest_assistant_text = preview_text(event.get("summary"), limit=140)
            latest_assistant_message_id = message_id

    result = {
        "anchor": {
            key: value
            for key, value in {
                "messageId": anchor.get("messageId") or latest_user_message_id,
                "summary": preview_text(anchor.get("summary") or snapshot.get("latestUserInputSummary"), limit=140),
                "createdAt": iso_from_epoch_ms(anchor.get("created")),
            }.items()
            if value is not None
        }
        or None,
        "eventCount": len(delta_events),
        "messageCount": len(unique_message_ids),
        "assistantMessageCount": len(unique_assistant_message_ids),
        "latestAssistantText": latest_assistant_text,
        "latestAssistantMessageId": latest_assistant_message_id,
        "completedWork": build_completed_event_items(delta_events, limit=limit),
        "notableEvents": build_notable_event_items(delta_events, limit=limit),
    }
    return {key: value for key, value in result.items() if value is not None}



def build_snapshot_coverage(snapshot: dict[str, Any], *, requested_message_limit: int | None) -> dict[str, Any]:
    message_window = snapshot.get("messageWindow") if isinstance(snapshot.get("messageWindow"), dict) else {}
    observed_count = message_window.get("observedMessageCount")
    if not isinstance(observed_count, int):
        observed_count = snapshot.get("messageWindowSize") if isinstance(snapshot.get("messageWindowSize"), int) else 0
    newest_message = snapshot.get("latestMessage") if isinstance(snapshot.get("latestMessage"), dict) else {}

    coverage = {
        "coverageMode": "recent_window_current_state_rebuild",
        "requestedMessageLimit": requested_message_limit,
        "observedMessageCount": observed_count,
        "eventCount": len(snapshot.get("eventLedger") or []),
        "mayExcludeOlderHistory": bool(requested_message_limit and observed_count >= requested_message_limit),
        "oldestObservedMessage": {
            key: value
            for key, value in {
                "messageId": message_window.get("oldestMessageId"),
                "role": message_window.get("oldestMessageRole"),
                "createdAt": iso_from_epoch_ms(message_window.get("oldestMessageCreated")),
            }.items()
            if value is not None
        }
        or None,
        "newestObservedMessage": {
            key: value
            for key, value in {
                "messageId": message_window.get("newestMessageId") or newest_message.get("id"),
                "role": message_window.get("newestMessageRole") or newest_message.get("role"),
                "createdAt": iso_from_epoch_ms(message_window.get("newestMessageCreated") or newest_message.get("created")),
            }.items()
            if value is not None
        }
        or None,
        "latestUserInputMessageId": snapshot.get("latestUserInputMessageId"),
    }
    return {key: value for key, value in coverage.items() if value is not None}



def build_rehydration_watcher_state(watcher_entry: dict[str, Any] | None) -> dict[str, Any] | None:
    if not watcher_entry:
        return None
    return {
        key: value
        for key, value in {
            "watcherId": watcher_entry.get("watcherId"),
            "watcherStatus": watcher_entry.get("watcherStatus"),
            "watchLive": watcher_entry.get("watchLive"),
            "watchProcessAlive": watcher_entry.get("watchProcessAlive"),
            "lastWatchRunAt": watcher_entry.get("lastWatchRunAt"),
            "lastWatchOperation": watcher_entry.get("lastWatchOperation"),
            "lastOpencodeStatus": watcher_entry.get("lastOpencodeStatus"),
            "lastOpencodePhase": watcher_entry.get("lastOpencodePhase"),
            "lastPreview": watcher_entry.get("lastPreview"),
            "lastRunningProgressObservation": watcher_entry.get("lastRunningProgressObservation"),
            "lastTransportErrorHints": watcher_entry.get("lastTransportErrorHints"),
            "watchExitReason": watcher_entry.get("watchExitReason"),
        }.items()
        if value is not None
    }



def build_rehydration_follow_up_hints(
    snapshot_coverage: dict[str, Any],
    *,
    running_progress_observation: dict[str, Any] | None = None,
    transport_error_hints: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    use_inspect_history_when = [
        "Need exact assistant/tool message behind latestMeaningfulPreview.",
        "Need recent shell/tool output or write/patch details.",
        "Need what happened between inspect points.",
    ]
    if snapshot_coverage.get("mayExcludeOlderHistory"):
        use_inspect_history_when.insert(1, "Need older context outside the retained inspect window.")
    if running_progress_observation:
        use_inspect_history_when.append(
            "Inspect still shows running without enough visible progress; inspect-history can confirm the latest assistant/tool step."
        )
    if transport_error_hints:
        use_inspect_history_when.append(
            "Transport/API errors may have hidden events; inspect-history can verify the latest durable message/output."
        )
    return {
        "preferTargetedLookup": True,
        "suggestedRecentIndexes": list(DEFAULT_TARGETED_HISTORY_RECENT_INDEXES),
        "useInspectHistoryWhen": use_inspect_history_when,
    }



def build_inspect_session_summary(
    session_data: dict[str, Any],
    *,
    opencode_base_url: str | None = None,
    watcher_entry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    session_summary = build_session_summary(
        session_data,
        opencode_base_url=opencode_base_url,
        watcher_entry=watcher_entry,
    )
    compact = {
        key: session_summary.get(key)
        for key in (
            "opencodeSessionId",
            "title",
            "opencodeWorkspace",
            "opencodeUiUrl",
            "activeWatcherId",
            "activeWatcherStatus",
        )
        if session_summary.get(key) is not None
    }
    return compact



def build_inspect_latest_message(latest_message: dict[str, Any]) -> dict[str, Any] | None:
    if not latest_message:
        return None
    return {
        key: value
        for key, value in {
            "id": latest_message.get("id"),
            "role": latest_message.get("role"),
            "status": latest_message.get("status"),
            "createdAt": iso_from_epoch_ms(latest_message.get("created")),
            "completedAt": iso_from_epoch_ms(latest_message.get("completedAt")),
            "finish": latest_message.get("finish"),
            "textPreview": latest_message.get("message.lastTextPreview") or latest_message.get("textPreview"),
            "toolOutputPreview": latest_message.get("toolOutputPreview"),
            "errorPreview": latest_message.get("errorPreview"),
            "message.aborted": latest_message.get("message.aborted"),
            "message.errorName": latest_message.get("message.errorName"),
        }.items()
        if value is not None
    }



def build_inspect_watcher_summary(entry: dict[str, Any]) -> dict[str, Any]:
    return build_rehydration_watcher_state(entry) or {}



def normalize_message_collection(messages: Any) -> list[dict[str, Any]]:
    if isinstance(messages, list):
        items = messages
    elif isinstance(messages, dict):
        for key in ("items", "messages", "data"):
            candidate = messages.get(key)
            if isinstance(candidate, list):
                items = candidate
                break
        else:
            items = [messages]
    elif messages is None:
        items = []
    else:
        items = [messages]
    return [item for item in items if isinstance(item, dict)]



def strip_ansi_text(value: Any) -> str:
    return ANSI_RE.sub("", str(value or ""))



def compact_multiline_text(
    value: Any,
    *,
    preview_limit: int = DETAIL_TEXT_PREVIEW_LIMIT,
    text_limit: int = DETAIL_TEXT_LIMIT,
) -> dict[str, Any] | None:
    if value is None:
        return None
    text = str(value).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return None
    compacted = text if len(text) <= text_limit else text[: text_limit - 1] + "…"
    return {
        "preview": preview_text(text, limit=preview_limit),
        "text": compacted,
        "truncated": len(compacted) < len(text),
        "charCount": len(text),
        "lineCount": text.count("\n") + 1,
    }



def extract_candidate_dicts(part: dict[str, Any], tool_state: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = [
        part,
        part.get("input"),
        part.get("arguments"),
        part.get("args"),
        tool_state,
        tool_state.get("input"),
        tool_state.get("metadata"),
    ]
    metadata_input = tool_state.get("metadata", {}).get("input") if isinstance(tool_state.get("metadata"), dict) else None
    if isinstance(metadata_input, dict):
        candidates.append(metadata_input)
    return [candidate for candidate in candidates if isinstance(candidate, dict)]



def extract_first_scalar(candidate_dicts: list[dict[str, Any]], keys: tuple[str, ...]) -> Any:
    for candidate in candidate_dicts:
        for key in keys:
            value = candidate.get(key)
            if value is not None and value != "":
                return value
    return None



def collect_path_targets(candidate_dicts: list[dict[str, Any]]) -> list[str] | None:
    path_keys = (
        "path",
        "paths",
        "file",
        "files",
        "file_path",
        "filePath",
        "filePaths",
        "target",
        "targets",
        "destination",
        "dest",
        "outPath",
        "outputPath",
    )
    seen: set[str] = set()
    targets: list[str] = []

    def add_value(value: Any) -> None:
        if isinstance(value, dict):
            for key in path_keys:
                if value.get(key) is not None:
                    add_value(value.get(key))
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                add_value(item)
            return
        if not isinstance(value, str):
            return
        target = shorten_path(value, limit=140)
        if not target or target == "/dev/null" or target in seen:
            return
        seen.add(target)
        targets.append(target)

    for candidate in candidate_dicts:
        for key in path_keys:
            if candidate.get(key) is not None:
                add_value(candidate.get(key))
    return targets or None



def infer_targets_from_text(value: Any) -> list[str] | None:
    if value is None:
        return None
    text = strip_ansi_text(value).replace("\r\n", "\n").replace("\r", "\n")
    matches: list[str] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        candidate: str | None = None
        for prefix in ("Index: ", "--- ", "+++ "):
            if line.startswith(prefix):
                candidate = line[len(prefix):].strip()
                break
        if candidate is None:
            match = re.match(r"^[AMDRCU?]\s+(.+)$", line)
            if match:
                candidate = match.group(1).strip()
        if not candidate or candidate == "/dev/null":
            continue
        shortened = shorten_path(candidate, limit=140)
        if not shortened or shortened in seen:
            continue
        seen.add(shortened)
        matches.append(shortened)
    return matches or None



def infer_tool_action(tool_name: Any) -> str:
    normalized = str(tool_name or "").strip().lower()
    if normalized in {"read", "view", "cat"}:
        return "read"
    if normalized in {"write", "create", "overwrite"}:
        return "write"
    if normalized in {"edit", "apply_patch", "patch"} or "patch" in normalized:
        return "patch"
    if normalized in {"bash", "shell", "exec", "terminal", "sh", "zsh"}:
        return "shell"
    return "tool"



def build_output_detail(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    raw_text = strip_ansi_text(value).replace("\r\n", "\n").replace("\r", "\n")
    preview = clean_preview(raw_text, limit=DETAIL_TEXT_PREVIEW_LIMIT) or preview_text(raw_text, limit=DETAIL_TEXT_PREVIEW_LIMIT)
    if not preview:
        return None
    lines = [" ".join(line.split()) for line in raw_text.splitlines() if line.strip()]
    tail_lines = [preview_text(line, limit=DETAIL_OUTPUT_TAIL_LINE_LIMIT) for line in lines[-DETAIL_OUTPUT_TAIL_LINES:]]
    tail_lines = [line for line in tail_lines if line]
    detail = {
        "preview": preview,
        "tailLines": tail_lines or None,
        "charCount": len(raw_text),
        "lineCount": raw_text.count("\n") + 1 if raw_text else 0,
    }
    return {key: value for key, value in detail.items() if value is not None}



def build_tool_write_detail(tool_name: str, candidate_dicts: list[dict[str, Any]]) -> dict[str, Any] | None:
    normalized = tool_name.lower()
    result: dict[str, Any] = {}
    if normalized == "write":
        content = extract_first_scalar(candidate_dicts, ("content", "text", "buffer"))
        content_detail = compact_multiline_text(content, preview_limit=220, text_limit=DETAIL_TEXT_LIMIT)
        if content_detail:
            result["contentPreview"] = content_detail.get("preview")
            result["contentCharCount"] = content_detail.get("charCount")
            result["content"] = content_detail.get("text")
            result["contentTruncated"] = content_detail.get("truncated")
    if normalized in {"edit", "apply_patch", "patch"} or "patch" in normalized:
        old_text = extract_first_scalar(candidate_dicts, ("oldText", "old_string", "old"))
        new_text = extract_first_scalar(candidate_dicts, ("newText", "new_string", "new", "content"))
        patch_text = extract_first_scalar(candidate_dicts, ("patch", "diff"))
        old_detail = compact_multiline_text(old_text, preview_limit=160, text_limit=600)
        new_detail = compact_multiline_text(new_text, preview_limit=200, text_limit=800)
        patch_detail = compact_multiline_text(patch_text, preview_limit=220, text_limit=1000)
        if old_detail:
            result["oldTextPreview"] = old_detail.get("preview")
            result["oldTextCharCount"] = old_detail.get("charCount")
        if new_detail:
            result["newTextPreview"] = new_detail.get("preview")
            result["newTextCharCount"] = new_detail.get("charCount")
            result["newText"] = new_detail.get("text")
            result["newTextTruncated"] = new_detail.get("truncated")
        if patch_detail:
            result["patchPreview"] = patch_detail.get("preview")
            result["patchCharCount"] = patch_detail.get("charCount")
            result["patch"] = patch_detail.get("text")
            result["patchTruncated"] = patch_detail.get("truncated")
    return {key: value for key, value in result.items() if value is not None} or None



def build_message_anchor(message: dict[str, Any], *, recent_index: int) -> dict[str, Any]:
    normalized = compact_latest_message(message)
    anchor = {
        "recentIndex": recent_index,
        "messageId": normalized.get("id"),
        "role": normalized.get("role"),
        "status": normalized.get("status"),
        "createdAt": iso_from_epoch_ms(normalized.get("created")),
        "preview": normalized.get("message.lastTextPreview") or normalized.get("toolOutputPreview"),
        "toolNames": normalized.get("toolNames"),
    }
    return {key: value for key, value in anchor.items() if value is not None}



def build_message_detail(message: dict[str, Any], *, recent_index: int) -> dict[str, Any]:
    normalized = compact_latest_message(message)
    info = message.get("info") if isinstance(message.get("info"), dict) else {}
    parts = message.get("parts") if isinstance(message.get("parts"), list) else []

    tool_calls: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    text_parts: list[dict[str, Any]] = []
    info_time = info.get("time") if isinstance(info.get("time"), dict) else {}

    for part_index, part in enumerate(parts):
        if not isinstance(part, dict):
            continue
        part_type = str(part.get("type") or "").strip().lower()
        tool_state = part.get("state") if isinstance(part.get("state"), dict) else {}
        event = build_event_record(message, part)
        if event:
            event_detail = {
                "eventIndex": len(events),
                "partIndex": part_index,
                "kind": event.get("kind"),
                "summary": event.get("summary"),
                "toolName": event.get("toolName"),
                "toolStatus": event.get("toolStatus"),
                "createdAt": iso_from_epoch_ms(event.get("created")),
            }
            events.append({key: value for key, value in event_detail.items() if value is not None})

        if part_type == "text":
            text_detail = compact_multiline_text(part.get("text"), preview_limit=DETAIL_TEXT_PREVIEW_LIMIT, text_limit=DETAIL_TEXT_LIMIT)
            if text_detail:
                text_part = {
                    "partIndex": part_index,
                    "role": normalized.get("role"),
                    "summary": text_detail.get("preview"),
                    "text": text_detail.get("text"),
                    "textTruncated": text_detail.get("truncated"),
                    "charCount": text_detail.get("charCount"),
                    "lineCount": text_detail.get("lineCount"),
                }
                text_parts.append({key: value for key, value in text_part.items() if value is not None})
            continue

        if part_type != "tool":
            continue

        tool_name = str(part.get("tool") or "").strip()
        tool_status = str(tool_state.get("status") or "").strip().lower() or None
        candidate_dicts = extract_candidate_dicts(part, tool_state)
        action = infer_tool_action(tool_name)
        tool_metadata = tool_state.get("metadata") if isinstance(tool_state.get("metadata"), dict) else {}
        raw_output = tool_state.get("output") or tool_metadata.get("output")
        targets = collect_path_targets(candidate_dicts)
        if not targets:
            targets = infer_targets_from_text(
                extract_first_scalar(candidate_dicts, ("patch", "diff"))
                or raw_output
            )
        output_detail = build_output_detail(raw_output)
        command_preview = preview_text(
            extract_first_scalar(candidate_dicts, ("command", "cmd", "script", "shellCommand", "literal", "text")),
            limit=220,
        )
        write_detail = build_tool_write_detail(tool_name, candidate_dicts) or {}

        tool_call = {
            "partIndex": part_index,
            "toolName": tool_name or None,
            "toolStatus": tool_status,
            "action": action,
            "targets": targets,
            "readTargets": targets if action == "read" else None,
            "writeTargets": targets if action == "write" else None,
            "patchTargets": targets if action == "patch" else None,
            "commandPreview": command_preview,
            "outputPreview": output_detail.get("preview") if output_detail else None,
            "outputTailLines": output_detail.get("tailLines") if output_detail else None,
            "outputCharCount": output_detail.get("charCount") if output_detail else None,
            "outputLineCount": output_detail.get("lineCount") if output_detail else None,
            **write_detail,
        }
        tool_calls.append({key: value for key, value in tool_call.items() if value is not None})

    result = {
        "messageId": normalized.get("id") or info.get("id") or message.get("id"),
        "recentIndex": recent_index,
        "role": normalized.get("role") or info.get("role") or message.get("role"),
        "status": normalized.get("status"),
        "createdAt": iso_from_epoch_ms(normalized.get("created") or info_time.get("created") or message.get("created")),
        "completedAt": iso_from_epoch_ms(normalized.get("completedAt") or info_time.get("completed") or message.get("completed")),
        "finish": normalized.get("finish") or info.get("finish") or message.get("finish"),
        "ignored": bool(normalized.get("ignored")),
        "partTypes": normalized.get("partTypes"),
        "textPreview": normalized.get("message.lastTextPreview"),
        "toolOutputPreview": normalized.get("toolOutputPreview"),
        "partCount": len(parts),
        "toolCallCount": len(tool_calls),
        "eventCount": len(events),
        "textParts": text_parts or None,
        "toolCalls": tool_calls or None,
        "events": events or None,
    }
    return {key: value for key, value in result.items() if value is not None}



def select_history_message(messages: list[dict[str, Any]], *, message_id: str | None, recent_index: int | None) -> tuple[dict[str, Any], int, int]:
    if not messages:
        raise ValueError("inspect-history requires at least one message in the fetched window")

    if message_id:
        for absolute_index, message in enumerate(messages):
            normalized = compact_latest_message(message)
            candidate_id = normalized.get("id") or (message.get("info") or {}).get("id") or message.get("id")
            if candidate_id == message_id:
                return message, absolute_index, len(messages) - 1 - absolute_index
        raise ValueError(f"inspect-history could not find messageId in fetched window: {message_id}")

    resolved_recent_index = 0 if recent_index is None else recent_index
    if resolved_recent_index < 0:
        raise ValueError("inspect-history --recent-index must be >= 0 (0 means latest message)")
    absolute_index = len(messages) - 1 - resolved_recent_index
    if absolute_index < 0 or absolute_index >= len(messages):
        raise ValueError(
            f"inspect-history --recent-index={resolved_recent_index} is outside the fetched window of {len(messages)} messages"
        )
    return messages[absolute_index], absolute_index, resolved_recent_index



def build_history_detail(
    session_data: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    opencode_base_url: str | None = None,
    watcher_entry: dict[str, Any] | None = None,
    message_limit: int,
    selected_message_id: str | None,
    selected_recent_index: int | None,
) -> dict[str, Any]:
    selected_message, absolute_index, recent_index = select_history_message(
        messages,
        message_id=selected_message_id,
        recent_index=selected_recent_index,
    )
    anchor_count = min(DEFAULT_HISTORY_ANCHOR_COUNT, len(messages))
    recent_slice = messages[-anchor_count:]
    recent_anchors = [
        build_message_anchor(message, recent_index=len(recent_slice) - 1 - index)
        for index, message in enumerate(recent_slice)
    ]
    recent_anchors.sort(key=lambda item: item.get("recentIndex", 0))

    result = {
        "opencodeSession": build_session_summary(session_data, opencode_base_url=opencode_base_url, watcher_entry=watcher_entry),
        "selection": {
            key: value
            for key, value in {
                "messageId": compact_latest_message(selected_message).get("id") or (selected_message.get("info") or {}).get("id") or selected_message.get("id"),
                "recentIndex": recent_index,
                "absoluteIndex": absolute_index,
                "messageCountInWindow": len(messages),
                "messageWindowLimit": message_limit,
                "mayExcludeOlderHistory": len(messages) >= message_limit,
            }.items()
            if value is not None
        },
        "recentAnchors": recent_anchors,
        "message": build_message_detail(selected_message, recent_index=recent_index),
        "watcher": build_watcher_summary(watcher_entry) if watcher_entry else None,
    }
    return {key: value for key, value in result.items() if value is not None}



def build_inspection(
    session_data: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    opencode_base_url: str | None = None,
    watcher_entry: dict[str, Any] | None = None,
    requested_message_limit: int | None = None,
) -> dict[str, Any]:
    todo = snapshot.get("todo") if isinstance(snapshot.get("todo"), dict) else {}
    latest_message = snapshot.get("latestMessage") if isinstance(snapshot.get("latestMessage"), dict) else {}
    completed_work = []
    for item in todo.get("items") or []:
        if isinstance(item, dict) and item.get("status") == "completed":
            completed_work.append(item.get("content"))

    current_status = derive_inspection_status(snapshot, latest_message, todo)
    latest_meaningful_preview = (
        snapshot.get("latestTextPreview")
        or snapshot.get("latestAssistantTextPreview")
        or latest_message.get("errorPreview")
        or latest_message.get("message.errorMessage")
    )
    running_progress_observation = analyze_running_progress(snapshot, current_status=current_status)
    transport_error_hints = summarize_transport_errors(snapshot.get("errors"))
    snapshot_coverage = build_snapshot_coverage(snapshot, requested_message_limit=requested_message_limit)
    rehydration = {
        "version": "v1",
        "purpose": "current_state_rebuild",
        "snapshotCoverage": snapshot_coverage,
        "currentState": {
            key: value
            for key, value in {
                "status": current_status,
                "phase": todo.get("phase"),
                "latestMeaningfulPreview": latest_meaningful_preview,
                "hasPendingWork": todo.get("hasPendingWork"),
                "allTodosCompleted": todo.get("allCompleted"),
                "pendingPermissionCount": len(snapshot.get("permission") or []) if isinstance(snapshot.get("permission"), list) else None,
                "openQuestionCount": len(snapshot.get("question") or []) if isinstance(snapshot.get("question"), list) else None,
                "runningProgressObservation": running_progress_observation,
                "transportErrorHints": transport_error_hints,
            }.items()
            if value is not None
        },
        "latestUserIntent": snapshot.get("latestUserInputSummary"),
        "sinceLatestUserInput": build_since_latest_user_input(snapshot),
        "recentCompletedWork": build_recent_completed_work(snapshot, todo),
        "recentNotableEvents": build_recent_notable_events(snapshot),
        "followUpHints": build_rehydration_follow_up_hints(
            snapshot_coverage,
            running_progress_observation=running_progress_observation,
            transport_error_hints=transport_error_hints,
        ),
        "watcherState": build_rehydration_watcher_state(watcher_entry),
    }

    result = {
        "opencodeSession": build_inspect_session_summary(
            session_data,
            opencode_base_url=opencode_base_url,
            watcher_entry=watcher_entry,
        ),
        "currentStatus": current_status,
        "currentPhase": todo.get("phase"),
        "hasPendingWork": todo.get("hasPendingWork"),
        "allTodosCompleted": todo.get("allCompleted"),
        "completedWork": completed_work[-5:] or None,
        "latestMeaningfulPreview": latest_meaningful_preview,
        "latestUserInputSummary": snapshot.get("latestUserInputSummary"),
        "latestMessage": build_inspect_latest_message(latest_message),
        "runningProgressObservation": running_progress_observation,
        "transportErrorHints": transport_error_hints,
        "rehydration": {key: value for key, value in rehydration.items() if value is not None},
    }
    if watcher_entry:
        result["watcher"] = build_inspect_watcher_summary(watcher_entry)
    return {key: value for key, value in result.items() if value is not None}



def build_watcher_summary(entry: dict[str, Any]) -> dict[str, Any]:
    opencode_base_url = entry.get("opencodeBaseUrl")
    opencode_workspace = entry.get("opencodeWorkspace")
    opencode_session_id = entry.get("opencodeSessionId")
    opencode_ui_url = None
    if isinstance(opencode_base_url, str) and opencode_base_url and isinstance(opencode_workspace, str) and opencode_workspace and isinstance(opencode_session_id, str) and opencode_session_id:
        opencode_ui_url = build_opencode_session_ui_url(opencode_base_url, opencode_workspace, opencode_session_id)
    return {
        key: value
        for key, value in {
            "watcherId": entry.get("watcherId"),
            "watcherStatus": entry.get("watcherStatus"),
            "watchProcessId": entry.get("watchProcessId"),
            "watchProcessAlive": entry.get("watchProcessAlive"),
            "opencodeBaseUrl": opencode_base_url,
            "opencodeSessionId": opencode_session_id,
            "opencodeWorkspace": opencode_workspace,
            "opencodeUiUrl": opencode_ui_url,
            "openclawSessionKey": entry.get("openclawSessionKey"),
            "openclawDeliveryTarget": entry.get("openclawDeliveryTarget"),
            "watchLive": entry.get("watchLive"),
            "watchIntervalSec": entry.get("watchIntervalSec"),
            "idleTimeoutSec": entry.get("idleTimeoutSec"),
            "notifyMinIntervalSec": entry.get("notifyMinIntervalSec"),
            "notifyMinPriority": entry.get("notifyMinPriority"),
            "notifyKeywords": entry.get("notifyKeywords"),
            "notifyFilterCritical": entry.get("notifyFilterCritical"),
            "watchStartedAt": entry.get("watchStartedAt"),
            "watchExitedAt": entry.get("watchExitedAt"),
            "watchExitReason": entry.get("watchExitReason"),
            "lastWatchRunAt": entry.get("lastWatchRunAt"),
            "lastWatchOperation": entry.get("lastWatchOperation"),
            "lastRouteStatus": entry.get("lastRouteStatus"),
            "lastDeliveryAction": entry.get("lastDeliveryAction"),
            "lastOpencodeStatus": entry.get("lastOpencodeStatus"),
            "lastOpencodePhase": entry.get("lastOpencodePhase"),
            "lastPreview": entry.get("lastPreview"),
            "lastRunningProgressObservation": entry.get("lastRunningProgressObservation"),
            "lastTransportErrorHints": entry.get("lastTransportErrorHints"),
            "watcherConfigPath": entry.get("watcherConfigPath"),
            "watcherStatePath": entry.get("watcherStatePath"),
            "watcherLogPath": entry.get("watcherLogPath"),
        }.items()
        if value is not None
    }



def build_agent_handoff_contract(
    *,
    watcher_entry: dict[str, Any] | None,
    watcher_requested: bool,
) -> dict[str, Any]:
    watcher_status = watcher_entry.get("watcherStatus") if isinstance(watcher_entry, dict) else None
    watch_live = bool(watcher_entry.get("watchLive")) if isinstance(watcher_entry, dict) else False
    watcher_running = watcher_status == "running"

    if watcher_running and watch_live:
        handoff_mode = "watcher_live"
        user_facing_ack = WATCHER_HANDOFF_ACK
    elif watcher_running:
        handoff_mode = "watcher_not_live"
        user_facing_ack = NON_LIVE_WATCHER_ACK
    elif watcher_requested:
        handoff_mode = "watcher_missing"
        user_facing_ack = MISSING_WATCHER_ACK
    else:
        handoff_mode = "no_watcher"
        user_facing_ack = NO_WATCHER_ACK

    return {
        "handoffMode": handoff_mode,
        "agentAction": "acknowledge_and_end_turn",
        "userFacingAck": user_facing_ack,
    }



def spawn_watcher(entry: dict[str, Any]) -> int:
    watcher_config_path = Path(entry["watcherConfigPath"])
    watcher_log_path = Path(entry["watcherLogPath"])
    watcher_log_path.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(WATCH_RUNTIME), "--config", str(watcher_config_path)]
    if not entry.get("watchLive"):
        command.append("--dry-run")
    log_file = watcher_log_path.open("a", encoding="utf-8")
    try:
        process = subprocess.Popen(
            command,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        log_file.close()
    return process.pid



def ensure_session_exists(
    client: OpenCodeClient,
    *,
    opencode_session_id: str,
    opencode_workspace: str | None,
) -> dict[str, Any]:
    session_data = client.get_session(opencode_session_id, directory=opencode_workspace)
    if not isinstance(session_data, dict):
        raise ValueError("OpenCode session lookup returned a non-object payload")
    return session_data



def normalize_workspace_scope(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return os.path.normpath(os.path.expanduser(text))



def resolve_stop_session_workspace(*, requested_workspace: str | None, session_data: dict[str, Any]) -> str:
    actual_workspace_raw = session_data.get("directory")
    actual_workspace = normalize_workspace_scope(actual_workspace_raw)
    requested_normalized = normalize_workspace_scope(requested_workspace)

    if requested_workspace is not None:
        if actual_workspace is None:
            raise ValueError(
                "stop-session could not verify the session directory for the explicit --opencode-workspace value"
            )
        if requested_normalized != actual_workspace:
            raise ValueError(
                "stop-session refused to abort because explicit --opencode-workspace does not match the "
                f"session directory (requested={requested_workspace!r}, actual={actual_workspace_raw!r})"
            )

    resolved_workspace = actual_workspace or requested_normalized
    if not isinstance(resolved_workspace, str) or not resolved_workspace:
        raise ValueError("stop-session requires a resolvable opencodeWorkspace")
    return resolved_workspace



def create_watcher_entry(
    *,
    watcher_id: str,
    opencode_base_url: str,
    opencode_session_id: str,
    opencode_workspace: str,
    openclaw_session_key: str,
    openclaw_delivery_target: str | None,
    opencode_token: str | None,
    opencode_token_env: str | None,
    watch_live: bool,
    watch_interval_sec: int,
    idle_timeout_sec: int,
    notify_min_interval_sec: int,
    notify_min_priority: str,
    notify_keywords: list[str],
    notify_filter_critical: bool = False,
    watch_message_limit: int = DEFAULT_MESSAGE_LIMIT,
    watch_timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    watcher_root: Path | None = None,
) -> dict[str, Any]:
    paths = watcher_paths_for_id(watcher_id, watcher_root=watcher_root)
    return {
        "watcherId": watcher_id,
        "watcherStatus": "starting",
        "opencodeBaseUrl": opencode_base_url,
        "opencodeSessionId": opencode_session_id,
        "opencodeWorkspace": opencode_workspace,
        "openclawSessionKey": openclaw_session_key,
        "openclawDeliveryTarget": openclaw_delivery_target,
        "opencodeToken": opencode_token,
        "opencodeTokenEnv": opencode_token_env,
        "watchLive": watch_live,
        "watchIntervalSec": watch_interval_sec,
        "idleTimeoutSec": idle_timeout_sec,
        "notifyMinIntervalSec": notify_min_interval_sec,
        "notifyMinPriority": notify_min_priority,
        "notifyKeywords": list(notify_keywords),
        "notifyFilterCritical": bool(notify_filter_critical),
        "watchMessageLimit": watch_message_limit,
        "watchTimeoutSec": watch_timeout_sec,
        "watchCreatedAt": now_iso(),
        "watcherConfigPath": str(paths["watcherConfigPath"]),
        "watcherStatePath": str(paths["watcherStatePath"]),
        "watcherLogPath": str(paths["watcherLogPath"]),
    }



def start_or_attach_watcher(
    *,
    registry_path: Path,
    opencode_base_url: str,
    opencode_session_id: str,
    opencode_workspace: str,
    openclaw_session_key: str,
    openclaw_delivery_target: str | None,
    opencode_token: str | None,
    opencode_token_env: str | None,
    watch_live: bool,
    watch_interval_sec: int,
    idle_timeout_sec: int,
    notify_min_interval_sec: int,
    notify_min_priority: str,
    notify_keywords: list[str],
    notify_filter_critical: bool = False,
    watch_message_limit: int = DEFAULT_MESSAGE_LIMIT,
    watch_timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> dict[str, Any]:
    with locked_registry(registry_path) as (registry, _path):
        refresh_registry_entries(registry, registry_path=registry_path)
        active_entry = find_active_watcher_entry(registry, opencode_session_id)
        if active_entry:
            raise RuntimeError(
                f"watcher lock active for opencodeSessionId={opencode_session_id}: watcherId={active_entry.get('watcherId')}"
            )

        watcher_id = f"ow_{uuid.uuid4().hex[:12]}"
        watcher_root = watcher_root_for_registry_path(registry_path)
        entry = create_watcher_entry(
            watcher_id=watcher_id,
            opencode_base_url=opencode_base_url,
            opencode_session_id=opencode_session_id,
            opencode_workspace=opencode_workspace,
            openclaw_session_key=openclaw_session_key,
            openclaw_delivery_target=openclaw_delivery_target,
            opencode_token=opencode_token,
            opencode_token_env=opencode_token_env,
            watch_live=watch_live,
            watch_interval_sec=watch_interval_sec,
            idle_timeout_sec=idle_timeout_sec,
            notify_min_interval_sec=notify_min_interval_sec,
            notify_min_priority=notify_min_priority,
            notify_keywords=notify_keywords,
            notify_filter_critical=notify_filter_critical,
            watch_message_limit=watch_message_limit,
            watch_timeout_sec=watch_timeout_sec,
            watcher_root=watcher_root,
        )
        save_json_object(Path(entry["watcherConfigPath"]), build_manager_watcher_config(entry))
        registry["watchers"].append(entry)
        try:
            watch_process_id = spawn_watcher(entry)
        except Exception:
            registry["watchers"] = [item for item in registry["watchers"] if item.get("watcherId") != watcher_id]
            raise

        for item in registry["watchers"]:
            if item.get("watcherId") == watcher_id:
                item["watchProcessId"] = watch_process_id
                item["watchProcessAlive"] = True
                item["watcherStatus"] = "running"
                item["watchStartedAt"] = now_iso()
                entry = item
                break
        return refresh_registry_entry(entry, registry_path=registry_path)



def validate_existing_binding(active_entry: dict[str, Any], args: argparse.Namespace) -> None:
    requested_session_key = getattr(args, "openclaw_session_key", None)
    requested_delivery_target = getattr(args, "openclaw_delivery_target", None)
    if requested_session_key and active_entry.get("openclawSessionKey") != requested_session_key:
        raise RuntimeError(
            "continue --ensure-watcher refused to silently rebind an active watcher: "
            f"existing openclawSessionKey={active_entry.get('openclawSessionKey')}"
        )
    if requested_delivery_target and active_entry.get("openclawDeliveryTarget") != requested_delivery_target:
        raise RuntimeError(
            "continue --ensure-watcher refused to silently rebind an active watcher: "
            f"existing openclawDeliveryTarget={active_entry.get('openclawDeliveryTarget')}"
        )



def coalesce(value: Any, fallback: Any) -> Any:
    return fallback if value is None else value



def resolve_continue_watcher_request(
    *,
    args: argparse.Namespace,
    session_data: dict[str, Any],
    registry_path: Path,
) -> dict[str, Any]:
    with locked_registry(registry_path) as (registry, _path):
        refresh_registry_entries(registry, registry_path=registry_path)
        active_entry = find_active_watcher_entry(registry, args.opencode_session_id)
        latest_entry = find_latest_watcher_entry(registry, args.opencode_session_id)
        if active_entry:
            validate_existing_binding(active_entry, args)
            return {
                "alreadyRunning": True,
                "watcher": build_watcher_summary(active_entry),
            }

    opencode_workspace = (
        args.opencode_workspace
        or session_data.get("directory")
        or (latest_entry.get("opencodeWorkspace") if latest_entry else None)
    )
    if not isinstance(opencode_workspace, str) or not opencode_workspace:
        raise ValueError("continue --ensure-watcher requires a resolvable opencodeWorkspace")

    openclaw_session_key = args.openclaw_session_key or (latest_entry.get("openclawSessionKey") if latest_entry else None)
    if not isinstance(openclaw_session_key, str) or not openclaw_session_key:
        raise ValueError(
            "continue --ensure-watcher requires --openclaw-session-key when no prior watcher binding exists"
        )

    openclaw_delivery_target = coalesce(
        args.openclaw_delivery_target,
        latest_entry.get("openclawDeliveryTarget") if latest_entry else None,
    )
    watch_live = bool(coalesce(args.watch_live, latest_entry.get("watchLive") if latest_entry else False))
    watch_interval_sec = int(coalesce(args.watch_interval_sec, latest_entry.get("watchIntervalSec") if latest_entry else DEFAULT_WATCH_INTERVAL_SEC))
    idle_timeout_sec = int(coalesce(args.idle_timeout_sec, latest_entry.get("idleTimeoutSec") if latest_entry else DEFAULT_IDLE_TIMEOUT_SEC))
    notify_min_interval_sec = int(
        coalesce(
            getattr(args, "notify_min_interval_sec", None),
            latest_entry.get("notifyMinIntervalSec") if latest_entry and latest_entry.get("notifyMinIntervalSec") is not None else 0,
        )
        or 0
    )
    notify_min_priority = str(coalesce(getattr(args, "notify_min_priority", None), latest_entry.get("notifyMinPriority") if latest_entry else "low") or "low")
    notify_keywords = list(coalesce(getattr(args, "notify_keyword", None), latest_entry.get("notifyKeywords") if latest_entry else []) or [])
    notify_filter_critical = bool(coalesce(getattr(args, "notify_filter_critical", None), latest_entry.get("notifyFilterCritical") if latest_entry else False))
    watch_message_limit = int(coalesce(args.watch_message_limit, latest_entry.get("watchMessageLimit") if latest_entry else DEFAULT_MESSAGE_LIMIT))
    watch_timeout_sec = int(coalesce(args.watch_timeout_sec, latest_entry.get("watchTimeoutSec") if latest_entry else DEFAULT_TIMEOUT_SEC))

    resolved_opencode_token = resolve_opencode_token(args.opencode_token, args.opencode_token_env)
    if resolved_opencode_token is not None:
        token_value = resolved_opencode_token
        token_env_value = None
    elif latest_entry:
        token_value = latest_entry.get("opencodeToken")
        token_env_value = latest_entry.get("opencodeTokenEnv")
    else:
        token_value = None
        token_env_value = None

    watcher_entry = start_or_attach_watcher(
        registry_path=registry_path,
        opencode_base_url=args.opencode_base_url,
        opencode_session_id=args.opencode_session_id,
        opencode_workspace=opencode_workspace,
        openclaw_session_key=openclaw_session_key,
        openclaw_delivery_target=openclaw_delivery_target,
        opencode_token=token_value,
        opencode_token_env=token_env_value,
        watch_live=watch_live,
        watch_interval_sec=watch_interval_sec,
        idle_timeout_sec=idle_timeout_sec,
        notify_min_interval_sec=notify_min_interval_sec,
        notify_min_priority=notify_min_priority,
        notify_keywords=notify_keywords,
        notify_filter_critical=notify_filter_critical,
        watch_message_limit=watch_message_limit,
        watch_timeout_sec=watch_timeout_sec,
    )
    return {
        "alreadyRunning": False,
        "watcher": build_watcher_summary(watcher_entry),
    }



def record_manager_watch_exit(state_path: Path, *, reason: str) -> dict[str, Any]:
    state_doc = load_json_object(state_path)
    watch_state = dict(state_doc.get("watchRunner") or {})
    watch_state["lastExitReason"] = reason
    watch_state["lastExitedAt"] = now_iso()
    state_doc["watchRunner"] = watch_state
    save_json_object(state_path, state_doc)
    return watch_state



def signal_process_group(process_id: int, sig: int) -> None:
    try:
        os.killpg(process_id, sig)
        return
    except (OSError, ProcessLookupError):
        pass
    try:
        os.kill(process_id, sig)
    except OSError:
        pass



def wait_for_runtime_exit(config_path: Path, *, timeout_sec: int) -> bool:
    deadline = time.monotonic() + max(timeout_sec, 0)
    config_key = str(config_path.resolve())
    while time.monotonic() <= deadline:
        if config_key not in list_watch_runtime_processes():
            return True
        time.sleep(0.2)
    return config_key not in list_watch_runtime_processes()



def stop_runtime_process_by_config(config_path: Path, *, timeout_sec: int) -> tuple[bool, int | None, str | None]:
    config_key = str(config_path.resolve())
    runtime_info = list_watch_runtime_processes().get(config_key)
    if not runtime_info:
        return False, None, None

    process_id = runtime_info.get("pid")
    if not isinstance(process_id, int) or process_id <= 0:
        return False, None, None

    signal_process_group(process_id, signal.SIGINT)
    if wait_for_runtime_exit(config_path, timeout_sec=timeout_sec):
        return True, process_id, "SIGINT"

    signal_process_group(process_id, signal.SIGTERM)
    if wait_for_runtime_exit(config_path, timeout_sec=max(1, timeout_sec // 2 or 1)):
        return True, process_id, "SIGTERM"

    signal_process_group(process_id, signal.SIGKILL)
    wait_for_runtime_exit(config_path, timeout_sec=1)
    return True, process_id, "SIGKILL"



def stop_watcher_entry(
    entry: dict[str, Any],
    *,
    registry_path: Path,
    exit_reason: str,
    stop_timeout_sec: int,
) -> dict[str, Any]:
    refreshed = refresh_registry_entry(entry, registry_path=registry_path)
    config_path = Path(refreshed["watcherConfigPath"]).expanduser().resolve()
    state_path = Path(refreshed["watcherStatePath"]).expanduser().resolve()
    was_running, stopped_pid, stop_signal = stop_runtime_process_by_config(config_path, timeout_sec=stop_timeout_sec)

    record_manager_watch_exit(state_path, reason=exit_reason)

    refreshed["watcherStatus"] = "exited"
    refreshed["watchProcessAlive"] = False
    if stopped_pid is not None:
        refreshed["watchProcessId"] = stopped_pid
    refreshed["watchExitedAt"] = now_iso()
    refreshed["watchExitReason"] = exit_reason
    if stop_signal:
        refreshed["watchStopSignal"] = stop_signal
    refreshed["watchStopRequestedAt"] = now_iso()
    refreshed["watchWasRunning"] = was_running
    return refresh_registry_entry(refreshed, registry_path=registry_path)



def select_matching_entries(
    registry: dict[str, Any],
    *,
    watcher_id: str | None,
    opencode_session_id: str | None,
) -> list[dict[str, Any]]:
    matches = []
    for entry in registry.get("watchers") or []:
        if watcher_id and entry.get("watcherId") != watcher_id:
            continue
        if opencode_session_id and entry.get("opencodeSessionId") != opencode_session_id:
            continue
        matches.append(entry)
    return matches



def select_running_entries(
    registry: dict[str, Any],
    *,
    watcher_id: str | None,
    opencode_session_id: str | None,
) -> list[dict[str, Any]]:
    return [
        entry
        for entry in select_matching_entries(
            registry,
            watcher_id=watcher_id,
            opencode_session_id=opencode_session_id,
        )
        if entry.get("watcherStatus") == "running"
    ]



def list_sessions_command(args: argparse.Namespace) -> dict[str, Any]:
    registry_path = Path(args.registry_path).expanduser().resolve()
    client = OpenCodeClient(
        base_url=args.opencode_base_url,
        token=resolve_opencode_token(args.opencode_token, getattr(args, "opencode_token_env", None)),
        timeout=args.watch_timeout_sec,
    )
    sessions = client.list_sessions(directory=args.opencode_workspace, limit=args.limit)
    if not isinstance(sessions, list):
        raise ValueError("OpenCode session list returned a non-list payload")

    with locked_registry(registry_path) as (registry, _path):
        refresh_registry_entries(registry, registry_path=registry_path)
        watcher_by_session_id = {
            entry.get("opencodeSessionId"): entry
            for entry in registry.get("watchers") or []
            if entry.get("watcherStatus") == "running"
        }
        normalized_sessions = [
            build_session_summary(session, opencode_base_url=args.opencode_base_url, watcher_entry=watcher_by_session_id.get(session.get("id")))
            for session in sessions
            if isinstance(session, dict)
        ]

    return {
        "kind": "opencode_manager_list_sessions_v1",
        "opencodeBaseUrl": args.opencode_base_url,
        "opencodeWorkspace": args.opencode_workspace,
        "sessionCount": len(normalized_sessions),
        "sessions": normalized_sessions,
    }



def inspect_command(args: argparse.Namespace) -> dict[str, Any]:
    registry_path = Path(args.registry_path).expanduser().resolve()
    client = OpenCodeClient(
        base_url=args.opencode_base_url,
        token=resolve_opencode_token(args.opencode_token, getattr(args, "opencode_token_env", None)),
        timeout=args.watch_timeout_sec,
    )
    session_data = ensure_session_exists(
        client,
        opencode_session_id=args.opencode_session_id,
        opencode_workspace=args.opencode_workspace,
    )
    snapshot, _errors = build_compact_snapshot(client, args.opencode_session_id, message_limit=args.watch_message_limit)

    with locked_registry(registry_path) as (registry, _path):
        refresh_registry_entries(registry, registry_path=registry_path)
        watcher_entry = next(
            (
                entry
                for entry in registry.get("watchers") or []
                if entry.get("opencodeSessionId") == args.opencode_session_id and entry.get("watcherStatus") == "running"
            ),
            None,
        )

    return {
        "kind": "opencode_manager_inspect_v1",
        "inspection": build_inspection(
            session_data,
            snapshot,
            opencode_base_url=args.opencode_base_url,
            watcher_entry=watcher_entry,
            requested_message_limit=args.watch_message_limit,
        ),
    }



def inspect_history_command(args: argparse.Namespace) -> dict[str, Any]:
    registry_path = Path(args.registry_path).expanduser().resolve()
    client = OpenCodeClient(
        base_url=args.opencode_base_url,
        token=resolve_opencode_token(args.opencode_token, getattr(args, "opencode_token_env", None)),
        timeout=args.watch_timeout_sec,
    )
    session_data = ensure_session_exists(
        client,
        opencode_session_id=args.opencode_session_id,
        opencode_workspace=args.opencode_workspace,
    )
    messages = normalize_message_collection(
        client.session_messages(
            args.opencode_session_id,
            limit=args.history_message_limit,
            directory=args.opencode_workspace,
        )
    )

    with locked_registry(registry_path) as (registry, _path):
        refresh_registry_entries(registry, registry_path=registry_path)
        watcher_entry = next(
            (
                entry
                for entry in registry.get("watchers") or []
                if entry.get("opencodeSessionId") == args.opencode_session_id and entry.get("watcherStatus") == "running"
            ),
            None,
        )

    selected_recent_index = args.recent_index
    if args.message_id is None and selected_recent_index is None:
        selected_recent_index = 0

    history = build_history_detail(
        session_data,
        messages,
        opencode_base_url=args.opencode_base_url,
        watcher_entry=watcher_entry,
        message_limit=args.history_message_limit,
        selected_message_id=args.message_id,
        selected_recent_index=selected_recent_index,
    )
    return {
        "kind": "opencode_manager_inspect_history_v1",
        "history": {key: value for key, value in history.items() if value is not None},
    }



def list_watchers_command(args: argparse.Namespace) -> dict[str, Any]:
    registry_path = Path(args.registry_path).expanduser().resolve()
    with locked_registry(registry_path) as (registry, _path):
        refresh_registry_entries(registry, registry_path=registry_path)
        entries = registry.get("watchers") or []
        if not args.include_exited:
            entries = [entry for entry in entries if entry.get("watcherStatus") == "running"]
        return {
            "kind": "opencode_manager_list_watchers_v1",
            "registryPath": str(registry_path),
            "watcherCount": len(entries),
            "watchers": [build_watcher_summary(entry) for entry in entries],
        }



def start_command(args: argparse.Namespace) -> dict[str, Any]:
    registry_path = Path(args.registry_path).expanduser().resolve()
    first_prompt = resolve_prompt_input(
        args.first_prompt,
        getattr(args, "first_prompt_file", None),
        text_flag="--first-prompt",
        file_flag="--first-prompt-file",
    )
    resolved_opencode_token = resolve_opencode_token(args.opencode_token, args.opencode_token_env)
    client = OpenCodeClient(base_url=args.opencode_base_url, token=resolved_opencode_token, timeout=args.watch_timeout_sec)
    created_session = client.create_session(directory=args.opencode_workspace, title=args.title)
    if not isinstance(created_session, dict):
        raise ValueError("OpenCode session creation returned a non-object payload")
    opencode_session_id = created_session.get("id") or created_session.get("sessionID") or created_session.get("sessionId")
    if not isinstance(opencode_session_id, str) or not opencode_session_id:
        raise ValueError("OpenCode session creation did not return an id")

    client.prompt_session(
        opencode_session_id,
        directory=args.opencode_workspace,
        parts=[{"type": "text", "text": first_prompt["text"]}],
        asynchronous=True,
    )

    watcher_entry = start_or_attach_watcher(
        registry_path=registry_path,
        opencode_base_url=args.opencode_base_url,
        opencode_session_id=opencode_session_id,
        opencode_workspace=args.opencode_workspace,
        openclaw_session_key=args.openclaw_session_key,
        openclaw_delivery_target=args.openclaw_delivery_target,
        opencode_token=resolved_opencode_token,
        opencode_token_env=args.opencode_token_env if resolved_opencode_token is None else None,
        watch_live=args.watch_live,
        watch_interval_sec=args.watch_interval_sec,
        idle_timeout_sec=args.idle_timeout_sec,
        notify_min_interval_sec=getattr(args, "notify_min_interval_sec", 0),
        notify_min_priority=getattr(args, "notify_min_priority", "low"),
        notify_keywords=getattr(args, "notify_keyword", []),
        notify_filter_critical=bool(getattr(args, "notify_filter_critical", False)),
        watch_message_limit=args.watch_message_limit,
        watch_timeout_sec=args.watch_timeout_sec,
    )

    result = {
        "kind": "opencode_manager_start_v1",
        "opencodeSession": build_session_summary(created_session, opencode_base_url=args.opencode_base_url, watcher_entry=watcher_entry),
        "firstPrompt": {
            "deliveryMode": "prompt_async",
            "accepted": True,
            "inputMethod": first_prompt["inputMethod"],
            "promptFile": first_prompt.get("promptFile"),
            "promptPreview": preview_text(first_prompt["text"]),
        },
        "watcher": build_watcher_summary(watcher_entry),
        "registryPath": str(registry_path),
    }
    result.update(build_agent_handoff_contract(watcher_entry=watcher_entry, watcher_requested=True))
    return result



def attach_command(args: argparse.Namespace) -> dict[str, Any]:
    registry_path = Path(args.registry_path).expanduser().resolve()
    resolved_opencode_token = resolve_opencode_token(args.opencode_token, args.opencode_token_env)
    client = OpenCodeClient(base_url=args.opencode_base_url, token=resolved_opencode_token, timeout=args.watch_timeout_sec)
    session_data = ensure_session_exists(
        client,
        opencode_session_id=args.opencode_session_id,
        opencode_workspace=args.opencode_workspace,
    )
    opencode_workspace = args.opencode_workspace or session_data.get("directory")
    if not isinstance(opencode_workspace, str) or not opencode_workspace:
        raise ValueError("attach requires a resolvable opencodeWorkspace")

    watcher_entry = start_or_attach_watcher(
        registry_path=registry_path,
        opencode_base_url=args.opencode_base_url,
        opencode_session_id=args.opencode_session_id,
        opencode_workspace=opencode_workspace,
        openclaw_session_key=args.openclaw_session_key,
        openclaw_delivery_target=args.openclaw_delivery_target,
        opencode_token=resolved_opencode_token,
        opencode_token_env=args.opencode_token_env if resolved_opencode_token is None else None,
        watch_live=args.watch_live,
        watch_interval_sec=args.watch_interval_sec,
        idle_timeout_sec=args.idle_timeout_sec,
        notify_min_interval_sec=getattr(args, "notify_min_interval_sec", 0),
        notify_min_priority=getattr(args, "notify_min_priority", "low"),
        notify_keywords=getattr(args, "notify_keyword", []),
        notify_filter_critical=bool(getattr(args, "notify_filter_critical", False)),
        watch_message_limit=args.watch_message_limit,
        watch_timeout_sec=args.watch_timeout_sec,
    )

    snapshot, _errors = build_compact_snapshot(client, args.opencode_session_id, message_limit=args.watch_message_limit)

    return {
        "kind": "opencode_manager_attach_v1",
        "opencodeSession": build_session_summary(session_data, opencode_base_url=args.opencode_base_url, watcher_entry=watcher_entry),
        "watcher": build_watcher_summary(watcher_entry),
        "inspection": build_inspection(
            session_data,
            snapshot,
            opencode_base_url=args.opencode_base_url,
            watcher_entry=watcher_entry,
            requested_message_limit=args.watch_message_limit,
        ),
        "registryPath": str(registry_path),
    }



def continue_command(args: argparse.Namespace) -> dict[str, Any]:
    registry_path = Path(args.registry_path).expanduser().resolve()
    follow_up_prompt = resolve_prompt_input(
        args.follow_up_prompt,
        getattr(args, "follow_up_prompt_file", None),
        text_flag="--follow-up-prompt",
        file_flag="--follow-up-prompt-file",
    )
    resolved_opencode_token = resolve_opencode_token(args.opencode_token, args.opencode_token_env)
    timeout = args.watch_timeout_sec if args.watch_timeout_sec is not None else DEFAULT_TIMEOUT_SEC
    client = OpenCodeClient(base_url=args.opencode_base_url, token=resolved_opencode_token, timeout=timeout)
    session_data = ensure_session_exists(
        client,
        opencode_session_id=args.opencode_session_id,
        opencode_workspace=args.opencode_workspace,
    )

    client.prompt_session(
        args.opencode_session_id,
        directory=args.opencode_workspace or session_data.get("directory"),
        parts=[{"type": "text", "text": follow_up_prompt["text"]}],
        asynchronous=True,
    )

    watcher_payload = None
    if args.ensure_watcher:
        watcher_payload = resolve_continue_watcher_request(args=args, session_data=session_data, registry_path=registry_path)
    else:
        with locked_registry(registry_path) as (registry, _path):
            refresh_registry_entries(registry, registry_path=registry_path)
            active_entry = find_active_watcher_entry(registry, args.opencode_session_id)
            if active_entry:
                watcher_payload = {
                    "alreadyRunning": True,
                    "watcher": build_watcher_summary(active_entry),
                }

    watcher_entry = watcher_payload["watcher"] if watcher_payload else None
    session_summary = build_session_summary(session_data, opencode_base_url=args.opencode_base_url, watcher_entry=watcher_entry)

    result = {
        "kind": "opencode_manager_continue_v1",
        "opencodeSession": session_summary,
        "followUpPrompt": {
            "deliveryMode": "prompt_async",
            "accepted": True,
            "inputMethod": follow_up_prompt["inputMethod"],
            "promptFile": follow_up_prompt.get("promptFile"),
            "promptPreview": preview_text(follow_up_prompt["text"]),
        },
        "ensureWatcherRequested": bool(args.ensure_watcher),
        "registryPath": str(registry_path),
    }
    if watcher_payload:
        result["watcher"] = watcher_payload["watcher"]
        result["watcherAlreadyRunning"] = bool(watcher_payload["alreadyRunning"])
    result.update(build_agent_handoff_contract(watcher_entry=watcher_entry, watcher_requested=bool(args.ensure_watcher)))
    return result



def stop_session_command(args: argparse.Namespace) -> dict[str, Any]:
    registry_path = Path(args.registry_path).expanduser().resolve()
    resolved_opencode_token = resolve_opencode_token(args.opencode_token, getattr(args, "opencode_token_env", None))
    timeout = args.watch_timeout_sec if args.watch_timeout_sec is not None else DEFAULT_TIMEOUT_SEC
    client = OpenCodeClient(base_url=args.opencode_base_url, token=resolved_opencode_token, timeout=timeout)
    session_data = ensure_session_exists(
        client,
        opencode_session_id=args.opencode_session_id,
        opencode_workspace=None,
    )
    opencode_workspace = resolve_stop_session_workspace(
        requested_workspace=args.opencode_workspace,
        session_data=session_data,
    )

    abort_requested_at_ms = int(time.time() * 1000)
    abort_result = client.abort_session(args.opencode_session_id, directory=opencode_workspace)

    with locked_registry(registry_path) as (registry, _path):
        refresh_registry_entries(registry, registry_path=registry_path)
        watcher_entry = next(
            (
                entry
                for entry in registry.get("watchers") or []
                if entry.get("opencodeSessionId") == args.opencode_session_id and entry.get("watcherStatus") == "running"
            ),
            None,
        )

    abort_accepted = True if isinstance(abort_result, dict) else bool(abort_result)
    verification = verify_stop_session_attempt(
        client,
        session_id=args.opencode_session_id,
        directory=opencode_workspace,
        abort_requested_at_ms=abort_requested_at_ms,
        verify_wait_sec=getattr(args, "verify_wait_sec", 3.0),
        verify_poll_sec=getattr(args, "verify_poll_sec", 1.0),
    )
    stop_verified = bool(verification.get("verified"))
    stop_likely_failed = bool(verification.get("stopLikelyFailed"))
    if stop_verified:
        stop_outcome = "verified_stopped"
    elif stop_likely_failed:
        stop_outcome = "likely_failed"
    else:
        stop_outcome = "unverified"
    result = {
        "kind": "opencode_manager_stop_session_v1",
        "stopMethod": "abort_api",
        "stopOutcome": stop_outcome,
        "stopped": stop_verified,
        "abortAccepted": abort_accepted,
        "abortResult": abort_result,
        "abortRequestedAt": iso_from_epoch_ms(abort_requested_at_ms),
        "stopVerified": stop_verified,
        "stopLikelyFailed": stop_likely_failed,
        "verification": verification,
        "opencodeSession": build_session_summary(
            session_data,
            opencode_base_url=args.opencode_base_url,
            watcher_entry=watcher_entry,
        ),
        "watcherStillAttached": bool(watcher_entry),
        "registryPath": str(registry_path),
    }
    if watcher_entry:
        result["watcher"] = build_watcher_summary(watcher_entry)
    return result


def stop_watcher_command(args: argparse.Namespace) -> dict[str, Any]:
    registry_path = Path(args.registry_path).expanduser().resolve()
    with locked_registry(registry_path) as (registry, _path):
        refresh_registry_entries(registry, registry_path=registry_path)
        targets = select_running_entries(
            registry,
            watcher_id=args.watcher_id,
            opencode_session_id=args.opencode_session_id,
        )
        stopped_watchers = [
            stop_watcher_entry(
                entry,
                registry_path=registry_path,
                exit_reason="manager_stop_requested",
                stop_timeout_sec=args.stop_timeout_sec,
            )
            for entry in targets
        ]
        stopped_by_id = {entry.get("watcherId"): entry for entry in stopped_watchers}
        registry["watchers"] = [stopped_by_id.get(entry.get("watcherId"), entry) for entry in registry.get("watchers") or []]

    return {
        "kind": "opencode_manager_stop_watcher_v1",
        "registryPath": str(registry_path),
        "stopped": bool(stopped_watchers),
        "watcherCount": len(stopped_watchers),
        "watchers": [build_watcher_summary(entry) for entry in stopped_watchers],
        "target": {
            "watcherId": args.watcher_id,
            "opencodeSessionId": args.opencode_session_id,
        },
    }



def detach_command(args: argparse.Namespace) -> dict[str, Any]:
    registry_path = Path(args.registry_path).expanduser().resolve()
    with locked_registry(registry_path) as (registry, _path):
        refresh_registry_entries(registry, registry_path=registry_path)
        matching_entries = select_matching_entries(
            registry,
            watcher_id=args.watcher_id,
            opencode_session_id=args.opencode_session_id,
        )
        targets = [entry for entry in matching_entries if entry.get("watcherStatus") == "running"]
        detached_watchers = [
            stop_watcher_entry(
                entry,
                registry_path=registry_path,
                exit_reason="manager_detach",
                stop_timeout_sec=args.stop_timeout_sec,
            )
            for entry in targets
        ]
        detached_by_id = {entry.get("watcherId"): entry for entry in detached_watchers}
        registry["watchers"] = [detached_by_id.get(entry.get("watcherId"), entry) for entry in registry.get("watchers") or []]

    if detached_watchers:
        detach_status = "detached_now"
        result_watchers = detached_watchers
        no_active_openclaw_binding_remaining = True
        detach_summary = "Detached watcher binding(s) now; no active OpenClaw binding remains for the targeted OpenCode session."
    elif matching_entries:
        detach_status = "already_detached"
        result_watchers = matching_entries
        no_active_openclaw_binding_remaining = True
        detach_summary = "Watcher binding already detached; no active OpenClaw binding remains for the targeted OpenCode session."
    else:
        detach_status = "not_found"
        result_watchers = []
        no_active_openclaw_binding_remaining = False
        detach_summary = "No matching OpenClaw watcher binding was found for the requested OpenCode target."

    return {
        "kind": "opencode_manager_detach_v1",
        "registryPath": str(registry_path),
        "detachStatus": detach_status,
        "detachSummary": detach_summary,
        "detached": bool(detached_watchers),
        "targetFound": bool(matching_entries),
        "activeWatcherFound": bool(targets),
        "noActiveOpenclawBindingRemaining": no_active_openclaw_binding_remaining,
        "watcherCount": len(result_watchers),
        "detachedWatcherCount": len(detached_watchers),
        "watchers": [build_watcher_summary(entry) for entry in result_watchers],
        "target": {
            "watcherId": args.watcher_id,
            "opencodeSessionId": args.opencode_session_id,
        },
    }



def add_common_runtime_options(
    command_parser: argparse.ArgumentParser,
    *,
    require_session_id: bool = False,
    require_openclaw_session_key: bool = False,
    require_workspace: bool = False,
) -> None:
    command_parser.add_argument("--opencode-base-url", required=True)
    command_parser.add_argument("--opencode-token")
    command_parser.add_argument("--opencode-token-env")
    if require_workspace:
        command_parser.add_argument("--opencode-workspace", required=True)
    else:
        command_parser.add_argument("--opencode-workspace")
    if require_session_id:
        command_parser.add_argument("--opencode-session-id", required=True)
    else:
        command_parser.add_argument("--opencode-session-id")
    if require_openclaw_session_key:
        command_parser.add_argument("--openclaw-session-key", required=True)
    else:
        command_parser.add_argument("--openclaw-session-key")
    command_parser.add_argument("--openclaw-delivery-target")
    command_parser.add_argument("--watch-live", action="store_true")
    command_parser.add_argument("--watch-interval-sec", type=int, default=DEFAULT_WATCH_INTERVAL_SEC)
    command_parser.add_argument("--idle-timeout-sec", type=int, default=DEFAULT_IDLE_TIMEOUT_SEC)
    command_parser.add_argument("--notify-min-interval-sec", type=int, default=0)
    command_parser.add_argument("--notify-min-priority", choices=("low", "normal", "high"), default="low")
    command_parser.add_argument("--notify-min-severity", dest="notify_min_priority", choices=("low", "normal", "high"))
    command_parser.add_argument("--notify-keyword", action="append", default=[])
    command_parser.add_argument("--notify-filter-critical", action="store_true")
    command_parser.add_argument("--watch-message-limit", type=int, default=DEFAULT_MESSAGE_LIMIT)
    command_parser.add_argument("--watch-timeout-sec", type=int, default=DEFAULT_TIMEOUT_SEC)
    command_parser.add_argument("--registry-path", default=str(DEFAULT_REGISTRY_PATH))



def add_continue_watcher_options(command_parser: argparse.ArgumentParser) -> None:
    command_parser.add_argument("--openclaw-session-key")
    command_parser.add_argument("--openclaw-delivery-target")
    command_parser.add_argument("--ensure-watcher", action="store_true")
    command_parser.add_argument("--watch-live", dest="watch_live", action="store_const", const=True, default=None)
    command_parser.add_argument("--watch-dry-run", dest="watch_live", action="store_const", const=False)
    command_parser.add_argument("--watch-interval-sec", type=int)
    command_parser.add_argument("--idle-timeout-sec", type=int)
    command_parser.add_argument("--notify-min-interval-sec", type=int)
    command_parser.add_argument("--notify-min-priority", choices=("low", "normal", "high"))
    command_parser.add_argument("--notify-min-severity", dest="notify_min_priority", choices=("low", "normal", "high"))
    command_parser.add_argument("--notify-keyword", action="append")
    command_parser.add_argument("--notify-filter-critical", dest="notify_filter_critical", action="store_const", const=True, default=None)
    command_parser.add_argument("--notify-preserve-critical", dest="notify_filter_critical", action="store_const", const=False)
    command_parser.add_argument("--watch-message-limit", type=int)
    command_parser.add_argument("--watch-timeout-sec", type=int)
    command_parser.add_argument("--registry-path", default=str(DEFAULT_REGISTRY_PATH))



def add_watcher_target_options(command_parser: argparse.ArgumentParser) -> None:
    target_group = command_parser.add_mutually_exclusive_group(required=True)
    target_group.add_argument("--watcher-id")
    target_group.add_argument("--opencode-session-id")
    command_parser.add_argument("--registry-path", default=str(DEFAULT_REGISTRY_PATH))
    command_parser.add_argument("--stop-timeout-sec", type=int, default=DEFAULT_STOP_TIMEOUT_SEC)



def add_prompt_options(
    command_parser: argparse.ArgumentParser,
    *,
    text_flag: str,
    file_flag: str,
    text_help: str,
    file_help: str,
) -> None:
    prompt_group = command_parser.add_mutually_exclusive_group(required=True)
    prompt_group.add_argument(text_flag, help=text_help)
    prompt_group.add_argument(file_flag, help=file_help)



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Phase 2 OpenCode manager: create or attach watchers, continue existing sessions, inspect state, drill into recent history, request a real OpenCode session stop via abort API, stop/detach watchers, and recover local watcher registry entries."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    start_parser = sub.add_parser(
        "start",
        help="create a new OpenCode session, send the first prompt, and attach a watcher",
        description="Create a new OpenCode session, send the first prompt (inline or via --first-prompt-file), and attach a watcher.",
    )
    add_common_runtime_options(start_parser, require_openclaw_session_key=True, require_workspace=True)
    start_parser.add_argument("--title")
    add_prompt_options(
        start_parser,
        text_flag="--first-prompt",
        file_flag="--first-prompt-file",
        text_help="inline first prompt text",
        file_help="read first prompt text from a UTF-8 file path or '-' for stdin",
    )
    start_parser.set_defaults(func=start_command)

    attach_parser = sub.add_parser("attach", help="attach a watcher to an existing OpenCode session")
    add_common_runtime_options(attach_parser, require_session_id=True, require_openclaw_session_key=True)
    attach_parser.set_defaults(func=attach_command)

    continue_parser = sub.add_parser(
        "continue",
        help="send a follow-up prompt to an existing OpenCode session; normal agent usage should also ensure watcher routing via --ensure-watcher",
        description="Send a follow-up prompt (inline or via --follow-up-prompt-file) to an existing OpenCode session. For normal conversation-driven agent usage, also pass --ensure-watcher so later progress keeps routing back to the originating OpenClaw session; omit it only for explicit no-watcher/debug intent.",
    )
    continue_parser.add_argument("--opencode-base-url", required=True)
    continue_parser.add_argument("--opencode-token")
    continue_parser.add_argument("--opencode-token-env")
    continue_parser.add_argument("--opencode-workspace")
    continue_parser.add_argument("--opencode-session-id", required=True)
    add_prompt_options(
        continue_parser,
        text_flag="--follow-up-prompt",
        file_flag="--follow-up-prompt-file",
        text_help="inline follow-up prompt text",
        file_help="read follow-up prompt text from a UTF-8 file path or '-' for stdin",
    )
    add_continue_watcher_options(continue_parser)
    continue_parser.set_defaults(func=continue_command)

    list_sessions_parser = sub.add_parser("list-sessions", help="list OpenCode sessions for a workspace")
    list_sessions_parser.add_argument("--opencode-base-url", required=True)
    list_sessions_parser.add_argument("--opencode-token")
    list_sessions_parser.add_argument("--opencode-token-env")
    list_sessions_parser.add_argument("--opencode-workspace", required=True)
    list_sessions_parser.add_argument("--limit", type=int)
    list_sessions_parser.add_argument("--watch-timeout-sec", type=int, default=DEFAULT_TIMEOUT_SEC)
    list_sessions_parser.add_argument("--registry-path", default=str(DEFAULT_REGISTRY_PATH))
    list_sessions_parser.set_defaults(func=list_sessions_command)

    inspect_parser = sub.add_parser("inspect", help="normalize one OpenCode session into current status, completed work, and recent events")
    inspect_parser.add_argument("--opencode-base-url", required=True)
    inspect_parser.add_argument("--opencode-token")
    inspect_parser.add_argument("--opencode-token-env")
    inspect_parser.add_argument("--opencode-workspace")
    inspect_parser.add_argument("--opencode-session-id", required=True)
    inspect_parser.add_argument("--watch-message-limit", type=int, default=DEFAULT_MESSAGE_LIMIT)
    inspect_parser.add_argument("--watch-timeout-sec", type=int, default=DEFAULT_TIMEOUT_SEC)
    inspect_parser.add_argument("--registry-path", default=str(DEFAULT_REGISTRY_PATH))
    inspect_parser.set_defaults(func=inspect_command)

    inspect_history_parser = sub.add_parser(
        "inspect-history",
        help="drill into one recent OpenCode message with detailed text/tool/output context",
        description="Drill into one recent OpenCode message with detailed text/tool/output context without bloating the default inspect hot path.",
    )
    inspect_history_parser.add_argument("--opencode-base-url", required=True)
    inspect_history_parser.add_argument("--opencode-token")
    inspect_history_parser.add_argument("--opencode-token-env")
    inspect_history_parser.add_argument("--opencode-workspace")
    inspect_history_parser.add_argument("--opencode-session-id", required=True)
    selector_group = inspect_history_parser.add_mutually_exclusive_group()
    selector_group.add_argument("--message-id")
    selector_group.add_argument("--recent-index", type=int)
    selector_group.add_argument("--latest", action="store_true")
    inspect_history_parser.add_argument("--history-message-limit", type=int, default=DEFAULT_HISTORY_MESSAGE_LIMIT)
    inspect_history_parser.add_argument("--watch-timeout-sec", type=int, default=DEFAULT_TIMEOUT_SEC)
    inspect_history_parser.add_argument("--registry-path", default=str(DEFAULT_REGISTRY_PATH))
    inspect_history_parser.set_defaults(func=inspect_history_command)

    stop_session_parser = sub.add_parser(
        "stop-session",
        help="request a real OpenCode stop via POST /session/{id}/abort, validate scope, and verify whether it actually reached a stopped terminal state",
        description="Request a real OpenCode stop via the verified abort API instead of sending a pause-like follow-up prompt. If a watcher is already attached, leave it running so it can observe the resulting terminal state; use stop-watcher or detach separately only when monitoring should also stop. The manager rejects an explicit --opencode-workspace mismatch before aborting, and it performs post-abort verification because upstream abort acceptance / busy-clearing does not always mean the underlying tool run truly stopped; results are reported as verified, unverified, or likely failed rather than assuming success.",
    )
    stop_session_parser.add_argument("--opencode-base-url", required=True)
    stop_session_parser.add_argument("--opencode-token")
    stop_session_parser.add_argument("--opencode-token-env")
    stop_session_parser.add_argument("--opencode-workspace")
    stop_session_parser.add_argument("--opencode-session-id", required=True)
    stop_session_parser.add_argument("--watch-timeout-sec", type=int, default=DEFAULT_TIMEOUT_SEC)
    stop_session_parser.add_argument("--verify-wait-sec", type=float, default=3.0)
    stop_session_parser.add_argument("--verify-poll-sec", type=float, default=1.0)
    stop_session_parser.add_argument("--registry-path", default=str(DEFAULT_REGISTRY_PATH))
    stop_session_parser.set_defaults(func=stop_session_command)

    watchers_parser = sub.add_parser("list-watchers", help="show watcher registry entries")
    watchers_parser.add_argument("--registry-path", default=str(DEFAULT_REGISTRY_PATH))
    watchers_parser.add_argument("--include-exited", action="store_true")
    watchers_parser.set_defaults(func=list_watchers_command)

    stop_parser = sub.add_parser("stop-watcher", help="stop one running watcher cleanly without deleting the OpenCode session")
    add_watcher_target_options(stop_parser)
    stop_parser.set_defaults(func=stop_watcher_command)

    detach_parser = sub.add_parser("detach", help="detach a running watcher binding from an OpenCode session without deleting the OpenCode session")
    add_watcher_target_options(detach_parser)
    detach_parser.set_defaults(func=detach_command)

    return parser



def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    result = args.func(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
