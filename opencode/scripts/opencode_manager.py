#!/usr/bin/env python3
import argparse
import json
import os
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

from opencode_api_client import OpenCodeClient
from opencode_snapshot import build_compact_snapshot

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
REPO_ROOT = SKILL_ROOT.parent
WATCH_RUNTIME = SCRIPT_DIR / "opencode_watch_runtime.py"
DEFAULT_REGISTRY_PATH = REPO_ROOT / ".local" / "opencode-manager" / "registry.json"
DEFAULT_WATCHER_ROOT = REPO_ROOT / ".local" / "opencode-manager" / "watchers"
DEFAULT_WATCH_INTERVAL_SEC = 60
DEFAULT_IDLE_TIMEOUT_SEC = 900
DEFAULT_MESSAGE_LIMIT = 10
DEFAULT_TIMEOUT_SEC = 20
DEFAULT_STOP_TIMEOUT_SEC = 10
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



def build_session_summary(session_data: dict[str, Any], *, watcher_entry: dict[str, Any] | None = None) -> dict[str, Any]:
    summary = session_data.get("summary") if isinstance(session_data.get("summary"), dict) else {}
    time_data = session_data.get("time") if isinstance(session_data.get("time"), dict) else {}
    result = {
        "opencodeSessionId": session_data.get("id") or session_data.get("sessionID") or session_data.get("sessionId"),
        "slug": session_data.get("slug"),
        "title": session_data.get("title"),
        "opencodeWorkspace": session_data.get("directory"),
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



def build_recent_notable_events(snapshot: dict[str, Any], *, limit: int = 5) -> list[dict[str, Any]] | None:
    events = snapshot.get("eventLedger") if isinstance(snapshot.get("eventLedger"), list) else []
    visible = [event for event in events if isinstance(event, dict) and not event.get("ignored")]
    if not visible:
        return None

    notable = []
    for event in visible[-limit:]:
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

    completed_tool_statuses = {"completed", "done", "finished", "succeeded", "success", "ok"}
    events = snapshot.get("eventLedger") if isinstance(snapshot.get("eventLedger"), list) else []
    for event in events:
        if not isinstance(event, dict) or event.get("ignored"):
            continue
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
            "watchExitReason": watcher_entry.get("watchExitReason"),
        }.items()
        if value is not None
    }



def build_inspection(
    session_data: dict[str, Any],
    snapshot: dict[str, Any],
    *,
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
    latest_meaningful_preview = snapshot.get("latestTextPreview") or snapshot.get("latestAssistantTextPreview")
    rehydration = {
        "version": "v1",
        "purpose": "current_state_rebuild",
        "snapshotCoverage": build_snapshot_coverage(snapshot, requested_message_limit=requested_message_limit),
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
            }.items()
            if value is not None
        },
        "latestUserIntent": snapshot.get("latestUserInputSummary"),
        "recentCompletedWork": build_recent_completed_work(snapshot, todo),
        "recentNotableEvents": build_recent_notable_events(snapshot),
        "watcherState": build_rehydration_watcher_state(watcher_entry),
    }

    result = {
        "opencodeSession": build_session_summary(session_data, watcher_entry=watcher_entry),
        "currentStatus": current_status,
        "currentPhase": todo.get("phase"),
        "hasPendingWork": todo.get("hasPendingWork"),
        "allTodosCompleted": todo.get("allCompleted"),
        "completedWork": completed_work[-5:] or None,
        "latestMeaningfulPreview": latest_meaningful_preview,
        "latestUserInputSummary": snapshot.get("latestUserInputSummary"),
        "recentEventSummary": snapshot.get("accumulatedEventSummary"),
        "recentEvents": snapshot.get("eventLedger"),
        "latestMessage": latest_message or None,
        "snapshotErrors": snapshot.get("errors") or None,
        "rehydration": {key: value for key, value in rehydration.items() if value is not None},
    }
    if watcher_entry:
        result["watcher"] = build_watcher_summary(watcher_entry)
    return {key: value for key, value in result.items() if value is not None}



