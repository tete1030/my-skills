import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from opencode_remote_cycle import derive_phase, derive_status, snapshot_to_observation  # noqa: E402
from opencode_snapshot import analyze_running_progress, compact_latest_message, normalize_todo, summarize_recent_messages  # noqa: E402


TOOL_ONLY_COMPLETED_MESSAGE = {
    "info": {
        "role": "assistant",
        "time": {
            "created": 1772903315951,
            "completed": 1772903332959,
        },
        "id": "msg_cc945b9ef00294qavn782M9jhG",
        "sessionID": "ses_336ba9ddaffeyQWRo0q0TknHJg",
    },
    "parts": [
        {
            "type": "tool",
            "tool": "bash",
            "state": {
                "status": "completed",
                "output": "Cloning into 'openclaw'...",
            },
        }
    ],
}

ASSISTANT_STOP_TEXT_MESSAGE = {
    "info": {
        "role": "assistant",
        "time": {
            "created": 1772718028071,
            "completed": 1772718033111,
        },
        "finish": "stop",
        "id": "msg_cbe3a7527001cZT0WrLUPWnamr",
        "sessionID": "ses_36b901e41ffeMtRiH9o7C0YHEw",
    },
    "parts": [
        {"type": "step-start"},
        {"type": "reasoning", "text": "**Crafting concise release response**"},
        {
            "type": "text",
            "text": "Released v0.3.4 successfully. Included the usage-label cleanup change.",
        },
        {"type": "step-finish", "reason": "stop"},
    ],
}

USER_TEXT_MESSAGE = {
    "info": {
        "role": "user",
        "time": {"created": 1772903315000},
        "id": "msg_user_latest",
        "sessionID": "ses_x",
    },
    "parts": [
        {
            "type": "text",
            "text": "Please continue and give me a short summary when done.",
        }
    ],
}

ASSISTANT_EMPTY_MESSAGE = {
    "info": {
        "role": "assistant",
        "time": {"created": 1772903315050},
        "id": "msg_assistant_empty",
        "sessionID": "ses_x",
    },
    "parts": [],
}

ABORTED_EMPTY_ASSISTANT_MESSAGE = {
    "info": {
        "role": "assistant",
        "time": {"created": 1773250176459, "completed": 1773250176760},
        "error": {
            "name": "MessageAbortedError",
            "data": {"message": "The operation was aborted."},
        },
        "id": "msg_aborted_empty",
        "sessionID": "ses_x",
    },
    "parts": [],
}

IGNORED_PLUGIN_MESSAGE = {
    "info": {
        "role": "user",
        "time": {"created": 1772903315500},
        "id": "msg_plugin_noise",
        "sessionID": "ses_x",
        "ignored": True,
    },
    "parts": [
        {
            "type": "text",
            "text": "DCP plugin sync marker",
            "ignored": True,
        }
    ],
}

READ_MESSAGE = {
    "info": {
        "role": "assistant",
        "time": {
            "created": 1772903315600,
            "completed": 1772903315610,
        },
        "id": "msg_read_latest",
        "sessionID": "ses_x",
    },
    "parts": [
        {
            "type": "tool",
            "tool": "read",
            "input": {"filePath": "/mnt/vault/test-opencode-skill/opencode/scripts/opencode_snapshot.py"},
            "state": {
                "status": "completed",
                "output": "#!/usr/bin/env python3\n" + ("x" * 320),
            },
        }
    ],
}

BASH_PROGRESS_MESSAGE = {
    "info": {
        "role": "assistant",
        "time": {
            "created": 1772903315700,
            "completed": 1772903315710,
        },
        "id": "msg_bash_progress",
        "sessionID": "ses_x",
    },
    "parts": [
        {
            "type": "tool",
            "tool": "bash",
            "state": {
                "status": "completed",
                "output": "PWD=/mnt/vault/test-opencode-skill\n" + ("y" * 280) + "\npatched summarizer and added coverage",
            },
        }
    ],
}

ASSISTANT_MULTILINGUAL_TEXT_MESSAGE = {
    "info": {
        "role": "assistant",
        "time": {
            "created": 1772903315800,
            "completed": 1772903315810,
        },
        "finish": "stop",
        "id": "msg_multilingual_text",
        "sessionID": "ses_x",
    },
    "parts": [
        {
            "type": "text",
            "text": "已改成结构化事件汇总，并补上回归测试。",
        }
    ],
}


