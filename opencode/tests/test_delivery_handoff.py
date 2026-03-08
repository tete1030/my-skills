import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from opencode_delivery_handoff import (  # noqa: E402
    ALLOWED_HANDOFF_TOP_LEVEL_KEYS,
    ALLOWED_OPENCLAW_DELIVERY_KEYS,
    ALLOWED_TOOL_REQUEST_TEMPLATE_KEYS,
    build_delivery_handoff,
)


class DeliveryHandoffTests(unittest.TestCase):
    def test_ready_handoff_uses_origin_target_template(self):
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

        self.assertEqual(result["openclawDelivery"]["deliveryAction"], "handoff")
        self.assertEqual(result["openclawDelivery"]["routeStatus"], "ready")
        self.assertEqual(result["openclawDelivery"]["resolutionSource"], "originTarget")
        self.assertTrue(result["openclawDelivery"]["dryRun"])
        self.assertTrue(result["openclawDelivery"]["requiresNarrative"])
        self.assertEqual(
            result["openclawDelivery"]["toolRequestTemplate"],
            {
                "tool": "message.send",
                "action": "send",
                "channel": "telegram",
                "target": "-1003607560565",
                "threadId": "3348",
            },
        )
        self.assertEqual(result["routing"]["originTarget"], "telegram:-1003607560565:topic:3348")

    def test_origin_session_can_resolve_when_origin_target_missing(self):
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
                "originSession": "agent:main:telegram:group:-1003607560565:topic:3348",
                "originTarget": None,
                "mustPreserveOrigin": True,
            },
        }

        result = build_delivery_handoff(agent_input)

        self.assertEqual(result["openclawDelivery"]["deliveryAction"], "handoff")
        self.assertEqual(result["openclawDelivery"]["routeStatus"], "ready")
        self.assertEqual(result["openclawDelivery"]["resolutionSource"], "originSession")
        self.assertEqual(result["openclawDelivery"]["toolRequestTemplate"]["threadId"], "3348")

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
        self.assertIsNone(result["openclawDelivery"]["toolRequestTemplate"])
        self.assertEqual(result["routing"]["originTarget"], "telegram:-1003607560565:topic:9999")

    def test_silent_turn_stays_skip_and_never_builds_send_template(self):
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
        self.assertIsNone(result["openclawDelivery"]["toolRequestTemplate"])

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
            set(result["openclawDelivery"]["toolRequestTemplate"]),
            ALLOWED_TOOL_REQUEST_TEMPLATE_KEYS,
        )
        for forbidden_key in ["message", "replyText", "plan", "strategy", "headline", "summary"]:
            self.assertNotIn(forbidden_key, result)
            self.assertNotIn(forbidden_key, result["openclawDelivery"])


if __name__ == "__main__":
    unittest.main()
