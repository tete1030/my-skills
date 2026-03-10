import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from opencode_agent_turn_input import (  # noqa: E402
    ALLOWED_CADENCE_KEYS,
    ALLOWED_FACT_KEYS,
    ALLOWED_ROUTING_KEYS,
    ALLOWED_RUNTIME_SIGNAL_KEYS,
    ALLOWED_TOP_LEVEL_KEYS,
    build_agent_turn_input,
)
from opencode_task_cluster import ALLOWED_REPLY_POLICY_KEYS, ALLOWED_TASK_CLUSTER_KEYS  # noqa: E402


class AgentTurnInputTests(unittest.TestCase):
    def test_running_state_change_maps_to_progress_signal(self):
        turn_result = {
            "opencodeSessionId": "ses_demo_progress",
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
            "taskCluster": {
                "key": "task-cluster-demo",
                "summary": "Release v0.3.4",
                "clusterStateRank": 20,
                "detailRank": 27,
                "sourceUpdateMs": 123456789,
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
        self.assertEqual(result["mentionFields"], ["status", "phase"])
        self.assertEqual(
            result["facts"],
            {
                "status": "running",
                "phase": "Collect verification status",
            },
        )
        self.assertTrue(result["routing"]["mustPreserveOrigin"])
        self.assertEqual(result["routing"]["originSession"], "origin-session-example")
        self.assertEqual(result["taskCluster"]["key"], "task-cluster-demo")
        self.assertEqual(result["taskCluster"]["clusterStateRank"], 20)
        self.assertEqual(result["replyPolicy"]["replyDefault"], "send_if_not_cluster_superseded")
        self.assertEqual(
            result["runtimeSignal"],
            {
                "action": "inspect_once_current_state",
                "opencodeSessionId": "ses_demo_progress",
            },
        )
        self.assertNotIn("message", result)

    def test_visible_no_change_maps_to_progress_signal_without_preview(self):
        turn_result = {
            "opencodeSessionId": "ses_demo_heartbeat",
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
        self.assertEqual(result["runtimeSignal"]["action"], "inspect_once_current_state")
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
        self.assertEqual(result["runtimeSignal"]["action"], "stay_silent")
        self.assertTrue(result["routing"]["mustPreserveOrigin"])

    def test_blocked_turn_maps_to_high_priority_blocker_signal(self):
        turn_result = {
            "opencodeSessionId": "ses_demo_blocked",
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
        self.assertEqual(result["facts"], {"status": "blocked", "phase": "Waiting for approval"})
        self.assertEqual(result["runtimeSignal"]["action"], "inspect_once_current_state")
        self.assertEqual(result["runtimeSignal"]["opencodeSessionId"], "ses_demo_blocked")

    def test_completed_no_change_stays_completion_signal_not_heartbeat(self):
        turn_result = {
            "opencodeSessionId": "ses_demo_completed",
            "factSkeleton": {
                "status": "completed",
                "phase": None,
                "latestMeaningfulPreview": "Validated final output and wrapped up the task.",
                "reason": "status=completed",
            },
            "shouldSend": True,
            "delivery": {
                "originSession": "origin-session-example",
                "originTarget": "origin-target-example",
            },
            "cadence": {
                "decision": "visible_update",
                "noChange": True,
                "consecutiveNoChangeCount": 6,
                "lastVisibleUpdateAt": "2026-03-08T11:02:30.059865+00:00",
            },
        }

        result = build_agent_turn_input(turn_result)

        self.assertEqual(result["updateType"], "completed")
        self.assertEqual(result["style"], "brief_completion")
        self.assertEqual(result["facts"], {"status": "completed"})
        self.assertEqual(result["runtimeSignal"]["action"], "inspect_once_current_state")
        self.assertTrue(result["routing"]["mustPreserveOrigin"])

    def test_agent_turn_input_schema_stays_inside_boundary(self):
        turn_result = {
            "opencodeSessionId": "ses_demo_schema",
            "factSkeleton": {
                "status": "completed",
                "phase": "Final verification",
                "latestMeaningfulPreview": "Validated delivery routing.",
                "reason": "status=completed",
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

        self.assertEqual(set(result), ALLOWED_TOP_LEVEL_KEYS)
        self.assertTrue(set(result["facts"]).issubset(ALLOWED_FACT_KEYS))
        self.assertTrue(set(result["mentionFields"]).issubset(ALLOWED_FACT_KEYS))
        self.assertEqual(set(result["cadence"]), ALLOWED_CADENCE_KEYS)
        self.assertEqual(set(result["routing"]), ALLOWED_ROUTING_KEYS)
        self.assertEqual(set(result["taskCluster"]), ALLOWED_TASK_CLUSTER_KEYS)
        self.assertEqual(set(result["replyPolicy"]), ALLOWED_REPLY_POLICY_KEYS)
        self.assertEqual(set(result["runtimeSignal"]), ALLOWED_RUNTIME_SIGNAL_KEYS)
        for forbidden_key in [
            "message",
            "summary",
            "headline",
            "replyText",
            "nextSteps",
            "strategy",
            "plan",
        ]:
            self.assertNotIn(forbidden_key, result)


if __name__ == "__main__":
    unittest.main()
