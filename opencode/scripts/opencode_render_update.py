#!/usr/bin/env python3
import argparse
import json
from pathlib import Path


def load_json(path: Path):
    return json.loads(path.read_text())


def get(obj, *keys, default=None):
    cur = obj
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def short(text, n=120):
    if not text:
        return None
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def derive_context(payload):
    if isinstance(payload, dict) and "decision" in payload and "observation" in payload:
        return payload
    if isinstance(payload, dict) and "steps" in payload and payload.get("steps"):
        return payload["steps"][-1]
    raise SystemExit("unsupported input payload for render-update")


def render(payload):
    ctx = derive_context(payload)
    decision = get(ctx, "decision", "decision") or get(ctx, "decision")
    if isinstance(decision, dict):
        decision_name = decision.get("decision")
        decision_reason = decision.get("reason")
    else:
        decision_name = decision
        decision_reason = None

    observation = get(ctx, "observation", default={}) or {}
    after = get(ctx, "after", default={}) or {}
    snapshot = get(payload, "snapshot", default={}) or {}
    latest = snapshot.get("latestMessage") or {}

    status = observation.get("status") or after.get("status") or "unknown"
    phase = observation.get("phase") or after.get("phase")
    preview = latest.get("message.lastTextPreview") or latest.get("textPreview")
    preview = short(preview)
    no_change = observation.get("noChange")
    count = after.get("consecutiveNoChangeCount")

    if decision_name != "visible_update":
        return ""

    if status == "completed":
        base = "OpenCode 进展：任务看起来已完成。"
        if phase:
            base += f" 当前阶段：{phase}。"
        if preview:
            base += f" 最近输出：{preview}"
        return base

    if status == "failed":
        base = "OpenCode 进展：任务看起来失败了，需要检查或介入。"
        if phase:
            base += f" 当前阶段：{phase}。"
        if preview:
            base += f" 最近输出：{preview}"
        return base

    if status == "blocked":
        base = "OpenCode 进展：任务目前被阻塞，可能需要输入或授权。"
        if phase:
            base += f" 当前阶段：{phase}。"
        if preview:
            base += f" 最近输出：{preview}"
        return base

    if no_change:
        base = "OpenCode 进展：暂无显著变化，继续监控中。"
        if phase:
            base += f" 当前阶段：{phase}。"
        if count is not None:
            base += f" 连续 no-change 次数：{count}。"
        return base

    base = "OpenCode 进展：状态有更新。"
    if phase:
        base += f" 当前阶段：{phase}。"
    if preview:
        base += f" 最近输出：{preview}"
    if decision_reason and decision_reason != "state_changed":
        base += f"（触发原因：{decision_reason}）"
    return base


def main():
    p = argparse.ArgumentParser(description="Render a concise main-session progress update from a cycle payload.")
    p.add_argument("--input", required=True)
    p.add_argument("--quiet-when-empty", action="store_true")
    args = p.parse_args()

    payload = load_json(Path(args.input))
    text = render(payload)
    if text:
        print(text)
    elif not args.quiet_when_empty:
        print("")


if __name__ == "__main__":
    main()
