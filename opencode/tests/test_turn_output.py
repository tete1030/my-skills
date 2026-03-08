import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from opencode_session_turn import build_turn_result  # noqa: E402


class TurnOutputTests(unittest.TestCase):
    def test_turn_result_emphasizes_fact_skeleton_and_delivery(self):
        payload = {
            "decision": {"decision": "visible_update", "reason": "state_changed"},
            "observation": {"status": "running", "phase": "Collect verification status", "noChange": False},
            "after": {
                "status": "running",
                "phase": "Collect verification status",
                "consecutiveNoChangeCount": 0,
                "lastVisibleUpdateAt": "2026-03-08T09:40:00+00:00",
            },
            "snapshot": {
                "latestAssistantTextPreview": "Released v0.3.4 successfully. Included the usage-label cleanup change.",
                "latestMessage": {"id": "msg_latest"},
            },
        }

        result = build_turn_result(
            payload,
            control={"executionMode": "main_session_centered"},
            origin_session="origin-session-example",
            origin_target="origin-target-example",
        )

        self.assertEqual(result["factSkeleton"]["status"], "running")
        self.assertEqual(result["factSkeleton"]["phase"], "Collect verification status")
        self.assertIn("Released v0.3.4 successfully", result["factSkeleton"]["latestMeaningfulPreview"])
        self.assertEqual(result["factSkeleton"]["reason"], "state_changed")
        self.assertTrue(result["shouldSend"])
        self.assertEqual(result["delivery"]["originSession"], "origin-session-example")
        self.assertEqual(result["delivery"]["originTarget"], "origin-target-example")
        self.assertEqual(result["cadence"]["decision"], "visible_update")
        self.assertFalse(result["cadence"]["noChange"])
        self.assertNotIn("fallback", result)

    def test_turn_result_keeps_silent_cadence_without_send(self):
        payload = {
            "decision": {"decision": "silent_noop", "reason": "recent_visible_update_exists"},
            "observation": {"status": "running", "phase": None, "noChange": True},
            "after": {
                "status": "running",
                "phase": None,
                "consecutiveNoChangeCount": 3,
                "lastVisibleUpdateAt": "2026-03-08T09:40:00+00:00",
            },
            "snapshot": {"latestMessage": {}},
        }

        result = build_turn_result(payload, origin_session="origin-session-example")

        self.assertFalse(result["shouldSend"])
        self.assertEqual(result["factSkeleton"]["status"], "running")
        self.assertEqual(result["factSkeleton"]["reason"], "recent_visible_update_exists")
        self.assertTrue(result["cadence"]["noChange"])
        self.assertEqual(result["cadence"]["consecutiveNoChangeCount"], 3)


if __name__ == "__main__":
    unittest.main()
