import json
import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from opencode_delivery_handoff import build_delivery_handoff  # noqa: E402
from opencode_openclaw_agent_call import (  # noqa: E402
    AGENT_CALL_KIND,
    build_gateway_agent_call,
    execute_gateway_agent_call,
)


class OpenClawAgentCallTests(unittest.TestCase):
    def ready_turn(self):
        return {
            "factSkeleton": {
                "status": "running",
                "phase": "Collect verification status",
                "latestMeaningfulPreview": "Released v0.3.4 successfully.",
                "reason": "state_changed",
            },
            "shouldSend": True,
            "delivery": {
                "originSession": "agent:main:telegram:group:-100123:topic:42",
                "originTarget": "telegram:-100123:topic:42",
            },
            "cadence": {
                "decision": "visible_update",
                "noChange": False,
                "consecutiveNoChangeCount": 0,
                "lastVisibleUpdateAt": "2026-03-08T09:40:00+00:00",
            },
        }

    def ready_handoff(self, *, dry_run: bool = False):
        return build_delivery_handoff(self.ready_turn(), dry_run=dry_run)

    def test_ready_handoff_builds_gateway_agent_call_plan(self):
        handoff = self.ready_handoff()

        plan = build_gateway_agent_call(handoff, timeout_ms=15000)

        self.assertEqual(plan["kind"], AGENT_CALL_KIND)
        self.assertTrue(plan["dryRun"])
        self.assertFalse(plan["handoffDryRun"])
        self.assertEqual(plan["deliveryAction"], "inject")
        self.assertEqual(plan["routeStatus"], "ready")
        self.assertEqual(plan["sessionKey"], "agent:main:telegram:group:-100123:topic:42")
        self.assertEqual(plan["gatewayMethod"], "agent")
        self.assertEqual(plan["gatewayParams"]["sessionKey"], plan["sessionKey"])
        self.assertTrue(plan["gatewayParams"]["deliver"])
        self.assertIn("OPENCODE_ORIGIN_SESSION_SYSTEM_EVENT_V1", plan["gatewayParams"]["message"])
        self.assertEqual(plan["argv"][:4], ["openclaw", "gateway", "call", "agent"])
        self.assertIn("--timeout", plan["argv"])
        self.assertIn("15000", plan["argv"])
        params_index = plan["argv"].index("--params") + 1
        decoded = json.loads(plan["argv"][params_index])
        self.assertEqual(decoded, plan["gatewayParams"])
        self.assertIn("openclaw gateway call agent", plan["shellCommand"])
        self.assertFalse(plan["executed"])
        self.assertIsNone(plan["execution"])

    def test_non_ready_handoff_stays_dry_run_without_command(self):
        turn_result = {
            "factSkeleton": {
                "status": "completed",
                "phase": None,
                "latestMeaningfulPreview": "Validated the final output.",
                "reason": "status=completed",
            },
            "shouldSend": True,
            "delivery": {
                "originSession": None,
                "originTarget": "telegram:-100123:topic:42",
            },
            "cadence": {
                "decision": "visible_update",
                "noChange": False,
                "consecutiveNoChangeCount": 0,
                "lastVisibleUpdateAt": "2026-03-08T11:02:30+00:00",
            },
        }
        handoff = build_delivery_handoff(turn_result)

        plan = build_gateway_agent_call(handoff)

        self.assertEqual(plan["deliveryAction"], "hold")
        self.assertEqual(plan["routeStatus"], "missing_origin_session")
        self.assertIsNone(plan["sessionKey"])
        self.assertIsNone(plan["gatewayParams"])
        self.assertIsNone(plan["argv"])
        self.assertFalse(plan["executed"])

    def test_execute_refuses_handoff_marked_dry_run_without_override(self):
        handoff = self.ready_handoff(dry_run=True)
        plan = build_gateway_agent_call(handoff)

        with self.assertRaisesRegex(ValueError, "dryRun=true"):
            execute_gateway_agent_call(plan)

    def test_execute_runs_generated_command_when_explicitly_allowed(self):
        handoff = self.ready_handoff()
        plan = build_gateway_agent_call(handoff, expect_final=True)

        calls = []

        def fake_runner(argv, capture_output, text):
            calls.append((argv, capture_output, text))

            class Result:
                returncode = 0
                stdout = '{"runId":"r1","status":"accepted"}\n'
                stderr = ""

            return Result()

        executed = execute_gateway_agent_call(plan, runner=fake_runner)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], plan["argv"])
        self.assertTrue(executed["executed"])
        self.assertFalse(executed["dryRun"])
        self.assertEqual(executed["execution"]["returncode"], 0)
        self.assertIn('"runId":"r1"', executed["execution"]["stdout"])

    def test_execute_rejects_session_rewrite_attempts(self):
        handoff = self.ready_handoff()
        handoff["openclawDelivery"]["systemEventTemplate"]["sessionKey"] = "agent:main:telegram:group:-100123:topic:999"

        with self.assertRaisesRegex(ValueError, "refuses session rewrite"):
            build_gateway_agent_call(handoff)


if __name__ == "__main__":
    unittest.main()
