import copy
import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from opencode_session_turn import (  # noqa: E402
    ALLOWED_CADENCE_KEYS,
    ALLOWED_DELIVERY_KEYS,
    ALLOWED_FACT_SKELETON_KEYS,
    ALLOWED_TURN_KEYS,
    DEBUG_ONLY_TURN_KEYS,
    build_turn_result,
)
from opencode_task_cluster import ALLOWED_TASK_CLUSTER_KEYS  # noqa: E402


class TurnOutputTests(unittest.TestCase):
    def test_turn_result_emphasizes_fact_skeleton_and_delivery(self):
        payload = {
            "decision": {"decision": "visible_update", "reason": "state_changed"},
            "observation": {"status": "running", "phase": "Collect verification status", "noChange": False, "lastUpdatedMs": 123456789},
            "after": {
                "status": "running",
                "phase": "Collect verification status",
                "consecutiveNoChangeCount": 0,
                "lastVisibleUpdateAt": "2026-03-08T09:40:00+00:00",
            },
            "snapshot": {
                "latestUserInputSummary": "Please continue and give me a short summary when done.",
                "accumulatedEventSummary": "user: Please continue and give me a short summary when done. | text: 已改成结构化事件汇总，并补上回归测试。",
                "latestAssistantTextPreview": "Released v0.3.4 successfully. Included the usage-label cleanup change.",
                "latestMessage": {"id": "msg_latest"},
            },
        }

        result = build_turn_result(
            payload,
            control={"executionMode": "main_session_centered"},
            origin_session="origin-session-example",
            origin_target="origin-target-example",
            session_id="ses_turn_demo",
        )

        self.assertEqual(result["opencodeSessionId"], "ses_turn_demo")
        self.assertEqual(result["factSkeleton"]["status"], "running")
        self.assertEqual(result["factSkeleton"]["phase"], "Collect verification status")
        self.assertIn("user: Please continue", result["factSkeleton"]["latestMeaningfulPreview"])
        self.assertIn("text: 已改成结构化事件汇总", result["factSkeleton"]["latestMeaningfulPreview"])
        self.assertEqual(result["factSkeleton"]["reason"], "state_changed")
        self.assertTrue(result["shouldSend"])
        self.assertEqual(result["delivery"]["originSession"], "origin-session-example")
        self.assertEqual(result["delivery"]["originTarget"], "origin-target-example")
        self.assertEqual(result["cadence"]["decision"], "visible_update")
        self.assertFalse(result["cadence"]["noChange"])
        self.assertIsNotNone(result["taskCluster"]["key"])
        self.assertEqual(result["taskCluster"]["summary"], "Please continue and give me a short summary when done.")
        self.assertEqual(result["taskCluster"]["sourceUpdateMs"], 123456789)
        self.assertNotIn("fallback", result)
        self.assertNotIn("payload", result)
        self.assertNotIn("control", result)

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

    def test_turn_schema_stays_mechanical_by_default(self):
        payload = {
            "decision": {"decision": "visible_update", "reason": "state_changed"},
            "observation": {"status": "completed", "phase": "Wrap up", "noChange": False},
            "after": {
                "status": "completed",
                "phase": "Wrap up",
                "consecutiveNoChangeCount": 0,
                "lastVisibleUpdateAt": "2026-03-08T09:40:00+00:00",
            },
            "snapshot": {
                "latestAssistantTextPreview": "Done and verified.",
                "latestMessage": {"id": "msg_latest"},
            },
        }

        result = build_turn_result(payload, origin_session="origin-session-example", origin_target="origin-target-example")

        self.assertEqual(set(result), ALLOWED_TURN_KEYS)
        self.assertEqual(set(result["factSkeleton"]), ALLOWED_FACT_SKELETON_KEYS)
        self.assertEqual(set(result["delivery"]), ALLOWED_DELIVERY_KEYS)
        self.assertEqual(set(result["cadence"]), ALLOWED_CADENCE_KEYS)
        self.assertEqual(set(result["taskCluster"]), ALLOWED_TASK_CLUSTER_KEYS)
        for forbidden_key in ["control", "message", "summary", "headline", "plan", "strategy"]:
            self.assertNotIn(forbidden_key, result)

    def test_completed_task_cluster_ignores_tool_only_preview_churn_until_final_text(self):
        payload = {
            "decision": {"decision": "visible_update", "reason": "status=completed"},
            "observation": {"status": "completed", "phase": None, "noChange": False, "lastUpdatedMs": 123456789},
            "after": {
                "status": "completed",
                "phase": None,
                "consecutiveNoChangeCount": 0,
                "lastVisibleUpdateAt": "2026-03-08T09:40:00+00:00",
            },
            "snapshot": {
                "latestUserInputSummary": "Create or overwrite the file step2.txt",
                "accumulatedEventSummary": "user: Create or overwrite the file step2.txt | read: /mnt/vault/test-opencode-skill/step2.txt",
                "latestMessage": {
                    "id": "msg_read",
                    "role": "assistant",
                    "status": "completed",
                },
            },
        }

        read_only = build_turn_result(payload)
        self.assertIn("read:", read_only["factSkeleton"]["latestMeaningfulPreview"])
        self.assertEqual(read_only["taskCluster"]["detailRank"], 0)

        prune_payload = copy.deepcopy(payload)
        prune_payload["snapshot"]["accumulatedEventSummary"] = (
            "user: Create or overwrite the file step2.txt | prune: → apply_patch: step1.txt | → read: step1.txt"
        )
        prune_payload["snapshot"]["latestMessage"]["id"] = "msg_prune"
        prune_only = build_turn_result(prune_payload)
        self.assertIn("prune:", prune_only["factSkeleton"]["latestMeaningfulPreview"])
        self.assertEqual(prune_only["taskCluster"]["detailRank"], 0)

        mid_text_payload = copy.deepcopy(payload)
        mid_text_payload["snapshot"]["accumulatedEventSummary"] = (
            "user: Create or overwrite the file step2.txt | text: Wrote the file; verifying now."
        )
        mid_text_payload["snapshot"]["latestMessage"] = {
            "id": "msg_text",
            "role": "assistant",
            "status": "completed",
            "message.lastTextPreview": "Wrote the file; verifying now.",
            "textPreview": "Wrote the file; verifying now.",
        }
        mid_text = build_turn_result(mid_text_payload)
        self.assertEqual(mid_text["taskCluster"]["detailRank"], 0)

        final_payload = copy.deepcopy(payload)
        final_payload["snapshot"]["accumulatedEventSummary"] = (
            "user: Create or overwrite the file step2.txt | text: Done and verified."
        )
        final_payload["snapshot"]["latestMessage"] = {
            "id": "msg_done",
            "role": "assistant",
            "status": "completed",
            "type": "step-finish",
            "finish": "stop",
            "message.stopReason": "stop",
            "message.lastTextPreview": "Done and verified.",
            "textPreview": "Done and verified.",
        }
        final_text = build_turn_result(final_payload)
        self.assertEqual(final_text["taskCluster"]["detailRank"], len("Done and verified."))

    def test_turn_payload_is_debug_only(self):
        payload = {
            "decision": {"decision": "visible_update", "reason": "state_changed"},
            "observation": {"status": "running", "phase": "Verify", "noChange": False},
            "after": {
                "status": "running",
                "phase": "Verify",
                "consecutiveNoChangeCount": 0,
                "lastVisibleUpdateAt": "2026-03-08T09:40:00+00:00",
            },
            "snapshot": {"latestAssistantTextPreview": "Working.", "latestMessage": {"id": "msg_latest"}},
        }

        result = build_turn_result(payload, include_payload=True)

        self.assertEqual(set(result), ALLOWED_TURN_KEYS | DEBUG_ONLY_TURN_KEYS)
        self.assertEqual(result["payload"], payload)

    def test_turn_result_falls_back_to_abort_error_preview_when_no_text_exists(self):
        payload = {
            "decision": {"decision": "visible_update", "reason": "status=failed"},
            "observation": {"status": "failed", "phase": None, "noChange": False, "lastUpdatedMs": 123456789},
            "after": {
                "status": "failed",
                "phase": None,
                "consecutiveNoChangeCount": 0,
                "lastVisibleUpdateAt": "2026-03-08T09:40:00+00:00",
            },
            "snapshot": {
                "latestUserInputSummary": "Continue the probe.",
                "latestMessage": {
                    "id": "msg_abort",
                    "role": "assistant",
                    "status": "failed",
                    "message.errorName": "MessageAbortedError",
                    "message.errorMessage": "The operation was aborted.",
                    "errorPreview": "MessageAbortedError: The operation was aborted.",
                },
            },
        }

        result = build_turn_result(payload)

        self.assertEqual(result["factSkeleton"]["status"], "failed")
        self.assertIn("MessageAbortedError", result["factSkeleton"]["latestMeaningfulPreview"])
        self.assertGreater(result["taskCluster"]["detailRank"], 0)


if __name__ == "__main__":
    unittest.main()
