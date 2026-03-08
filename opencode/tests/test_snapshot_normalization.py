import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from opencode_remote_cycle import derive_phase, derive_status, snapshot_to_observation  # noqa: E402
from opencode_snapshot import compact_latest_message, normalize_todo, summarize_recent_messages  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
