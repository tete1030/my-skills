import io
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

import sys

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from opencode_manager import (  # noqa: E402
    attach_command,
    build_agent_handoff_contract,
    build_manager_watcher_config,
    build_parser,
    build_watcher_summary,
    continue_command,
    create_watcher_entry,
    detach_command,
    inspect_command,
    inspect_history_command,
    list_watchers_command,
    parse_model_override,
    read_watch_log_banner,
    resolve_prompt_input,
    normalize_inline_prompt_text,
    refresh_registry_entry,
    refresh_registry_entries,
    save_json_object,
    start_command,
    start_or_attach_watcher,
    stop_session_command,
    stop_watcher_command,
)


class OpenCodeManagerTests(unittest.TestCase):
    def _runtime_map(self, config_path: Path, pid: int = 12345) -> dict[str, dict[str, object]]:
        resolved = str(config_path.resolve())
        return {
            resolved: {
                "pid": pid,
                "configPath": resolved,
                "command": f"python {SCRIPT_DIR / 'opencode_watch_runtime.py'} --config {resolved}",
            }
        }

    def _write_registry_running_entry(self, tmpdir: str, *, watcher_id: str = "ow_demo123", opencode_session_id: str = "ses_demo") -> tuple[Path, Path, Path]:
        registry_path = Path(tmpdir) / "registry.json"
        watcher_dir = Path(tmpdir) / "watchers" / watcher_id
        config_path = watcher_dir / "config.json"
        state_path = watcher_dir / "state.json"
        log_path = watcher_dir / "watch.log"
        save_json_object(
            config_path,
            {
                "opencodeBaseUrl": "http://127.0.0.1:4096",
                "opencodeSessionId": opencode_session_id,
                "opencodeWorkspace": "/tmp/demo-workspace",
                "openclawSessionKey": "agent:main:discord:target:example-origin-thread",
                "openclawDeliveryTarget": "discord:example-origin-thread",
                "watchStatePath": str(state_path),
                "watchLogPath": str(log_path),
                "watchTimeoutSec": 20,
                "watchMessageLimit": 10,
                "watchIntervalSec": 60,
                "watchLive": False,
                "idleTimeoutSec": 900,
            },
        )
        save_json_object(registry_path, {
            "kind": "opencode_manager_registry_v1",
            "watchers": [
                {
                    "watcherId": watcher_id,
                    "watcherStatus": "running",
                    "watchProcessId": 12345,
                    "opencodeSessionId": opencode_session_id,
                    "opencodeWorkspace": "/tmp/demo-workspace",
                    "openclawSessionKey": "agent:main:discord:target:example-origin-thread",
                    "openclawDeliveryTarget": "discord:example-origin-thread",
                    "watcherStatePath": str(state_path),
                    "watcherConfigPath": str(config_path),
                    "watcherLogPath": str(log_path),
                }
            ],
        })
        return registry_path, config_path, state_path

    def test_create_watcher_entry_uses_explicit_session_names(self):
        entry = create_watcher_entry(
            watcher_id="ow_demo123",
            opencode_base_url="http://127.0.0.1:4096",
            opencode_session_id="ses_demo",
            opencode_workspace="/tmp/demo-workspace",
            openclaw_session_key="agent:main:discord:target:example-origin-thread",
            openclaw_delivery_target="discord:example-origin-thread",
            opencode_token=None,
            opencode_token_env="OPENCODE_TOKEN",
            watch_live=False,
            watch_interval_sec=60,
            idle_timeout_sec=900,
            notify_min_interval_sec=300,
            notify_min_priority="normal",
            notify_keywords=["deploy", "release"],
            notify_filter_critical=True,
            watch_message_limit=10,
            watch_timeout_sec=20,
        )

        self.assertIn("opencodeSessionId", entry)
        self.assertIn("openclawSessionKey", entry)
        self.assertNotIn("sessionId", entry)
        self.assertNotIn("originSession", entry)

        manager_config = build_manager_watcher_config(entry)
        self.assertEqual(manager_config["opencodeSessionId"], "ses_demo")
        self.assertEqual(manager_config["openclawSessionKey"], "agent:main:discord:target:example-origin-thread")
        self.assertEqual(manager_config["notifyMinIntervalSec"], 300)
        self.assertEqual(manager_config["notifyMinPriority"], "normal")
        self.assertEqual(manager_config["notifyKeywords"], ["deploy", "release"])
        self.assertTrue(manager_config["notifyFilterCritical"])
        self.assertNotIn("session_id", manager_config)
        self.assertNotIn("origin_session", manager_config)

    def test_build_agent_handoff_contract_prefers_live_watcher_updates(self):
        contract = build_agent_handoff_contract(
            watcher_entry={"watcherStatus": "running", "watchLive": True},
            watcher_requested=True,
        )

        self.assertEqual(contract["handoffMode"], "watcher_live")
        self.assertEqual(contract["agentAction"], "acknowledge_and_end_turn")
        self.assertIn("OpenCode", contract["userFacingAck"])
        self.assertIn("OpenClaw", contract["userFacingAck"])

    def test_build_agent_handoff_contract_makes_missing_live_handoff_explicit(self):
        contract = build_agent_handoff_contract(
            watcher_entry={"watcherStatus": "running", "watchLive": False},
            watcher_requested=True,
        )

        self.assertEqual(contract["handoffMode"], "watcher_not_live")
        self.assertEqual(contract["agentAction"], "acknowledge_and_end_turn")
        self.assertIn("OpenCode", contract["userFacingAck"])
        self.assertIn("OpenClaw", contract["userFacingAck"])

    def test_build_agent_handoff_contract_marks_requested_but_missing_watcher(self):
        contract = build_agent_handoff_contract(
            watcher_entry=None,
            watcher_requested=True,
        )

        self.assertEqual(contract["handoffMode"], "watcher_missing")
        self.assertEqual(contract["agentAction"], "acknowledge_and_end_turn")
        self.assertIn("OpenCode", contract["userFacingAck"])
        self.assertIn("OpenClaw", contract["userFacingAck"])

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
                "openclawSessionKey": "agent:main:discord:target:example-origin-thread",
                "openclawDeliveryTarget": "discord:example-origin-thread",
                "watcherStatePath": str(state_path),
                "watcherConfigPath": str(Path(tmpdir) / "config.json"),
                "watcherLogPath": str(Path(tmpdir) / "watch.log"),
            }

            with mock.patch("opencode_manager.list_watch_runtime_processes", return_value={}):
                refreshed = refresh_registry_entry(entry)

            self.assertEqual(refreshed["watcherStatus"], "exited")
            self.assertEqual(refreshed["watchExitReason"], "idle_timeout:terminal_status:completed")
            self.assertEqual(refreshed["lastOpencodeStatus"], "completed")
            self.assertEqual(refreshed["openclawSessionKey"], "agent:main:discord:target:example-origin-thread")

    def test_refresh_registry_entry_marks_stale_process_reference_when_pid_alive_but_runtime_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            entry = {
                "watcherId": "ow_demo123",
                "watcherStatus": "running",
                "watchProcessId": 12345,
                "opencodeSessionId": "ses_demo",
                "opencodeWorkspace": "/tmp/demo-workspace",
                "openclawSessionKey": "agent:main:discord:target:example-origin-thread",
                "openclawDeliveryTarget": "discord:example-origin-thread",
                "watcherStatePath": str(Path(tmpdir) / "state.json"),
                "watcherConfigPath": str(Path(tmpdir) / "config.json"),
                "watcherLogPath": str(Path(tmpdir) / "watch.log"),
            }

            with mock.patch("opencode_manager.list_watch_runtime_processes", return_value={}), mock.patch(
                "opencode_manager.process_is_alive", return_value=True
            ):
                refreshed = refresh_registry_entry(entry)

            self.assertEqual(refreshed["watcherStatus"], "exited")
            self.assertEqual(refreshed["watchExitReason"], "stale_process_reference")

    def test_read_watch_log_banner_reads_prefix_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "watch.log"
            banner_line = '{"kind":"opencode_watch_runtime_start_v1","startedAt":"2026-03-09T10:00:00+00:00"}\n'
            trailing_bytes = 400_000
            log_path.write_bytes(banner_line.encode("utf-8") + (b"x" * trailing_bytes))

            original_open = Path.open
            bytes_read = 0

            def counting_open(path_self: Path, *args, **kwargs):
                handle = original_open(path_self, *args, **kwargs)
                if path_self != log_path:
                    return handle

                class CountingHandle:
                    def __init__(self, wrapped):
                        self._wrapped = wrapped

                    def read(self, size=-1):
                        nonlocal bytes_read
                        data = self._wrapped.read(size)
                        bytes_read += len(data)
                        return data

                    def __enter__(self):
                        self._wrapped.__enter__()
                        return self

                    def __exit__(self, exc_type, exc, tb):
                        return self._wrapped.__exit__(exc_type, exc, tb)

                    def __getattr__(self, name):
                        return getattr(self._wrapped, name)

                return CountingHandle(handle)

            with mock.patch("pathlib.Path.open", new=counting_open):
                banner = read_watch_log_banner(log_path)

            self.assertEqual(banner.get("startedAt"), "2026-03-09T10:00:00+00:00")
            self.assertLess(bytes_read, log_path.stat().st_size)

    def test_read_watch_log_banner_prefers_latest_banner_in_prefix(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "watch.log"
            log_path.write_text(
                "\n".join(
                    [
                        '{"kind":"opencode_watch_runtime_start_v1","startedAt":"2026-03-09T10:00:00+00:00"}',
                        "not-json",
                        '{"kind":"opencode_watch_runtime_start_v1","startedAt":"2026-03-09T10:05:00+00:00"}',
                        "runtime output after startup",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            banner = read_watch_log_banner(log_path)

            self.assertEqual(banner.get("startedAt"), "2026-03-09T10:05:00+00:00")

    def test_start_or_attach_watcher_refuses_duplicate_active_lock(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path, config_path, _state_path = self._write_registry_running_entry(tmpdir)

            with mock.patch("opencode_manager.list_watch_runtime_processes", return_value=self._runtime_map(config_path)):
                with self.assertRaisesRegex(RuntimeError, "watcher lock active"):
                    start_or_attach_watcher(
                        registry_path=registry_path,
                        opencode_base_url="http://127.0.0.1:4096",
                        opencode_session_id="ses_demo",
                        opencode_workspace="/tmp/demo-workspace",
                        openclaw_session_key="agent:main:discord:target:example-route-99",
                        openclaw_delivery_target="discord:example-route-99",
                        opencode_token=None,
                        opencode_token_env=None,
                        watch_live=False,
                        watch_interval_sec=60,
                        idle_timeout_sec=900,
                        notify_min_interval_sec=0,
                        notify_min_priority="low",
                        notify_keywords=[],
                        watch_message_limit=10,
                        watch_timeout_sec=20,
                    )

    def test_list_watchers_reports_openclaw_binding(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path, config_path, _state_path = self._write_registry_running_entry(tmpdir)
            args = Namespace(registry_path=str(registry_path), include_exited=False)

            with mock.patch("opencode_manager.list_watch_runtime_processes", return_value=self._runtime_map(config_path)):
                result = list_watchers_command(args)

            self.assertEqual(result["watcherCount"], 1)
            watcher = result["watchers"][0]
            self.assertEqual(watcher["openclawSessionKey"], "agent:main:discord:target:example-origin-thread")
            self.assertEqual(watcher["opencodeSessionId"], "ses_demo")

    def test_list_watchers_default_does_not_recover_missing_registry_entry_from_watcher_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / "registry.json"
            watcher_dir = Path(tmpdir) / "watchers" / "ow_recovered"
            config_path = watcher_dir / "config.json"
            state_path = watcher_dir / "state.json"
            log_path = watcher_dir / "watch.log"
            save_json_object(registry_path, {"kind": "opencode_manager_registry_v1", "watchers": []})
            save_json_object(
                config_path,
                {
                    "opencodeBaseUrl": "http://127.0.0.1:4096",
                    "opencodeSessionId": "ses_recovered",
                    "opencodeWorkspace": "/tmp/demo-workspace",
                    "openclawSessionKey": "agent:main:discord:target:example-origin-thread",
                    "openclawDeliveryTarget": "discord:example-origin-thread",
                    "watchStatePath": str(state_path),
                    "watchLogPath": str(log_path),
                    "watchTimeoutSec": 20,
                    "watchMessageLimit": 10,
                    "watchIntervalSec": 60,
                    "watchLive": False,
                    "idleTimeoutSec": 900,
                },
            )
            save_json_object(state_path, {"watchRunner": {"lastRunAt": "2026-03-09T10:00:00+00:00"}})
            log_path.write_text('{"kind":"opencode_watch_runtime_start_v1","startedAt":"2026-03-09T10:00:00+00:00"}\n')

            args = Namespace(registry_path=str(registry_path), include_exited=False, recover_missing_from_disk=False)
            with mock.patch("opencode_manager.list_watch_runtime_processes", return_value=self._runtime_map(config_path, pid=54321)):
                result = list_watchers_command(args)

            self.assertEqual(result["watcherCount"], 0)
            registry = __import__("opencode_manager").load_json_object(registry_path)
            self.assertEqual(registry["watchers"], [])

    def test_list_watchers_can_recover_missing_registry_entry_from_watcher_dir_when_requested(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / "registry.json"
            watcher_dir = Path(tmpdir) / "watchers" / "ow_recovered"
            config_path = watcher_dir / "config.json"
            state_path = watcher_dir / "state.json"
            log_path = watcher_dir / "watch.log"
            save_json_object(registry_path, {"kind": "opencode_manager_registry_v1", "watchers": []})
            save_json_object(
                config_path,
                {
                    "opencodeBaseUrl": "http://127.0.0.1:4096",
                    "opencodeSessionId": "ses_recovered",
                    "opencodeWorkspace": "/tmp/demo-workspace",
                    "openclawSessionKey": "agent:main:discord:target:example-origin-thread",
                    "openclawDeliveryTarget": "discord:example-origin-thread",
                    "watchStatePath": str(state_path),
                    "watchLogPath": str(log_path),
                    "watchTimeoutSec": 20,
                    "watchMessageLimit": 10,
                    "watchIntervalSec": 60,
                    "watchLive": False,
                    "idleTimeoutSec": 900,
                },
            )
            save_json_object(state_path, {"watchRunner": {"lastRunAt": "2026-03-09T10:00:00+00:00"}})
            log_path.write_text('{"kind":"opencode_watch_runtime_start_v1","startedAt":"2026-03-09T10:00:00+00:00"}\n')

            args = Namespace(registry_path=str(registry_path), include_exited=False, recover_missing_from_disk=True)
            with mock.patch("opencode_manager.list_watch_runtime_processes", return_value=self._runtime_map(config_path, pid=54321)):
                result = list_watchers_command(args)

            self.assertEqual(result["watcherCount"], 1)
            watcher = result["watchers"][0]
            self.assertEqual(watcher["watcherId"], "ow_recovered")
            self.assertEqual(watcher["opencodeSessionId"], "ses_recovered")
            self.assertEqual(watcher["watchProcessId"], 54321)

    def test_refresh_registry_entries_refreshes_existing_registry_entry_without_disk_recovery(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path, config_path, state_path = self._write_registry_running_entry(tmpdir, watcher_id="ow_existing")
            watcher_dir = Path(tmpdir) / "watchers" / "ow_untracked"
            untracked_config_path = watcher_dir / "config.json"
            untracked_state_path = watcher_dir / "state.json"
            untracked_log_path = watcher_dir / "watch.log"
            save_json_object(
                untracked_config_path,
                {
                    "opencodeBaseUrl": "http://127.0.0.1:4096",
                    "opencodeSessionId": "ses_untracked",
                    "opencodeWorkspace": "/tmp/demo-workspace",
                    "openclawSessionKey": "agent:main:discord:target:example-origin-thread",
                    "openclawDeliveryTarget": "discord:example-origin-thread",
                    "watchStatePath": str(untracked_state_path),
                    "watchLogPath": str(untracked_log_path),
                    "watchTimeoutSec": 20,
                    "watchMessageLimit": 10,
                    "watchIntervalSec": 60,
                    "watchLive": False,
                    "idleTimeoutSec": 900,
                },
            )
            save_json_object(state_path, {"watchRunner": {"lastRunAt": "2026-03-09T11:00:00+00:00", "lastOperation": "deliver"}})
            save_json_object(untracked_state_path, {"watchRunner": {"lastRunAt": "2026-03-09T12:00:00+00:00"}})
            untracked_log_path.write_text('{"kind":"opencode_watch_runtime_start_v1","startedAt":"2026-03-09T12:00:00+00:00"}\n')

            registry = __import__("opencode_manager").load_json_object(registry_path)
            runtime_map = self._runtime_map(config_path, pid=12345)
            runtime_map[str(untracked_config_path.resolve())] = {
                "pid": 54321,
                "configPath": str(untracked_config_path.resolve()),
                "command": f"python {SCRIPT_DIR / 'opencode_watch_runtime.py'} --config {untracked_config_path.resolve()}",
            }

            with mock.patch("opencode_manager.list_watch_runtime_processes", return_value=runtime_map):
                refreshed = refresh_registry_entries(registry, registry_path=registry_path)

            self.assertEqual(len(refreshed["watchers"]), 1)
            self.assertEqual(refreshed["watchers"][0]["watcherId"], "ow_existing")
            self.assertEqual(refreshed["watchers"][0]["watcherStatus"], "running")
            self.assertEqual(refreshed["watchers"][0]["lastWatchRunAt"], "2026-03-09T11:00:00+00:00")

    def test_start_command_defaults_to_live_watcher(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / "registry.json"
            args = Namespace(
                registry_path=str(registry_path),
                opencode_base_url="http://127.0.0.1:4096",
                opencode_token=None,
                opencode_token_env=None,
                watch_timeout_sec=20,
                opencode_workspace="/tmp/demo-workspace",
                title="Demo task",
                first_prompt="please start",
                first_prompt_file=None,
                ensure_watcher=True,
                openclaw_session_key="agent:main:discord:target:example-origin-thread",
                openclaw_delivery_target="discord:example-origin-thread",
                watch_live=True,
                watch_interval_sec=15,
                idle_timeout_sec=45,
                notify_min_interval_sec=0,
                notify_min_priority="low",
                notify_keyword=[],
                notify_filter_critical=False,
                watch_message_limit=8,
            )
            fake_client = mock.Mock()
            fake_client.create_session.return_value = {"id": "ses_demo", "directory": "/tmp/demo-workspace"}
            fake_client.prompt_session.return_value = None
            fake_watcher = {
                "watcherId": "ow_new",
                "watcherStatus": "running",
                "watchLive": True,
                "opencodeSessionId": "ses_demo",
                "opencodeWorkspace": "/tmp/demo-workspace",
                "openclawSessionKey": "agent:main:discord:target:example-origin-thread",
                "openclawDeliveryTarget": "discord:example-origin-thread",
            }

            with mock.patch("opencode_manager.OpenCodeClient", return_value=fake_client), mock.patch(
                "opencode_manager.start_or_attach_watcher", return_value=fake_watcher
            ) as start_watcher:
                result = start_command(args)

            self.assertEqual(start_watcher.call_args.kwargs["watch_live"], True)
            self.assertEqual(result["handoffMode"], "watcher_live")

    def test_start_command_returns_live_watcher_handoff_contract(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / "registry.json"
            args = Namespace(
                registry_path=str(registry_path),
                opencode_base_url="http://127.0.0.1:4096",
                opencode_token=None,
                opencode_token_env=None,
                watch_timeout_sec=20,
                opencode_workspace="/tmp/demo-workspace",
                title="Demo task",
                first_prompt="please start",
                openclaw_session_key="agent:main:discord:target:example-origin-thread",
                openclaw_delivery_target="discord:example-origin-thread",
                watch_live=True,
                watch_interval_sec=15,
                idle_timeout_sec=45,
                watch_message_limit=8,
            )
            fake_client = mock.Mock()
            fake_client.create_session.return_value = {"id": "ses_demo", "directory": "/tmp/demo-workspace"}
            fake_client.prompt_session.return_value = None
            fake_watcher = {
                "watcherId": "ow_new",
                "watcherStatus": "running",
                "watchLive": True,
                "opencodeSessionId": "ses_demo",
                "opencodeWorkspace": "/tmp/demo-workspace",
                "openclawSessionKey": "agent:main:discord:target:example-origin-thread",
                "openclawDeliveryTarget": "discord:example-origin-thread",
            }

            with mock.patch("opencode_manager.OpenCodeClient", return_value=fake_client), mock.patch(
                "opencode_manager.start_or_attach_watcher", return_value=fake_watcher
            ):
                result = start_command(args)

            self.assertEqual(result["handoffMode"], "watcher_live")
            self.assertEqual(result["agentAction"], "acknowledge_and_end_turn")
            self.assertIn("OpenCode", result["userFacingAck"])
            self.assertIn("OpenClaw", result["userFacingAck"])
            self.assertEqual(
                result["opencodeSession"]["opencodeUiUrl"],
                "http://127.0.0.1:4096/L3RtcC9kZW1vLXdvcmtzcGFjZQ/session/ses_demo",
            )
            fake_client.prompt_session.assert_called_once()

    def test_start_command_route_mismatch_fails_before_prompt_delivery(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / "registry.json"
            args = Namespace(
                registry_path=str(registry_path),
                opencode_base_url="http://127.0.0.1:4096",
                opencode_token=None,
                opencode_token_env=None,
                watch_timeout_sec=20,
                opencode_workspace="/tmp/demo-workspace",
                title="Demo task",
                first_prompt="please start",
                first_prompt_file=None,
                openclaw_session_key="agent:main:discord:target:example-origin-thread",
                openclaw_delivery_target="discord:example-origin-thread",
                watch_live=True,
                watch_interval_sec=15,
                idle_timeout_sec=45,
                watch_message_limit=8,
            )
            fake_client = mock.Mock()
            fake_client.create_session.return_value = {"id": "ses_demo", "directory": "/tmp/demo-workspace"}
            fake_watcher = {
                "watcherId": "ow_wrong",
                "watcherStatus": "running",
                "watchLive": True,
                "opencodeSessionId": "ses_demo",
                "opencodeWorkspace": "/tmp/demo-workspace",
                "openclawSessionKey": "agent:main:discord:target:example-opencode-thread",
                "openclawDeliveryTarget": "discord:example-opencode-thread",
            }

            with mock.patch("opencode_manager.OpenCodeClient", return_value=fake_client), mock.patch(
                "opencode_manager.start_or_attach_watcher", return_value=fake_watcher
            ):
                with self.assertRaisesRegex(RuntimeError, "route mismatch"):
                    start_command(args)

            fake_client.prompt_session.assert_not_called()

    def test_start_command_reads_first_prompt_from_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / "registry.json"
            prompt_path = Path(tmpdir) / "first-prompt.txt"
            prompt_path.write_text("line 1 with `backticks`\nline 2", encoding="utf-8")
            args = Namespace(
                registry_path=str(registry_path),
                opencode_base_url="http://127.0.0.1:4096",
                opencode_token=None,
                opencode_token_env=None,
                watch_timeout_sec=20,
                opencode_workspace="/tmp/demo-workspace",
                title="Demo task",
                first_prompt=None,
                first_prompt_file=str(prompt_path),
                openclaw_session_key="agent:main:discord:target:example-origin-thread",
                openclaw_delivery_target="discord:example-origin-thread",
                watch_live=True,
                watch_interval_sec=15,
                idle_timeout_sec=45,
                watch_message_limit=8,
            )
            fake_client = mock.Mock()
            fake_client.create_session.return_value = {"id": "ses_demo", "directory": "/tmp/demo-workspace"}
            fake_client.prompt_session.return_value = None
            fake_watcher = {
                "watcherId": "ow_new",
                "watcherStatus": "running",
                "watchLive": True,
                "opencodeSessionId": "ses_demo",
                "opencodeWorkspace": "/tmp/demo-workspace",
                "openclawSessionKey": "agent:main:discord:target:example-origin-thread",
                "openclawDeliveryTarget": "discord:example-origin-thread",
            }

            with mock.patch("opencode_manager.OpenCodeClient", return_value=fake_client), mock.patch(
                "opencode_manager.start_or_attach_watcher", return_value=fake_watcher
            ):
                result = start_command(args)

            fake_client.prompt_session.assert_called_once_with(
                "ses_demo",
                directory="/tmp/demo-workspace",
                parts=[{"type": "text", "text": "line 1 with `backticks`\nline 2"}],
                model=None,
                agent=None,
                variant=None,
                asynchronous=True,
            )
            self.assertEqual(result["firstPrompt"]["inputMethod"], "file")
            self.assertEqual(result["firstPrompt"]["promptFile"], str(prompt_path.resolve()))
            self.assertIn("line 1 with `backticks`", result["firstPrompt"]["promptPreview"])

    def test_start_command_explicit_watch_dry_run_overrides_default_live(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / "registry.json"
            args = Namespace(
                registry_path=str(registry_path),
                opencode_base_url="http://127.0.0.1:4096",
                opencode_token=None,
                opencode_token_env=None,
                watch_timeout_sec=20,
                opencode_workspace="/tmp/demo-workspace",
                title="Demo task",
                first_prompt="please start in dry-run mode",
                first_prompt_file=None,
                ensure_watcher=True,
                openclaw_session_key="agent:main:discord:target:example-origin-thread",
                openclaw_delivery_target="discord:example-origin-thread",
                watch_live=False,
                watch_interval_sec=15,
                idle_timeout_sec=45,
                notify_min_interval_sec=0,
                notify_min_priority="low",
                notify_keyword=[],
                notify_filter_critical=False,
                watch_message_limit=8,
            )
            fake_client = mock.Mock()
            fake_client.create_session.return_value = {"id": "ses_demo", "directory": "/tmp/demo-workspace"}
            fake_client.prompt_session.return_value = None
            fake_watcher = {
                "watcherId": "ow_new",
                "watcherStatus": "running",
                "watchLive": False,
                "opencodeSessionId": "ses_demo",
                "opencodeWorkspace": "/tmp/demo-workspace",
                "openclawSessionKey": "agent:main:discord:target:example-origin-thread",
                "openclawDeliveryTarget": "discord:example-origin-thread",
            }

            with mock.patch("opencode_manager.OpenCodeClient", return_value=fake_client), mock.patch(
                "opencode_manager.start_or_attach_watcher", return_value=fake_watcher
            ) as start_watcher:
                result = start_command(args)

            self.assertEqual(start_watcher.call_args.kwargs["watch_live"], False)
            self.assertEqual(result["handoffMode"], "watcher_not_live")

    def test_start_command_allows_explicit_no_watcher_opt_out(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / "registry.json"
            args = Namespace(
                registry_path=str(registry_path),
                opencode_base_url="http://127.0.0.1:4096",
                opencode_token=None,
                opencode_token_env=None,
                watch_timeout_sec=20,
                opencode_workspace="/tmp/demo-workspace",
                title="Demo task",
                first_prompt="please start without watcher",
                first_prompt_file=None,
                ensure_watcher=False,
                openclaw_session_key=None,
                openclaw_delivery_target=None,
                watch_live=False,
                watch_interval_sec=15,
                idle_timeout_sec=45,
                notify_min_interval_sec=0,
                notify_min_priority="low",
                notify_keyword=[],
                notify_filter_critical=False,
                watch_message_limit=8,
            )
            fake_client = mock.Mock()
            fake_client.create_session.return_value = {"id": "ses_demo", "directory": "/tmp/demo-workspace"}
            fake_client.prompt_session.return_value = None

            with mock.patch("opencode_manager.OpenCodeClient", return_value=fake_client), mock.patch(
                "opencode_manager.start_or_attach_watcher"
            ) as start_watcher:
                result = start_command(args)

            start_watcher.assert_not_called()
            self.assertEqual(result["handoffMode"], "no_watcher")
            self.assertTrue(result["firstPrompt"]["accepted"])
            self.assertNotIn("watcher", result)

    def test_start_command_forwards_agent_model_and_variant_overrides(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / "registry.json"
            args = Namespace(
                registry_path=str(registry_path),
                opencode_base_url="http://127.0.0.1:4096",
                opencode_token=None,
                opencode_token_env=None,
                watch_timeout_sec=20,
                opencode_workspace="/tmp/demo-workspace",
                title="Demo task",
                first_prompt="please start with explicit runtime overrides",
                first_prompt_file=None,
                opencode_agent="build",
                opencode_model="openai/gpt-5",
                opencode_variant="high",
                openclaw_session_key="agent:main:discord:target:example-origin-thread",
                openclaw_delivery_target="discord:example-origin-thread",
                watch_live=True,
                watch_interval_sec=15,
                idle_timeout_sec=45,
                watch_message_limit=8,
            )
            fake_client = mock.Mock()
            fake_client.create_session.return_value = {"id": "ses_demo", "directory": "/tmp/demo-workspace"}
            fake_client.prompt_session.return_value = None
            fake_watcher = {
                "watcherId": "ow_new",
                "watcherStatus": "running",
                "watchLive": True,
                "opencodeSessionId": "ses_demo",
                "opencodeWorkspace": "/tmp/demo-workspace",
                "openclawSessionKey": "agent:main:discord:target:example-origin-thread",
                "openclawDeliveryTarget": "discord:example-origin-thread",
            }

            with mock.patch("opencode_manager.OpenCodeClient", return_value=fake_client), mock.patch(
                "opencode_manager.start_or_attach_watcher", return_value=fake_watcher
            ):
                result = start_command(args)

            fake_client.prompt_session.assert_called_once_with(
                "ses_demo",
                directory="/tmp/demo-workspace",
                parts=[{"type": "text", "text": "please start with explicit runtime overrides"}],
                model={"providerID": "openai", "modelID": "gpt-5"},
                agent="build",
                variant="high",
                asynchronous=True,
            )
            self.assertEqual(
                result["promptOverrides"],
                {
                    "agent": "build",
                    "model": {"providerID": "openai", "modelID": "gpt-5"},
                    "variant": "high",
                },
            )

    def test_inspect_command_returns_rehydration_block_with_window_coverage(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path, config_path, _state_path = self._write_registry_running_entry(tmpdir)
            args = Namespace(
                registry_path=str(registry_path),
                opencode_base_url="http://127.0.0.1:4096",
                opencode_token=None,
                opencode_token_env=None,
                watch_timeout_sec=20,
                opencode_workspace="/tmp/demo-workspace",
                opencode_session_id="ses_demo",
                watch_message_limit=4,
            )
            fake_client = mock.Mock()
            fake_client.get_session.return_value = {
                "id": "ses_demo",
                "directory": "/tmp/demo-workspace",
                "title": "Demo task",
            }
            snapshot = {
                "latestMessage": {
                    "id": "msg_latest",
                    "role": "assistant",
                    "created": 1772718033111,
                    "status": "completed",
                    "message.lastTextPreview": "Patched manager inspect output.",
                },
                "latestTextPreview": "Patched manager inspect output.",
                "latestAssistantTextPreview": "Patched manager inspect output.",
                "latestUserInputSummary": "Please continue and summarize the current state.",
                "latestUserInputMessageId": "msg_user_latest",
                "accumulatedEventSummary": "user: Please continue and summarize the current state. | prune: Context compacted | text: Patched manager inspect output.",
                "eventLedger": [
                    {"kind": "user_input", "messageId": "msg_user_latest", "summary": "Please continue and summarize the current state.", "created": 1772718028071},
                    {"kind": "prune", "messageId": "msg_prune", "summary": "Context compacted", "created": 1772718029071},
                    {"kind": "text", "messageId": "msg_latest", "summary": "Patched manager inspect output.", "created": 1772718033111},
                ],
                "messageWindow": {
                    "observedMessageCount": 4,
                    "oldestMessageId": "msg_user_oldest",
                    "oldestMessageRole": "user",
                    "oldestMessageCreated": 1772718027000,
                    "newestMessageId": "msg_latest",
                    "newestMessageRole": "assistant",
                    "newestMessageCreated": 1772718033111,
                },
                "messageWindowSize": 4,
                "messageWindowLimit": 4,
                "todo": {
                    "items": [
                        {"content": "Collect current state", "status": "completed"},
                        {"content": "Return takeover summary", "status": "completed"},
                    ],
                    "phase": "Return takeover summary",
                    "hasPendingWork": False,
                    "allCompleted": True,
                },
                "permission": [],
                "question": [],
                "errors": {},
            }

            with mock.patch("opencode_manager.OpenCodeClient", return_value=fake_client), mock.patch(
                "opencode_manager.list_watch_runtime_processes", return_value=self._runtime_map(config_path)
            ), mock.patch("opencode_manager.build_compact_snapshot", return_value=(snapshot, {})):
                result = inspect_command(args)

            inspection = result["inspection"]
            self.assertEqual(inspection["currentStatus"], "completed")
            self.assertEqual(inspection["latestUserInputSummary"], "Please continue and summarize the current state.")
            self.assertEqual(inspection["completedWork"], ["Collect current state", "Return takeover summary"])
            self.assertNotIn("recentEventSummary", inspection)
            self.assertNotIn("recentEvents", inspection)
            self.assertNotIn("snapshotErrors", inspection)
            self.assertEqual(
                inspection["opencodeSession"],
                {
                    "opencodeSessionId": "ses_demo",
                    "title": "Demo task",
                    "opencodeWorkspace": "/tmp/demo-workspace",
                    "opencodeUiUrl": "http://127.0.0.1:4096/L3RtcC9kZW1vLXdvcmtzcGFjZQ/session/ses_demo",
                    "activeWatcherId": "ow_demo123",
                    "activeWatcherStatus": "running",
                },
            )
            self.assertEqual(
                inspection["latestMessage"],
                {
                    "id": "msg_latest",
                    "role": "assistant",
                    "status": "completed",
                    "createdAt": "2026-03-05T13:40:33.111000+00:00",
                    "textPreview": "Patched manager inspect output.",
                },
            )
            self.assertEqual(inspection["watcher"]["watcherId"], "ow_demo123")
            self.assertEqual(inspection["watcher"]["watcherStatus"], "running")
            self.assertIn("watchProcessAlive", inspection["watcher"])

            rehydration = inspection["rehydration"]
            self.assertEqual(rehydration["purpose"], "current_state_rebuild")
            self.assertEqual(rehydration["snapshotCoverage"]["requestedMessageLimit"], 4)
            self.assertEqual(rehydration["snapshotCoverage"]["observedMessageCount"], 4)
            self.assertTrue(rehydration["snapshotCoverage"]["mayExcludeOlderHistory"])
            self.assertEqual(rehydration["latestUserIntent"], "Please continue and summarize the current state.")
            self.assertEqual(rehydration["sinceLatestUserInput"]["anchor"]["messageId"], "msg_user_latest")
            self.assertEqual(rehydration["sinceLatestUserInput"]["eventCount"], 2)
            self.assertEqual(rehydration["sinceLatestUserInput"]["assistantMessageCount"], 2)
            self.assertEqual(rehydration["sinceLatestUserInput"]["latestAssistantText"], "Patched manager inspect output.")
            self.assertEqual(rehydration["sinceLatestUserInput"]["completedWork"][-1]["summary"], "Patched manager inspect output.")
            self.assertEqual(rehydration["recentCompletedWork"][-1]["summary"], "Patched manager inspect output.")
            self.assertEqual(rehydration["recentNotableEvents"][1]["label"], "prune")
            self.assertTrue(rehydration["followUpHints"]["preferTargetedLookup"])
            self.assertEqual(rehydration["followUpHints"]["suggestedRecentIndexes"], [0, 1, 2])
            self.assertIn("Need older context outside the retained inspect window.", rehydration["followUpHints"]["useInspectHistoryWhen"])
            self.assertIn("Need what happened between inspect points.", rehydration["followUpHints"]["useInspectHistoryWhen"])
            self.assertEqual(rehydration["watcherState"]["watcherStatus"], "running")
            self.assertEqual(rehydration["watcherState"]["watcherId"], "ow_demo123")

    def test_inspect_command_surfaces_running_stall_and_transport_hints(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path, config_path, state_path = self._write_registry_running_entry(tmpdir)
            save_json_object(
                state_path,
                {
                    "watchRunner": {
                        "lastRunAt": "2026-03-11T11:25:18+00:00",
                        "lastOperation": "skip",
                        "lastFactStatus": "running",
                        "lastRunningProgressObservation": {
                            "kind": "opencode_running_progress_observation_v1",
                            "status": "running_without_visible_progress",
                            "signalCodes": [
                                "running_no_visible_progress_since_latest_user_input",
                                "assistant_turn_started_without_visible_progress",
                            ],
                        },
                        "lastTransportErrorHints": [
                            {"name": "messages", "status": 429, "retryAfter": "30", "message": "HTTP 429 Too Many Requests"}
                        ],
                    }
                },
            )
            args = Namespace(
                registry_path=str(registry_path),
                opencode_base_url="http://127.0.0.1:4096",
                opencode_token=None,
                opencode_token_env=None,
                watch_timeout_sec=20,
                opencode_workspace="/tmp/demo-workspace",
                opencode_session_id="ses_demo",
                watch_message_limit=4,
            )
            fake_client = mock.Mock()
            fake_client.get_session.return_value = {
                "id": "ses_demo",
                "directory": "/tmp/demo-workspace",
                "title": "Demo task",
            }
            snapshot = {
                "latestMessage": {
                    "id": "msg_user_latest",
                    "role": "user",
                    "created": 1773227812213,
                    "status": "running",
                },
                "latestTextPreview": "Please continue this task.",
                "latestUserInputSummary": "Please continue this task.",
                "latestUserInputMessageId": "msg_user_latest",
                "accumulatedEventSummary": "user: Please continue this task.",
                "eventLedger": [
                    {"kind": "user_input", "messageId": "msg_user_latest", "summary": "Please continue this task.", "created": 1773227812213},
                ],
                "messageWindow": {
                    "observedMessageCount": 2,
                    "oldestMessageId": "msg_user_latest",
                    "oldestMessageRole": "user",
                    "oldestMessageCreated": 1773227812213,
                    "newestMessageId": "msg_assistant_empty",
                    "newestMessageRole": "assistant",
                    "newestMessageCreated": 1773227812219,
                },
                "messageWindowSize": 2,
                "messageWindowLimit": 4,
                "todo": {"items": [], "hasPendingWork": False, "allCompleted": False},
                "permission": [],
                "question": [],
                "errors": {
                    "messages": {
                        "kind": "opencode_api_error_v1",
                        "status": 429,
                        "retryAfter": "30",
                        "message": "GET /session/demo/message -> HTTP 429 Too Many Requests",
                    }
                },
            }

            with mock.patch("opencode_manager.OpenCodeClient", return_value=fake_client), mock.patch(
                "opencode_manager.list_watch_runtime_processes", return_value=self._runtime_map(config_path)
            ), mock.patch("opencode_manager.build_compact_snapshot", return_value=(snapshot, {})), mock.patch(
                "opencode_manager.datetime"
            ) as mocked_datetime:
                from datetime import datetime, timezone

                mocked_datetime.now.return_value = datetime.fromtimestamp(1773228118, tz=timezone.utc)
                mocked_datetime.fromtimestamp.side_effect = lambda *args, **kwargs: datetime.fromtimestamp(*args, **kwargs)
                result = inspect_command(args)

            inspection = result["inspection"]
            self.assertEqual(inspection["runningProgressObservation"]["status"], "running_without_visible_progress")
            self.assertTrue(inspection["runningProgressObservation"]["derived"])
            self.assertEqual(inspection["runningProgressObservation"]["origin"], "openclaw_compact_snapshot")
            self.assertIn(
                "assistant_turn_started_without_visible_progress",
                inspection["runningProgressObservation"]["signalCodes"],
            )
            self.assertEqual(inspection["runningProgressObservation"]["signals"][0]["origin"], "openclaw_compact_snapshot")
            self.assertIn("Derived from compact snapshot:", inspection["runningProgressObservation"]["signals"][0]["detail"])
            self.assertEqual(inspection["transportErrorHints"][0]["status"], 429)
            self.assertEqual(inspection["transportErrorHints"][0]["retryAfter"], "30")
            self.assertTrue(inspection["transportErrorHints"][0]["derived"])
            self.assertEqual(inspection["transportErrorHints"][0]["origin"], "openclaw_snapshot_errors")
            self.assertEqual(
                inspection["rehydration"]["currentState"]["runningProgressObservation"]["status"],
                "running_without_visible_progress",
            )
            self.assertIn(
                "If inspect still shows running without enough visible progress, expand one timeline item first; use inspect-history only if the gap remains.",
                inspection["rehydration"]["followUpHints"]["useInspectHistoryWhen"],
            )
            self.assertIn(
                "If transport/API errors may have hidden events, expand one timeline item first; use inspect-history only to verify older durable output.",
                inspection["rehydration"]["followUpHints"]["useInspectHistoryWhen"],
            )
            self.assertEqual(
                inspection["watcher"]["lastRunningProgressObservation"]["status"],
                "running_without_visible_progress",
            )
            self.assertEqual(inspection["watcher"]["lastTransportErrorHints"][0]["status"], 429)

    def test_inspect_command_marks_externally_aborted_message_failed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path, config_path, _state_path = self._write_registry_running_entry(tmpdir)
            args = Namespace(
                registry_path=str(registry_path),
                opencode_base_url="http://127.0.0.1:4096",
                opencode_token=None,
                opencode_token_env=None,
                watch_timeout_sec=20,
                opencode_workspace="/tmp/demo-workspace",
                opencode_session_id="ses_demo",
                watch_message_limit=4,
            )
            fake_client = mock.Mock()
            fake_client.get_session.return_value = {
                "id": "ses_demo",
                "directory": "/tmp/demo-workspace",
                "title": "Demo task",
            }
            snapshot = {
                "latestMessage": {
                    "id": "msg_abort",
                    "role": "assistant",
                    "created": 1773250176459,
                    "completedAt": 1773250176760,
                    "status": "failed",
                    "message.errorName": "MessageAbortedError",
                    "message.errorMessage": "The operation was aborted.",
                    "message.aborted": True,
                    "errorPreview": "MessageAbortedError: The operation was aborted.",
                },
                "latestUserInputSummary": "Continue the stop probe.",
                "latestUserInputMessageId": "msg_user_latest",
                "accumulatedEventSummary": "user: Continue the stop probe.",
                "eventLedger": [
                    {"kind": "user_input", "messageId": "msg_user_latest", "summary": "Continue the stop probe.", "created": 1773250176000},
                ],
                "messageWindow": {
                    "observedMessageCount": 2,
                    "oldestMessageId": "msg_user_latest",
                    "oldestMessageRole": "user",
                    "oldestMessageCreated": 1773250176000,
                    "newestMessageId": "msg_abort",
                    "newestMessageRole": "assistant",
                    "newestMessageCreated": 1773250176459,
                },
                "messageWindowSize": 2,
                "messageWindowLimit": 4,
                "todo": {"items": [], "hasPendingWork": False, "allCompleted": False},
                "permission": [],
                "question": [],
                "errors": {},
            }

            with mock.patch("opencode_manager.OpenCodeClient", return_value=fake_client), mock.patch(
                "opencode_manager.list_watch_runtime_processes", return_value=self._runtime_map(config_path)
            ), mock.patch("opencode_manager.build_compact_snapshot", return_value=(snapshot, {})):
                result = inspect_command(args)

            inspection = result["inspection"]
            self.assertEqual(inspection["currentStatus"], "failed")
            self.assertIn("MessageAbortedError", inspection["latestMeaningfulPreview"])
            self.assertTrue(inspection["latestMessage"]["message.aborted"])
            self.assertEqual(inspection["rehydration"]["currentState"]["status"], "failed")
            self.assertIn("aborted", inspection["rehydration"]["currentState"]["latestMeaningfulPreview"].lower())

    def test_inspect_command_surfaces_structured_blocker_details(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path, config_path, _state_path = self._write_registry_running_entry(tmpdir)
            args = Namespace(
                registry_path=str(registry_path),
                opencode_base_url="http://127.0.0.1:4096",
                opencode_token=None,
                opencode_token_env=None,
                watch_timeout_sec=20,
                opencode_workspace="/tmp/demo-workspace",
                opencode_session_id="ses_demo",
                watch_message_limit=4,
            )
            fake_client = mock.Mock()
            fake_client.get_session.return_value = {
                "id": "ses_demo",
                "directory": "/tmp/demo-workspace",
                "title": "Demo task",
            }
            snapshot = {
                "latestMessage": {
                    "id": "msg_user_latest",
                    "role": "user",
                    "created": 1773227812213,
                    "status": "running",
                },
                "latestTextPreview": "Please continue this task.",
                "latestUserInputSummary": "Please continue this task.",
                "latestUserInputMessageId": "msg_user_latest",
                "accumulatedEventSummary": "user: Please continue this task.",
                "eventLedger": [
                    {"kind": "user_input", "messageId": "msg_user_latest", "summary": "Please continue this task.", "created": 1773227812213},
                ],
                "messageWindow": {
                    "observedMessageCount": 1,
                    "oldestMessageId": "msg_user_latest",
                    "oldestMessageRole": "user",
                    "oldestMessageCreated": 1773227812213,
                    "newestMessageId": "msg_user_latest",
                    "newestMessageRole": "user",
                    "newestMessageCreated": 1773227812213,
                },
                "messageWindowSize": 1,
                "messageWindowLimit": 4,
                "todo": {"items": [], "hasPendingWork": False, "allCompleted": False},
                "permission": [{"id": "perm_1"}],
                "question": [],
                "pendingPrompts": [
                    {
                        "kind": "permission",
                        "promptKey": "permission:id:perm_1",
                        "promptId": "perm_1",
                        "scope": "session_match",
                        "summary": "Allow write to scripts/opencode_snapshot.py",
                        "messageId": "msg_user_latest",
                        "callId": "call_patch_1",
                    }
                ],
                "blockedPromptCount": 1,
                "blockedPromptKey": "permission:id:perm_1",
                "blockedPhase": "Permission pending",
                "blockedSummary": "Permission: Allow write to scripts/opencode_snapshot.py",
                "promptScope": {
                    "mode": "session_match",
                    "total": 1,
                    "matched": 1,
                    "unscoped": 0,
                    "mismatched": 0,
                    "permissionRawCount": 1,
                    "questionRawCount": 0,
                },
                "errors": {},
            }

            with mock.patch("opencode_manager.OpenCodeClient", return_value=fake_client), mock.patch(
                "opencode_manager.list_watch_runtime_processes", return_value=self._runtime_map(config_path)
            ), mock.patch("opencode_manager.build_compact_snapshot", return_value=(snapshot, {})):
                result = inspect_command(args)

            inspection = result["inspection"]
            self.assertEqual(inspection["currentStatus"], "blocked")
            self.assertEqual(inspection["currentPhase"], "Permission pending")
            self.assertIn("Permission: Allow write", inspection["latestMeaningfulPreview"])
            self.assertEqual(inspection["currentBlocker"]["blockedPromptKey"], "permission:id:perm_1")
            self.assertEqual(inspection["currentBlocker"]["pendingPrompts"][0]["messageId"], "msg_user_latest")
            self.assertEqual(inspection["currentBlocker"]["pendingPrompts"][0]["callId"], "call_patch_1")
            self.assertEqual(inspection["rehydration"]["currentState"]["blockedPromptCount"], 1)
            self.assertEqual(inspection["rehydration"]["currentState"]["pendingPermissionCount"], 1)
            self.assertEqual(inspection["rehydration"]["currentState"]["openQuestionCount"], 0)

    def test_inspect_command_default_text_output_is_timeline_first(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path, config_path, _state_path = self._write_registry_running_entry(tmpdir)
            args = Namespace(
                registry_path=str(registry_path),
                opencode_base_url="http://127.0.0.1:4096",
                opencode_token=None,
                opencode_token_env=None,
                watch_timeout_sec=20,
                opencode_workspace="/tmp/demo-workspace",
                opencode_session_id="ses_demo",
                watch_message_limit=4,
                output_format="text",
                timeline_limit=4,
                expand_index=None,
                expand_message_limit=10,
                show_ids=False,
            )
            fake_client = mock.Mock()
            fake_client.get_session.return_value = {
                "id": "ses_demo",
                "directory": "/tmp/demo-workspace",
                "title": "Demo task",
            }
            snapshot = {
                "latestMessage": {"id": "msg_a", "role": "assistant", "status": "running", "created": 1773227812220},
                "latestTextPreview": "Still running",
                "latestUserInputSummary": "Please continue",
                "latestUserInputMessageId": "msg_u",
                "eventLedger": [
                    {"kind": "user_input", "messageId": "msg_u", "summary": "Please continue", "created": 1773227812200},
                    {"kind": "tool", "messageId": "msg_a", "toolName": "edit", "summary": "patched file", "toolStatus": "completed", "created": 1773227812210},
                    {"kind": "text", "messageId": "msg_a", "role": "assistant", "summary": "Applied patch and running tests", "created": 1773227812220},
                ],
                "messageWindow": {
                    "observedMessageCount": 2,
                    "oldestMessageId": "msg_u",
                    "newestMessageId": "msg_a",
                },
                "messageWindowSize": 2,
                "messageWindowLimit": 4,
                "todo": {"items": [], "hasPendingWork": False, "allCompleted": False},
                "permission": [],
                "question": [],
                "errors": {},
            }

            with mock.patch("opencode_manager.OpenCodeClient", return_value=fake_client), mock.patch(
                "opencode_manager.list_watch_runtime_processes", return_value=self._runtime_map(config_path)
            ), mock.patch("opencode_manager.build_compact_snapshot", return_value=(snapshot, {})):
                result = inspect_command(args)

            rendered = result["renderedText"]
            self.assertIn("Session: Demo task", rendered)
            self.assertIn("Timeline:", rendered)
            self.assertIn("[#01]", rendered)
            self.assertIn("tool[edit]", rendered)
            self.assertIn("Tip: use --expand-index <n>", rendered)
            self.assertNotIn("messageId=", rendered)

    def test_inspect_command_expand_index_returns_single_item_detail(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path, config_path, _state_path = self._write_registry_running_entry(tmpdir)
            args = Namespace(
                registry_path=str(registry_path),
                opencode_base_url="http://127.0.0.1:4096",
                opencode_token=None,
                opencode_token_env=None,
                watch_timeout_sec=20,
                opencode_workspace="/tmp/demo-workspace",
                opencode_session_id="ses_demo",
                watch_message_limit=4,
                output_format="text",
                timeline_limit=4,
                expand_index=2,
                expand_message_limit=10,
                show_ids=False,
            )
            fake_client = mock.Mock()
            fake_client.get_session.return_value = {
                "id": "ses_demo",
                "directory": "/tmp/demo-workspace",
                "title": "Demo task",
            }
            snapshot = {
                "latestMessage": {"id": "msg_a", "role": "assistant", "status": "running", "created": 1773227812220},
                "latestTextPreview": "Still running",
                "latestUserInputSummary": "Please continue",
                "latestUserInputMessageId": "msg_u",
                "eventLedger": [
                    {"kind": "user_input", "messageId": "msg_u", "summary": "Please continue", "created": 1773227812200},
                    {"kind": "tool", "messageId": "msg_a", "toolName": "bash", "summary": "tests passed", "toolStatus": "completed", "created": 1773227812210},
                ],
                "messageWindow": {
                    "observedMessageCount": 2,
                    "oldestMessageId": "msg_u",
                    "newestMessageId": "msg_a",
                },
                "messageWindowSize": 2,
                "messageWindowLimit": 4,
                "todo": {"items": [], "hasPendingWork": False, "allCompleted": False},
                "permission": [],
                "question": [],
                "errors": {},
            }
            fake_client.session_messages.return_value = [
                {
                    "info": {"id": "msg_u", "role": "user", "time": {"created": 1773227812200}},
                    "parts": [{"type": "text", "text": "Please continue"}],
                },
                {
                    "info": {"id": "msg_a", "role": "assistant", "time": {"created": 1773227812210}},
                    "parts": [
                        {
                            "type": "tool",
                            "tool": "bash",
                            "state": {
                                "status": "completed",
                                "input": {"command": "python3 -m unittest"},
                                "output": "ok\nall tests passed",
                            },
                        },
                        {"type": "text", "text": "Done. Tests are green."},
                    ],
                },
            ]

            with mock.patch("opencode_manager.OpenCodeClient", return_value=fake_client), mock.patch(
                "opencode_manager.list_watch_runtime_processes", return_value=self._runtime_map(config_path)
            ), mock.patch("opencode_manager.build_compact_snapshot", return_value=(snapshot, {})):
                result = inspect_command(args)

            self.assertEqual(result["expanded"]["timelineItem"]["index"], 2)
            self.assertIn("Expanded timeline item", result["renderedText"])
            self.assertIn("Tool details:", result["renderedText"])

    def test_inspect_history_command_surfaces_patch_targets_and_new_text(self):
        args = Namespace(
            registry_path="/tmp/opencode-history-registry.json",
            opencode_base_url="http://127.0.0.1:4096",
            opencode_token=None,
            opencode_token_env=None,
            watch_timeout_sec=20,
            opencode_workspace="/tmp/demo-workspace",
            opencode_session_id="ses_demo",
            message_id=None,
            recent_index=2,
            latest=False,
            history_message_limit=6,
        )
        fake_client = mock.Mock()
        fake_client.get_session.return_value = {
            "id": "ses_demo",
            "directory": "/tmp/demo-workspace",
            "title": "Demo task",
        }
        fake_client.session_messages.return_value = [
            {
                "info": {"role": "user", "time": {"created": 1772903315000}, "id": "msg_user"},
                "parts": [{"type": "text", "text": "Please patch notes.py and show the last pytest lines."}],
            },
            {
                "info": {"role": "assistant", "time": {"created": 1772903315600, "completed": 1772903315610}, "id": "msg_read"},
                "parts": [
                    {
                        "type": "tool",
                        "tool": "read",
                        "input": {"filePath": "/mnt/vault/test-opencode-skill/app/notes.py"},
                        "state": {"status": "completed", "output": "def old_func():\n    return 1\n"},
                    }
                ],
            },
            {
                "info": {"role": "assistant", "time": {"created": 1772903315700, "completed": 1772903315710}, "id": "msg_edit"},
                "parts": [
                    {
                        "type": "tool",
                        "tool": "edit",
                        "input": {
                            "path": "/mnt/vault/test-opencode-skill/app/notes.py",
                            "oldText": "return 1",
                            "newText": "return 2",
                        },
                        "state": {"status": "completed", "output": "patched app/notes.py"},
                    }
                ],
            },
            {
                "info": {"role": "assistant", "time": {"created": 1772903315800, "completed": 1772903315810}, "id": "msg_bash"},
                "parts": [
                    {
                        "type": "tool",
                        "tool": "bash",
                        "input": {"command": "cd /mnt/vault/test-opencode-skill && pytest -q"},
                        "state": {
                            "status": "completed",
                            "output": "== pytest ==\n1 failed, 10 passed\nnotes.py::test_edit PASSED\nAll done",
                        },
                    }
                ],
            },
            {
                "info": {"role": "assistant", "time": {"created": 1772903315900, "completed": 1772903315910}, "id": "msg_text"},
                "parts": [{"type": "text", "text": "Patched notes.py and reran pytest. The final lines show the suite is green."}],
            },
        ]

        with mock.patch("opencode_manager.OpenCodeClient", return_value=fake_client), mock.patch(
            "opencode_manager.locked_registry"
        ) as locked_registry, mock.patch("opencode_manager.refresh_registry_entries"):
            locked_registry.return_value.__enter__.return_value = ({"watchers": []}, Path(args.registry_path))
            locked_registry.return_value.__exit__.return_value = None
            result = inspect_history_command(args)

        history = result["history"]
        self.assertEqual(history["selection"]["messageId"], "msg_edit")
        self.assertEqual(history["selection"]["recentIndex"], 2)
        self.assertEqual(history["recentAnchors"][0]["recentIndex"], 0)
        tool_call = history["message"]["toolCalls"][0]
        self.assertEqual(tool_call["action"], "patch")
        self.assertEqual(tool_call["patchTargets"], ["/mnt/vault/test-opencode-skill/app/notes.py"])
        self.assertEqual(tool_call["newText"], "return 2")
        self.assertEqual(tool_call["newTextPreview"], "return 2")

    def test_inspect_history_command_can_select_message_id_and_tail_shell_output(self):
        args = Namespace(
            registry_path="/tmp/opencode-history-registry.json",
            opencode_base_url="http://127.0.0.1:4096",
            opencode_token=None,
            opencode_token_env=None,
            watch_timeout_sec=20,
            opencode_workspace="/tmp/demo-workspace",
            opencode_session_id="ses_demo",
            message_id="msg_bash",
            recent_index=None,
            latest=False,
            history_message_limit=6,
        )
        fake_client = mock.Mock()
        fake_client.get_session.return_value = {
            "id": "ses_demo",
            "directory": "/tmp/demo-workspace",
            "title": "Demo task",
        }
        fake_client.session_messages.return_value = [
            {"info": {"role": "user", "time": {"created": 1772903315000}, "id": "msg_user"}, "parts": [{"type": "text", "text": "run pytest"}]},
            {
                "info": {"role": "assistant", "time": {"created": 1772903315800, "completed": 1772903315810}, "id": "msg_bash"},
                "parts": [
                    {
                        "type": "tool",
                        "tool": "bash",
                        "input": {"command": "cd /mnt/vault/test-opencode-skill && pytest -q"},
                        "state": {
                            "status": "completed",
                            "output": "PWD=/mnt/vault/test-opencode-skill\ncollecting tests\nnotes.py::test_edit PASSED\nAll done",
                        },
                    }
                ],
            },
        ]

        with mock.patch("opencode_manager.OpenCodeClient", return_value=fake_client), mock.patch(
            "opencode_manager.locked_registry"
        ) as locked_registry, mock.patch("opencode_manager.refresh_registry_entries"):
            locked_registry.return_value.__enter__.return_value = ({"watchers": []}, Path(args.registry_path))
            locked_registry.return_value.__exit__.return_value = None
            result = inspect_history_command(args)

        history = result["history"]
        self.assertEqual(history["selection"]["messageId"], "msg_bash")
        self.assertEqual(history["message"]["recentIndex"], 0)
        tool_call = history["message"]["toolCalls"][0]
        self.assertEqual(tool_call["action"], "shell")
        self.assertIn("pytest -q", tool_call["commandPreview"])
        self.assertEqual(tool_call["outputTailLines"][-1], "All done")
        self.assertIn("notes.py::test_edit PASSED", tool_call["outputTailLines"])

    def test_attach_command_defaults_to_live_watcher(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / "registry.json"
            args = Namespace(
                registry_path=str(registry_path),
                opencode_base_url="http://127.0.0.1:4096",
                opencode_token=None,
                opencode_token_env=None,
                watch_timeout_sec=20,
                opencode_workspace=None,
                opencode_session_id="ses_demo",
                openclaw_session_key="agent:main:discord:target:example-origin-thread",
                openclaw_delivery_target="discord:example-origin-thread",
                watch_live=True,
                watch_interval_sec=15,
                idle_timeout_sec=45,
                notify_min_interval_sec=0,
                notify_min_priority="low",
                notify_keyword=[],
                notify_filter_critical=False,
                watch_message_limit=3,
            )
            fake_client = mock.Mock()
            fake_client.get_session.return_value = {
                "id": "ses_demo",
                "directory": "/tmp/demo-workspace",
                "title": "Demo task",
            }
            fake_watcher = {
                "watcherId": "ow_new",
                "watcherStatus": "running",
                "watchLive": True,
                "opencodeSessionId": "ses_demo",
                "opencodeWorkspace": "/tmp/demo-workspace",
                "openclawSessionKey": "agent:main:discord:target:example-origin-thread",
                "openclawDeliveryTarget": "discord:example-origin-thread",
            }

            with mock.patch("opencode_manager.OpenCodeClient", return_value=fake_client), mock.patch(
                "opencode_manager.start_or_attach_watcher", return_value=fake_watcher
            ) as start_watcher, mock.patch("opencode_manager.build_compact_snapshot", return_value=({}, {})):
                attach_command(args)

            self.assertEqual(start_watcher.call_args.kwargs["watch_live"], True)

    def test_attach_command_returns_immediate_inspection_for_takeover(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / "registry.json"
            args = Namespace(
                registry_path=str(registry_path),
                opencode_base_url="http://127.0.0.1:4096",
                opencode_token=None,
                opencode_token_env=None,
                watch_timeout_sec=20,
                opencode_workspace=None,
                opencode_session_id="ses_demo",
                openclaw_session_key="agent:main:discord:target:example-origin-thread",
                openclaw_delivery_target="discord:example-origin-thread",
                watch_live=True,
                watch_interval_sec=15,
                idle_timeout_sec=45,
                watch_message_limit=3,
            )
            fake_client = mock.Mock()
            fake_client.get_session.return_value = {
                "id": "ses_demo",
                "directory": "/tmp/demo-workspace",
                "title": "Demo task",
            }
            fake_watcher = {
                "watcherId": "ow_new",
                "watcherStatus": "running",
                "watchLive": True,
                "watchProcessAlive": True,
                "opencodeSessionId": "ses_demo",
                "opencodeWorkspace": "/tmp/demo-workspace",
                "openclawSessionKey": "agent:main:discord:target:example-origin-thread",
                "openclawDeliveryTarget": "discord:example-origin-thread",
                "lastWatchRunAt": "2026-03-10T05:00:00+00:00",
                "lastWatchOperation": "plan",
            }
            snapshot = {
                "latestMessage": {
                    "id": "msg_user_latest",
                    "role": "user",
                    "created": 1772718034000,
                    "status": "running",
                },
                "latestTextPreview": "Need a quick takeover summary.",
                "latestAssistantTextPreview": "Earlier work completed successfully.",
                "latestUserInputSummary": "Need a quick takeover summary.",
                "latestUserInputMessageId": "msg_user_latest",
                "accumulatedEventSummary": "text: Earlier work completed successfully. | user: Need a quick takeover summary.",
                "eventLedger": [
                    {"kind": "text", "messageId": "msg_prev", "summary": "Earlier work completed successfully.", "created": 1772718033000},
                    {"kind": "user_input", "messageId": "msg_user_latest", "summary": "Need a quick takeover summary.", "created": 1772718034000},
                ],
                "messageWindow": {
                    "observedMessageCount": 2,
                    "oldestMessageId": "msg_prev",
                    "oldestMessageRole": "assistant",
                    "oldestMessageCreated": 1772718033000,
                    "newestMessageId": "msg_user_latest",
                    "newestMessageRole": "user",
                    "newestMessageCreated": 1772718034000,
                },
                "messageWindowSize": 2,
                "messageWindowLimit": 3,
                "todo": {
                    "items": [
                        {"content": "Earlier work completed successfully", "status": "completed"},
                        {"content": "Answer the latest user request", "status": "pending"},
                    ],
                    "phase": "Answer the latest user request",
                    "hasPendingWork": True,
                    "allCompleted": False,
                },
                "permission": [],
                "question": [],
                "errors": {},
            }

            with mock.patch("opencode_manager.OpenCodeClient", return_value=fake_client), mock.patch(
                "opencode_manager.start_or_attach_watcher", return_value=fake_watcher
            ), mock.patch("opencode_manager.build_compact_snapshot", return_value=(snapshot, {})):
                result = attach_command(args)

            self.assertEqual(result["watcher"]["watcherId"], "ow_new")
            self.assertIn("inspection", result)
            inspection = result["inspection"]
            self.assertEqual(inspection["currentStatus"], "running")
            self.assertEqual(inspection["currentPhase"], "Answer the latest user request")
            self.assertEqual(inspection["rehydration"]["snapshotCoverage"]["requestedMessageLimit"], 3)
            self.assertEqual(inspection["rehydration"]["latestUserIntent"], "Need a quick takeover summary.")
            self.assertEqual(inspection["rehydration"]["sinceLatestUserInput"]["eventCount"], 0)
            self.assertEqual(inspection["rehydration"]["sinceLatestUserInput"]["assistantMessageCount"], 0)
            self.assertIsNone(inspection["rehydration"]["sinceLatestUserInput"].get("latestAssistantText"))
            self.assertEqual(inspection["rehydration"]["watcherState"]["watcherId"], "ow_new")

    def test_continue_command_defaults_to_live_watcher_for_new_binding(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / "registry.json"
            save_json_object(registry_path, {"kind": "opencode_manager_registry_v1", "watchers": []})
            args = Namespace(
                opencode_base_url="http://127.0.0.1:4096",
                opencode_token=None,
                opencode_token_env=None,
                opencode_workspace=None,
                opencode_session_id="ses_demo",
                follow_up_prompt="please continue",
                follow_up_prompt_file=None,
                ensure_watcher=True,
                openclaw_session_key="agent:main:discord:target:example-origin-thread",
                openclaw_delivery_target="discord:example-origin-thread",
                watch_live=True,
                watch_interval_sec=None,
                idle_timeout_sec=None,
                watch_message_limit=None,
                watch_timeout_sec=None,
                notify_min_interval_sec=None,
                notify_min_priority=None,
                notify_keyword=None,
                notify_filter_critical=None,
                registry_path=str(registry_path),
                opencode_agent=None,
                opencode_model=None,
                opencode_variant=None,
            )
            fake_client = mock.Mock()
            fake_client.get_session.return_value = {"id": "ses_demo", "directory": "/tmp/demo-workspace"}
            fake_client.prompt_session.return_value = None
            fake_watcher = {
                "watcherId": "ow_new",
                "watcherStatus": "running",
                "watchLive": True,
                "opencodeSessionId": "ses_demo",
                "opencodeWorkspace": "/tmp/demo-workspace",
                "openclawSessionKey": "agent:main:discord:target:example-origin-thread",
                "openclawDeliveryTarget": "discord:example-origin-thread",
            }

            with mock.patch("opencode_manager.OpenCodeClient", return_value=fake_client), mock.patch(
                "opencode_manager.list_watch_runtime_processes", return_value={}
            ), mock.patch("opencode_manager.start_or_attach_watcher", return_value=fake_watcher) as start_watcher:
                result = continue_command(args)

            self.assertEqual(start_watcher.call_args.kwargs["watch_live"], True)
            self.assertEqual(result["handoffMode"], "watcher_live")

    def test_continue_command_defaults_to_live_even_if_latest_watcher_was_dry_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / "registry.json"
            save_json_object(
                registry_path,
                {
                    "kind": "opencode_manager_registry_v1",
                    "watchers": [
                        {
                            "watcherId": "ow_old",
                            "watcherStatus": "exited",
                            "opencodeSessionId": "ses_demo",
                            "opencodeWorkspace": "/tmp/demo-workspace",
                            "openclawSessionKey": "agent:main:discord:target:old-origin-thread",
                            "openclawDeliveryTarget": "discord:old-origin-thread",
                            "watchLive": False,
                            "watchIntervalSec": 15,
                            "idleTimeoutSec": 45,
                            "watchMessageLimit": 8,
                            "watchTimeoutSec": 25,
                            "watcherConfigPath": str(Path(tmpdir) / "watchers" / "ow_old" / "config.json"),
                            "watcherStatePath": str(Path(tmpdir) / "watchers" / "ow_old" / "state.json"),
                            "watcherLogPath": str(Path(tmpdir) / "watchers" / "ow_old" / "watch.log"),
                        }
                    ],
                },
            )
            args = Namespace(
                opencode_base_url="http://127.0.0.1:4096",
                opencode_token=None,
                opencode_token_env=None,
                opencode_workspace=None,
                opencode_session_id="ses_demo",
                follow_up_prompt="please continue",
                follow_up_prompt_file=None,
                ensure_watcher=True,
                openclaw_session_key="agent:main:discord:target:example-origin-thread",
                openclaw_delivery_target="discord:example-origin-thread",
                watch_live=None,
                watch_interval_sec=None,
                idle_timeout_sec=None,
                watch_message_limit=None,
                watch_timeout_sec=None,
                notify_min_interval_sec=None,
                notify_min_priority=None,
                notify_keyword=None,
                notify_filter_critical=None,
                registry_path=str(registry_path),
                opencode_agent=None,
                opencode_model=None,
                opencode_variant=None,
            )
            fake_client = mock.Mock()
            fake_client.get_session.return_value = {"id": "ses_demo", "directory": "/tmp/demo-workspace"}
            fake_client.prompt_session.return_value = None
            fake_watcher = {
                "watcherId": "ow_new",
                "watcherStatus": "running",
                "watchLive": True,
                "opencodeSessionId": "ses_demo",
                "opencodeWorkspace": "/tmp/demo-workspace",
                "openclawSessionKey": "agent:main:discord:target:example-origin-thread",
                "openclawDeliveryTarget": "discord:example-origin-thread",
            }

            with mock.patch("opencode_manager.OpenCodeClient", return_value=fake_client), mock.patch(
                "opencode_manager.list_watch_runtime_processes", return_value={}
            ), mock.patch("opencode_manager.start_or_attach_watcher", return_value=fake_watcher) as start_watcher:
                result = continue_command(args)

            self.assertEqual(start_watcher.call_args.kwargs["watch_live"], True)
            self.assertEqual(result["handoffMode"], "watcher_live")

    def test_continue_command_explicit_watch_dry_run_overrides_default_live(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / "registry.json"
            save_json_object(registry_path, {"kind": "opencode_manager_registry_v1", "watchers": []})
            args = Namespace(
                opencode_base_url="http://127.0.0.1:4096",
                opencode_token=None,
                opencode_token_env=None,
                opencode_workspace=None,
                opencode_session_id="ses_demo",
                follow_up_prompt="please continue",
                follow_up_prompt_file=None,
                ensure_watcher=True,
                openclaw_session_key="agent:main:discord:target:example-origin-thread",
                openclaw_delivery_target="discord:example-origin-thread",
                watch_live=False,
                watch_interval_sec=None,
                idle_timeout_sec=None,
                watch_message_limit=None,
                watch_timeout_sec=None,
                notify_min_interval_sec=None,
                notify_min_priority=None,
                notify_keyword=None,
                notify_filter_critical=None,
                registry_path=str(registry_path),
                opencode_agent=None,
                opencode_model=None,
                opencode_variant=None,
            )
            fake_client = mock.Mock()
            fake_client.get_session.return_value = {"id": "ses_demo", "directory": "/tmp/demo-workspace"}
            fake_client.prompt_session.return_value = None
            fake_watcher = {
                "watcherId": "ow_new",
                "watcherStatus": "running",
                "watchLive": False,
                "opencodeSessionId": "ses_demo",
                "opencodeWorkspace": "/tmp/demo-workspace",
                "openclawSessionKey": "agent:main:discord:target:example-origin-thread",
                "openclawDeliveryTarget": "discord:example-origin-thread",
            }

            with mock.patch("opencode_manager.OpenCodeClient", return_value=fake_client), mock.patch(
                "opencode_manager.list_watch_runtime_processes", return_value={}
            ), mock.patch("opencode_manager.start_or_attach_watcher", return_value=fake_watcher) as start_watcher:
                result = continue_command(args)

            self.assertEqual(start_watcher.call_args.kwargs["watch_live"], False)
            self.assertEqual(result["handoffMode"], "watcher_not_live")

    def test_continue_command_requires_explicit_current_openclaw_binding(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / "registry.json"
            save_json_object(
                registry_path,
                {
                    "kind": "opencode_manager_registry_v1",
                    "watchers": [
                        {
                            "watcherId": "ow_old",
                            "watcherStatus": "exited",
                            "opencodeSessionId": "ses_demo",
                            "opencodeWorkspace": "/tmp/demo-workspace",
                            "openclawSessionKey": "agent:main:discord:target:example-origin-thread",
                            "openclawDeliveryTarget": "discord:example-origin-thread",
                            "watchLive": True,
                            "watchIntervalSec": 15,
                            "idleTimeoutSec": 45,
                            "watchMessageLimit": 8,
                            "watchTimeoutSec": 25,
                            "watcherConfigPath": str(Path(tmpdir) / "watchers" / "ow_old" / "config.json"),
                            "watcherStatePath": str(Path(tmpdir) / "watchers" / "ow_old" / "state.json"),
                            "watcherLogPath": str(Path(tmpdir) / "watchers" / "ow_old" / "watch.log"),
                        }
                    ],
                },
            )
            args = Namespace(
                opencode_base_url="http://127.0.0.1:4096",
                opencode_token=None,
                opencode_token_env=None,
                opencode_workspace=None,
                opencode_session_id="ses_demo",
                follow_up_prompt="please continue",
                ensure_watcher=True,
                openclaw_session_key=None,
                openclaw_delivery_target=None,
                watch_live=None,
                watch_interval_sec=None,
                idle_timeout_sec=None,
                watch_message_limit=None,
                watch_timeout_sec=None,
                registry_path=str(registry_path),
            )
            fake_client = mock.Mock()

            with mock.patch("opencode_manager.OpenCodeClient", return_value=fake_client):
                with self.assertRaisesRegex(ValueError, "requires --openclaw-session-key"):
                    continue_command(args)

            fake_client.get_session.assert_not_called()
            fake_client.prompt_session.assert_not_called()

    def test_continue_command_route_mismatch_fails_before_prompt_delivery(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / "registry.json"
            save_json_object(registry_path, {"kind": "opencode_manager_registry_v1", "watchers": []})
            args = Namespace(
                opencode_base_url="http://127.0.0.1:4096",
                opencode_token=None,
                opencode_token_env=None,
                opencode_workspace=None,
                opencode_session_id="ses_demo",
                follow_up_prompt="please continue",
                follow_up_prompt_file=None,
                ensure_watcher=True,
                openclaw_session_key="agent:main:discord:target:example-route-10",
                openclaw_delivery_target="discord:example-route-10",
                watch_live=None,
                watch_interval_sec=None,
                idle_timeout_sec=None,
                watch_message_limit=None,
                watch_timeout_sec=None,
                registry_path=str(registry_path),
            )
            fake_client = mock.Mock()
            fake_client.get_session.return_value = {"id": "ses_demo", "directory": "/tmp/demo-workspace"}
            fake_watcher = {
                "watcherId": "ow_wrong",
                "watcherStatus": "running",
                "watchLive": True,
                "opencodeSessionId": "ses_demo",
                "opencodeWorkspace": "/tmp/demo-workspace",
                "openclawSessionKey": "agent:main:discord:target:example-opencode-thread",
                "openclawDeliveryTarget": "discord:example-opencode-thread",
            }

            with mock.patch("opencode_manager.OpenCodeClient", return_value=fake_client), mock.patch(
                "opencode_manager.list_watch_runtime_processes", return_value={}
            ), mock.patch("opencode_manager.start_or_attach_watcher", return_value=fake_watcher):
                with self.assertRaisesRegex(RuntimeError, "route mismatch"):
                    continue_command(args)

            fake_client.prompt_session.assert_not_called()

    def test_continue_command_reads_follow_up_prompt_from_stdin(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / "registry.json"
            save_json_object(registry_path, {"kind": "opencode_manager_registry_v1", "watchers": []})
            args = Namespace(
                opencode_base_url="http://127.0.0.1:4096",
                opencode_token=None,
                opencode_token_env=None,
                opencode_workspace=None,
                opencode_session_id="ses_demo",
                follow_up_prompt=None,
                follow_up_prompt_file="-",
                ensure_watcher=False,
                openclaw_session_key=None,
                openclaw_delivery_target=None,
                watch_live=None,
                watch_interval_sec=None,
                idle_timeout_sec=None,
                watch_message_limit=None,
                watch_timeout_sec=None,
                registry_path=str(registry_path),
            )
            fake_client = mock.Mock()
            fake_client.get_session.return_value = {"id": "ses_demo", "directory": "/tmp/demo-workspace"}
            fake_client.prompt_session.return_value = None

            with mock.patch("opencode_manager.OpenCodeClient", return_value=fake_client), mock.patch(
                "opencode_manager.list_watch_runtime_processes", return_value={}
            ), mock.patch("sys.stdin", new=io.StringIO("continue via stdin with `video-sum run`\nsecond line")):
                result = continue_command(args)

            fake_client.prompt_session.assert_called_once_with(
                "ses_demo",
                directory="/tmp/demo-workspace",
                parts=[{"type": "text", "text": "continue via stdin with `video-sum run`\nsecond line"}],
                model=None,
                agent=None,
                variant=None,
                asynchronous=True,
            )
            self.assertEqual(result["followUpPrompt"]["inputMethod"], "stdin")
            self.assertIsNone(result["followUpPrompt"].get("promptFile"))
            self.assertIn("video-sum run", result["followUpPrompt"]["promptPreview"])

    def test_continue_command_without_active_watcher_returns_explicit_non_handoff_contract(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / "registry.json"
            save_json_object(registry_path, {"kind": "opencode_manager_registry_v1", "watchers": []})
            args = Namespace(
                opencode_base_url="http://127.0.0.1:4096",
                opencode_token=None,
                opencode_token_env=None,
                opencode_workspace=None,
                opencode_session_id="ses_demo",
                follow_up_prompt="please continue",
                ensure_watcher=False,
                openclaw_session_key=None,
                openclaw_delivery_target=None,
                watch_live=None,
                watch_interval_sec=None,
                idle_timeout_sec=None,
                watch_message_limit=None,
                watch_timeout_sec=None,
                registry_path=str(registry_path),
            )
            fake_client = mock.Mock()
            fake_client.get_session.return_value = {"id": "ses_demo", "directory": "/tmp/demo-workspace"}
            fake_client.prompt_session.return_value = None

            with mock.patch("opencode_manager.OpenCodeClient", return_value=fake_client), mock.patch(
                "opencode_manager.list_watch_runtime_processes", return_value={}
            ):
                result = continue_command(args)

            self.assertEqual(result["handoffMode"], "no_watcher")
            self.assertEqual(result["agentAction"], "acknowledge_and_end_turn")
            self.assertIn("OpenCode", result["userFacingAck"])
            self.assertIn("OpenClaw", result["userFacingAck"])
            self.assertNotIn("watcher", result)
            fake_client.prompt_session.assert_called_once()

    def test_continue_command_forwards_agent_model_and_variant_overrides(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / "registry.json"
            save_json_object(registry_path, {"kind": "opencode_manager_registry_v1", "watchers": []})
            args = Namespace(
                opencode_base_url="http://127.0.0.1:4096",
                opencode_token=None,
                opencode_token_env=None,
                opencode_workspace=None,
                opencode_session_id="ses_demo",
                follow_up_prompt="please continue with explicit runtime overrides",
                opencode_agent="build",
                opencode_model="openai/gpt-5",
                opencode_variant="medium",
                ensure_watcher=False,
                openclaw_session_key=None,
                openclaw_delivery_target=None,
                watch_live=None,
                watch_interval_sec=None,
                idle_timeout_sec=None,
                watch_message_limit=None,
                watch_timeout_sec=None,
                registry_path=str(registry_path),
            )
            fake_client = mock.Mock()
            fake_client.get_session.return_value = {"id": "ses_demo", "directory": "/tmp/demo-workspace"}
            fake_client.prompt_session.return_value = None

            with mock.patch("opencode_manager.OpenCodeClient", return_value=fake_client), mock.patch(
                "opencode_manager.list_watch_runtime_processes", return_value={}
            ):
                result = continue_command(args)

            fake_client.prompt_session.assert_called_once_with(
                "ses_demo",
                directory="/tmp/demo-workspace",
                parts=[{"type": "text", "text": "please continue with explicit runtime overrides"}],
                model={"providerID": "openai", "modelID": "gpt-5"},
                agent="build",
                variant="medium",
                asynchronous=True,
            )
            self.assertEqual(
                result["promptOverrides"],
                {
                    "agent": "build",
                    "model": {"providerID": "openai", "modelID": "gpt-5"},
                    "variant": "medium",
                },
            )

    def test_stop_session_command_rejects_explicit_workspace_mismatch_before_abort(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path, _config_path, _state_path = self._write_registry_running_entry(tmpdir)
            args = Namespace(
                registry_path=str(registry_path),
                opencode_base_url="http://127.0.0.1:4096",
                opencode_token=None,
                opencode_token_env=None,
                watch_timeout_sec=20,
                verify_wait_sec=0,
                verify_poll_sec=0,
                opencode_workspace="/tmp/wrong-workspace",
                opencode_session_id="ses_demo",
            )
            fake_client = mock.Mock()
            fake_client.get_session.return_value = {
                "id": "ses_demo",
                "directory": "/tmp/demo-workspace",
                "title": "Demo task",
            }

            with mock.patch("opencode_manager.OpenCodeClient", return_value=fake_client):
                with self.assertRaisesRegex(ValueError, "does not match the session directory"):
                    stop_session_command(args)

            fake_client.abort_session.assert_not_called()
            fake_client.session_status.assert_not_called()

    def test_stop_session_command_uses_abort_api_and_preserves_live_watcher(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path, config_path, _state_path = self._write_registry_running_entry(tmpdir)
            args = Namespace(
                registry_path=str(registry_path),
                opencode_base_url="http://127.0.0.1:4096",
                opencode_token=None,
                opencode_token_env=None,
                watch_timeout_sec=20,
                verify_wait_sec=0,
                verify_poll_sec=0,
                opencode_workspace=None,
                opencode_session_id="ses_demo",
            )
            fake_client = mock.Mock()
            fake_client.get_session.return_value = {
                "id": "ses_demo",
                "directory": "/tmp/demo-workspace",
                "title": "Demo task",
            }
            fake_client.abort_session.return_value = True
            fake_client.session_status.return_value = {}
            verified_snapshot = {
                "latestMessage": {
                    "id": "msg_abort",
                    "role": "assistant",
                    "status": "failed",
                    "message.errorName": "MessageAbortedError",
                    "message.aborted": True,
                },
                "todo": {"hasPendingWork": False, "allCompleted": False},
                "permission": [],
                "question": [],
            }

            with mock.patch("opencode_manager.OpenCodeClient", return_value=fake_client), mock.patch(
                "opencode_manager.build_compact_snapshot", return_value=(verified_snapshot, {})
            ), mock.patch("opencode_manager.list_watch_runtime_processes", return_value=self._runtime_map(config_path)):
                result = stop_session_command(args)

            fake_client.get_session.assert_called_once_with("ses_demo", directory=None)
            fake_client.abort_session.assert_called_once_with("ses_demo", directory="/tmp/demo-workspace")
            fake_client.session_status.assert_called_with(directory="/tmp/demo-workspace")
            self.assertEqual(result["stopMethod"], "abort_api")
            self.assertEqual(result["stopOutcome"], "verified_stopped")
            self.assertTrue(result["stopped"])
            self.assertTrue(result["abortAccepted"])
            self.assertTrue(result["abortResult"])
            self.assertTrue(result["stopVerified"])
            self.assertFalse(result["stopLikelyFailed"])
            self.assertEqual(result["verification"]["outcome"], "aborted_terminal")
            self.assertTrue(result["watcherStillAttached"])
            self.assertEqual(result["watcher"]["watcherId"], "ow_demo123")
            self.assertEqual(
                result["opencodeSession"]["opencodeUiUrl"],
                "http://127.0.0.1:4096/L3RtcC9kZW1vLXdvcmtzcGFjZQ/session/ses_demo",
            )

    def test_stop_session_command_returns_explicit_unverified_outcome_when_abort_cannot_be_confirmed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path, config_path, _state_path = self._write_registry_running_entry(tmpdir)
            args = Namespace(
                registry_path=str(registry_path),
                opencode_base_url="http://127.0.0.1:4096",
                opencode_token=None,
                opencode_token_env=None,
                watch_timeout_sec=20,
                verify_wait_sec=0,
                verify_poll_sec=0,
                opencode_workspace=None,
                opencode_session_id="ses_demo",
            )
            fake_client = mock.Mock()
            fake_client.get_session.return_value = {
                "id": "ses_demo",
                "directory": "/tmp/demo-workspace",
                "title": "Demo task",
            }
            fake_client.abort_session.return_value = True
            fake_client.session_status.return_value = {}
            unverified_snapshot = {
                "latestMessage": {
                    "id": "msg_unknown",
                    "role": "assistant",
                    "status": "queued",
                    "textPreview": "waiting for more data",
                },
                "todo": {"hasPendingWork": False, "allCompleted": False},
                "permission": [],
                "question": [],
            }

            with mock.patch("opencode_manager.OpenCodeClient", return_value=fake_client), mock.patch(
                "opencode_manager.build_compact_snapshot", return_value=(unverified_snapshot, {})
            ), mock.patch("opencode_manager.list_watch_runtime_processes", return_value=self._runtime_map(config_path)):
                result = stop_session_command(args)

            self.assertEqual(result["stopOutcome"], "unverified")
            self.assertFalse(result["stopped"])
            self.assertFalse(result["stopVerified"])
            self.assertFalse(result["stopLikelyFailed"])
            self.assertEqual(result["verification"]["outcome"], "abort_acknowledged_but_unverified")
            self.assertEqual(result["verification"]["inspectionStatus"], "queued")

    def test_stop_session_command_reports_likely_failure_when_busy_clears_but_message_keeps_running(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path, config_path, _state_path = self._write_registry_running_entry(tmpdir)
            args = Namespace(
                registry_path=str(registry_path),
                opencode_base_url="http://127.0.0.1:4096",
                opencode_token=None,
                opencode_token_env=None,
                watch_timeout_sec=20,
                verify_wait_sec=0,
                verify_poll_sec=0,
                opencode_workspace=None,
                opencode_session_id="ses_demo",
            )
            fake_client = mock.Mock()
            fake_client.get_session.return_value = {
                "id": "ses_demo",
                "directory": "/tmp/demo-workspace",
                "title": "Demo task",
            }
            fake_client.abort_session.return_value = True
            fake_client.session_status.return_value = {}
            running_snapshot = {
                "latestMessage": {
                    "id": "msg_running",
                    "role": "assistant",
                    "status": "running",
                    "textPreview": "still going",
                },
                "todo": {"hasPendingWork": False, "allCompleted": False},
                "permission": [],
                "question": [],
            }

            with mock.patch("opencode_manager.OpenCodeClient", return_value=fake_client), mock.patch(
                "opencode_manager.build_compact_snapshot", return_value=(running_snapshot, {})
            ), mock.patch("opencode_manager.list_watch_runtime_processes", return_value=self._runtime_map(config_path)):
                result = stop_session_command(args)

            self.assertEqual(result["stopOutcome"], "likely_failed")
            self.assertFalse(result["stopped"])
            self.assertFalse(result["stopVerified"])
            self.assertTrue(result["stopLikelyFailed"])
            self.assertEqual(result["verification"]["outcome"], "busy_cleared_but_message_still_running")
            self.assertEqual(result["verification"]["inspectionStatus"], "running")

    def test_stop_watcher_updates_registry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path, config_path, state_path = self._write_registry_running_entry(tmpdir, watcher_id="ow_stop")
            args = Namespace(
                registry_path=str(registry_path),
                watcher_id="ow_stop",
                opencode_session_id=None,
                stop_timeout_sec=5,
            )

            with mock.patch(
                "opencode_manager.list_watch_runtime_processes",
                side_effect=[self._runtime_map(config_path), self._runtime_map(config_path), {}],
            ), mock.patch(
                "opencode_manager.stop_runtime_process_by_config", return_value=(True, 12345, "SIGINT")
            ):
                result = stop_watcher_command(args)

            self.assertTrue(result["stopped"])
            self.assertEqual(result["watcherCount"], 1)
            self.assertEqual(result["watchers"][0]["watchExitReason"], "manager_stop_requested")
            registry = __import__("opencode_manager").load_json_object(registry_path)
            self.assertEqual(registry["watchers"][0]["watcherStatus"], "exited")
            self.assertEqual(registry["watchers"][0]["watchExitReason"], "manager_stop_requested")
            state = __import__("opencode_manager").load_json_object(state_path)
            self.assertEqual(state["watchRunner"]["lastExitReason"], "manager_stop_requested")

    def test_detach_updates_registry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path, config_path, state_path = self._write_registry_running_entry(tmpdir, watcher_id="ow_detach")
            args = Namespace(
                registry_path=str(registry_path),
                watcher_id=None,
                opencode_session_id="ses_demo",
                stop_timeout_sec=5,
            )

            with mock.patch(
                "opencode_manager.list_watch_runtime_processes",
                side_effect=[self._runtime_map(config_path), self._runtime_map(config_path), {}],
            ), mock.patch(
                "opencode_manager.stop_runtime_process_by_config", return_value=(True, 12345, "SIGINT")
            ):
                result = detach_command(args)

            self.assertEqual(result["detachStatus"], "detached_now")
            self.assertIn("OpenClaw", result["detachSummary"])
            self.assertIn("OpenCode", result["detachSummary"])
            self.assertTrue(result["detached"])
            self.assertTrue(result["targetFound"])
            self.assertTrue(result["activeWatcherFound"])
            self.assertTrue(result["noActiveOpenclawBindingRemaining"])
            self.assertEqual(result["watcherCount"], 1)
            self.assertEqual(result["detachedWatcherCount"], 1)
            self.assertEqual(result["watchers"][0]["watchExitReason"], "manager_detach")
            registry = __import__("opencode_manager").load_json_object(registry_path)
            self.assertEqual(registry["watchers"][0]["watcherStatus"], "exited")
            self.assertEqual(registry["watchers"][0]["watchExitReason"], "manager_detach")
            state = __import__("opencode_manager").load_json_object(state_path)
            self.assertEqual(state["watchRunner"]["lastExitReason"], "manager_detach")

    def test_detach_reports_already_detached_when_target_exists_but_is_not_running(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path, _config_path, state_path = self._write_registry_running_entry(tmpdir, watcher_id="ow_detached")
            save_json_object(
                registry_path,
                {
                    "kind": "opencode_manager_registry_v1",
                    "watchers": [
                        {
                            "watcherId": "ow_detached",
                            "watcherStatus": "exited",
                            "watchProcessAlive": False,
                            "opencodeSessionId": "ses_demo",
                            "opencodeWorkspace": "/tmp/demo-workspace",
                            "openclawSessionKey": "agent:main:discord:target:example-origin-thread",
                            "openclawDeliveryTarget": "discord:example-origin-thread",
                            "watchExitReason": "manager_detach",
                            "watcherStatePath": str(state_path),
                            "watcherConfigPath": str(Path(tmpdir) / "watchers" / "ow_detached" / "config.json"),
                            "watcherLogPath": str(Path(tmpdir) / "watchers" / "ow_detached" / "watch.log"),
                        }
                    ],
                },
            )
            save_json_object(state_path, {"watchRunner": {"lastExitReason": "manager_detach"}})
            args = Namespace(
                registry_path=str(registry_path),
                watcher_id=None,
                opencode_session_id="ses_demo",
                stop_timeout_sec=5,
            )

            with mock.patch("opencode_manager.list_watch_runtime_processes", return_value={}):
                result = detach_command(args)

            self.assertEqual(result["detachStatus"], "already_detached")
            self.assertFalse(result["detached"])
            self.assertTrue(result["targetFound"])
            self.assertFalse(result["activeWatcherFound"])
            self.assertTrue(result["noActiveOpenclawBindingRemaining"])
            self.assertEqual(result["watcherCount"], 1)
            self.assertEqual(result["detachedWatcherCount"], 0)
            self.assertEqual(result["watchers"][0]["watcherStatus"], "exited")
            self.assertEqual(result["watchers"][0]["watchExitReason"], "manager_detach")
            self.assertIn("OpenClaw", result["detachSummary"])
            self.assertIn("OpenCode", result["detachSummary"])

    def test_detach_reports_not_found_when_no_matching_binding_exists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / "registry.json"
            save_json_object(registry_path, {"kind": "opencode_manager_registry_v1", "watchers": []})
            args = Namespace(
                registry_path=str(registry_path),
                watcher_id=None,
                opencode_session_id="ses_missing",
                stop_timeout_sec=5,
            )

            with mock.patch("opencode_manager.list_watch_runtime_processes", return_value={}):
                result = detach_command(args)

            self.assertEqual(result["detachStatus"], "not_found")
            self.assertFalse(result["detached"])
            self.assertFalse(result["targetFound"])
            self.assertFalse(result["activeWatcherFound"])
            self.assertFalse(result["noActiveOpenclawBindingRemaining"])
            self.assertEqual(result["watcherCount"], 0)
            self.assertEqual(result["detachedWatcherCount"], 0)
            self.assertEqual(result["watchers"], [])
            self.assertIn("OpenClaw", result["detachSummary"])
            self.assertIn("OpenCode", result["detachSummary"])

    def test_parser_exposes_phase2_subcommands(self):
        parser = build_parser()
        parsed_start = parser.parse_args(
            [
                "start",
                "--opencode-base-url",
                "http://127.0.0.1:4096",
                "--opencode-workspace",
                "/tmp/demo-workspace",
                "--openclaw-session-key",
                "agent:main:discord:target:example-origin-thread",
                "--first-prompt-file",
                "prompt.txt",
                "--opencode-agent",
                "build",
                "--opencode-model",
                "openai/gpt-5",
                "--opencode-variant",
                "high",
            ]
        )
        self.assertEqual(parsed_start.command, "start")
        self.assertEqual(parsed_start.first_prompt_file, "prompt.txt")
        self.assertIsNone(parsed_start.first_prompt)
        self.assertTrue(parsed_start.ensure_watcher)
        self.assertTrue(parsed_start.watch_live)
        self.assertEqual(parsed_start.opencode_agent, "build")
        self.assertEqual(parsed_start.opencode_model, "openai/gpt-5")
        self.assertEqual(parsed_start.opencode_variant, "high")

        parsed_start_no_watcher = parser.parse_args(
            [
                "start",
                "--opencode-base-url",
                "http://127.0.0.1:4096",
                "--opencode-workspace",
                "/tmp/demo-workspace",
                "--first-prompt-file",
                "prompt.txt",
                "--no-watcher",
            ]
        )
        self.assertFalse(parsed_start_no_watcher.ensure_watcher)
        self.assertTrue(parsed_start_no_watcher.watch_live)

        parsed_start_dry_run = parser.parse_args(
            [
                "start",
                "--opencode-base-url",
                "http://127.0.0.1:4096",
                "--opencode-workspace",
                "/tmp/demo-workspace",
                "--first-prompt-file",
                "prompt.txt",
                "--watch-dry-run",
            ]
        )
        self.assertFalse(parsed_start_dry_run.watch_live)

        parsed_attach = parser.parse_args(
            [
                "attach",
                "--opencode-base-url",
                "http://127.0.0.1:4096",
                "--opencode-session-id",
                "ses_demo",
                "--openclaw-session-key",
                "agent:main:discord:target:example-origin-thread",
            ]
        )
        self.assertEqual(parsed_attach.command, "attach")
        self.assertTrue(parsed_attach.watch_live)

        parsed_attach_dry_run = parser.parse_args(
            [
                "attach",
                "--opencode-base-url",
                "http://127.0.0.1:4096",
                "--opencode-session-id",
                "ses_demo",
                "--openclaw-session-key",
                "agent:main:discord:target:example-origin-thread",
                "--watch-dry-run",
            ]
        )
        self.assertFalse(parsed_attach_dry_run.watch_live)

        parsed_continue = parser.parse_args(
            [
                "continue",
                "--opencode-base-url",
                "http://127.0.0.1:4096",
                "--opencode-session-id",
                "ses_demo",
                "--follow-up-prompt-file",
                "-",
                "--opencode-agent",
                "build",
                "--opencode-model",
                "openai/gpt-5",
                "--opencode-variant",
                "medium",
            ]
        )
        self.assertEqual(parsed_continue.command, "continue")
        self.assertEqual(parsed_continue.opencode_session_id, "ses_demo")
        self.assertEqual(parsed_continue.follow_up_prompt_file, "-")
        self.assertIsNone(parsed_continue.follow_up_prompt)
        self.assertTrue(parsed_continue.ensure_watcher)
        self.assertTrue(parsed_continue.watch_live)
        self.assertEqual(parsed_continue.opencode_agent, "build")
        self.assertEqual(parsed_continue.opencode_model, "openai/gpt-5")
        self.assertEqual(parsed_continue.opencode_variant, "medium")

        parsed_continue_no_watcher = parser.parse_args(
            [
                "continue",
                "--opencode-base-url",
                "http://127.0.0.1:4096",
                "--opencode-session-id",
                "ses_demo",
                "--follow-up-prompt-file",
                "-",
                "--no-watcher",
            ]
        )
        self.assertFalse(parsed_continue_no_watcher.ensure_watcher)
        self.assertTrue(parsed_continue_no_watcher.watch_live)

        parsed_continue_dry_run = parser.parse_args(
            [
                "continue",
                "--opencode-base-url",
                "http://127.0.0.1:4096",
                "--opencode-session-id",
                "ses_demo",
                "--follow-up-prompt-file",
                "-",
                "--watch-dry-run",
            ]
        )
        self.assertFalse(parsed_continue_dry_run.watch_live)

        subparser_action = next(
            action
            for action in parser._actions
            if isinstance(getattr(action, "choices", None), dict) and "continue" in action.choices
        )
        start_help = subparser_action.choices["start"].format_help()
        self.assertIn("--first-prompt", start_help)
        self.assertIn("--first-prompt-file", start_help)
        self.assertIn("--opencode-agent", start_help)
        self.assertIn("--opencode-model", start_help)
        self.assertIn("--opencode-variant", start_help)
        self.assertIn("--no-watcher", start_help)
        self.assertIn("--watch-dry-run", start_help)
        self.assertNotIn("--watch-live", start_help)
        self.assertIn("defaults to live delivery", start_help)
        attach_help = subparser_action.choices["attach"].format_help()
        self.assertIn("--watch-dry-run", attach_help)
        self.assertNotIn("--watch-live", attach_help)
        self.assertIn("defaults to live delivery", attach_help)
        continue_help = subparser_action.choices["continue"].format_help()
        self.assertIn("--follow-up-prompt", continue_help)
        self.assertIn("--follow-up-prompt-file", continue_help)
        self.assertIn("--opencode-agent", continue_help)
        self.assertIn("--opencode-model", continue_help)
        self.assertIn("--opencode-variant", continue_help)
        self.assertIn("--ensure-watcher", continue_help)
        self.assertIn("--no-watcher", continue_help)
        self.assertIn("--watch-dry-run", continue_help)
        self.assertNotIn("--watch-live", continue_help)
        self.assertIn("live delivery mode by default", continue_help)

        parsed_inspect = parser.parse_args(
            [
                "inspect",
                "--opencode-base-url",
                "http://127.0.0.1:4096",
                "--opencode-session-id",
                "ses_demo",
            ]
        )
        self.assertEqual(parsed_inspect.command, "inspect")
        self.assertEqual(parsed_inspect.output_format, "text")
        self.assertEqual(parsed_inspect.timeline_limit, 8)

        inspect_help = subparser_action.choices["inspect"].format_help()
        self.assertIn("--format {text,json}", inspect_help)
        self.assertIn("--expand-index", inspect_help)
        self.assertIn("--timeline-limit", inspect_help)
        self.assertIn("--show-ids", inspect_help)

        parsed_history = parser.parse_args(
            [
                "inspect-history",
                "--opencode-base-url",
                "http://127.0.0.1:4096",
                "--opencode-session-id",
                "ses_demo",
                "--recent-index",
                "1",
            ]
        )
        self.assertEqual(parsed_history.command, "inspect-history")
        self.assertEqual(parsed_history.opencode_session_id, "ses_demo")
        self.assertEqual(parsed_history.recent_index, 1)

        history_help = subparser_action.choices["inspect-history"].format_help()
        self.assertIn("--message-id", history_help)
        self.assertIn("--recent-index", history_help)
        self.assertIn("--history-message-limit", history_help)
        self.assertIn("--recover-missing-from-disk", history_help)
        self.assertIn("recovery/debug only", history_help)
        self.assertIn("needed for normal usage", history_help)

        parsed_stop_session = parser.parse_args(
            [
                "stop-session",
                "--opencode-base-url",
                "http://127.0.0.1:4096",
                "--opencode-session-id",
                "ses_demo",
            ]
        )
        self.assertEqual(parsed_stop_session.command, "stop-session")
        self.assertEqual(parsed_stop_session.opencode_session_id, "ses_demo")
        stop_session_help = subparser_action.choices["stop-session"].format_help()
        self.assertIn("abort", stop_session_help)
        self.assertIn("post-abort verification", stop_session_help)
        self.assertIn("--opencode-workspace mismatch", stop_session_help)
        self.assertIn("verified, unverified, or likely failed", stop_session_help)
        self.assertIn("use stop-watcher or", stop_session_help)
        self.assertIn("detach separately only when monitoring should also stop", stop_session_help)
        self.assertIn("--recover-missing-from-disk", stop_session_help)

        watchers_help = subparser_action.choices["list-watchers"].format_help()
        self.assertIn("--recover-missing-from-disk", watchers_help)

        parsed_stop = parser.parse_args(["stop-watcher", "--watcher-id", "ow_demo123"])
        self.assertEqual(parsed_stop.command, "stop-watcher")
        self.assertEqual(parsed_stop.watcher_id, "ow_demo123")

    def test_parse_model_override_requires_provider_and_model(self):
        self.assertEqual(parse_model_override("openai/gpt-5"), {"providerID": "openai", "modelID": "gpt-5"})
        with self.assertRaises(ValueError):
            parse_model_override("gpt-5")

    def test_normalize_inline_prompt_text_decodes_escaped_newlines_only_when_needed(self):
        self.assertEqual(
            normalize_inline_prompt_text("line1\\n\\nline2"),
            "line1\n\nline2",
        )
        self.assertEqual(
            normalize_inline_prompt_text("line1\n\nline2"),
            "line1\n\nline2",
        )

    def test_resolve_prompt_input_applies_inline_newline_normalization(self):
        out = resolve_prompt_input(
            "A\\nB",
            None,
            text_flag="--follow-up-prompt",
            file_flag="--follow-up-prompt-file",
        )
        self.assertEqual(out["inputMethod"], "text")
        self.assertEqual(out["text"], "A\nB")

    def test_watcher_summary_keeps_explicit_field_names(self):
        summary = build_watcher_summary(
            {
                "watcherId": "ow_demo123",
                "watcherStatus": "running",
                "opencodeBaseUrl": "http://127.0.0.1:4096",
                "opencodeSessionId": "ses_demo",
                "opencodeWorkspace": "/tmp/demo-workspace",
                "openclawSessionKey": "agent:main:discord:target:example-origin-thread",
            }
        )
        self.assertIn("opencodeSessionId", summary)
        self.assertIn("openclawSessionKey", summary)
        self.assertEqual(summary["opencodeUiUrl"], "http://127.0.0.1:4096/L3RtcC9kZW1vLXdvcmtzcGFjZQ/session/ses_demo")
        self.assertNotIn("sessionId", summary)
        self.assertNotIn("originSession", summary)

    def test_readme_mentions_handoff_contract_fields(self):
        readme = (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")
        self.assertIn("--follow-up-prompt", readme)
        self.assertIn("--follow-up-prompt-file", readme)
        self.assertIn("--first-prompt-file", readme)
        self.assertIn("--opencode-agent", readme)
        self.assertIn("--opencode-model", readme)
        self.assertIn("--opencode-variant", readme)
        self.assertIn("stdin", readme)
        self.assertIn("--ensure-watcher", readme)
        self.assertIn("--no-watcher", readme)
        self.assertIn("normal conversation-driven usage", readme)
        self.assertIn("keep any watcher attached unless the user explicitly asks", readme)
        self.assertIn("inspect-history", readme)
        self.assertIn("followUpHints", readme)
        self.assertIn("what happened between inspect points?", readme)
        self.assertIn("handoffMode", readme)
        self.assertIn("agentAction", readme)
        self.assertIn("watcher_live", readme)
        self.assertIn("acknowledge_and_end_turn", readme)
        self.assertIn("stop-session", readme)
        self.assertIn("opencodeUiUrl", readme)
        self.assertIn("base64url(workspace-no-padding)", readme)

    def test_skill_mentions_manager_handoff_contract(self):
        skill = (Path(__file__).resolve().parents[1] / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("inspect-history", skill)
        self.assertIn("what happened between inspect points?", skill)
        self.assertIn("narrow", skill)
        self.assertIn("handoffMode", skill)
        self.assertIn("agentAction", skill)
        self.assertIn("watcher_live", skill)
        self.assertIn("acknowledge_and_end_turn", skill)
        self.assertIn("preflight that path on the current host", skill)
        self.assertIn("stop-session", skill)
        self.assertIn("Starting / continuing work should normally ensure a watcher is present", skill)
        self.assertIn("--no-watcher", skill)
        self.assertIn("use `stop-watcher` or `detach` only when the user explicitly asks", skill)
        self.assertIn("opencodeUiUrl", skill)
        self.assertIn("base64url(workspace-no-padding)", skill)
        self.assertIn("opencode_manager.py", skill)
        self.assertIn("--follow-up-prompt-file", skill)
        self.assertIn("--first-prompt-file", skill)
        self.assertIn("--opencode-agent", skill)
        self.assertIn("--opencode-model", skill)
        self.assertIn("--opencode-variant", skill)
        self.assertIn("stdin", skill)


if __name__ == "__main__":
    unittest.main()
