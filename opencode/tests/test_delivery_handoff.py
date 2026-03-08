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
    ALLOWED_WATCHDOG_CRON_TEMPLATE_KEYS,
    SYSTEM_EVENT_TEXT_HEADER,
    build_delivery_handoff,
)


class DeliveryHandoffTests(unittest.TestCase):
    def parse_system_event_text(self, text: str):
        self.assertTrue(text.startswith(SYSTEM_EVENT_TEXT_HEADER + "\n"))
        payload = text.split("\n", 1)[1]
        return json.loads(payload)

    def test_ready_handoff_builds_origin_session_system_event_templates(self):
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
                "originSession": "agent:main:telegram:group:-1003607560565:topic:3348",
                "originTarget": "telegram:-1003607560565:topic:3348",
                "mustPreserveOrigin": True,
            },
        }

        result = build_delivery_handoff(agent_input)

        self.assertEqual(result["openclawDelivery"]["deliveryAction"], "inject")
        self.assertEqual(result["openclawDelivery"]["routeStatus"], "ready")
        self.assertEqual(result["openclawDelivery"]["resolutionSource"], "originSession")
        self.assertTrue(result["openclawDelivery"]["dryRun"])
        self.assertTrue(result["openclawDelivery"]["requiresNarrative"])
        self.assertEqual(result["openclawDelivery"]["primaryDelivery"], "origin_session_system_event")
        self.assertEqual(result["openclawDelivery"]["cronFallback"], "watchdog_only")
        self.assertEqual(
            result["openclawDelivery"]["systemEventTemplate"]["sessionKey"],
            "agent:main:telegram:group:-1003607560565:topic:3348",
        )
        self.assertEqual(
            result["openclawDelivery"]["watchdogCronTemplate"],
            {
                "sessionTarget": "main",
                "sessionKey": "agent:main:telegram:group:-1003607560565:topic:3348",
                "payload": result["openclawDelivery"]["systemEventTemplate"]["payload"],
            },
        )

        payload = result["openclawDelivery"]["systemEventTemplate"]["payload"]
        self.assertEqual(set(payload), ALLOWED_SYSTEM_EVENT_PAYLOAD_KEYS)
        self.assertEqual(payload["kind"], "systemEvent")
        envelope = self.parse_system_event_text(payload["text"])
        self.assertEqual(envelope["kind"], "opencode_origin_session_handoff")
        self.assertEqual(envelope["deliveryPolicy"], {
            "primary": "origin_session_system_event",
            "cronFallback": "watchdog_only",
        })
        self.assertEqual(envelope["agentInput"]["routing"]["originSession"], agent_input["routing"]["originSession"])

    def test_origin_session_missing_holds_even_when_origin_target_exists(self):
        agent_input = {
            "shouldSend": True,
            "action": "send_update",
            "updateType": "completed",
            "priority": "normal",
            "style": "brief_completion",
            "reason": "status=completed",
            "narrativeOwner": "main_session_agent",
            "mentionFields": ["status", "latestMeaningfulPreview"],
            "facts": {
                "status": "completed",
                "latestMeaningfulPreview": "Validated the final output.",
            },
            "cadence": {
                "decision": "visible_update",
                "noChange": True,
                "consecutiveNoChangeCount": 5,
                "lastVisibleUpdateAt": "2026-03-08T11:02:30.059865+00:00",
            },
            "routing": {
                "originSession": None,
                "originTarget": "telegram:-1003607560565:topic:3348",
                "mustPreserveOrigin": True,
            },
        }

        result = build_delivery_handoff(agent_input)

        self.assertEqual(result["openclawDelivery"]["deliveryAction"], "hold")
        self.assertEqual(result["openclawDelivery"]["routeStatus"], "missing_origin_session")
        self.assertEqual(result["openclawDelivery"]["reason"], "origin_session_required")
        self.assertIsNone(result["openclawDelivery"]["systemEventTemplate"])
        self.assertIsNone(result["openclawDelivery"]["watchdogCronTemplate"])

    def test_conflicting_origin_routes_hold_without_rewriting(self):
        agent_input = {
            "shouldSend": True,
            "action": "send_update",
            "updateType": "blocked",
            "priority": "high",
            "style": "brief_blocker",
            "reason": "status=blocked",
            "narrativeOwner": "main_session_agent",
            "mentionFields": ["status", "phase", "latestMeaningfulPreview"],
            "facts": {
                "status": "blocked",
                "phase": "Waiting for approval",
                "latestMeaningfulPreview": "Need confirmation before deploy.",
            },
            "cadence": {
                "decision": "visible_update",
                "noChange": False,
                "consecutiveNoChangeCount": 0,
                "lastVisibleUpdateAt": "2026-03-08T09:40:00+00:00",
            },
            "routing": {
                "originSession": "agent:main:telegram:group:-1003607560565:topic:3348",
                "originTarget": "telegram:-1003607560565:topic:9999",
                "mustPreserveOrigin": True,
            },
        }

        result = build_delivery_handoff(agent_input)

        self.assertEqual(result["openclawDelivery"]["deliveryAction"], "hold")
        self.assertEqual(result["openclawDelivery"]["routeStatus"], "conflict")
        self.assertEqual(result["openclawDelivery"]["reason"], "origin_route_conflict")
        self.assertIsNone(result["openclawDelivery"]["systemEventTemplate"])
        self.assertIsNone(result["openclawDelivery"]["watchdogCronTemplate"])
        self.assertEqual(result["routing"]["originTarget"], "telegram:-1003607560565:topic:9999")

    def test_silent_turn_stays_skip_and_never_builds_templates(self):
        agent_input = {
            "shouldSend": False,
            "action": "stay_silent",
            "updateType": "silent",
            "priority": "low",
            "style": "silent",
            "reason": "recent_visible_update_exists",
            "narrativeOwner": "main_session_agent",
            "mentionFields": [],
            "facts": {},
            "cadence": {
                "decision": "silent_noop",
                "noChange": True,
                "consecutiveNoChangeCount": 3,
                "lastVisibleUpdateAt": "2026-03-08T09:40:00+00:00",
            },
            "routing": {
                "originSession": "agent:main:telegram:group:-1003607560565:topic:3348",
                "originTarget": "telegram:-1003607560565:topic:3348",
                "mustPreserveOrigin": True,
            },
        }

        result = build_delivery_handoff(agent_input)

        self.assertEqual(result["openclawDelivery"]["deliveryAction"], "skip")
        self.assertEqual(result["openclawDelivery"]["routeStatus"], "ready")
        self.assertEqual(result["openclawDelivery"]["reason"], "should_not_send")
        self.assertFalse(result["openclawDelivery"]["requiresNarrative"])
        self.assertIsNone(result["openclawDelivery"]["systemEventTemplate"])
        self.assertIsNone(result["openclawDelivery"]["watchdogCronTemplate"])

    def test_delivery_handoff_schema_stays_mechanical(self):
        agent_input = {
            "shouldSend": True,
            "action": "send_update",
            "updateType": "progress",
            "priority": "normal",
            "style": "brief_progress",
            "reason": "state_changed",
            "narrativeOwner": "main_session_agent",
            "mentionFields": ["status", "phase"],
            "facts": {
                "status": "running",
                "phase": "Collect verification status",
            },
            "cadence": {
                "decision": "visible_update",
                "noChange": False,
                "consecutiveNoChangeCount": 0,
                "lastVisibleUpdateAt": "2026-03-08T09:40:00+00:00",
            },
            "routing": {
                "originSession": "agent:main:telegram:group:-1003607560565:topic:3348",
                "originTarget": "telegram:-1003607560565:topic:3348",
                "mustPreserveOrigin": True,
            },
        }

        result = build_delivery_handoff(agent_input)

        self.assertEqual(set(result), ALLOWED_HANDOFF_TOP_LEVEL_KEYS)
        self.assertEqual(set(result["openclawDelivery"]), ALLOWED_OPENCLAW_DELIVERY_KEYS)
        self.assertEqual(
            set(result["openclawDelivery"]["systemEventTemplate"]),
            ALLOWED_SYSTEM_EVENT_TEMPLATE_KEYS,
        )
        self.assertEqual(
            set(result["openclawDelivery"]["watchdogCronTemplate"]),
            ALLOWED_WATCHDOG_CRON_TEMPLATE_KEYS,
        )
        for forbidden_key in ["message", "replyText", "plan", "strategy", "headline", "summary", "toolRequestTemplate"]:
            self.assertNotIn(forbidden_key, result)
            self.assertNotIn(forbidden_key, result["openclawDelivery"])


if __name__ == "__main__":
    unittest.main()
