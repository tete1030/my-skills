import os
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from opencode_watch_runtime import (  # noqa: E402
    DEFAULT_RUNTIME_NAME,
    RuntimePaths,
    build_watch_command,
    default_runtime_paths,
    runtime_paths_for_args,
)


class WatchRuntimeTests(unittest.TestCase):
    def test_default_runtime_paths_use_repo_local_named_profile(self):
        paths = default_runtime_paths("demo")

        self.assertTrue(str(paths.config).endswith(".local/opencode/watch/demo/config.json"))
        self.assertTrue(str(paths.state).endswith(".local/opencode/watch/demo/state.json"))
        self.assertTrue(str(paths.log).endswith(".local/opencode/watch/demo/watch.log"))

    def test_runtime_paths_default_to_sibling_state_and_log_for_explicit_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "runtime" / "config.json"
            config_path.parent.mkdir(parents=True)
            args = Namespace(
                name=DEFAULT_RUNTIME_NAME,
                config=str(config_path),
                state=None,
                log=None,
            )

            paths = runtime_paths_for_args(args, {})

            self.assertEqual(paths.config, config_path.resolve())
            self.assertEqual(paths.state, (config_path.parent / "state.json").resolve())
            self.assertEqual(paths.log, (config_path.parent / "watch.log").resolve())

    def test_build_watch_command_defaults_to_loop_and_resolves_token_env(self):
        paths = RuntimePaths(
            config=Path("/tmp/config.json"),
            state=Path("/tmp/state.json"),
            log=Path("/tmp/watch.log"),
        )
        config = {
            "base_url": "http://127.0.0.1:4096",
            "session_id": "ses_demo",
            "origin_session": "agent:main:telegram:group:-100123:topic:42",
            "origin_target": "telegram:-100123:topic:42",
            "token_env": "WATCH_RUNTIME_TOKEN",
        }

        original = os.environ.get("WATCH_RUNTIME_TOKEN")
        os.environ["WATCH_RUNTIME_TOKEN"] = "secret-token"
        try:
            command = build_watch_command(paths, config, once=False, live_override=True)
        finally:
            if original is None:
                os.environ.pop("WATCH_RUNTIME_TOKEN", None)
            else:
                os.environ["WATCH_RUNTIME_TOKEN"] = original

        self.assertIn("--loop", command)
        self.assertIn("--live", command)
        self.assertIn("secret-token", command)
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(command[2], "watch")

    def test_build_watch_command_honors_once_and_dry_run_override(self):
        paths = RuntimePaths(
            config=Path("/tmp/config.json"),
            state=Path("/tmp/state.json"),
            log=Path("/tmp/watch.log"),
        )
        config = {
            "base_url": "http://127.0.0.1:4096",
            "session_id": "ses_demo",
            "live": True,
        }

        command = build_watch_command(paths, config, once=True, live_override=False)

        self.assertNotIn("--loop", command)
        self.assertNotIn("--live", command)
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(command[2], "watch")

    def test_build_watch_command_accepts_manager_named_fields(self):
        paths = RuntimePaths(
            config=Path("/tmp/config.json"),
            state=Path("/tmp/state.json"),
            log=Path("/tmp/watch.log"),
        )
        config = {
            "opencodeBaseUrl": "http://127.0.0.1:4096",
            "opencodeSessionId": "ses_demo",
            "openclawSessionKey": "agent:main:telegram:group:-100123:topic:42",
            "openclawDeliveryTarget": "telegram:-100123:topic:42",
            "watchIntervalSec": 15,
            "idleTimeoutSec": 900,
            "notifyMinIntervalSec": 300,
            "notifyMinSeverity": "normal",
            "notifyKeywords": ["deploy", "release"],
            "notifyFilterCritical": True,
            "watchLive": True,
        }

        command = build_watch_command(paths, config, once=False, live_override=None)

        self.assertIn("--loop", command)
        self.assertIn("--live", command)
        self.assertIn("--idle-timeout-sec", command)
        self.assertIn("900", command)
        self.assertIn("--notify-min-interval-sec", command)
        self.assertIn("300", command)
        self.assertIn("--notify-min-priority", command)
        self.assertIn("normal", command)
        self.assertIn("--notify-filter-critical", command)
        self.assertEqual(command.count("--notify-keyword"), 2)
        self.assertIn("--origin-session", command)
        self.assertIn("agent:main:telegram:group:-100123:topic:42", command)

    def test_runtime_paths_resolve_manager_named_state_and_log(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "runtime" / "config.json"
            config_path.parent.mkdir(parents=True)
            args = Namespace(
                name=DEFAULT_RUNTIME_NAME,
                config=str(config_path),
                state=None,
                log=None,
            )
            config = {
                "watchStatePath": "manager-state.json",
                "watchLogPath": "manager-watch.log",
            }

            paths = runtime_paths_for_args(args, config)

            self.assertEqual(paths.state, (config_path.parent / "manager-state.json").resolve())
            self.assertEqual(paths.log, (config_path.parent / "manager-watch.log").resolve())


if __name__ == "__main__":
    unittest.main()
