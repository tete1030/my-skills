#!/usr/bin/env python3
import argparse
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from opencode_api_client import OpenCodeClient

ACTIVE_TODO_STATUSES = {"in_progress", "active", "running", "current"}
PENDING_TODO_STATUSES = {"pending", "todo", "queued", "next", "open"}
COMPLETED_TODO_STATUSES = {"completed", "done", "finished", "closed", "resolved"}
FAILED_MESSAGE_STATUSES = {"error", "failed", "failure", "cancelled", "canceled"}
ABORTED_MESSAGE_ERROR_NAMES = {"messageabortederror"}
RUNNING_TOOL_STATUSES = {"queued", "pending", "running", "active", "started", "in_progress"}
COMPLETED_TOOL_STATUSES = {"completed", "done", "finished", "succeeded", "success", "ok"}

EVENT_LEDGER_MAX = 12
EVENT_SELECTION_MAX = 4
EVENT_SUMMARY_LIMIT = 260
EVENT_ITEM_LIMIT = 90

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
HEXISH_RE = re.compile(r"^[0-9a-f]{7,}$", re.IGNORECASE)


def strip_ansi(value: str) -> str:
    return ANSI_RE.sub("", value)



def truncate_text(value: Any, limit: int = 200) -> Optional[str]:
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1] + "…"



def preview_segments(value: Any) -> List[str]:
    if value is None:
        return []
    text = strip_ansi(str(value)).replace("\r", "\n")
    raw_parts: List[str] = []
    for chunk in text.split("\n"):
        raw_parts.extend(chunk.split(" --- "))
    parts = []
    for part in raw_parts:
        cleaned = " ".join(part.split()).strip()
        if cleaned:
            parts.append(cleaned)
    return parts



def is_noise_segment(segment: str) -> bool:
    s = segment.strip()
    low = s.lower()
    if not s:
        return True
    if low.startswith("pwd=") or low.startswith("cwd="):
        return True
    if low.startswith("assistant to=") or low.startswith("tool to="):
        return True
    if "functions.process" in low or "commentary to=" in low:
        return True
    if HEXISH_RE.match(s):
        return True
    if len(s) < 3:
        return True
    return False



def score_segment(segment: str, index: int) -> tuple[int, int]:
    s = segment.strip()
    low = s.lower()
    score = 0

    if any(ch.isalpha() for ch in s):
        score += 2
    if " " in s:
        score += 1
    if any(ch in s for ch in ".,:;!?，。:：()[]{}"):
        score += 1
    if 6 <= len(s) <= 140:
        score += 1
    if len(s) > 180:
        score -= 2
    if len(s) > 120 and " " not in s:
        score -= 2
    if low.startswith("return exactly") or low.startswith("assistant to=") or low.startswith("tool to="):
        score -= 2

    return (score, index)



def clean_preview(value: Any, limit: int = 200) -> Optional[str]:
    segments = [seg for seg in preview_segments(value) if not is_noise_segment(seg)]
    if not segments:
        return truncate_text(value, limit=limit)

    scored = [(score_segment(segment, index), segment) for index, segment in enumerate(segments)]
    winners = [item for item in scored if item[0][0] >= 2]
    if not winners:
        winners = [max(scored, key=lambda item: item[0])]
    else:
        winners = sorted(winners, key=lambda item: item[0], reverse=True)[:2]

    winners = sorted(winners, key=lambda item: item[0][1])
    text = " | ".join(segment for _score, segment in winners)
    return truncate_text(text, limit=limit)



def get_nested(obj: Any, *keys: str) -> Any:
    cur = obj
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur



def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False



