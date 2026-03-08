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
    if isinstance(payload, dict) and "factSkeleton" in payload:
        fact = payload.get("factSkeleton") or {}
        cadence = payload.get("cadence") or {}
        return {
            "decision_name": cadence.get("decision") or ("visible_update" if payload.get("shouldSend") else None),
            "decision_reason": fact.get("reason") or cadence.get("reason"),
            "status": fact.get("status") or "unknown",
            "phase": fact.get("phase"),
            "preview": short(fact.get("latestMeaningfulPreview")),
            "no_change": cadence.get("noChange"),
            "count": cadence.get("consecutiveNoChangeCount"),
        }

    if isinstance(payload, dict) and "payload" in payload and "decision" not in payload and "observation" not in payload:
        payload = payload["payload"]

    if isinstance(payload, dict) and "decision" in payload and "observation" in payload:
        ctx = payload
    elif isinstance(payload, dict) and "steps" in payload and payload.get("steps"):
        ctx = payload["steps"][-1]
    else:
        raise SystemExit("unsupported input payload for render-update")

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

    preview = (
        snapshot.get("latestAssistantTextPreview")
        or snapshot.get("latestTextPreview")
        or latest.get("message.lastTextPreview")
        or latest.get("textPreview")
    )

    return {
        "decision_name": decision_name,
        "decision_reason": decision_reason,
        "status": observation.get("status") or after.get("status") or "unknown",
        "phase": observation.get("phase") or after.get("phase"),
        "preview": short(preview),
        "no_change": observation.get("noChange"),
        "count": after.get("consecutiveNoChangeCount"),
    }


def render(payload):
    ctx = derive_context(payload)
    decision_name = ctx.get("decision_name")
    decision_reason = ctx.get("decision_reason")
    status = ctx.get("status")
    phase = ctx.get("phase")
    preview = ctx.get("preview")
    no_change = ctx.get("no_change")
    count = ctx.get("count")

    if decision_name != "visible_update":
        return ""

    if status == "completed":
        base = "OpenCode 进展：任务看起来已完成。"
        if phase:
            base += f" 当前阶段：{phase}。"
        elif preview:
            base += " 已提取到最终输出摘要。"
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
        elif status == "running":
            base += " 当前仍处于运行中。"
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
    p = argparse.ArgumentParser(description="Render a fallback/debug main-session update from a turn or cycle payload.")
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
