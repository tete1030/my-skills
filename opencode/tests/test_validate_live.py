import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import opencode_validate_live as validate_live  # noqa: E402
from opencode_delivery_handoff import SYSTEM_EVENT_TEXT_HEADER  # noqa: E402


class OpenCodeValidateLiveTests(unittest.TestCase):
    def test_load_validation_config_falls_back_to_manager_defaults_env(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_config = Path(tmpdir) / "opencode-validation" / "config.json"
            defaults_env = Path(tmpdir) / "opencode-manager" / "local-defaults.env"
            defaults_env.parent.mkdir(parents=True, exist_ok=True)
            defaults_env.write_text(
                "\n".join(
                    [
                        "OPENCODE_BASE_URL='http://127.0.0.1:4096'",
                        "OPENCODE_WORKSPACE='/mnt/vault/test-opencode-skill'",
                        "OPENCLAW_SESSION_KEY='agent:main:telegram:group:-100:topic:42'",
                        "OPENCLAW_DELIVERY_TARGET='telegram:-100:topic:42'",
                        "OPENCODE_WATCH_INTERVAL_SEC='7'",
                        "OPENCODE_IDLE_TIMEOUT_SEC='55'",
                        "OPENCODE_WATCH_MESSAGE_LIMIT='11'",
                        "OPENCODE_WATCH_TIMEOUT_SEC='25'",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with mock.patch.object(validate_live, "DEFAULT_CONFIG_PATH", missing_config), mock.patch.object(
                validate_live, "DEFAULT_MANAGER_DEFAULTS_ENV", defaults_env
            ):
                config = validate_live.load_validation_config(missing_config)

            self.assertEqual(config["sourcePath"], str(defaults_env.resolve()))
            self.assertEqual(config["baseUrl"], "http://127.0.0.1:4096")
            self.assertEqual(config["workspace"], "/mnt/vault/test-opencode-skill")
            self.assertEqual(config["openclawSessionKey"], "agent:main:telegram:group:-100:topic:42")
            self.assertEqual(config["openclawDeliveryTarget"], "telegram:-100:topic:42")
            self.assertEqual(config["watchIntervalSec"], 7)
            self.assertEqual(config["idleTimeoutSec"], 55)
            self.assertEqual(config["watchMessageLimit"], 11)
            self.assertEqual(config["watchTimeoutSec"], 25)
            self.assertGreaterEqual(config["historyMessageLimit"], 11)

    def test_summarize_watch_log_handles_consecutive_duplicate_lines(self):
        envelope = {
            "kind": "opencode_origin_session_handoff",
            "version": "v2",
            "runtimeSignal": {
                "action": "inspect_once_current_state",
                "opencodeSessionId": "ses_demo_validate_live",
            },
        }
        step = {
            "kind": "opencode_watch_runner_step_v1",
            "watchAction": {
                "mode": "live",
                "operation": "execute",
                "shouldExecute": True,
                "actionKey": "opencode-origin-handoff-demo",
                "reason": "ready_inject_live",
            },
            "handoff": {
                "openclawDelivery": {
                    "deliveryAction": "inject",
                    "routeStatus": "ready",
                    "systemEventTemplate": {
                        "payload": {
                            "kind": "systemEvent",
                            "text": SYSTEM_EVENT_TEXT_HEADER + "\n" + json.dumps(envelope, ensure_ascii=False, indent=2),
                        }
                    },
                }
            },
        }
        pretty_step = json.dumps(step, ensure_ascii=False, indent=2).splitlines()
        duplicated_pretty_step = "\n".join(line for item in pretty_step for line in (item, item)) + "\n"
        log_text = (
            json.dumps({"kind": "opencode_watch_runtime_start_v1", "mode": "loop"})
            + "\n"
            + json.dumps({"kind": "opencode_watch_runtime_start_v1", "mode": "loop"})
            + "\n"
            + duplicated_pretty_step
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            watch_log = Path(tmpdir) / "watch.log"
            watch_log.write_text(log_text, encoding="utf-8")

            summary = validate_live.summarize_watch_log(watch_log)

        self.assertEqual(summary["documentCount"], 2)
        self.assertEqual(summary["stepCount"], 1)
        self.assertEqual(summary["handoffEnvelope"]["version"], "v2")
        self.assertEqual(summary["handoffEnvelope"]["runtimeSignal"]["opencodeSessionId"], "ses_demo_validate_live")
        self.assertEqual(summary["lastStep"]["watchAction"]["operation"], "execute")

    def test_extract_event_window_finds_one_off_inspect_and_reply(self):
        session_id = "ses_demo_receiver"
        entries = [
            {
                "type": "message",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": SYSTEM_EVENT_TEXT_HEADER + "\n" + json.dumps(
                                {
                                    "kind": "opencode_origin_session_handoff",
                                    "version": "v2",
                                    "runtimeSignal": {
                                        "action": "inspect_once_current_state",
                                        "opencodeSessionId": session_id,
                                    },
                                },
                                ensure_ascii=False,
                                indent=2,
                            ),
                        }
                    ],
                },
                "_lineNumber": 11,
            },
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "toolCall",
                            "name": "exec",
                            "arguments": {
                                "command": (
                                    "bash -lc 'python3 /tmp/opencode_manager.py inspect "
                                    f"--opencode-session-id {session_id}'"
                                )
                            },
                        }
                    ],
                },
                "_lineNumber": 12,
            },
            {
                "type": "message",
                "message": {
                    "role": "toolResult",
                    "toolName": "exec",
                    "content": [{"type": "text", "text": '{"kind":"opencode_manager_inspect_v1","inspection":{"opencodeSession":{"opencodeSessionId":"ses_demo_receiver"},"rehydration":{"currentState":{"status":"completed"}},"currentStatus":"completed"}}'}],
                },
                "_lineNumber": 13,
            },
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "已经完成，文件内容已确认。"}],
                },
                "_lineNumber": 14,
            },
        ]

        window = validate_live.extract_event_window(entries, session_id=session_id, occurrence=1)

        self.assertIsNotNone(window)
        self.assertEqual(window["inspectEntryCount"], 1)
        self.assertEqual(window["replyText"], "已经完成，文件内容已确认。")
        self.assertTrue(validate_live.tool_result_is_opencode_inspect(window, session_id=session_id))
        self.assertTrue(validate_live.receiver_reply_looks_like_current_state(window["replyText"], session_id=session_id))

    def test_receiver_reply_looks_like_current_state_rejects_payload_parroting(self):
        session_id = "ses_demo_receiver"
        self.assertFalse(
            validate_live.receiver_reply_looks_like_current_state(
                f"OPENCODE_ORIGIN_SESSION_SYSTEM_EVENT_V1 runtimeSignal {session_id}",
                session_id=session_id,
            )
        )
        self.assertFalse(validate_live.receiver_reply_looks_like_current_state("NO_REPLY", session_id=session_id))
        self.assertTrue(validate_live.receiver_reply_looks_like_current_state("现在已经完成了。", session_id=session_id))

    def test_build_manager_argv_omits_runtime_flags_for_registry_only_commands(self):
        config = {
            "baseUrl": "http://127.0.0.1:4096",
            "token": None,
            "tokenEnv": None,
        }
        registry_path = Path("/tmp/registry.json")

        stop_argv = validate_live.build_manager_argv(
            config,
            registry_path=registry_path,
            command="stop-watcher",
            extra_args=["--watcher-id", "ow_demo"],
        )
        inspect_argv = validate_live.build_manager_argv(
            config,
            registry_path=registry_path,
            command="inspect",
            extra_args=["--opencode-session-id", "ses_demo"],
        )

        self.assertNotIn("--opencode-base-url", stop_argv)
        self.assertIn("--registry-path", stop_argv)
        self.assertIn("--opencode-base-url", inspect_argv)
        self.assertIn("--registry-path", inspect_argv)

    def test_build_verdict_marks_partial_readiness_when_core_checks_exist(self):
        verdict = validate_live.build_verdict(
            [
                {"name": "git_preflight", "passed": True},
                {"name": "start_watcher_live", "passed": True},
                {"name": "inspect_history", "passed": False},
            ],
            preflight_ok=True,
        )

        self.assertEqual(verdict, "partly_ready")

    def test_build_verdict_never_returns_ready_when_error_present(self):
        verdict = validate_live.build_verdict(
            [
                {"name": "git_preflight", "passed": True},
                {"name": "start_watcher_live", "passed": True},
                {"name": "continue_watcher_reuse", "passed": True},
            ],
            preflight_ok=True,
            has_error=True,
        )

        self.assertEqual(verdict, "partly_ready")

    def test_evaluate_workspace_business_completion_validates_latest_completed_turn_and_final_output(self):
        run_id = "vtest-123"
        history_messages = [
            {
                "messageId": "msg_running_stub",
                "recentIndex": 0,
                "role": "assistant",
                "status": "running",
                "toolCallCount": 0,
                "toolCalls": [],
            },
            {
                "messageId": "msg_continue",
                "recentIndex": 1,
                "role": "assistant",
                "status": "completed",
                "completedAt": "2026-03-10T16:00:05Z",
                "toolCallCount": 1,
                "toolCalls": [
                    {
                        "toolName": "bash",
                        "action": "shell",
                        "commandPreview": f"cat {validate_live.validation_output_path(run_id)}",
                        "outputTailLines": [
                            validate_live.expected_start_text(run_id),
                            validate_live.expected_continue_text(run_id),
                        ],
                    }
                ],
            },
            {
                "messageId": "msg_start",
                "recentIndex": 3,
                "role": "assistant",
                "status": "completed",
                "completedAt": "2026-03-10T16:00:01Z",
                "toolCallCount": 1,
                "toolCalls": [
                    {
                        "toolName": "bash",
                        "action": "shell",
                        "commandPreview": f"cat {validate_live.validation_output_path(run_id)}",
                        "outputTailLines": [validate_live.expected_start_text(run_id)],
                    }
                ],
            },
        ]

        result = validate_live.evaluate_workspace_business_completion(run_id, history_messages)

        self.assertEqual(result["assistantTurnCount"], 2)
        self.assertTrue(result["latestCompletedAssistantTurn"]["passed"])
        self.assertTrue(result["finalFileContent"]["passed"])

    def test_evaluate_workspace_business_completion_accepts_output_preview_with_expected_lines(self):
        run_id = "vtest-preview"
        history_messages = [
            {
                "messageId": "msg_continue",
                "recentIndex": 0,
                "role": "assistant",
                "status": "completed",
                "completedAt": "2026-03-10T16:10:05Z",
                "toolCallCount": 1,
                "toolCalls": [
                    {
                        "toolName": "bash",
                        "action": "shell",
                        "commandPreview": f"cat {validate_live.validation_output_path(run_id)}",
                        "outputPreview": (
                            "listing...\n"
                            + validate_live.expected_start_text(run_id)
                            + "\n"
                            + validate_live.expected_continue_text(run_id)
                        ),
                    }
                ],
            }
        ]

        result = validate_live.evaluate_workspace_business_completion(run_id, history_messages)

        self.assertTrue(result["latestCompletedAssistantTurn"]["passed"])
        self.assertTrue(result["finalFileContent"]["passed"])

    def test_evaluate_workspace_business_completion_accepts_final_text_preview_without_tool_output(self):
        run_id = "vtest-text-preview"
        history_messages = [
            {
                "messageId": "msg_done",
                "recentIndex": 0,
                "role": "assistant",
                "status": "completed",
                "completedAt": "2026-03-10T16:20:05Z",
                "textPreview": (
                    "Printed contents: "
                    + validate_live.expected_start_text(run_id)
                    + " "
                    + validate_live.expected_continue_text(run_id)
                    + " status ok"
                ),
                "toolCallCount": 0,
                "toolCalls": [],
            }
        ]

        result = validate_live.evaluate_workspace_business_completion(run_id, history_messages)

        self.assertTrue(result["latestCompletedAssistantTurn"]["passed"])
        self.assertTrue(result["finalFileContent"]["passed"])
        self.assertEqual(result["finalFileContent"]["match"]["contentSource"], "textPreview")

    def test_evaluate_workspace_business_completion_requires_completed_assistant_turn(self):
        run_id = "vtest-fail"
        history_messages = [
            {
                "messageId": "msg_continue",
                "recentIndex": 0,
                "role": "assistant",
                "status": "running",
                "toolCallCount": 1,
                "toolCalls": [
                    {
                        "toolName": "bash",
                        "action": "shell",
                        "outputTailLines": [
                            validate_live.expected_start_text(run_id),
                            validate_live.expected_continue_text(run_id),
                        ],
                    }
                ],
            }
        ]

        result = validate_live.evaluate_workspace_business_completion(run_id, history_messages)

        self.assertEqual(result["assistantTurnCount"], 0)
        self.assertFalse(result["latestCompletedAssistantTurn"]["passed"])
        self.assertFalse(result["finalFileContent"]["passed"])

    def test_evaluate_workspace_business_completion_fails_without_expected_final_output(self):
        run_id = "vtest-missing-output"
        history_messages = [
            {
                "messageId": "msg_continue",
                "recentIndex": 0,
                "role": "assistant",
                "status": "completed",
                "completedAt": "2026-03-10T16:30:05Z",
                "toolCallCount": 1,
                "toolCalls": [
                    {
                        "toolName": "bash",
                        "action": "shell",
                        "commandPreview": f"cat {validate_live.validation_output_path(run_id)}",
                        "outputTailLines": [validate_live.expected_start_text(run_id)],
                    }
                ],
            }
        ]

        result = validate_live.evaluate_workspace_business_completion(run_id, history_messages)

        self.assertTrue(result["latestCompletedAssistantTurn"]["passed"])
        self.assertFalse(result["finalFileContent"]["passed"])

    def test_normalize_raw_session_messages_preserves_full_bash_output(self):
        raw_messages = [
            {
                "info": {
                    "id": "msg_raw_1",
                    "role": "assistant",
                    "time": {"created": 1, "completed": 2},
                },
                "parts": [
                    {"type": "text", "text": "done"},
                    {
                        "type": "tool",
                        "tool": "bash",
                        "state": {
                            "status": "completed",
                            "input": {"command": "cat .claw-validation/x.txt"},
                            "output": "start ok x.\ncontinue ok x.\nok\n",
                        },
                    },
                ],
            }
        ]

        normalized = validate_live.normalize_raw_session_messages(raw_messages)

        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0]["role"], "assistant")
        self.assertEqual(normalized[0]["status"], "completed")
        self.assertEqual(normalized[0]["toolCalls"][0]["content"], "start ok x.\ncontinue ok x.\nok\n")
        self.assertEqual(
            normalized[0]["toolCalls"][0]["outputTailLines"],
            ["start ok x.", "continue ok x.", "ok"],
        )


if __name__ == "__main__":
    unittest.main()
