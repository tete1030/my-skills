#!/usr/bin/env python3
import hashlib
from typing import Any

ALLOWED_TASK_CLUSTER_KEYS = frozenset({
    "key",
    "summary",
    "clusterStateRank",
    "detailRank",
    "sourceUpdateMs",
})
ALLOWED_REPLY_POLICY_KEYS = frozenset({"replyDefault", "userValue"})
DEFAULT_REPLY_POLICY = {
    "replyDefault": "send_if_not_cluster_superseded",
    "userValue": "suppress_if_cluster_superseded",
}

UPDATE_TYPE_RANKS = {
    "silent": 0,
    "heartbeat": 10,
    "progress": 20,
    "completed": 40,
    "blocked": 50,
    "failed": 60,
}
STATUS_RANKS = {
    "idle": 5,
    "running": 20,
    "completed": 40,
    "blocked": 50,
    "failed": 60,
    "deviated": 60,
    "stalled": 60,
}


def _normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split()).strip()
    return text or None


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def empty_task_cluster() -> dict[str, Any]:
    return {
        "key": None,
        "summary": None,
        "clusterStateRank": 0,
        "detailRank": 0,
        "sourceUpdateMs": None,
    }


def normalize_task_cluster(task_cluster: Any) -> dict[str, Any]:
    if not isinstance(task_cluster, dict):
        return empty_task_cluster()
    return {
        "key": _normalize_text(task_cluster.get("key")),
        "summary": _normalize_text(task_cluster.get("summary")),
        "clusterStateRank": _coerce_int(task_cluster.get("clusterStateRank")) or 0,
        "detailRank": _coerce_int(task_cluster.get("detailRank")) or 0,
        "sourceUpdateMs": _coerce_int(task_cluster.get("sourceUpdateMs")),
    }


def normalize_reply_policy(reply_policy: Any) -> dict[str, str]:
    if not isinstance(reply_policy, dict):
        return dict(DEFAULT_REPLY_POLICY)
    normalized = dict(DEFAULT_REPLY_POLICY)
    for key in ALLOWED_REPLY_POLICY_KEYS:
        value = _normalize_text(reply_policy.get(key))
        if value:
            normalized[key] = value
    return normalized


def task_cluster_key(summary: Any) -> str | None:
    normalized = _normalize_text(summary)
    if not normalized:
        return None
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"task-cluster-{digest[:16]}"


def cluster_state_rank(*, update_type: str | None = None, status: str | None = None) -> int:
    update_rank = UPDATE_TYPE_RANKS.get(str(update_type or "").strip().lower(), 0)
    status_rank = STATUS_RANKS.get(str(status or "").strip().lower(), 0)
    return max(update_rank, status_rank)


def build_task_cluster(
    summary: Any,
    preview: Any,
    *,
    update_type: str | None = None,
    status: str | None = None,
    source_update_ms: Any = None,
) -> dict[str, Any]:
    normalized_summary = _normalize_text(summary)
    normalized_preview = _normalize_text(preview)
    return normalize_task_cluster(
        {
            "key": task_cluster_key(normalized_summary),
            "summary": normalized_summary,
            "clusterStateRank": cluster_state_rank(update_type=update_type, status=status),
            "detailRank": len(normalized_preview or ""),
            "sourceUpdateMs": source_update_ms,
        }
    )


def task_cluster_strength(task_cluster: Any) -> tuple[int, int, int]:
    normalized = normalize_task_cluster(task_cluster)
    return (
        normalized.get("clusterStateRank") or 0,
        normalized.get("sourceUpdateMs") or 0,
        normalized.get("detailRank") or 0,
    )


def same_task_cluster(left: Any, right: Any) -> bool:
    left_norm = normalize_task_cluster(left)
    right_norm = normalize_task_cluster(right)
    return bool(left_norm.get("key") and left_norm.get("key") == right_norm.get("key"))


def task_cluster_is_superseded(candidate: Any, incumbent: Any) -> bool:
    if not same_task_cluster(candidate, incumbent):
        return False
    return task_cluster_strength(incumbent) > task_cluster_strength(candidate)
