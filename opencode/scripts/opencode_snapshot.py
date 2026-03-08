#!/usr/bin/env python3
import argparse
import json
from typing import Any, Dict

from opencode_api_client import OpenCodeClient


def compact_latest_message(msg: Any) -> Dict[str, Any]:
    if not isinstance(msg, dict):
        return {"raw": msg}
    out = {}
    for key in ["id", "role", "created", "updated", "status", "type", "finish", "completed"]:
        if key in msg:
            out[key] = msg[key]
    if "message" in msg and isinstance(msg["message"], dict):
        m = msg["message"]
        for key in ["role", "stopReason", "timestamp"]:
            if key in m:
                out[f"message.{key}"] = m[key]
        content = m.get("content")
        if isinstance(content, list) and content:
            last = content[-1]
            if isinstance(last, dict):
                out["message.lastContentType"] = last.get("type")
                if "text" in last:
                    text = last.get("text") or ""
                    out["message.lastTextPreview"] = text[:200]
    return out


def main() -> None:
    p = argparse.ArgumentParser(description='Build a compact OpenCode snapshot for main-session decisions.')
    p.add_argument('--base-url', required=True)
    p.add_argument('--session-id', required=True)
    p.add_argument('--token')
    p.add_argument('--timeout', type=int, default=20)
    args = p.parse_args()

    client = OpenCodeClient(base_url=args.base_url, token=args.token, timeout=args.timeout)

    latest_message = None
    todo = None
    status = None
    permission = None
    question = None
    errors = {}

    def attempt(name, fn):
        try:
            return fn()
        except Exception as e:
            errors[name] = str(e)
            return None

    latest_message = attempt('latest_message', lambda: client.latest_message(args.session_id))
    todo = attempt('todo', lambda: client.session_todo(args.session_id))
    status = attempt('status', client.session_status)
    permission = attempt('permission', client.permission)
    question = attempt('question', client.question)

    snapshot = {
        "sessionId": args.session_id,
        "latestMessage": compact_latest_message(latest_message),
        "todo": todo,
        "status": status,
        "permission": permission,
        "question": question,
        "errors": errors,
    }
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
