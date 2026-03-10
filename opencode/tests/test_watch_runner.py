import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from opencode_delivery_handoff import build_delivery_handoff  # noqa: E402
from opencode_openclaw_agent_call import build_gateway_agent_call  # noqa: E402
from opencode_task_cluster import build_task_cluster  # noqa: E402
from opencode_watch_runner import (  # noqa: E402
    action_key_from_agent_call,
    decide_watch_action,
    should_stop_for_idle_timeout,
    turn_for_handoff,
    update_watch_state,
)


class WatchRunnerTests(unittest.TestCase):
    def task_cluster(self, summary: str = "Create watcher-test files", preview: str = "Created watcher-test.txt"):
        return build_task_cluster(summary, preview, status="running", source_update_ms=123456789)

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

    def test_turn_for_handoff_includes_opencode_session_id_signal_context(self):
        handoff_turn = turn_for_handoff(
            {
                "factSkeleton": {"status": "running", "phase": "Collect verification status", "latestMeaningfulPreview": "Created files.", "reason": "state_changed"},
                "shouldSend": True,
                "delivery": {"originSession": "origin-session", "originTarget": "origin-target"},
                "cadence": {"decision": "visible_update", "noChange": False, "consecutiveNoChangeCount": 0, "lastVisibleUpdateAt": "2026-03-08T09:40:00+00:00"},
                "taskCluster": self.task_cluster(),
            },
            opencode_session_id="ses_watch_demo",
        )

        self.assertEqual(handoff_turn["opencodeSessionId"], "ses_watch_demo")
        self.assertEqual(handoff_turn["factSkeleton"]["status"], "running")

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

    def test_newer_task_cluster_conclusion_suppresses_older_late_update(self):
        late_step_one = self.task_cluster(
            preview="Step 1 completed.",
        )
        late_step_one["clusterStateRank"] = 40
        late_step_one["sourceUpdateMs"] = 100
        late_step_one["detailRank"] = len("Step 1 completed.")

        newer_both_steps = self.task_cluster(
            preview="Step 1 completed and Step 2 completed.",
        )
        newer_both_steps["clusterStateRank"] = 40
        newer_both_steps["sourceUpdateMs"] = 200
        newer_both_steps["detailRank"] = len("Step 1 completed and Step 2 completed.")

        action = decide_watch_action(
            self.ready_agent_call(),
            {"clusterHeads": {newer_both_steps["key"]: newer_both_steps}},
            live=True,
            task_cluster=late_step_one,
        )

        self.assertEqual(action["operation"], "skip_superseded")
        self.assertFalse(action["shouldExecute"])
        self.assertTrue(action["supersededSuppressed"])
        self.assertEqual(action["reason"], "superseded_task_cluster_update")

    def test_earlier_richer_completion_suppresses_later_weaker_completion(self):
        earlier_richer_completion = self.task_cluster(
            preview="Step 1 completed and Step 2 completed.",
        )
        earlier_richer_completion["clusterStateRank"] = 40
        earlier_richer_completion["sourceUpdateMs"] = 100
        earlier_richer_completion["detailRank"] = len("Step 1 completed and Step 2 completed.")

        later_weaker_completion = self.task_cluster(preview="Step 2 completed.")
        later_weaker_completion["clusterStateRank"] = 40
        later_weaker_completion["sourceUpdateMs"] = 200
        later_weaker_completion["detailRank"] = len("Step 2 completed.")

        action = decide_watch_action(
            self.ready_agent_call(),
            {"clusterHeads": {earlier_richer_completion["key"]: earlier_richer_completion}},
            live=True,
            task_cluster=later_weaker_completion,
        )

        self.assertEqual(action["operation"], "skip_superseded")
        self.assertFalse(action["shouldExecute"])
        self.assertTrue(action["supersededSuppressed"])
        self.assertEqual(action["reason"], "superseded_task_cluster_update")

    def test_genuinely_new_useful_cluster_update_stays_visible(self):
        earlier_progress = self.task_cluster(preview="Step 1 completed.")
        earlier_progress["clusterStateRank"] = 20
        earlier_progress["sourceUpdateMs"] = 100

        newer_completion = self.task_cluster(preview="Step 1 completed and Step 2 completed.")
        newer_completion["clusterStateRank"] = 40
        newer_completion["sourceUpdateMs"] = 200

        action = decide_watch_action(
            self.ready_agent_call(),
            {"clusterHeads": {earlier_progress["key"]: earlier_progress}},
            live=True,
            task_cluster=newer_completion,
        )

        self.assertEqual(action["operation"], "execute")
        self.assertTrue(action["shouldExecute"])
        self.assertFalse(action["supersededSuppressed"])
        self.assertEqual(action["reason"], "ready_inject_live")

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
                    "supersededSuppressed": False,
                    "actionKey": "opencode-origin-handoff-abc123",
                    "reason": "ready_inject_live",
                },
                agent_call=agent_call,
                turn={
                    "factSkeleton": {
                        "status": "completed",
                        "phase": "done",
                        "latestMeaningfulPreview": "Validated the final output.",
                        "reason": "status=completed",
                    },
                    "cadence": {
                        "decision": "visible_update",
                        "noChange": True,
                        "consecutiveNoChangeCount": 0,
                        "lastVisibleUpdateAt": "2026-03-08T10:45:00+00:00",
                    },
                    "taskCluster": self.task_cluster(preview="Validated the final output."),
                },
                task_cluster=self.task_cluster(preview="Validated the final output."),
            )

            self.assertEqual(watch_state["lastExecutedActionKey"], "opencode-origin-handoff-abc123")
            self.assertIn(watch_state["lastTaskClusterKey"], watch_state["clusterHeads"])
            reloaded = json.loads(state_path.read_text())
            self.assertEqual(reloaded["watchRunner"]["lastExecutedActionKey"], "opencode-origin-handoff-abc123")
            self.assertEqual(reloaded["status"], "running")

    def test_terminal_state_can_trigger_idle_timeout_exit(self):
        watch_state = {
            "idleEligibleSince": "2026-03-08T10:00:00+00:00",
            "lastActivityAt": "2026-03-08T10:00:00+00:00",
        }

        with mock.patch("opencode_watch_runner.now_utc") as now_utc:
            from datetime import datetime, timezone

            now_utc.return_value = datetime(2026, 3, 8, 10, 20, 0, tzinfo=timezone.utc)
            self.assertTrue(should_stop_for_idle_timeout(watch_state, idle_timeout_sec=600))


if __name__ == "__main__":
    unittest.main()