def extract_ignored_flag(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False
    if truthy(obj.get("ignored")):
        return True
    for path in [
        ("info", "ignored"),
        ("metadata", "ignored"),
        ("meta", "ignored"),
        ("state", "ignored"),
        ("state", "metadata", "ignored"),
    ]:
        if truthy(get_nested(obj, *path)):
            return True
    return False



def normalize_message_status(
    finish: Any,
    completed_at: Any,
    tool_statuses: Iterable[str],
    part_types: Iterable[str],
    error_name: Any = None,
    error_message: Any = None,
) -> str:
    finish_norm = str(finish or "").strip().lower()
    statuses = [str(status or "").strip().lower() for status in tool_statuses if status]
    error_name_norm = str(error_name or "").strip().lower()
    error_message_norm = str(error_message or "").strip().lower()
    if (
        finish_norm in FAILED_MESSAGE_STATUSES
        or any(status in FAILED_MESSAGE_STATUSES for status in statuses)
        or error_name_norm in ABORTED_MESSAGE_ERROR_NAMES
        or (error_name_norm and "abort" in error_name_norm)
        or (error_message_norm and "abort" in error_message_norm)
    ):
        return "failed"
    if not completed_at and any(status in RUNNING_TOOL_STATUSES for status in statuses):
        return "running"
    if completed_at or finish_norm:
        return "completed"
    if part_types:
        return "running"
    return "unknown"



def compact_latest_message(msg: Any) -> Dict[str, Any]:
    if not isinstance(msg, dict):
        return {"raw": msg}

    info = msg.get("info") if isinstance(msg.get("info"), dict) else {}
    parts = msg.get("parts") if isinstance(msg.get("parts"), list) else []

    role = info.get("role") or msg.get("role")
    created = get_nested(info, "time", "created") or msg.get("created")
    completed_at = get_nested(info, "time", "completed") or msg.get("completed")
    finish = info.get("finish") or msg.get("finish")
    message_id = info.get("id") or msg.get("id")
    ignored = extract_ignored_flag(msg) or extract_ignored_flag(info)
    info_error = info.get("error") if isinstance(info.get("error"), dict) else {}
    error_name = info_error.get("name")
    error_message = get_nested(info_error, "data", "message") or info_error.get("message")
    aborted = str(error_name or "").strip().lower() in ABORTED_MESSAGE_ERROR_NAMES or "abort" in str(error_message or "").strip().lower()
    error_preview = truncate_text(
        ": ".join(part for part in [str(error_name).strip() if error_name else None, str(error_message).strip() if error_message else None] if part),
        limit=200,
    )

    part_types: List[str] = []
    tool_names: List[str] = []
    tool_statuses: List[str] = []
    last_text_preview = None
    last_tool_output_preview = None

    for part in parts:
        if not isinstance(part, dict):
            continue
        part_type = str(part.get("type") or "").strip()
        if part_type:
            part_types.append(part_type)
        if part_type == "text":
            preview = clean_preview(part.get("text"))
            if preview:
                last_text_preview = preview
        elif part_type == "tool":
            tool_name = part.get("tool")
            if tool_name:
                tool_names.append(str(tool_name))
            tool_state = part.get("state") if isinstance(part.get("state"), dict) else {}
            tool_status = str(tool_state.get("status") or "").strip().lower()
            if tool_status:
                tool_statuses.append(tool_status)
            preview = clean_preview(tool_state.get("output") or get_nested(tool_state, "metadata", "output"))
            if preview:
                last_tool_output_preview = preview
        elif part_type == "step-finish" and not finish:
            finish = part.get("reason")

    normalized_status = normalize_message_status(
        finish=finish,
        completed_at=completed_at,
        tool_statuses=tool_statuses,
        part_types=part_types,
        error_name=error_name,
        error_message=error_message,
    )

    out: Dict[str, Any] = {}
    for key, value in {
        "id": message_id,
        "role": role,
        "created": created,
        "completedAt": completed_at,
        "status": normalized_status,
        "type": part_types[-1] if part_types else None,
        "finish": finish,
        "completed": bool(completed_at),
        "ignored": ignored,
        "partTypes": part_types or None,
        "hasText": bool(last_text_preview),
        "hasToolCalls": "tool" in part_types if part_types else False,
        "toolNames": tool_names or None,
        "toolStatuses": tool_statuses or None,
        "message.role": role,
        "message.stopReason": finish,
        "message.timestamp": created,
        "message.lastContentType": "text" if last_text_preview else (part_types[-1] if part_types else None),
        "message.lastTextPreview": last_text_preview,
        "message.errorName": error_name,
        "message.errorMessage": error_message,
        "message.aborted": aborted or None,
        "textPreview": last_text_preview,
        "toolOutputPreview": last_tool_output_preview,
        "errorPreview": error_preview,
    }.items():
        if value is None:
            continue
        out[key] = value
    return out



def normalize_todo_item(item: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    content = item.get("content") or item.get("title") or item.get("name")
    status = str(item.get("status") or "").strip().lower() or None
    priority = item.get("priority")
    normalized: Dict[str, Any] = {}
    if content is not None:
        normalized["content"] = str(content)
    if status is not None:
        normalized["status"] = status
    if priority is not None:
        normalized["priority"] = priority
    return normalized or None



def normalize_todo(todo: Any) -> Any:
    if todo is None:
        return None
    if isinstance(todo, dict) and "items" in todo and isinstance(todo.get("items"), list):
        items = [normalize_todo_item(item) for item in todo.get("items") or []]
        items = [item for item in items if item]
    elif isinstance(todo, list):
        items = [normalize_todo_item(item) for item in todo]
        items = [item for item in items if item]
    elif isinstance(todo, dict):
        # Preserve dict-shaped payloads while still surfacing phase-like fields if present.
        normalized = {k: v for k, v in todo.items() if v is not None}
        if "phase" not in normalized:
            for key in ["title", "name", "current"]:
                if normalized.get(key):
                    normalized["phase"] = normalized[key]
                    break
        return normalized
    else:
        return {"raw": todo}

    current = next((item for item in items if item.get("status") in ACTIVE_TODO_STATUSES), None)
    next_item = next((item for item in items if item.get("status") in PENDING_TODO_STATUSES), None)
    latest_completed = next((item for item in reversed(items) if item.get("status") in COMPLETED_TODO_STATUSES), None)

    counts = {
        "total": len(items),
        "active": sum(1 for item in items if item.get("status") in ACTIVE_TODO_STATUSES),
        "pending": sum(1 for item in items if item.get("status") in PENDING_TODO_STATUSES),
        "completed": sum(1 for item in items if item.get("status") in COMPLETED_TODO_STATUSES),
    }
    counts["other"] = counts["total"] - counts["active"] - counts["pending"] - counts["completed"]

    phase = None
    for candidate in (current, next_item, latest_completed):
        if isinstance(candidate, dict) and candidate.get("content"):
            phase = candidate["content"]
            break

    return {
        "items": items,
        "counts": counts,
        "current": current,
        "next": next_item,
        "latestCompleted": latest_completed,
        "phase": phase,
        "hasPendingWork": bool(counts["active"] or counts["pending"]),
        "allCompleted": bool(items) and counts["completed"] == len(items),
        "isEmpty": not items,
    }



def shorten_path(value: Any, limit: int = EVENT_ITEM_LIMIT) -> Optional[str]:
    text = " ".join(str(value).split())
    if not text:
        return None
    if len(text) <= limit:
        return text
    parts = [part for part in re.split(r"[\\/]", text) if part]
    if len(parts) >= 2:
        return truncate_text(f"…/{'/'.join(parts[-2:])}", limit=limit)
    return truncate_text(text, limit=limit)



def compact_path_value(value: Any, limit: int = EVENT_ITEM_LIMIT) -> Optional[str]:
    if isinstance(value, (list, tuple)):
        items = [shorten_path(item, limit=max(24, limit // 2)) for item in value]
        items = [item for item in items if item]
        if not items:
            return None
        joined = ", ".join(items[:2])
        if len(items) > 2:
            joined += ", …"
        return truncate_text(joined, limit=limit)
    return shorten_path(value, limit=limit)



def extract_read_target(part: Any, tool_state: Any) -> Optional[str]:
    candidates = [
        part,
        part.get("input") if isinstance(part, dict) else None,
        part.get("arguments") if isinstance(part, dict) else None,
        part.get("args") if isinstance(part, dict) else None,
        tool_state,
        tool_state.get("input") if isinstance(tool_state, dict) else None,
        tool_state.get("metadata") if isinstance(tool_state, dict) else None,
        get_nested(tool_state, "metadata", "input") if isinstance(tool_state, dict) else None,
    ]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for key in ("path", "file_path", "filePath", "paths", "file", "files", "target"):
            if candidate.get(key):
                return compact_path_value(candidate.get(key))
    return None



def infer_event_kind(role: str, part: Any) -> Optional[str]:
    part_type = str(part.get("type") or "").strip().lower()
    tool_name = str(part.get("tool") or "").strip().lower()

    if part_type == "text":
        return "user_input" if role == "user" else "text"
    if part_type == "tool":
        if tool_name in {"read", "view", "cat"}:
            return "read"
        if tool_name.startswith("prune") or tool_name in {"compact", "summarize", "summary"}:
            return "prune"
        return "tool"
    if "prune" in part_type:
        return "prune"
    return None



def build_event_record(message: Any, part: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(message, dict) or not isinstance(part, dict):
        return None

    info = message.get("info") if isinstance(message.get("info"), dict) else {}
    role = str(info.get("role") or message.get("role") or "")
    message_id = info.get("id") or message.get("id")
    created = get_nested(info, "time", "created") or message.get("created")
    tool_state = part.get("state") if isinstance(part.get("state"), dict) else {}
    tool_name = str(part.get("tool") or "").strip() or None
    tool_status = str(tool_state.get("status") or "").strip().lower() or None
    kind = infer_event_kind(role.lower(), part)
    if not kind:
        return None

    ignored = any([
        extract_ignored_flag(message),
        extract_ignored_flag(info),
        extract_ignored_flag(part),
        extract_ignored_flag(tool_state),
    ])

    summary = None
    if kind in {"user_input", "text"}:
        summary = clean_preview(part.get("text"), limit=EVENT_ITEM_LIMIT)
    elif kind == "read":
        summary = extract_read_target(part, tool_state) or clean_preview(
            tool_state.get("output") or get_nested(tool_state, "metadata", "output"),
            limit=EVENT_ITEM_LIMIT,
        )
    elif kind == "prune":
        summary = clean_preview(
            tool_state.get("output") or get_nested(tool_state, "metadata", "output") or part.get("text") or part.get("reason"),
            limit=EVENT_ITEM_LIMIT,
        ) or "context trimmed"
    elif kind == "tool":
        summary = clean_preview(
            tool_state.get("output") or get_nested(tool_state, "metadata", "output"),
            limit=EVENT_ITEM_LIMIT,
        )
        if not summary and tool_status:
            summary = tool_status
        if not summary and tool_name:
            summary = tool_name

    if not summary:
        return None

    out: Dict[str, Any] = {
        "kind": kind,
        "messageId": message_id,
        "role": role,
        "summary": summary,
        "ignored": ignored,
    }
    if created is not None:
        out["created"] = created
    if tool_name:
        out["toolName"] = tool_name
    if tool_status:
        out["toolStatus"] = tool_status
    return out



def extract_message_events(message: Any) -> List[Dict[str, Any]]:
    if not isinstance(message, dict):
        return []
    parts = message.get("parts") if isinstance(message.get("parts"), list) else []
    events: List[Dict[str, Any]] = []
    for part in parts:
        event = build_event_record(message, part)
        if event:
            events.append(event)
    return events



def collapse_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    collapsed: List[Dict[str, Any]] = []
    for event in events:
        signature = (
            event.get("kind"),
            event.get("messageId"),
            event.get("toolName"),
            event.get("summary"),
            event.get("ignored"),
        )
        if collapsed:
            previous = collapsed[-1]
            previous_signature = (
                previous.get("kind"),
                previous.get("messageId"),
                previous.get("toolName"),
                previous.get("summary"),
                previous.get("ignored"),
            )
            if signature == previous_signature:
                continue
        collapsed.append(event)
    return collapsed



def format_event_fragment(event: Dict[str, Any]) -> Optional[str]:
    summary = truncate_text(event.get("summary"), limit=EVENT_ITEM_LIMIT)
    if not summary:
        return None

    kind = event.get("kind")
    if kind == "tool" and event.get("toolName"):
        label = f"tool[{event['toolName']}]"
    else:
        label = {
            "user_input": "user",
            "text": "text",
            "read": "read",
            "prune": "prune",
            "tool": "tool",
        }.get(kind, kind or "event")
    return f"{label}: {summary}"



def summarize_event_ledger(events: List[Dict[str, Any]], limit: int = EVENT_SUMMARY_LIMIT) -> Optional[str]:
    visible = collapse_events([event for event in events if not event.get("ignored")])
    if not visible:
        return None

    selected = visible[-EVENT_SELECTION_MAX:]
    latest_user = next((event for event in reversed(visible) if event.get("kind") == "user_input"), None)
    if latest_user and latest_user not in selected:
        selected = [latest_user, *selected[-(EVENT_SELECTION_MAX - 1):]]

    deduped: List[Dict[str, Any]] = []
    seen = set()
    for event in selected:
        signature = (event.get("messageId"), event.get("kind"), event.get("toolName"), event.get("summary"))
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(event)

    selected_events = list(deduped)
    fragments = [fragment for fragment in (format_event_fragment(event) for event in selected_events) if fragment]
    if not fragments:
        return None

    while len(" | ".join(fragments)) > limit and len(selected_events) > 1:
        drop_index = 0
        if selected_events[0].get("kind") == "user_input" and len(selected_events) > 2:
            drop_index = 1
        del selected_events[drop_index]
        fragments = [fragment for fragment in (format_event_fragment(event) for event in selected_events) if fragment]
    return truncate_text(" | ".join(fragments), limit=limit)



def summarize_message_window(normalized_messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    oldest = normalized_messages[0] if normalized_messages else {}
    newest = normalized_messages[-1] if normalized_messages else {}
    return {
        "observedMessageCount": len(normalized_messages),
        "oldestMessageId": oldest.get("id"),
        "oldestMessageRole": oldest.get("role"),
        "oldestMessageCreated": oldest.get("created"),
        "newestMessageId": newest.get("id"),
        "newestMessageRole": newest.get("role"),
        "newestMessageCreated": newest.get("created"),
    }



def summarize_recent_messages(messages: Any) -> Dict[str, Any]:
    message_list = messages if isinstance(messages, list) else ([messages] if messages is not None else [])
    normalized_messages = [compact_latest_message(message) for message in message_list]
    per_message_events = [extract_message_events(message) for message in message_list]

    def message_has_relevant_terminal_signal(message: Dict[str, Any]) -> bool:
        status = str(message.get("status") or "").strip().lower()
        return bool(
            message.get("message.aborted")
            or message.get("message.errorName")
            or message.get("errorPreview")
            or status in {"failed", "error", "cancelled", "canceled"}
        )

    relevant_indexes = [
        index
        for index, events in enumerate(per_message_events)
        if any(not event.get("ignored") for event in events) or message_has_relevant_terminal_signal(normalized_messages[index])
    ]
    latest_index = len(normalized_messages) - 1 if normalized_messages else None
    latest_relevant_index = relevant_indexes[-1] if relevant_indexes else latest_index
    latest = normalized_messages[latest_relevant_index] if latest_relevant_index is not None else {}

    searchable_indexes = relevant_indexes or list(range(len(normalized_messages)))

    latest_text_preview_any = None
    latest_text_preview_any_message_id = None
    latest_text_preview_any_role = None
    latest_assistant_text_preview = None
    latest_assistant_text_preview_message_id = None

    for index in reversed(searchable_indexes):
        message = normalized_messages[index]
        preview = message.get("message.lastTextPreview")
        if preview and latest_text_preview_any is None:
            latest_text_preview_any = preview
            latest_text_preview_any_message_id = message.get("id")
            latest_text_preview_any_role = message.get("role")
        if preview and message.get("role") == "assistant" and latest_assistant_text_preview is None:
            latest_assistant_text_preview = preview
            latest_assistant_text_preview_message_id = message.get("id")

    latest_tool_output_preview = latest.get("toolOutputPreview")
    latest_text_preview = (
        latest.get("message.lastTextPreview")
        or latest.get("errorPreview")
        or latest_assistant_text_preview
        or latest_tool_output_preview
        or latest_text_preview_any
    )
    latest_text_preview_message_id = (
        latest.get("id") if latest.get("message.lastTextPreview") or latest.get("errorPreview") or latest_tool_output_preview else latest_assistant_text_preview_message_id or latest_text_preview_any_message_id
    )
    latest_text_preview_role = (
        latest.get("role") if latest.get("message.lastTextPreview") or latest.get("errorPreview") or latest_tool_output_preview else ("assistant" if latest_assistant_text_preview else latest_text_preview_any_role)
    )

    event_ledger = collapse_events([event for events in per_message_events for event in events])[-EVENT_LEDGER_MAX:]
    accumulated_event_summary = summarize_event_ledger(event_ledger)
    latest_user_input = next((event for event in reversed(event_ledger) if event.get("kind") == "user_input" and not event.get("ignored")), None)
    message_window = summarize_message_window(normalized_messages)

    return {
        "latestMessage": latest,
        "latestTextPreview": latest_text_preview,
        "latestTextPreviewMessageId": latest_text_preview_message_id,
        "latestTextPreviewRole": latest_text_preview_role,
        "latestAssistantTextPreview": latest_assistant_text_preview,
        "latestAssistantTextPreviewMessageId": latest_assistant_text_preview_message_id,
        "latestToolOutputPreview": latest_tool_output_preview,
        "latestUserInputSummary": latest_user_input.get("summary") if latest_user_input else None,
        "latestUserInputMessageId": latest_user_input.get("messageId") if latest_user_input else None,
        "accumulatedEventSummary": accumulated_event_summary,
        "eventLedger": event_ledger or None,
        "messageWindow": message_window,
        "messageWindowSize": message_window["observedMessageCount"],
    }



def serialize_snapshot_error(exc: Exception) -> Dict[str, Any]:
    if hasattr(exc, "to_dict") and callable(exc.to_dict):
        data = exc.to_dict()
        if isinstance(data, dict):
            return data
    return {
        "kind": "snapshot_error_v1",
        "message": str(exc),
        "type": exc.__class__.__name__,
    }



def summarize_transport_errors(errors: Any) -> List[Dict[str, Any]] | None:
    if not isinstance(errors, dict):
        return None
    hints: List[Dict[str, Any]] = []
    for name, value in errors.items():
        entry: Dict[str, Any] = {
            "name": name,
            "derived": True,
            "origin": "openclaw_snapshot_errors",
        }
        if isinstance(value, dict):
            for key in ("kind", "status", "reason", "retryAfter", "requestId", "bodyPreview", "message", "cause"):
                if value.get(key) is not None:
                    entry[key] = value.get(key)
        elif value is not None:
            entry["message"] = str(value)
        hints.append(entry)
    return hints or None



def iso_from_epoch_ms(value: Any) -> Optional[str]:
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(numeric / 1000.0, tz=timezone.utc).isoformat()



def analyze_running_progress(snapshot: Dict[str, Any], *, current_status: str | None, now_ms: int | None = None, long_running_threshold_sec: int = 180) -> Dict[str, Any] | None:
    if str(current_status or "").strip().lower() != "running":
        return None

    visible = [
        event
        for event in (snapshot.get("eventLedger") if isinstance(snapshot.get("eventLedger"), list) else [])
        if isinstance(event, dict) and not event.get("ignored")
    ]
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
    progress_events = [event for event in delta_events if event.get("kind") != "user_input"]
    assistant_message_ids: List[str] = []
    seen_assistant_message_ids: set[str] = set()
    for event in delta_events:
        role = str(event.get("role") or ("user" if event.get("kind") == "user_input" else "assistant")).strip().lower()
        message_id = event.get("messageId")
        if role != "assistant" or not isinstance(message_id, str) or not message_id or message_id in seen_assistant_message_ids:
            continue
        seen_assistant_message_ids.add(message_id)
        assistant_message_ids.append(message_id)

    newest_observed = snapshot.get("messageWindow") if isinstance(snapshot.get("messageWindow"), dict) else {}
    newest_role = str(newest_observed.get("newestMessageRole") or "").strip().lower()
    newest_created = newest_observed.get("newestMessageCreated")
    anchor_created = anchor.get("created")
    assistant_turn_started = (
        newest_role == "assistant"
        and isinstance(newest_created, (int, float))
        and isinstance(anchor_created, (int, float))
        and newest_created >= anchor_created
    )

    now_ms = now_ms if isinstance(now_ms, int) else int(datetime.now(timezone.utc).timestamp() * 1000)
    seconds_since_anchor = None
    if isinstance(anchor_created, (int, float)):
        seconds_since_anchor = max(0, int((now_ms - int(anchor_created)) / 1000))

    signal_codes: List[str] = []
    signals: List[Dict[str, Any]] = []

    def add_signal(code: str, detail: str, *, severity: str = "warning") -> None:
        if code in signal_codes:
            return
        signal_codes.append(code)
        signals.append(
            {
                "code": code,
                "severity": severity,
                "detail": detail,
                "derived": True,
                "origin": "openclaw_compact_snapshot",
            }
        )

    if not progress_events:
        add_signal(
            "running_no_visible_progress_since_latest_user_input",
            "Derived from compact snapshot: no non-user progress events were retained after the latest user input.",
        )
    if assistant_turn_started and not progress_events:
        add_signal(
            "assistant_turn_started_without_visible_progress",
            "Derived from compact snapshot: the newest observed message is assistant, but no visible progress parts were retained.",
        )
    if visible and all(event.get("kind") == "user_input" for event in visible):
        add_signal(
            "recent_window_only_user_input",
            "Derived from compact snapshot: the retained recent window contains only user_input events.",
            severity="info",
        )

    if not signals:
        return None

    return {
        "kind": "opencode_running_progress_observation_v1",
        "status": "running_without_visible_progress",
        "derived": True,
        "origin": "openclaw_compact_snapshot",
        "signalCodes": signal_codes,
        "signals": signals,
        "longRunningThresholdSec": long_running_threshold_sec,
        "longRunning": bool(seconds_since_anchor is not None and seconds_since_anchor >= long_running_threshold_sec),
        "secondsSinceLatestUserInput": seconds_since_anchor,
        "latestUserInput": {
            key: value
            for key, value in {
                "messageId": anchor.get("messageId") or latest_user_message_id,
                "summary": truncate_text(anchor.get("summary"), limit=140),
                "createdAt": iso_from_epoch_ms(anchor_created),
            }.items()
            if value is not None
        },
        "visibleEventCountSinceLatestUserInput": len(delta_events),
        "assistantMessageCountSinceLatestUserInput": len(assistant_message_ids),
        "progressEventCountSinceLatestUserInput": len(progress_events),
        "assistantTurnStartedAfterLatestUserInput": assistant_turn_started,
        "newestObservedMessage": {
            key: value
            for key, value in {
                "messageId": newest_observed.get("newestMessageId"),
                "role": newest_observed.get("newestMessageRole"),
                "createdAt": iso_from_epoch_ms(newest_created),
            }.items()
            if value is not None
        }
        or None,
    }



def build_compact_snapshot(client: OpenCodeClient, session_id: str, message_limit: int = 10) -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
    errors: Dict[str, Dict[str, Any]] = {}

    def attempt(name, fn):
        try:
            return fn()
        except Exception as exc:
            errors[name] = serialize_snapshot_error(exc)
            return None

    messages = attempt("messages", lambda: client.session_messages(session_id, limit=message_limit))
    todo = attempt("todo", lambda: client.session_todo(session_id))
    status = attempt("status", client.session_status)
    permission = attempt("permission", client.permission)
    question = attempt("question", client.question)

    message_summary = summarize_recent_messages(messages)

    snapshot = {
        "sessionId": session_id,
        "latestMessage": message_summary["latestMessage"],
        "latestTextPreview": message_summary.get("latestTextPreview"),
        "latestTextPreviewMessageId": message_summary.get("latestTextPreviewMessageId"),
        "latestTextPreviewRole": message_summary.get("latestTextPreviewRole"),
        "latestAssistantTextPreview": message_summary.get("latestAssistantTextPreview"),
        "latestAssistantTextPreviewMessageId": message_summary.get("latestAssistantTextPreviewMessageId"),
        "latestToolOutputPreview": message_summary.get("latestToolOutputPreview"),
        "latestUserInputSummary": message_summary.get("latestUserInputSummary"),
        "latestUserInputMessageId": message_summary.get("latestUserInputMessageId"),
        "accumulatedEventSummary": message_summary.get("accumulatedEventSummary"),
        "eventLedger": message_summary.get("eventLedger"),
        "messageWindow": message_summary.get("messageWindow"),
        "messageWindowSize": message_summary.get("messageWindowSize"),
        "messageWindowLimit": message_limit,
        "todo": normalize_todo(todo),
        "status": status,
        "permission": permission,
        "question": question,
        "errors": errors,
    }
    return snapshot, errors



def main() -> None:
    p = argparse.ArgumentParser(description="Build a compact OpenCode snapshot for main-session decisions.")
    p.add_argument("--base-url", required=True)
    p.add_argument("--session-id", required=True)
    p.add_argument("--token")
    p.add_argument("--timeout", type=int, default=20)
    p.add_argument("--message-limit", type=int, default=10)
    args = p.parse_args()

    client = OpenCodeClient(base_url=args.base_url, token=args.token, timeout=args.timeout)
    snapshot, _errors = build_compact_snapshot(client, args.session_id, message_limit=args.message_limit)
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
