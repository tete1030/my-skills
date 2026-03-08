import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from opencode_delivery_handoff import build_delivery_handoff  # noqa: E402
from opencode_origin_session_consume import (  # noqa: E402
    ALLOWED_RUNTIME_CONSUMPTION_KEYS,
    ALLOWED_RUNTIME_TOP_LEVEL_KEYS,
    build_runtime_consumption_from_payload,
    extract_system_event_payload,
)


class OriginSessionConsumeTests(unittest.TestCase):
    def ready_handoff(self):
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
                "originSession": "agent:main:telegram:group:example-target:topic:example-thread",
                "originTarget": "telegram:example-target:topic:example-thread",
                "mustPreserveOrigin": True,
            },
        }
        return build_delivery_handoff(agent_input)

    def test_matched_origin_session_becomes_runtime_decision_input(self):
        handoff = self.ready_handoff()
        payload = handoff["openclawDelivery"]["systemEventTemplate"]["payload"]

        result = build_runtime_consumption_from_payload(
            payload,
            expected_session="agent:main:telegram:group:example-target:topic:example-thread",
        )

        self.assertEqual(result["agentInput"]["routing"]["originSession"], "agent:main:telegram:group:example-target:topic:example-thread")
        self.assertEqual(result["runtimeConsumption"]["consumeAction"], "offer_decision")
        self.assertEqual(result["runtimeConsumption"]["sessionCheck"], "matched")
        self.assertEqual(result["runtimeConsumption"]["decisionOwner"], "main_session_agent")
        self.assertEqual(result["runtimeConsumption"]["narrativeOwner"], "main_session_agent")
        self.assertTrue(result["runtimeConsumption"]["preserveOrigin"])
        self.assertEqual(
            result["runtimeConsumption"]["deliveryPolicy"],
            {"primary": "origin_session_system_event", "cronFallback": "watchdog_only"},
        )

    def test_runtime_consumer_accepts_delivery_handoff_result_as_input_source(self):
        handoff = self.ready_handoff()
        payload = extract_system_event_payload(handoff)

        result = build_runtime_consumption_from_payload(payload)

        self.assertEqual(result["runtimeConsumption"]["consumeAction"], "offer_decision")
        self.assertEqual(result["runtimeConsumption"]["sessionCheck"], "not_checked")
        self.assertEqual(result["agentInput"]["updateType"], "progress")

    def test_mismatched_expected_session_holds_instead_of_rerouting(self):
        handoff = self.ready_handoff()
        payload = handoff["openclawDelivery"]["systemEventTemplate"]["payload"]

        result = build_runtime_consumption_from_payload(
            payload,
            expected_session="agent:main:telegram:group:different-target:topic:other-thread",
        )

        self.assertEqual(result["runtimeConsumption"]["consumeAction"], "hold")
        self.assertEqual(result["runtimeConsumption"]["sessionCheck"], "mismatch")
        self.assertEqual(result["runtimeConsumption"]["reason"], "expected_origin_session_mismatch")
        self.assertEqual(
            result["agentInput"]["routing"]["originSession"],
            "agent:main:telegram:group:example-target:topic:example-thread",
        )

    def test_schema_stays_mechanical(self):
        handoff = self.ready_handoff()
        payload = handoff["openclawDelivery"]["systemEventTemplate"]["payload"]

        result = build_runtime_consumption_from_payload(payload)

        self.assertEqual(set(result), ALLOWED_RUNTIME_TOP_LEVEL_KEYS)
        self.assertEqual(set(result["runtimeConsumption"]), ALLOWED_RUNTIME_CONSUMPTION_KEYS)
        for forbidden_key in ["message", "replyText", "plan", "strategy", "headline", "summary"]:
            self.assertNotIn(forbidden_key, result)
            self.assertNotIn(forbidden_key, result["runtimeConsumption"])

    def test_unrecognized_header_is_rejected(self):
        payload = {
            "kind": "systemEvent",
            "text": "not-an-opencode-handoff\n{}",
        }

        with self.assertRaises(ValueError):
            build_runtime_consumption_from_payload(payload)


if __name__ == "__main__":
    unittest.main()
