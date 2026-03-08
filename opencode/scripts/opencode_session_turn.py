#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

PY = sys.executable
SCRIPT_DIR = Path(__file__).resolve().parent


def run_capture(script_name: str, args: list[str]) -> str:
    script = SCRIPT_DIR / script_name
    proc = subprocess.run([PY, str(script), *args], capture_output=True, text=True)
    if proc.returncode != 0:
        if proc.stdout:
            print(proc.stdout, end="", file=sys.stderr)
        if proc.stderr:
            print(proc.stderr, end="", file=sys.stderr)
        raise SystemExit(proc.returncode)
    return proc.stdout


def load_json(path: str | None):
    if not path:
        return None
    return json.loads(Path(path).read_text())


def main() -> None:
    p = argparse.ArgumentParser(description="Run one main-session turn: optional control input + remote cycle + rendered update.")
    p.add_argument("--base-url", required=True)
    p.add_argument("--session-id", required=True)
    p.add_argument("--state", required=True)
    p.add_argument("--control")
    p.add_argument("--token")
    p.add_argument("--timeout", type=int, default=20)
    p.add_argument("--no-change-visible-after-min", type=int, default=30)
    p.add_argument("--write", action="store_true")
    p.add_argument("--payload-out")
    p.add_argument("--update-out")
    p.add_argument("--quiet-when-empty", action="store_true")
    args = p.parse_args()

    cycle_args = [
        "--base-url", args.base_url,
        "--session-id", args.session_id,
        "--state", args.state,
        "--timeout", str(args.timeout),
        "--no-change-visible-after-min", str(args.no_change_visible_after_min),
    ]
    if args.token:
        cycle_args += ["--token", args.token]
    if args.write:
        cycle_args.append("--write")

    cycle_stdout = run_capture("opencode_remote_cycle.py", cycle_args)
    payload = json.loads(cycle_stdout)
    control = load_json(args.control)

    if control is not None:
        payload["control"] = control
        if isinstance(payload.get("after"), dict):
            payload["after"].update(control)
        if isinstance(payload.get("before"), dict):
            # preserve original before-state; do not mutate it with control
            pass

    if args.payload_out:
        Path(args.payload_out).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as tmp:
        tmp.write(json.dumps(payload, ensure_ascii=False, indent=2))
        tmp_path = tmp.name

    render_args = ["--input", tmp_path]
    if args.quiet_when_empty:
        render_args.append("--quiet-when-empty")
    update_text = run_capture("opencode_render_update.py", render_args).rstrip("\n")

    if args.update_out:
        Path(args.update_out).write_text(update_text + ("\n" if update_text else ""))

    print(json.dumps({
        "control": control,
        "payload": payload,
        "update": update_text,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
