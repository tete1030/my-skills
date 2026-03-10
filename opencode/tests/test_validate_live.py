import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import opencode_validate_live as validate_live  # noqa: E402
from opencode_delivery_handoff import SYSTEM_EVENT_TEXT_HEADER  # noqa: E402


class OpenCodeValidateLiveTests(unittest.TestCase):
    def test_load_validation_config_falls_back_to_manager_defaults_env(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_config = Path(tmpdir) / "opencode-validation" / "config.json"
            defaults_env = Path(tmpdir) / "opencode-manager" / "local-defaults.env"
            defaults_env.parent.mkdir(parents=True, exist_ok=True)
            defaults_env.write_text(
                "\n".join(
                    [
                        "OPENCODE_BASE_URL='http://127.0.0.1:4096'",
                        "OPENCODE_WORKSPACE='/mnt/vault/test-opencode-skill'",
                        "OPENCLAW_SESSION_KEY='agent:main:telegram:group:-100:topic:42'",
                        "OPENCLAW_DELIVERY_TARGET='telegram:-100:topic:42'",
                        "OPENCODE_WATCH_INTERVAL_SEC='7'",
                        "OPENCODE_IDLE_TIMEOUT_SEC='55'",
                        "OPENCODE_WATCH_MESSAGE_LIMIT='11'",
                        "OPENCODE_WATCH_TIMEOUT_SEC='25'",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with mock.patch.object(validate_live, "DEFAULT_CONFIG_PATH", missing_config), mock.patch.object(
                validate_live, "DEFAULT_MANAGER_DEFAULTS_ENV", defaults_env
            ):
                config = validate_live.load_validation_config(missing_config)

            self.assertEqual(config["sourcePath"], str(defaults_env.resolve()))
            self.assertEqual(config["baseUrl"], "http://127.0.0.1:4096")
            self.assertEqual(config["workspace"], "/mnt/vault/test-opencode-skill")
            self.assertEqual(config["openclawSessionKey"], "agent:main:telegram:group:-100:topic:42")
            self.assertEqual(config["openclawDeliveryTarget"], "telegram:-100:topic:42")
            self.assertEqual(config["watchIntervalSec"], 7)
            self.assertEqual(config["idleTimeoutSec"], 55)
            self.assertEqual(config["watchMessageLimit"], 11)
            self.assertEqual(config["watchTimeoutSec"], 25)
            self.assertGreaterEqual(config["historyMessageLimit"], 11)

    def test_summarize_watch_log_handles_consecutive_duplicate_lines(self):
        envelope = {
            "kind": "opencode_origin_session_handoff",
            "version": "v2",
            "runtimeSignal": {
                "action": "inspect_once_current_state",
                "opencodeSessionId": "ses_demo_validate_live",
            },
        }
        step = {
            "kind": "opencode_watch_runner_step_v1",
            "watchAction": {
                "mode": "live",
                "operation": "execute",
                "shouldExecute": True,
                "actionKey": "opencode-origin-handoff-demo",
                "reason": "ready_inject_live",
            },
            "handoff": {
                "openclawDelivery": {
                    "deliveryAction": "inject",
                    "routeStatus": "ready",
                    "systemEventTemplate": {
                        "payload": {
                            "kind": "systemEvent",
                            "text": SYSTEM_EVENT_TEXT_HEADER + "\n" + json.dumps(envelope, ensure_ascii=False, indent=2),
                        }
                    },
                }
            },
        }
        pretty_step = json.dumps(step, ensure_ascii=False, indent=2).splitlines()
        duplicated_pretty_step = "\n".join(line for item in pretty_step for line in (item, item)) + "\n"
        log_text = (
            json.dumps({"kind": "opencode_watch_runtime_start_v1", "mode": "loop"})
            + "\n"
            + json.dumps({"kind": "opencode_watch_runtime_start_v1", "mode": "loop"})
            + "\n"
            + duplicated_pretty_step
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            watch_log = Path(tmpdir) / "watch.log"
            watch_log.write_text(log_text, encoding="utf-8")

            summary = validate_live.summarize_watch_log(watch_log)

        self.assertEqual(summary["documentCount"], 2)
        self.assertEqual(summary["stepCount"], 1)
        self.assertEqual(summary["handoffEnvelope"]["version"], "v2")
        self.assertEqual(summary["handoffEnvelope"]["runtimeSignal"]["opencodeSessionId"], "ses_demo_validate_live")
        self.assertEqual(summary["lastStep"]["watchAction"]["operation"], "execute")

    def test_build_verdict_marks_partial_readiness_when_core_checks_exist(self):
        verdict = validate_live.build_verdict(
            [
                {"name": "git_preflight", "passed": True},
                {"name": "start_watcher_live", "passed": True},
                {"name": "inspect_history", "passed": False},
            ],
            preflight_ok=True,
        )

        self.assertEqual(verdict, "partly_ready")


if __name__ == "__main__":
    unittest.main()
