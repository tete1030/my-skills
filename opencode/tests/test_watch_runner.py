import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from opencode_delivery_handoff import build_delivery_handoff  # noqa: E402
from opencode_openclaw_agent_call import build_gateway_agent_call  # noqa: E402
from opencode_watch_runner import (  # noqa: E402
    action_key_from_agent_call,
    decide_watch_action,
    update_watch_state,
)


class WatchRunnerTests(unittest.TestCase):
    def ready_agent_call(self):
        return {
            "kind": "openclaw_gateway_agent_call_v1",
            "dryRun": True,
            "handoffDryRun": False,
            "deliveryAction": "inject",
            "routeStatus": "ready",
            "reason": "resolved_from_origin_session",
            "sessionKey": "agent:main:telegram:group:-100123:topic:42",
            "gatewayMethod": "agent",
            "gatewayParams": {
                "sessionKey": "agent:main:telegram:group:-100123:topic:42",
                "message": "Runtime task update for the current conversation.",
                "deliver": True,
                "idempotencyKey": "opencode-origin-handoff-abc123",
            },
            "argv": ["openclaw", "gateway", "call", "agent"],
            "shellCommand": "openclaw gateway call agent",
            "executed": False,
            "execution": None,
        }

    def test_action_key_uses_gateway_idempotency_key(self):
        self.assertEqual(
            action_key_from_agent_call(self.ready_agent_call()),
            "opencode-origin-handoff-abc123",
        )

    def test_dry_run_plans_ready_inject_without_execution(self):
        action = decide_watch_action(self.ready_agent_call(), {}, live=False)

        self.assertEqual(action["operation"], "plan")
        self.assertFalse(action["shouldExecute"])
        self.assertFalse(action["duplicateSuppressed"])
        self.assertEqual(action["reason"], "ready_inject_dry_run")

    def test_live_mode_executes_ready_inject_when_not_seen(self):
        action = decide_watch_action(self.ready_agent_call(), {}, live=True)

        self.assertEqual(action["operation"], "execute")
        self.assertTrue(action["shouldExecute"])
        self.assertFalse(action["duplicateSuppressed"])
        self.assertEqual(action["reason"], "ready_inject_live")

    def test_live_mode_suppresses_duplicate_action_key(self):
        action = decide_watch_action(
            self.ready_agent_call(),
            {"lastExecutedActionKey": "opencode-origin-handoff-abc123"},
            live=True,
        )

        self.assertEqual(action["operation"], "skip_duplicate")
        self.assertFalse(action["shouldExecute"])
        self.assertTrue(action["duplicateSuppressed"])
        self.assertEqual(action["reason"], "duplicate_action_key")

    def test_live_mode_suppresses_second_run_when_only_cadence_changes(self):
        first_turn = {
            "factSkeleton": {
                "status": "completed",
                "phase": None,
                "latestMeaningfulPreview": "Validated the final output.",
                "reason": "status=completed",
            },
            "shouldSend": True,
            "delivery": {
                "originSession": "agent:main:telegram:group:-100123:topic:42",
                "originTarget": "telegram:-100123:topic:42",
            },
            "cadence": {
                "decision": "visible_update",
                "noChange": True,
                "consecutiveNoChangeCount": 5,
                "lastVisibleUpdateAt": "2026-03-08T10:45:00+00:00",
            },
        }
        second_turn = json.loads(json.dumps(first_turn))
        second_turn["cadence"]["consecutiveNoChangeCount"] = 6
        second_turn["cadence"]["lastVisibleUpdateAt"] = "2026-03-08T11:02:30+00:00"

        first_agent_call = build_gateway_agent_call(build_delivery_handoff(first_turn, dry_run=False))
        second_agent_call = build_gateway_agent_call(build_delivery_handoff(second_turn, dry_run=False))

        first_action = decide_watch_action(first_agent_call, {}, live=True)
        second_action = decide_watch_action(
            second_agent_call,
            {"lastExecutedActionKey": first_action["actionKey"]},
            live=True,
        )

        self.assertEqual(first_action["operation"], "execute")
        self.assertEqual(second_action["operation"], "skip_duplicate")
        self.assertTrue(second_action["duplicateSuppressed"])
        self.assertEqual(first_action["actionKey"], second_action["actionKey"])

    def test_non_ready_agent_call_skips_before_live_or_dry_run(self):
        agent_call = self.ready_agent_call()
        agent_call["deliveryAction"] = "hold"
        agent_call["routeStatus"] = "missing_origin_session"
        agent_call["reason"] = "origin_session_required"

        dry_action = decide_watch_action(agent_call, {}, live=False)
        live_action = decide_watch_action(agent_call, {}, live=True)

        self.assertEqual(dry_action["operation"], "skip")
        self.assertEqual(live_action["operation"], "skip")
        self.assertEqual(dry_action["reason"], "origin_session_required")
        self.assertEqual(live_action["reason"], "origin_session_required")

    def test_update_watch_state_persists_last_executed_key_across_restart(self):
        agent_call = self.ready_agent_call()
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "watch-state.json"
            state_path.write_text(json.dumps({"status": "running"}) + "\n")

            watch_state = update_watch_state(
                state_path,
                session_id="ses_123",
                watch_action={
                    "mode": "live",
                    "operation": "execute",
                    "shouldExecute": True,
                    "duplicateSuppressed": False,
                    "actionKey": "opencode-origin-handoff-abc123",
                    "reason": "ready_inject_live",
                },
                agent_call=agent_call,
            )

            self.assertEqual(watch_state["lastExecutedActionKey"], "opencode-origin-handoff-abc123")
            reloaded = json.loads(state_path.read_text())
            self.assertEqual(reloaded["watchRunner"]["lastExecutedActionKey"], "opencode-origin-handoff-abc123")
            self.assertEqual(reloaded["status"], "running")


if __name__ == "__main__":
    unittest.main()
