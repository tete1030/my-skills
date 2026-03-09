import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from opencode_manager import (  # noqa: E402
    build_manager_watcher_config,
    build_parser,
    build_watcher_summary,
    create_watcher_entry,
    list_watchers_command,
    refresh_registry_entry,
    save_json_object,
    start_or_attach_watcher,
)


class OpenCodeManagerTests(unittest.TestCase):
    def test_create_watcher_entry_uses_explicit_session_names(self):
        entry = create_watcher_entry(
            watcher_id="ow_demo123",
            opencode_base_url="http://127.0.0.1:4096",
            opencode_session_id="ses_demo",
            opencode_workspace="/tmp/demo-workspace",
            openclaw_session_key="agent:main:telegram:group:-100123:topic:42",
            openclaw_delivery_target="telegram:-100123:topic:42",
            opencode_token=None,
            opencode_token_env="OPENCODE_TOKEN",
            watch_live=False,
            watch_interval_sec=60,
            idle_timeout_sec=900,
            watch_message_limit=10,
            watch_timeout_sec=20,
        )

        self.assertIn("opencodeSessionId", entry)
        self.assertIn("openclawSessionKey", entry)
        self.assertNotIn("sessionId", entry)
        self.assertNotIn("originSession", entry)

        manager_config = build_manager_watcher_config(entry)
        self.assertEqual(manager_config["opencodeSessionId"], "ses_demo")
        self.assertEqual(manager_config["openclawSessionKey"], "agent:main:telegram:group:-100123:topic:42")
        self.assertNotIn("session_id", manager_config)
        self.assertNotIn("origin_session", manager_config)

    def test_refresh_registry_entry_marks_dead_process_exited(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            save_json_object(
                state_path,
                {
                    "watchRunner": {
                        "lastRunAt": "2026-03-09T10:00:00+00:00",
                        "lastOperation": "skip_duplicate",
                        "lastRouteStatus": "ready",
                        "lastDeliveryAction": "inject",
                        "lastFactStatus": "completed",
                        "lastFactPhase": "done",
                        "lastPreview": "Finished the requested work.",
                        "lastExitReason": "idle_timeout:terminal_status:completed",
                        "lastExitedAt": "2026-03-09T10:15:00+00:00",
                    }
                },
            )
            entry = {
                "watcherId": "ow_demo123",
                "watcherStatus": "running",
                "watchProcessId": 999999,
                "opencodeSessionId": "ses_demo",
                "opencodeWorkspace": "/tmp/demo-workspace",
                "openclawSessionKey": "agent:main:telegram:group:-100123:topic:42",
                "openclawDeliveryTarget": "telegram:-100123:topic:42",
                "watcherStatePath": str(state_path),
                "watcherConfigPath": str(Path(tmpdir) / "config.json"),
                "watcherLogPath": str(Path(tmpdir) / "watch.log"),
            }

            refreshed = refresh_registry_entry(entry)

            self.assertEqual(refreshed["watcherStatus"], "exited")
            self.assertEqual(refreshed["watchExitReason"], "idle_timeout:terminal_status:completed")
            self.assertEqual(refreshed["lastOpencodeStatus"], "completed")
            self.assertEqual(refreshed["openclawSessionKey"], "agent:main:telegram:group:-100123:topic:42")

    def test_start_or_attach_watcher_refuses_duplicate_active_lock(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / "registry.json"
            save_json_object(
                registry_path,
                {
                    "kind": "opencode_manager_registry_v1",
                    "watchers": [
                        {
                            "watcherId": "ow_existing",
                            "watcherStatus": "running",
                            "watchProcessId": 12345,
                            "opencodeSessionId": "ses_demo",
                            "opencodeWorkspace": "/tmp/demo-workspace",
                            "openclawSessionKey": "agent:main:telegram:group:-100123:topic:42",
                            "openclawDeliveryTarget": "telegram:-100123:topic:42",
                            "watcherStatePath": str(Path(tmpdir) / "state.json"),
                            "watcherConfigPath": str(Path(tmpdir) / "config.json"),
                            "watcherLogPath": str(Path(tmpdir) / "watch.log"),
                        }
                    ],
                },
            )

            with mock.patch("opencode_manager.process_is_alive", return_value=True):
                with self.assertRaisesRegex(RuntimeError, "watcher lock active"):
                    start_or_attach_watcher(
                        registry_path=registry_path,
                        opencode_base_url="http://127.0.0.1:4096",
                        opencode_session_id="ses_demo",
                        opencode_workspace="/tmp/demo-workspace",
                        openclaw_session_key="agent:main:telegram:group:-100123:topic:99",
                        openclaw_delivery_target="telegram:-100123:topic:99",
                        opencode_token=None,
                        opencode_token_env=None,
                        watch_live=False,
                        watch_interval_sec=60,
                        idle_timeout_sec=900,
                        watch_message_limit=10,
                        watch_timeout_sec=20,
                    )

    def test_list_watchers_reports_openclaw_binding(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / "registry.json"
            save_json_object(
                registry_path,
                {
                    "kind": "opencode_manager_registry_v1",
                    "watchers": [
                        {
                            "watcherId": "ow_demo123",
                            "watcherStatus": "running",
                            "watchProcessId": 12345,
                            "watchProcessAlive": True,
                            "opencodeSessionId": "ses_demo",
                            "opencodeWorkspace": "/tmp/demo-workspace",
                            "openclawSessionKey": "agent:main:telegram:group:-100123:topic:42",
                            "openclawDeliveryTarget": "telegram:-100123:topic:42",
                            "watchIntervalSec": 60,
                            "idleTimeoutSec": 900,
                            "watcherConfigPath": str(Path(tmpdir) / "config.json"),
                            "watcherStatePath": str(Path(tmpdir) / "state.json"),
                            "watcherLogPath": str(Path(tmpdir) / "watch.log"),
                        }
                    ],
                },
            )
            args = Namespace(registry_path=str(registry_path), include_exited=False)

            with mock.patch("opencode_manager.process_is_alive", return_value=True):
                result = list_watchers_command(args)

            self.assertEqual(result["watcherCount"], 1)
            watcher = result["watchers"][0]
            self.assertEqual(watcher["openclawSessionKey"], "agent:main:telegram:group:-100123:topic:42")
            self.assertEqual(watcher["opencodeSessionId"], "ses_demo")

    def test_parser_exposes_phase1_subcommands(self):
        parser = build_parser()
        parsed = parser.parse_args(
            [
                "start",
                "--opencode-base-url",
                "http://127.0.0.1:4096",
                "--opencode-workspace",
                "/tmp/demo-workspace",
                "--openclaw-session-key",
                "agent:main:telegram:group:-100123:topic:42",
                "--first-prompt",
                "hello",
            ]
        )
        self.assertEqual(parsed.command, "start")
        self.assertEqual(parsed.openclaw_session_key, "agent:main:telegram:group:-100123:topic:42")
        self.assertEqual(parsed.opencode_workspace, "/tmp/demo-workspace")

    def test_watcher_summary_keeps_explicit_field_names(self):
        summary = build_watcher_summary(
            {
                "watcherId": "ow_demo123",
                "watcherStatus": "running",
                "opencodeSessionId": "ses_demo",
                "openclawSessionKey": "agent:main:telegram:group:-100123:topic:42",
            }
        )
        self.assertIn("opencodeSessionId", summary)
        self.assertIn("openclawSessionKey", summary)
        self.assertNotIn("sessionId", summary)
        self.assertNotIn("originSession", summary)


if __name__ == "__main__":
    unittest.main()
