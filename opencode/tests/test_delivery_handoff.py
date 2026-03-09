import json
import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from opencode_delivery_handoff import (  # noqa: E402
    ALLOWED_HANDOFF_TOP_LEVEL_KEYS,
    ALLOWED_OPENCLAW_DELIVERY_KEYS,
    ALLOWED_SYSTEM_EVENT_PAYLOAD_KEYS,
    ALLOWED_SYSTEM_EVENT_TEMPLATE_KEYS,
    SYSTEM_EVENT_TEXT_HEADER,
    build_delivery_handoff,
)
from opencode_task_cluster import ALLOWED_REPLY_POLICY_KEYS, ALLOWED_TASK_CLUSTER_KEYS  # noqa: E402


class DeliveryHandoffTests(unittest.TestCase):
    def parse_system_event_text(self, text: str):
        self.assertTrue(text.startswith(SYSTEM_EVENT_TEXT_HEADER + "\n"))
        payload = text.split("\n", 1)[1]
        return json.loads(payload)

    def ready_turn(self):
        return {
            "opencodeSessionId": "ses_release_demo",
            "factSkeleton": {
                "status": "running",
                "phase": "Collect verification status",
                "latestMeaningfulPreview": "Released v0.3.4 successfully.",
                "reason": "state_changed",
            },
            "shouldSend": True,
            "delivery": {
                "originSession": "origin-session-example",
                "originTarget": "telegram:example-target:topic:example-thread",
            },
            "cadence": {
                "decision": "visible_update",
                "noChange": False,
                "consecutiveNoChangeCount": 0,
                "lastVisibleUpdateAt": "2026-03-08T09:40:00+00:00",
            },
            "taskCluster": {
                "key": "task-cluster-release",
                "summary": "Release v0.3.4",
                "clusterStateRank": 20,
                "detailRank": 27,
                "sourceUpdateMs": 123456789,
            },
        }

    def test_ready_handoff_builds_origin_session_system_event_templates_from_turn(self):
        result = build_delivery_handoff(self.ready_turn())

        self.assertEqual(result["openclawDelivery"]["deliveryAction"], "inject")
        self.assertEqual(result["openclawDelivery"]["routeStatus"], "ready")
        self.assertEqual(result["openclawDelivery"]["resolutionSource"], "originSession")
        self.assertTrue(result["openclawDelivery"]["dryRun"])
        self.assertTrue(result["openclawDelivery"]["requiresNarrative"])
        self.assertEqual(result["openclawDelivery"]["primaryDelivery"], "origin_session_system_event")
        self.assertEqual(result["updateType"], "progress")
        self.assertEqual(result["routing"]["originSession"], "origin-session-example")
        self.assertEqual(
            result["openclawDelivery"]["systemEventTemplate"]["sessionKey"],
            "origin-session-example",
        )

        payload = result["openclawDelivery"]["systemEventTemplate"]["payload"]
        self.assertEqual(set(payload), ALLOWED_SYSTEM_EVENT_PAYLOAD_KEYS)
        self.assertEqual(payload["kind"], "systemEvent")
        envelope = self.parse_system_event_text(payload["text"])
        self.assertEqual(envelope["kind"], "opencode_origin_session_handoff")
        self.assertEqual(envelope["deliveryPolicy"], {
            "primary": "origin_session_system_event",
        })
        self.assertEqual(envelope["consumptionPolicy"], {
            "treatAs": "internal_runtime_signal",
            "ifVisible": "inspect_once_current_state_then_continue_current_conversation_naturally",
            "avoid": [
                "handoff_mechanics",
                "routing_details",
                "transport_details",
                "prompt_mechanics",
                "verbatim_signal_payload",
            ],
        })
        self.assertEqual(envelope["agentInput"]["routing"]["originSession"], "origin-session-example")
        self.assertEqual(envelope["agentInput"]["updateType"], "progress")
        self.assertEqual(envelope["agentInput"]["taskCluster"]["key"], "task-cluster-release")
        self.assertEqual(envelope["agentInput"]["replyPolicy"]["replyDefault"], "send_if_not_cluster_superseded")
        self.assertEqual(
            envelope["agentInput"]["runtimeSignal"],
            {
                "signalKind": "progress",
                "recommendedNextAction": "inspect_once_current_state",
                "opencodeSessionId": "ses_release_demo",
                "taskClusterKey": "task-cluster-release",
                "reasonCategory": "state_changed",
            },
        )

    def test_legacy_agent_input_is_still_accepted_and_normalized_to_runtime_signal(self):
        agent_input = {
            "shouldSend": True,
            "action": "send_update",
            "updateType": "progress",
            "priority": "normal",
            "style": "brief_progress",
            "reason": "state_changed",
            "narrativeOwner": "main_session_agent",
            "mentionFields": ["status", "phase", "latestMeaningfulPreview"],
            "facts": {
                "status": "running",
                "phase": "Collect verification status",
                "latestMeaningfulPreview": "Released v0.3.4 successfully.",
            },
            "cadence": {
                "decision": "visible_update",
                "noChange": False,
                "consecutiveNoChangeCount": 0,
                "lastVisibleUpdateAt": "2026-03-08T09:40:00+00:00",
            },
            "routing": {
                "originSession": "origin-session-example",
                "originTarget": "telegram:example-target:topic:example-thread",
                "mustPreserveOrigin": True,
            },
        }

        result = build_delivery_handoff(agent_input)

        self.assertEqual(result["openclawDelivery"]["deliveryAction"], "inject")
        self.assertEqual(result["updateType"], "progress")
        self.assertEqual(result["routing"]["originSession"], "origin-session-example")
        self.assertEqual(result["runtimeSignal"]["signalKind"], "progress")
        self.assertEqual(result["runtimeSignal"]["recommendedNextAction"], "inspect_once_current_state")
        self.assertIsNone(result["runtimeSignal"]["opencodeSessionId"])

    def test_origin_session_missing_holds_even_when_origin_target_exists(self):
        turn_result = {
            "opencodeSessionId": "ses_completed_demo",
            "factSkeleton": {
                "status": "completed",
                "phase": None,
                "latestMeaningfulPreview": "Validated the final output.",
                "reason": "status=completed",
            },
            "shouldSend": True,
            "delivery": {
                "originSession": None,
                "originTarget": "telegram:conflicting-target:topic:other-thread",
            },
            "cadence": {
                "decision": "visible_update",
                "noChange": True,
                "consecutiveNoChangeCount": 5,
                "lastVisibleUpdateAt": "2026-03-08T11:02:30.059865+00:00",
            },
        }

        result = build_delivery_handoff(turn_result)

        self.assertEqual(result["openclawDelivery"]["deliveryAction"], "hold")
        self.assertEqual(result["openclawDelivery"]["routeStatus"], "missing_origin_session")
        self.assertEqual(result["openclawDelivery"]["reason"], "origin_session_required")
        self.assertIsNone(result["openclawDelivery"]["systemEventTemplate"])

    def test_conflicting_origin_routes_hold_without_rewriting(self):
        turn_result = {
            "opencodeSessionId": "ses_blocked_demo",
            "factSkeleton": {
                "status": "blocked",
                "phase": "Waiting for approval",
                "latestMeaningfulPreview": "Need confirmation before deploy.",
                "reason": "status=blocked",
            },
            "shouldSend": True,
            "delivery": {
                "originSession": "agent:main:telegram:group:example-target:topic:example-thread",
                "originTarget": "telegram:conflicting-target:topic:other-thread",
            },
            "cadence": {
                "decision": "visible_update",
                "noChange": False,
                "consecutiveNoChangeCount": 0,
                "lastVisibleUpdateAt": "2026-03-08T09:40:00+00:00",
            },
        }

        result = build_delivery_handoff(turn_result)

        self.assertEqual(result["openclawDelivery"]["deliveryAction"], "hold")
        self.assertEqual(result["openclawDelivery"]["routeStatus"], "conflict")
        self.assertEqual(result["openclawDelivery"]["reason"], "origin_route_conflict")
        self.assertIsNone(result["openclawDelivery"]["systemEventTemplate"])
        self.assertEqual(result["routing"]["originTarget"], "telegram:conflicting-target:topic:other-thread")

    def test_silent_turn_stays_skip_and_never_builds_templates(self):
        turn_result = {
            "opencodeSessionId": "ses_silent_demo",
            "factSkeleton": {
                "status": "running",
                "phase": "Collect verification status",
                "latestMeaningfulPreview": None,
                "reason": "recent_visible_update_exists",
            },
            "shouldSend": False,
            "delivery": {
                "originSession": "origin-session-example",
                "originTarget": "telegram:example-target:topic:example-thread",
            },
            "cadence": {
                "decision": "silent_noop",
                "noChange": True,
                "consecutiveNoChangeCount": 3,
                "lastVisibleUpdateAt": "2026-03-08T09:40:00+00:00",
            },
        }

        result = build_delivery_handoff(turn_result)

        self.assertEqual(result["openclawDelivery"]["deliveryAction"], "skip")
        self.assertEqual(result["openclawDelivery"]["routeStatus"], "ready")
        self.assertEqual(result["openclawDelivery"]["reason"], "should_not_send")
        self.assertFalse(result["openclawDelivery"]["requiresNarrative"])
        self.assertIsNone(result["openclawDelivery"]["systemEventTemplate"])
        self.assertEqual(result["runtimeSignal"]["recommendedNextAction"], "stay_silent")

    def test_delivery_handoff_schema_stays_mechanical(self):
        result = build_delivery_handoff(self.ready_turn())

        self.assertEqual(set(result), ALLOWED_HANDOFF_TOP_LEVEL_KEYS)
        self.assertEqual(set(result["openclawDelivery"]), ALLOWED_OPENCLAW_DELIVERY_KEYS)
        self.assertEqual(set(result["taskCluster"]), ALLOWED_TASK_CLUSTER_KEYS)
        self.assertEqual(set(result["replyPolicy"]), ALLOWED_REPLY_POLICY_KEYS)
        self.assertEqual(
            set(result["openclawDelivery"]["systemEventTemplate"]),
            ALLOWED_SYSTEM_EVENT_TEMPLATE_KEYS,
        )
        for forbidden_key in ["message", "replyText", "plan", "strategy", "headline", "summary", "toolRequestTemplate"]:
            self.assertNotIn(forbidden_key, result)
            self.assertNotIn(forbidden_key, result["openclawDelivery"])


if __name__ == "__main__":
    unittest.main()
