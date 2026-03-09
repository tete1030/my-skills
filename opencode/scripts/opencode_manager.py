#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
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


def watcher_paths_for_id(watcher_id: str) -> dict[str, Path]:
    watcher_dir = DEFAULT_WATCHER_ROOT / watcher_id
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


def refresh_registry_entry(entry: dict[str, Any]) -> dict[str, Any]:
    refreshed = dict(entry)
    watch_state_path = Path(refreshed["watcherStatePath"])
    watch_state_document = load_json_object(watch_state_path) if watch_state_path.exists() else {}
    watch_state = watch_state_document.get("watchRunner") if isinstance(watch_state_document.get("watchRunner"), dict) else {}

    refreshed["watchProcessAlive"] = process_is_alive(refreshed.get("watchProcessId"))
    if refreshed["watchProcessAlive"]:
        refreshed["watcherStatus"] = "running"
        refreshed.pop("watchExitedAt", None)
        if not refreshed.get("watchStartedAt"):
            refreshed["watchStartedAt"] = now_iso()
    else:
        if refreshed.get("watcherStatus") in {"running", "starting"}:
            refreshed["watcherStatus"] = "exited"
            refreshed.setdefault("watchExitedAt", watch_state.get("lastExitedAt") or now_iso())
        refreshed["watchExitReason"] = watch_state.get("lastExitReason") or refreshed.get("watchExitReason") or "process_not_running"

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


def refresh_registry_entries(registry: dict[str, Any]) -> dict[str, Any]:
    registry["watchers"] = [refresh_registry_entry(entry) for entry in registry.get("watchers") or []]
    return registry


def find_active_watcher_entry(registry: dict[str, Any], opencode_session_id: str) -> dict[str, Any] | None:
    for entry in registry.get("watchers") or []:
        if entry.get("opencodeSessionId") == opencode_session_id and entry.get("watcherStatus") == "running":
            return entry
    return None


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


def build_inspection(
    session_data: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    watcher_entry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    todo = snapshot.get("todo") if isinstance(snapshot.get("todo"), dict) else {}
    latest_message = snapshot.get("latestMessage") if isinstance(snapshot.get("latestMessage"), dict) else {}
    completed_work = []
    for item in todo.get("items") or []:
        if isinstance(item, dict) and item.get("status") == "completed":
            completed_work.append(item.get("content"))

    result = {
        "opencodeSession": build_session_summary(session_data, watcher_entry=watcher_entry),
        "currentStatus": latest_message.get("status") or todo.get("phase") or "unknown",
        "currentPhase": todo.get("phase"),
        "hasPendingWork": todo.get("hasPendingWork"),
        "allTodosCompleted": todo.get("allCompleted"),
        "completedWork": completed_work[-5:] or None,
        "latestMeaningfulPreview": snapshot.get("latestTextPreview") or snapshot.get("latestAssistantTextPreview"),
        "latestUserInputSummary": snapshot.get("latestUserInputSummary"),
        "recentEventSummary": snapshot.get("accumulatedEventSummary"),
        "recentEvents": snapshot.get("eventLedger"),
        "latestMessage": latest_message or None,
        "snapshotErrors": snapshot.get("errors") or None,
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
) -> dict[str, Any]:
    paths = watcher_paths_for_id(watcher_id)
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
        refresh_registry_entries(registry)
        active_entry = find_active_watcher_entry(registry, opencode_session_id)
        if active_entry:
            raise RuntimeError(
                f"watcher lock active for opencodeSessionId={opencode_session_id}: watcherId={active_entry.get('watcherId')}"
            )

        watcher_id = f"ow_{uuid.uuid4().hex[:12]}"
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
        return refresh_registry_entry(entry)


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
        refresh_registry_entries(registry)
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
        refresh_registry_entries(registry)
        watcher_entry = next(
            (entry for entry in registry.get("watchers") or [] if entry.get("opencodeSessionId") == args.opencode_session_id and entry.get("watcherStatus") == "running"),
            None,
        )

    return {
        "kind": "opencode_manager_inspect_v1",
        "inspection": build_inspection(session_data, snapshot, watcher_entry=watcher_entry),
    }


def list_watchers_command(args: argparse.Namespace) -> dict[str, Any]:
    registry_path = Path(args.registry_path).expanduser().resolve()
    with locked_registry(registry_path) as (registry, _path):
        refresh_registry_entries(registry)
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

    return {
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

    return {
        "kind": "opencode_manager_attach_v1",
        "opencodeSession": build_session_summary(session_data, watcher_entry=watcher_entry),
        "watcher": build_watcher_summary(watcher_entry),
        "registryPath": str(registry_path),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Phase 1 OpenCode manager: create or attach watchers, list sessions, inspect session state, and report local watcher registry entries."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common_runtime_options(command_parser: argparse.ArgumentParser, *, require_session_id: bool = False, require_openclaw_session_key: bool = False, require_workspace: bool = False) -> None:
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

    start_parser = sub.add_parser("start", help="create a new OpenCode session, send the first prompt, and attach a watcher")
    add_common_runtime_options(start_parser, require_openclaw_session_key=True, require_workspace=True)
    start_parser.add_argument("--title")
    start_parser.add_argument("--first-prompt", required=True)
    start_parser.set_defaults(func=start_command)

    attach_parser = sub.add_parser("attach", help="attach a watcher to an existing OpenCode session")
    add_common_runtime_options(attach_parser, require_session_id=True, require_openclaw_session_key=True)
    attach_parser.set_defaults(func=attach_command)

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

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    result = args.func(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