def build_watcher_summary(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "watcherId": entry.get("watcherId"),
            "watcherStatus": entry.get("watcherStatus"),
            "watchProcessId": entry.get("watchProcessId"),
            "watchProcessAlive": entry.get("watchProcessAlive"),
            "opencodeSessionId": entry.get("opencodeSessionId"),
            "opencodeWorkspace": entry.get("opencodeWorkspace"),
            "openclawSessionKey": entry.get("openclawSessionKey"),
            "openclawDeliveryTarget": entry.get("openclawDeliveryTarget"),
            "watchLive": entry.get("watchLive"),
            "watchIntervalSec": entry.get("watchIntervalSec"),
            "idleTimeoutSec": entry.get("idleTimeoutSec"),
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
        return {
            "progressSource": "watcher",
            "agentShouldPoll": False,
            "recommendedNextAction": "wait_for_runtime_updates",
            "turnShouldEnd": True,
            "completionCheckOwner": "watcher_runtime_updates",
            "disallowImmediateCompletionCheck": True,
            "recommendedUserVisibleAction": "acknowledge_handoff_then_end_turn",
            "userFacingAck": WATCHER_HANDOFF_ACK,
        }
    if watcher_running:
        return {
            "progressSource": "manager_result_only",
            "agentShouldPoll": False,
            "recommendedNextAction": "acknowledge_no_live_watcher",
            "turnShouldEnd": True,
            "completionCheckOwner": "future_explicit_turn",
            "disallowImmediateCompletionCheck": False,
            "recommendedUserVisibleAction": "acknowledge_no_live_watcher",
            "userFacingAck": NON_LIVE_WATCHER_ACK,
        }
    if watcher_requested:
        return {
            "progressSource": "manager_result_only",
            "agentShouldPoll": False,
            "recommendedNextAction": "acknowledge_missing_watcher",
            "turnShouldEnd": True,
            "completionCheckOwner": "future_explicit_turn",
            "disallowImmediateCompletionCheck": False,
            "recommendedUserVisibleAction": "acknowledge_missing_watcher",
            "userFacingAck": MISSING_WATCHER_ACK,
        }
    return {
        "progressSource": "manager_result_only",
        "agentShouldPoll": False,
        "recommendedNextAction": "acknowledge_async_without_watcher",
        "turnShouldEnd": True,
        "completionCheckOwner": "future_explicit_turn",
        "disallowImmediateCompletionCheck": False,
        "recommendedUserVisibleAction": "acknowledge_async_without_watcher",
        "userFacingAck": NO_WATCHER_ACK,
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
    watch_message_limit: int,
    watch_timeout_sec: int,
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
    watch_message_limit: int,
    watch_timeout_sec: int,
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
            build_session_summary(session, watcher_entry=watcher_by_session_id.get(session.get("id")))
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
            watcher_entry=watcher_entry,
            requested_message_limit=args.watch_message_limit,
        ),
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
        parts=[{"type": "text", "text": args.first_prompt}],
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
        watch_message_limit=args.watch_message_limit,
        watch_timeout_sec=args.watch_timeout_sec,
    )

    result = {
        "kind": "opencode_manager_start_v1",
        "opencodeSession": build_session_summary(created_session, watcher_entry=watcher_entry),
        "firstPrompt": {
            "deliveryMode": "prompt_async",
            "accepted": True,
            "promptPreview": preview_text(args.first_prompt),
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
        watch_message_limit=args.watch_message_limit,
        watch_timeout_sec=args.watch_timeout_sec,
    )

    snapshot, _errors = build_compact_snapshot(client, args.opencode_session_id, message_limit=args.watch_message_limit)

    return {
        "kind": "opencode_manager_attach_v1",
        "opencodeSession": build_session_summary(session_data, watcher_entry=watcher_entry),
        "watcher": build_watcher_summary(watcher_entry),
        "inspection": build_inspection(
            session_data,
            snapshot,
            watcher_entry=watcher_entry,
            requested_message_limit=args.watch_message_limit,
        ),
        "registryPath": str(registry_path),
    }



def continue_command(args: argparse.Namespace) -> dict[str, Any]:
    registry_path = Path(args.registry_path).expanduser().resolve()
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
        parts=[{"type": "text", "text": args.follow_up_prompt}],
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
    session_summary = build_session_summary(session_data, watcher_entry=watcher_entry)

    result = {
        "kind": "opencode_manager_continue_v1",
        "opencodeSession": session_summary,
        "followUpPrompt": {
            "deliveryMode": "prompt_async",
            "accepted": True,
            "promptPreview": preview_text(args.follow_up_prompt),
        },
        "ensureWatcherRequested": bool(args.ensure_watcher),
        "registryPath": str(registry_path),
    }
    if watcher_payload:
        result["watcher"] = watcher_payload["watcher"]
        result["watcherAlreadyRunning"] = bool(watcher_payload["alreadyRunning"])
    result.update(build_agent_handoff_contract(watcher_entry=watcher_entry, watcher_requested=bool(args.ensure_watcher)))
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
    command_parser.add_argument("--watch-message-limit", type=int)
    command_parser.add_argument("--watch-timeout-sec", type=int)
    command_parser.add_argument("--registry-path", default=str(DEFAULT_REGISTRY_PATH))



def add_watcher_target_options(command_parser: argparse.ArgumentParser) -> None:
    target_group = command_parser.add_mutually_exclusive_group(required=True)
    target_group.add_argument("--watcher-id")
    target_group.add_argument("--opencode-session-id")
    command_parser.add_argument("--registry-path", default=str(DEFAULT_REGISTRY_PATH))
    command_parser.add_argument("--stop-timeout-sec", type=int, default=DEFAULT_STOP_TIMEOUT_SEC)



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Phase 2 OpenCode manager: create or attach watchers, continue existing sessions, inspect state, stop/detach watchers, and recover local watcher registry entries."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    start_parser = sub.add_parser("start", help="create a new OpenCode session, send the first prompt, and attach a watcher")
    add_common_runtime_options(start_parser, require_openclaw_session_key=True, require_workspace=True)
    start_parser.add_argument("--title")
    start_parser.add_argument("--first-prompt", required=True)
    start_parser.set_defaults(func=start_command)

    attach_parser = sub.add_parser("attach", help="attach a watcher to an existing OpenCode session")
    add_common_runtime_options(attach_parser, require_session_id=True, require_openclaw_session_key=True)
    attach_parser.set_defaults(func=attach_command)

    continue_parser = sub.add_parser(
        "continue",
        help="send --follow-up-prompt to an existing OpenCode session and optionally ensure a watcher via --ensure-watcher",
        description="Send --follow-up-prompt to an existing OpenCode session and optionally ensure a watcher via --ensure-watcher.",
    )
    continue_parser.add_argument("--opencode-base-url", required=True)
    continue_parser.add_argument("--opencode-token")
    continue_parser.add_argument("--opencode-token-env")
    continue_parser.add_argument("--opencode-workspace")
    continue_parser.add_argument("--opencode-session-id", required=True)
    continue_parser.add_argument("--follow-up-prompt", required=True)
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
