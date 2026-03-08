#!/usr/bin/env python3
import argparse
import json
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from opencode_api_client import OpenCodeClient

ACTIVE_TODO_STATUSES = {"in_progress", "active", "running", "current"}
PENDING_TODO_STATUSES = {"pending", "todo", "queued", "next", "open"}
COMPLETED_TODO_STATUSES = {"completed", "done", "finished", "closed", "resolved"}
FAILED_MESSAGE_STATUSES = {"error", "failed", "failure", "cancelled", "canceled"}
RUNNING_TOOL_STATUSES = {"queued", "pending", "running", "active", "started", "in_progress"}
COMPLETED_TOOL_STATUSES = {"completed", "done", "finished", "succeeded", "success", "ok"}



ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
HEXISH_RE = re.compile(r"^[0-9a-f]{7,}$", re.IGNORECASE)


def strip_ansi(value: str) -> str:
    return ANSI_RE.sub("", value)


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


def score_segment(segment: str) -> tuple[int, int]:
    s = segment.strip()
    low = s.lower()
    score = 0
    keywords = ["done", "completed", "released", "success", "succeeded", "passed", "fixed", "failed", "blocked", "summary", "result"]
    if any(k in low for k in keywords):
        score += 5
    if any(ch.isalpha() for ch in s):
        score += 2
    if " " in s:
        score += 1
    if len(s) > 100:
        score -= 1
    if low.startswith("cloning into") or low.startswith("return exactly") or low.startswith("assistant to="):
        score -= 2
    return (score, -len(s))


def clean_preview(value: Any, limit: int = 200) -> Optional[str]:
    segments = [seg for seg in preview_segments(value) if not is_noise_segment(seg)]
    if not segments:
        return truncate_text(value, limit=limit)
    best = sorted(segments, key=score_segment, reverse=True)[:2]
    text = " | ".join(best)
    return truncate_text(text, limit=limit)


def truncate_text(value: Any, limit: int = 200) -> Optional[str]:
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1] + "…"



def get_nested(obj: Any, *keys: str) -> Any:
    cur = obj
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur



def normalize_message_status(
    finish: Any,
    completed_at: Any,
    tool_statuses: Iterable[str],
    part_types: Iterable[str],
) -> str:
    finish_norm = str(finish or "").strip().lower()
    statuses = [str(status or "").strip().lower() for status in tool_statuses if status]
    if finish_norm in FAILED_MESSAGE_STATUSES or any(status in FAILED_MESSAGE_STATUSES for status in statuses):
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
        "textPreview": last_text_preview,
        "toolOutputPreview": last_tool_output_preview,
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



def summarize_recent_messages(messages: Any) -> Dict[str, Any]:
    if not isinstance(messages, list):
        latest = compact_latest_message(messages)
        preview = latest.get("message.lastTextPreview") or latest.get("toolOutputPreview")
        return {
            "latestMessage": latest,
            "latestTextPreview": preview,
            "latestTextPreviewMessageId": latest.get("id"),
            "latestTextPreviewRole": latest.get("role"),
            "latestAssistantTextPreview": preview if latest.get("role") == "assistant" and latest.get("message.lastTextPreview") else None,
            "latestAssistantTextPreviewMessageId": latest.get("id") if latest.get("role") == "assistant" and latest.get("message.lastTextPreview") else None,
            "latestToolOutputPreview": latest.get("toolOutputPreview"),
            "messageWindowSize": 1 if latest else 0,
        }

    normalized_messages = [compact_latest_message(message) for message in messages]
    latest = normalized_messages[-1] if normalized_messages else {}

    latest_text_preview_any = None
    latest_text_preview_any_message_id = None
    latest_text_preview_any_role = None
    latest_assistant_text_preview = None
    latest_assistant_text_preview_message_id = None

    for message in reversed(normalized_messages):
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
        or latest_assistant_text_preview
        or latest_tool_output_preview
        or latest_text_preview_any
    )
    latest_text_preview_message_id = (
        latest.get("id") if latest.get("message.lastTextPreview") or latest_tool_output_preview else latest_assistant_text_preview_message_id or latest_text_preview_any_message_id
    )
    latest_text_preview_role = (
        latest.get("role") if latest.get("message.lastTextPreview") or latest_tool_output_preview else ("assistant" if latest_assistant_text_preview else latest_text_preview_any_role)
    )

    return {
        "latestMessage": latest,
        "latestTextPreview": latest_text_preview,
        "latestTextPreviewMessageId": latest_text_preview_message_id,
        "latestTextPreviewRole": latest_text_preview_role,
        "latestAssistantTextPreview": latest_assistant_text_preview,
        "latestAssistantTextPreviewMessageId": latest_assistant_text_preview_message_id,
        "latestToolOutputPreview": latest_tool_output_preview,
        "messageWindowSize": len(normalized_messages),
    }



def build_compact_snapshot(client: OpenCodeClient, session_id: str, message_limit: int = 10) -> Tuple[Dict[str, Any], Dict[str, str]]:
    errors: Dict[str, str] = {}

    def attempt(name, fn):
        try:
            return fn()
        except Exception as exc:
            errors[name] = str(exc)
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
        "messageWindowSize": message_summary.get("messageWindowSize"),
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
