#!/usr/bin/env python3
import argparse
import json
import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
REPO_ROOT = SKILL_ROOT.parent
OPENCODECTL = SCRIPT_DIR / "opencodectl.py"
DEFAULT_RUNTIME_NAME = "default"
DEFAULT_TIMEOUT = 20
DEFAULT_MESSAGE_LIMIT = 10
DEFAULT_NO_CHANGE_VISIBLE_AFTER_MIN = 30
DEFAULT_INTERVAL_SEC = 60


@dataclass(frozen=True)
class RuntimePaths:
    config: Path
    state: Path
    log: Path


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_runtime_dir(name: str) -> Path:
    return REPO_ROOT / ".local" / "opencode" / "watch" / name


def default_runtime_paths(name: str = DEFAULT_RUNTIME_NAME) -> RuntimePaths:
    runtime_dir = default_runtime_dir(name)
    return RuntimePaths(
        config=runtime_dir / "config.json",
        state=runtime_dir / "state.json",
        log=runtime_dir / "watch.log",
    )


def parse_json_object(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"config must contain a JSON object: {path}")
    return data


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"config file not found: {path}\n"
            f"Copy the tracked example to a local ignored runtime path first, for example:\n"
            f"  mkdir -p {path.parent}\n"
            f"  cp {SKILL_ROOT / 'examples' / 'watch-runtime.example.json'} {path}"
        )
    return parse_json_object(path)


def resolve_optional_path(value: str | None, *, base_dir: Path) -> Path | None:
    if not value:
        return None
    raw = Path(value).expanduser()
    if raw.is_absolute():
        return raw
    return (base_dir / raw).resolve()


def runtime_paths_for_args(args: argparse.Namespace, config: dict[str, Any]) -> RuntimePaths:
    if args.config:
        config_path = Path(args.config).expanduser().resolve()
    else:
        config_path = default_runtime_paths(args.name).config.resolve()

    base_dir = config_path.parent
    configured_state = resolve_optional_path(config.get("state"), base_dir=base_dir)
    configured_log = resolve_optional_path(config.get("log"), base_dir=base_dir)

    state_path = Path(args.state).expanduser().resolve() if args.state else configured_state or (base_dir / "state.json")
    log_path = Path(args.log).expanduser().resolve() if args.log else configured_log or (base_dir / "watch.log")
    return RuntimePaths(config=config_path, state=state_path, log=log_path)


def require_string(config: dict[str, Any], key: str) -> str:
    value = config.get(key)
    if isinstance(value, str) and value:
        return value
    raise ValueError(f"config field '{key}' must be a non-empty string")


def optional_string(config: dict[str, Any], key: str) -> str | None:
    value = config.get(key)
    if value is None:
        return None
    if isinstance(value, str) and value:
        return value
    raise ValueError(f"config field '{key}' must be a non-empty string when provided")


def optional_int(config: dict[str, Any], key: str, default: int) -> int:
    value = config.get(key, default)
    if isinstance(value, int):
        return value
    raise ValueError(f"config field '{key}' must be an integer")


def optional_bool(config: dict[str, Any], key: str, default: bool) -> bool:
    value = config.get(key, default)
    if isinstance(value, bool):
        return value
    raise ValueError(f"config field '{key}' must be a boolean")


def resolve_token(config: dict[str, Any]) -> str | None:
    token = optional_string(config, "token") if config.get("token") is not None else None
    token_env = optional_string(config, "token_env") if config.get("token_env") is not None else None
    if token and token_env:
        raise ValueError("config may set either 'token' or 'token_env', not both")
    if token_env:
        env_value = os.environ.get(token_env)
        if not env_value:
            raise ValueError(f"environment variable named by token_env is empty or unset: {token_env}")
        return env_value
    return token


def build_watch_command(paths: RuntimePaths, config: dict[str, Any], *, once: bool, live_override: bool | None) -> list[str]:
    command = [
        sys.executable,
        str(OPENCODECTL),
        "watch",
        "--base-url", require_string(config, "base_url"),
        "--session-id", require_string(config, "session_id"),
        "--state", str(paths.state),
        "--timeout", str(optional_int(config, "timeout", DEFAULT_TIMEOUT)),
        "--message-limit", str(optional_int(config, "message_limit", DEFAULT_MESSAGE_LIMIT)),
        "--no-change-visible-after-min", str(optional_int(config, "no_change_visible_after_min", DEFAULT_NO_CHANGE_VISIBLE_AFTER_MIN)),
        "--interval-sec", str(optional_int(config, "interval_sec", DEFAULT_INTERVAL_SEC)),
    ]

    origin_session = optional_string(config, "origin_session") if config.get("origin_session") is not None else None
    if origin_session:
        command += ["--origin-session", origin_session]

    origin_target = optional_string(config, "origin_target") if config.get("origin_target") is not None else None
    if origin_target:
        command += ["--origin-target", origin_target]

    token = resolve_token(config)
    if token:
        command += ["--token", token]

    live = optional_bool(config, "live", False) if live_override is None else live_override
    if live:
        command.append("--live")

    if not once:
        command.append("--loop")

    return command


def redact_command(command: list[str]) -> list[str]:
    redacted = list(command)
    for index, part in enumerate(redacted[:-1]):
        if part == "--token":
            redacted[index + 1] = "***REDACTED***"
    return redacted


def emit_line(text: str, log_file) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()
    log_file.write(text)
    log_file.flush()


def run_runtime(command: list[str], paths: RuntimePaths, *, once: bool) -> int:
    paths.state.parent.mkdir(parents=True, exist_ok=True)
    paths.log.parent.mkdir(parents=True, exist_ok=True)

    with paths.log.open("a", encoding="utf-8") as log_file:
        banner = {
            "kind": "opencode_watch_runtime_start_v1",
            "startedAt": now_iso(),
            "mode": "once" if once else "loop",
            "config": str(paths.config),
            "state": str(paths.state),
            "log": str(paths.log),
            "command": redact_command(command),
        }
        emit_line(json.dumps(banner, ensure_ascii=False) + "\n", log_file)

        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                emit_line(line, log_file)
            return proc.wait()
        except KeyboardInterrupt:
            proc.send_signal(signal.SIGINT)
            return proc.wait()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Thin long-run entrypoint for the existing opencodectl watch command. Local config is expected in .local by default; state and log live beside that config unless overridden."
    )
    parser.add_argument("--name", default=DEFAULT_RUNTIME_NAME, help="named runtime profile under .local/opencode/watch/<name>/ (default: %(default)s)")
    parser.add_argument("--config", help="explicit config path; defaults to .local/opencode/watch/<name>/config.json")
    parser.add_argument("--state", help="optional state path override; defaults to sibling state.json")
    parser.add_argument("--log", help="optional log path override; defaults to sibling watch.log")
    parser.add_argument("--once", action="store_true", help="run a single watch step instead of the default long-run loop")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="force live execution even if the config says dry-run")
    mode.add_argument("--dry-run", action="store_true", help="force dry-run planning even if the config says live")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    config_path = Path(args.config).expanduser().resolve() if args.config else default_runtime_paths(args.name).config.resolve()
    config = load_config(config_path)
    paths = runtime_paths_for_args(args, config)

    live_override = None
    if args.live:
        live_override = True
    elif args.dry_run:
        live_override = False

    command = build_watch_command(paths, config, once=args.once, live_override=live_override)
    return run_runtime(command, paths, once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
