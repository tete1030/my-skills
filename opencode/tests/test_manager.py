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
    refresh_registry_entry,
    save_json_object,
    start_command,
    start_or_attach_watcher,
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
                "openclawSessionKey": "agent:main:telegram:group:-100123:topic:42",
                "openclawDeliveryTarget": "telegram:-100123:topic:42",
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
                    "openclawSessionKey": "agent:main:telegram:group:-100123:topic:42",
                    "openclawDeliveryTarget": "telegram:-100123:topic:42",
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
                "openclawSessionKey": "agent:main:telegram:group:-100123:topic:42",
                "openclawDeliveryTarget": "telegram:-100123:topic:42",
                "watcherStatePath": str(state_path),
                "watcherConfigPath": str(Path(tmpdir) / "config.json"),
                "watcherLogPath": str(Path(tmpdir) / "watch.log"),
            }

            with mock.patch("opencode_manager.list_watch_runtime_processes", return_value={}):
                refreshed = refresh_registry_entry(entry)

            self.assertEqual(refreshed["watcherStatus"], "exited")
            self.assertEqual(refreshed["watchExitReason"], "idle_timeout:terminal_status:completed")
            self.assertEqual(refreshed["lastOpencodeStatus"], "completed")
            self.assertEqual(refreshed["openclawSessionKey"], "agent:main:telegram:group:-100123:topic:42")

    def test_refresh_registry_entry_marks_stale_process_reference_when_pid_alive_but_runtime_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            entry = {
                "watcherId": "ow_demo123",
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

            with mock.patch("opencode_manager.list_watch_runtime_processes", return_value={}), mock.patch(
                "opencode_manager.process_is_alive", return_value=True
            ):
                refreshed = refresh_registry_entry(entry)

            self.assertEqual(refreshed["watcherStatus"], "exited")
            self.assertEqual(refreshed["watchExitReason"], "stale_process_reference")

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
            registry_path, config_path, _state_path = self._write_registry_running_entry(tmpdir)
            args = Namespace(registry_path=str(registry_path), include_exited=False)

            with mock.patch("opencode_manager.list_watch_runtime_processes", return_value=self._runtime_map(config_path)):
                result = list_watchers_command(args)

            self.assertEqual(result["watcherCount"], 1)
            watcher = result["watchers"][0]
            self.assertEqual(watcher["openclawSessionKey"], "agent:main:telegram:group:-100123:topic:42")
            self.assertEqual(watcher["opencodeSessionId"], "ses_demo")

    def test_list_watchers_recovers_missing_registry_entry_from_watcher_dir(self):
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
                    "openclawSessionKey": "agent:main:telegram:group:-100123:topic:42",
                    "openclawDeliveryTarget": "telegram:-100123:topic:42",
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

            args = Namespace(registry_path=str(registry_path), include_exited=False)
            with mock.patch("opencode_manager.list_watch_runtime_processes", return_value=self._runtime_map(config_path, pid=54321)):
                result = list_watchers_command(args)

            self.assertEqual(result["watcherCount"], 1)
            watcher = result["watchers"][0]
            self.assertEqual(watcher["watcherId"], "ow_recovered")
            self.assertEqual(watcher["opencodeSessionId"], "ses_recovered")
            self.assertEqual(watcher["watchProcessId"], 54321)

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
                openclaw_session_key="agent:main:telegram:group:-100123:topic:42",
                openclaw_delivery_target="telegram:-100123:topic:42",
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
                "openclawSessionKey": "agent:main:telegram:group:-100123:topic:42",
                "openclawDeliveryTarget": "telegram:-100123:topic:42",
            }

            with mock.patch("opencode_manager.OpenCodeClient", return_value=fake_client), mock.patch(
                "opencode_manager.start_or_attach_watcher", return_value=fake_watcher
            ):
                result = start_command(args)

            self.assertEqual(result["handoffMode"], "watcher_live")
            self.assertEqual(result["agentAction"], "acknowledge_and_end_turn")
            self.assertIn("OpenCode", result["userFacingAck"])
            self.assertIn("OpenClaw", result["userFacingAck"])
            fake_client.prompt_session.assert_called_once()

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

            rehydration = inspection["rehydration"]
            self.assertEqual(rehydration["purpose"], "current_state_rebuild")
            self.assertEqual(rehydration["snapshotCoverage"]["requestedMessageLimit"], 4)
            self.assertEqual(rehydration["snapshotCoverage"]["observedMessageCount"], 4)
            self.assertTrue(rehydration["snapshotCoverage"]["mayExcludeOlderHistory"])
            self.assertEqual(rehydration["latestUserIntent"], "Please continue and summarize the current state.")
            self.assertEqual(rehydration["recentCompletedWork"][-1]["summary"], "Patched manager inspect output.")
            self.assertEqual(rehydration["recentNotableEvents"][1]["label"], "prune")
            self.assertEqual(rehydration["watcherState"]["watcherStatus"], "running")
            self.assertEqual(rehydration["watcherState"]["watcherId"], "ow_demo123")

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
                openclaw_session_key="agent:main:telegram:group:-100123:topic:42",
                openclaw_delivery_target="telegram:-100123:topic:42",
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
                "openclawSessionKey": "agent:main:telegram:group:-100123:topic:42",
                "openclawDeliveryTarget": "telegram:-100123:topic:42",
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
            self.assertEqual(inspection["rehydration"]["watcherState"]["watcherId"], "ow_new")

    def test_continue_command_can_ensure_watcher_using_previous_binding(self):
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
                            "openclawSessionKey": "agent:main:telegram:group:-100123:topic:42",
                            "openclawDeliveryTarget": "telegram:-100123:topic:42",
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
            fake_client.get_session.return_value = {"id": "ses_demo", "directory": "/tmp/demo-workspace"}
            fake_client.prompt_session.return_value = None
            fake_watcher = {
                "watcherId": "ow_new",
                "watcherStatus": "running",
                "watchLive": True,
                "opencodeSessionId": "ses_demo",
                "opencodeWorkspace": "/tmp/demo-workspace",
                "openclawSessionKey": "agent:main:telegram:group:-100123:topic:42",
                "openclawDeliveryTarget": "telegram:-100123:topic:42",
            }

            with mock.patch("opencode_manager.OpenCodeClient", return_value=fake_client), mock.patch(
                "opencode_manager.list_watch_runtime_processes", return_value={}
            ), mock.patch("opencode_manager.start_or_attach_watcher", return_value=fake_watcher) as mocked_start:
                result = continue_command(args)

            mocked_start.assert_called_once()
            self.assertEqual(mocked_start.call_args.kwargs["openclaw_session_key"], "agent:main:telegram:group:-100123:topic:42")
            self.assertEqual(result["watcher"]["watcherId"], "ow_new")
            self.assertEqual(result["handoffMode"], "watcher_live")
            self.assertEqual(result["agentAction"], "acknowledge_and_end_turn")
            self.assertIn("OpenCode", result["userFacingAck"])
            self.assertIn("OpenClaw", result["userFacingAck"])
            fake_client.prompt_session.assert_called_once()

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
                            "openclawSessionKey": "agent:main:telegram:group:-100123:topic:42",
                            "openclawDeliveryTarget": "telegram:-100123:topic:42",
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
        parsed_continue = parser.parse_args(
            [
                "continue",
                "--opencode-base-url",
                "http://127.0.0.1:4096",
                "--opencode-session-id",
                "ses_demo",
                "--follow-up-prompt",
                "hello again",
                "--ensure-watcher",
            ]
        )
        self.assertEqual(parsed_continue.command, "continue")
        self.assertEqual(parsed_continue.opencode_session_id, "ses_demo")
        self.assertTrue(parsed_continue.ensure_watcher)

        subparser_action = next(
            action
            for action in parser._actions
            if isinstance(getattr(action, "choices", None), dict) and "continue" in action.choices
        )
        continue_help = subparser_action.choices["continue"].format_help()
        self.assertIn("--follow-up-prompt", continue_help)
        self.assertIn("--ensure-watcher", continue_help)

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

        parsed_stop = parser.parse_args(["stop-watcher", "--watcher-id", "ow_demo123"])
        self.assertEqual(parsed_stop.command, "stop-watcher")
        self.assertEqual(parsed_stop.watcher_id, "ow_demo123")

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

    def test_readme_mentions_handoff_contract_fields(self):
        readme = (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")
        self.assertIn("--follow-up-prompt", readme)
        self.assertIn("--ensure-watcher", readme)
        self.assertIn("inspect-history", readme)
        self.assertIn("handoffMode", readme)
        self.assertIn("agentAction", readme)
        self.assertIn("watcher_live", readme)
        self.assertIn("acknowledge_and_end_turn", readme)

    def test_skill_mentions_manager_handoff_contract(self):
        skill = (Path(__file__).resolve().parents[1] / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("inspect-history", skill)
        self.assertIn("handoffMode", skill)
        self.assertIn("agentAction", skill)
        self.assertIn("watcher_live", skill)
        self.assertIn("acknowledge_and_end_turn", skill)
        self.assertIn("preflight that path on the current host", skill)


if __name__ == "__main__":
    unittest.main()