class SnapshotNormalizationTests(unittest.TestCase):
    def test_compact_latest_message_extracts_real_message_shape(self):
        normalized = compact_latest_message(ASSISTANT_STOP_TEXT_MESSAGE)
        self.assertEqual(normalized["id"], "msg_cbe3a7527001cZT0WrLUPWnamr")
        self.assertEqual(normalized["role"], "assistant")
        self.assertEqual(normalized["finish"], "stop")
        self.assertEqual(normalized["status"], "completed")
        self.assertTrue(normalized["completed"])
        self.assertEqual(normalized["message.lastContentType"], "text")
        self.assertIn("Released v0.3.4 successfully", normalized["message.lastTextPreview"])

    def test_compact_latest_message_handles_tool_only_completion(self):
        normalized = compact_latest_message(TOOL_ONLY_COMPLETED_MESSAGE)
        self.assertEqual(normalized["id"], "msg_cc945b9ef00294qavn782M9jhG")
        self.assertEqual(normalized["status"], "completed")
        self.assertTrue(normalized["completed"])
        self.assertTrue(normalized["hasToolCalls"])
        self.assertEqual(normalized["toolNames"], ["bash"])
        self.assertEqual(normalized["toolStatuses"], ["completed"])
        self.assertNotIn("message.lastTextPreview", normalized)

    def test_compact_latest_message_marks_early_aborted_message_failed(self):
        normalized = compact_latest_message(ABORTED_EMPTY_ASSISTANT_MESSAGE)
        self.assertEqual(normalized["id"], "msg_aborted_empty")
        self.assertEqual(normalized["status"], "failed")
        self.assertTrue(normalized["completed"])
        self.assertEqual(normalized["message.errorName"], "MessageAbortedError")
        self.assertEqual(normalized["message.errorMessage"], "The operation was aborted.")
        self.assertTrue(normalized["message.aborted"])
        self.assertIn("MessageAbortedError", normalized["errorPreview"])

    def test_normalize_todo_prefers_active_then_pending_then_latest_completed(self):
        normalized = normalize_todo(
            [
                {"content": "old completed", "status": "completed", "priority": "high"},
                {"content": "current work", "status": "in_progress", "priority": "high"},
                {"content": "next work", "status": "pending", "priority": "medium"},
            ]
        )
        self.assertEqual(normalized["phase"], "current work")
        self.assertEqual(normalized["current"]["content"], "current work")
        self.assertEqual(normalized["next"]["content"], "next work")
        self.assertTrue(normalized["hasPendingWork"])

        completed_only = normalize_todo(
            [
                {"content": "first done", "status": "completed", "priority": "high"},
                {"content": "last done", "status": "completed", "priority": "medium"},
            ]
        )
        self.assertEqual(completed_only["phase"], "last done")
        self.assertEqual(completed_only["latestCompleted"]["content"], "last done")
        self.assertTrue(completed_only["allCompleted"])

    def test_recent_message_summary_keeps_latest_id_but_finds_previous_text(self):
        summary = summarize_recent_messages([ASSISTANT_STOP_TEXT_MESSAGE, TOOL_ONLY_COMPLETED_MESSAGE])
        self.assertEqual(summary["latestMessage"]["id"], "msg_cc945b9ef00294qavn782M9jhG")
        self.assertEqual(summary["latestAssistantTextPreviewMessageId"], "msg_cbe3a7527001cZT0WrLUPWnamr")
        self.assertIn("Released v0.3.4 successfully", summary["latestAssistantTextPreview"])

    def test_recent_message_summary_preserves_terminal_abort_without_events(self):
        summary = summarize_recent_messages([USER_TEXT_MESSAGE, ABORTED_EMPTY_ASSISTANT_MESSAGE])
        self.assertEqual(summary["latestMessage"]["id"], "msg_aborted_empty")
        self.assertEqual(summary["latestMessage"]["status"], "failed")
        self.assertTrue(summary["latestMessage"]["message.aborted"])
        self.assertIn("MessageAbortedError", summary["latestTextPreview"])

    def test_recent_message_summary_exposes_window_metadata(self):
        summary = summarize_recent_messages([USER_TEXT_MESSAGE, ASSISTANT_STOP_TEXT_MESSAGE, TOOL_ONLY_COMPLETED_MESSAGE])

        self.assertEqual(summary["messageWindow"]["observedMessageCount"], 3)
        self.assertEqual(summary["messageWindow"]["oldestMessageId"], "msg_user_latest")
        self.assertEqual(summary["messageWindow"]["oldestMessageRole"], "user")
        self.assertEqual(summary["messageWindow"]["newestMessageId"], "msg_cc945b9ef00294qavn782M9jhG")
        self.assertEqual(summary["messageWindow"]["newestMessageRole"], "assistant")
        self.assertEqual(summary["messageWindowSize"], 3)

    def test_user_input_is_included_in_accumulated_event_summary(self):
        summary = summarize_recent_messages([
            USER_TEXT_MESSAGE,
            READ_MESSAGE,
            BASH_PROGRESS_MESSAGE,
            ASSISTANT_MULTILINGUAL_TEXT_MESSAGE,
        ])

        self.assertEqual(summary["latestUserInputMessageId"], "msg_user_latest")
        self.assertIn("Please continue", summary["latestUserInputSummary"])
        self.assertIn("user:", summary["accumulatedEventSummary"])
        self.assertIn("read:", summary["accumulatedEventSummary"])
        self.assertIn("tool[bash]:", summary["accumulatedEventSummary"])
        self.assertIn("text:", summary["accumulatedEventSummary"])

    def test_ignored_plugin_input_is_filtered_from_summary_and_latest_message(self):
        summary = summarize_recent_messages([ASSISTANT_STOP_TEXT_MESSAGE, IGNORED_PLUGIN_MESSAGE])

        self.assertEqual(summary["latestMessage"]["id"], "msg_cbe3a7527001cZT0WrLUPWnamr")
        self.assertIsNone(summary["latestUserInputSummary"])
        self.assertNotIn("plugin", summary["accumulatedEventSummary"].lower())

    def test_accumulated_summary_stays_compact_without_raw_stdout_dump(self):
        summary = summarize_recent_messages([
            USER_TEXT_MESSAGE,
            READ_MESSAGE,
            BASH_PROGRESS_MESSAGE,
            ASSISTANT_MULTILINGUAL_TEXT_MESSAGE,
        ])

        accumulated = summary["accumulatedEventSummary"]
        self.assertIn("opencode_snapshot.py", accumulated)
        self.assertIn("patched summarizer and added coverage", accumulated)
        self.assertNotIn("PWD=", accumulated)
        self.assertNotIn("x" * 40, accumulated)
        self.assertNotIn("y" * 40, accumulated)

    def test_event_based_summary_keeps_multilingual_progress_without_keywords(self):
        summary = summarize_recent_messages([
            USER_TEXT_MESSAGE,
            ASSISTANT_MULTILINGUAL_TEXT_MESSAGE,
        ])

        self.assertIn("已改成结构化事件汇总", summary["accumulatedEventSummary"])
        self.assertIn("text:", summary["accumulatedEventSummary"])

    def test_derive_status_and_observation_use_normalized_fields(self):
        completed_snapshot = {
            "latestMessage": compact_latest_message(TOOL_ONLY_COMPLETED_MESSAGE),
            "todo": normalize_todo([]),
            "permission": [],
            "question": [],
            "errors": {},
        }
        self.assertEqual(derive_status(completed_snapshot, previous_status="running"), "completed")

        running_snapshot = {
            "latestMessage": compact_latest_message(TOOL_ONLY_COMPLETED_MESSAGE),
            "todo": normalize_todo(
                [
                    {"content": "Automate macOS VM UI install steps", "status": "in_progress", "priority": "high"},
                    {"content": "Collect verification status", "status": "pending", "priority": "medium"},
                ]
            ),
            "permission": [],
            "question": [],
            "errors": {},
        }
        self.assertEqual(derive_status(running_snapshot, previous_status="running"), "running")
        self.assertEqual(derive_phase(running_snapshot["todo"]), "Automate macOS VM UI install steps")

        waiting_snapshot = {
            "latestMessage": compact_latest_message(USER_TEXT_MESSAGE),
            "todo": normalize_todo([]),
            "permission": [],
            "question": [],
            "errors": {},
        }
        self.assertEqual(derive_status(waiting_snapshot, previous_status="idle"), "running")

        aborted_snapshot = {
            "latestMessage": compact_latest_message(ABORTED_EMPTY_ASSISTANT_MESSAGE),
            "todo": normalize_todo([]),
            "permission": [],
            "question": [],
            "errors": {},
        }
        self.assertEqual(derive_status(aborted_snapshot, previous_status="running"), "failed")

        observation = snapshot_to_observation(
            {
                **completed_snapshot,
                "latestMessage": compact_latest_message(ASSISTANT_STOP_TEXT_MESSAGE),
            },
            {
                "status": "running",
                "phase": None,
                "lastSeenMessageId": None,
                "lastCompletedMessageId": None,
                "lastTodoDigest": None,
            },
        )
        self.assertEqual(observation["lastSeenMessageId"], "msg_cbe3a7527001cZT0WrLUPWnamr")
        self.assertEqual(observation["status"], "completed")
        self.assertTrue(observation["lastCompletedMessageId"])

    def test_analyze_running_progress_detects_user_only_stall_after_assistant_turn_starts(self):
        summary = summarize_recent_messages([USER_TEXT_MESSAGE, ASSISTANT_EMPTY_MESSAGE])
        snapshot = {
            **summary,
            "todo": normalize_todo([]),
            "permission": [],
            "question": [],
            "errors": {},
        }

        observation = analyze_running_progress(snapshot, current_status="running", now_ms=1772903375000)

        self.assertIsNotNone(observation)
        self.assertIn("running_no_visible_progress_since_latest_user_input", observation["signalCodes"])
        self.assertIn("assistant_turn_started_without_visible_progress", observation["signalCodes"])
        self.assertIn("recent_window_only_user_input", observation["signalCodes"])
        self.assertTrue(observation["derived"])
        self.assertEqual(observation["origin"], "openclaw_compact_snapshot")
        self.assertTrue(observation["assistantTurnStartedAfterLatestUserInput"])
        self.assertEqual(observation["progressEventCountSinceLatestUserInput"], 0)
        self.assertGreaterEqual(observation["secondsSinceLatestUserInput"], 60)
        self.assertTrue(all(signal.get("derived") for signal in observation["signals"]))
        self.assertTrue(all(signal.get("origin") == "openclaw_compact_snapshot" for signal in observation["signals"]))
        self.assertTrue(all("Derived from compact snapshot:" in signal.get("detail", "") for signal in observation["signals"]))

    def test_snapshot_to_observation_carries_stuck_and_transport_hints(self):
        summary = summarize_recent_messages([USER_TEXT_MESSAGE, ASSISTANT_EMPTY_MESSAGE])
        snapshot = {
            **summary,
            "todo": normalize_todo([]),
            "permission": [],
            "question": [],
            "errors": {
                "messages": {
                    "kind": "opencode_api_error_v1",
                    "status": 429,
                    "retryAfter": "30",
                    "message": "GET /session/x/message -> HTTP 429 Too Many Requests",
                }
            },
        }

        with mock.patch("opencode_remote_cycle.datetime") as mocked_datetime:
            from datetime import datetime, timezone

            mocked_datetime.now.return_value = datetime.fromtimestamp(1772903375, tz=timezone.utc)
            mocked_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
            observation = snapshot_to_observation(
                snapshot,
                {
                    "status": "running",
                    "phase": None,
                    "lastSeenMessageId": None,
                    "lastCompletedMessageId": None,
                    "lastTodoDigest": None,
                },
            )

        self.assertEqual(observation["transportErrorHints"][0]["status"], 429)
        self.assertEqual(observation["transportErrorHints"][0]["retryAfter"], "30")
        self.assertTrue(observation["transportErrorHints"][0]["derived"])
        self.assertEqual(observation["transportErrorHints"][0]["origin"], "openclaw_snapshot_errors")
        self.assertEqual(observation["runningProgressObservation"]["status"], "running_without_visible_progress")


if __name__ == "__main__":
    unittest.main()
