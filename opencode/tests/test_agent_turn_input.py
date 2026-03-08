import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from opencode_agent_turn_input import build_agent_turn_input  # noqa: E402


class AgentTurnInputTests(unittest.TestCase):
    def test_running_state_change_maps_to_progress_update(self):
        turn_result = {
            "factSkeleton": {
                "status": "running",
                "phase": "Collect verification status",
                "latestMeaningfulPreview": "Released v0.3.4 successfully.",
                "reason": "state_changed",
            },
            "shouldSend": True,
            "delivery": {
                "originSession": "origin-session-example",
                "originTarget": "origin-target-example",
            },
            "cadence": {
                "decision": "visible_update",
                "noChange": False,
                "consecutiveNoChangeCount": 0,
                "lastVisibleUpdateAt": "2026-03-08T09:40:00+00:00",
            },
        }

        result = build_agent_turn_input(turn_result)

        self.assertTrue(result["shouldSend"])
        self.assertEqual(result["action"], "send_update")
        self.assertEqual(result["updateType"], "progress")
        self.assertEqual(result["priority"], "normal")
        self.assertEqual(result["style"], "brief_progress")
        self.assertEqual(result["reason"], "state_changed")
        self.assertEqual(result["narrativeOwner"], "main_session_agent")
        self.assertEqual(
            result["mentionFields"],
            ["status", "phase", "latestMeaningfulPreview"],
        )
        self.assertEqual(result["facts"]["status"], "running")
        self.assertEqual(result["facts"]["phase"], "Collect verification status")
        self.assertTrue(result["routing"]["mustPreserveOrigin"])
        self.assertEqual(result["routing"]["originSession"], "origin-session-example")
        self.assertNotIn("message", result)

    def test_visible_no_change_maps_to_heartbeat_without_preview_requirement(self):
        turn_result = {
            "factSkeleton": {
                "status": "running",
                "phase": "Waiting for verification",
                "latestMeaningfulPreview": "Still monitoring logs.",
                "reason": "no_change_age>=30m",
            },
            "shouldSend": True,
            "delivery": {},
            "cadence": {
                "decision": "visible_update",
                "noChange": True,
                "consecutiveNoChangeCount": 4,
                "lastVisibleUpdateAt": "2026-03-08T09:40:00+00:00",
            },
        }

        result = build_agent_turn_input(turn_result)

        self.assertEqual(result["action"], "send_update")
        self.assertEqual(result["updateType"], "heartbeat")
        self.assertEqual(result["style"], "brief_heartbeat")
        self.assertEqual(result["mentionFields"], ["status", "phase"])
        self.assertEqual(result["facts"], {"status": "running", "phase": "Waiting for verification"})
        self.assertFalse(result["routing"]["mustPreserveOrigin"])

    def test_silent_turn_stays_silent_but_preserves_routing(self):
        turn_result = {
            "factSkeleton": {
                "status": "running",
                "phase": "Collect verification status",
                "latestMeaningfulPreview": None,
                "reason": "recent_visible_update_exists",
            },
            "shouldSend": False,
            "delivery": {
                "originSession": "origin-session-example",
                "originTarget": None,
            },
            "cadence": {
                "decision": "silent_noop",
                "noChange": True,
                "consecutiveNoChangeCount": 3,
                "lastVisibleUpdateAt": "2026-03-08T09:40:00+00:00",
            },
        }

        result = build_agent_turn_input(turn_result)

        self.assertFalse(result["shouldSend"])
        self.assertEqual(result["action"], "stay_silent")
        self.assertEqual(result["updateType"], "silent")
        self.assertEqual(result["priority"], "low")
        self.assertEqual(result["style"], "silent")
        self.assertEqual(result["facts"], {})
        self.assertTrue(result["routing"]["mustPreserveOrigin"])

    def test_blocked_turn_maps_to_high_priority_blocker_update(self):
        turn_result = {
            "factSkeleton": {
                "status": "blocked",
                "phase": "Waiting for approval",
                "latestMeaningfulPreview": "Need user confirmation before deploy.",
                "reason": "status=blocked",
            },
            "shouldSend": True,
            "delivery": {
                "originSession": "origin-session-example",
                "originTarget": "origin-target-example",
            },
            "cadence": {
                "decision": "visible_update",
                "noChange": False,
                "consecutiveNoChangeCount": 0,
                "lastVisibleUpdateAt": "2026-03-08T09:40:00+00:00",
            },
        }

        result = build_agent_turn_input(turn_result)

        self.assertEqual(result["updateType"], "blocked")
        self.assertEqual(result["priority"], "high")
        self.assertEqual(result["style"], "brief_blocker")
        self.assertEqual(result["facts"]["latestMeaningfulPreview"], "Need user confirmation before deploy.")


if __name__ == "__main__":
    unittest.main()
