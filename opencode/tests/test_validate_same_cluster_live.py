import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import opencode_validate_same_cluster_live as same_cluster_live  # noqa: E402


class OpenCodeValidateSameClusterLiveTests(unittest.TestCase):
    def test_is_well_formed_same_cluster_reply_requires_exact_no_reply_token(self):
        session_id = "ses_samecluster_demo"

        self.assertTrue(same_cluster_live.is_well_formed_same_cluster_reply("NO_REPLY", session_id=session_id))
        self.assertFalse(
            same_cluster_live.is_well_formed_same_cluster_reply(
                "We should suppress this.\nNO_REPLY",
                session_id=session_id,
            )
        )
        self.assertTrue(
            same_cluster_live.is_well_formed_same_cluster_reply(
                "这条现在已经完成了。",
                session_id=session_id,
            )
        )

    def test_build_same_cluster_prompt_reuses_one_stable_prompt_shape(self):
        run_id = "samecluster-demo"
        prompt = same_cluster_live.build_same_cluster_prompt(run_id)

        self.assertIn("Same-cluster stress validation only.", prompt)
        self.assertIn(f".claw-validation/{run_id}.txt", prompt)
        self.assertIn(f"alpha {run_id}", prompt)
        self.assertIn(f"beta {run_id}", prompt)
        self.assertIn(f"gamma {run_id}", prompt)
        self.assertIn("Append exactly one next missing line", prompt)
        self.assertIn("Do not create shell variables named status.", prompt)
        self.assertIn("sleep 20 seconds", prompt)
        self.assertIn("Finish with one short final line: done.", prompt)

    def test_evaluate_same_cluster_windows_accepts_one_visible_then_suppressed_then_terminal(self):
        session_id = "ses_samecluster_demo"
        windows = [
            {"replyText": "我刚看了下，这条目前还在跑。"},
            {"replyText": "NO_REPLY"},
            {"replyText": "这条现在已经完成了。"},
        ]

        result = same_cluster_live.evaluate_same_cluster_windows(windows, session_id=session_id)

        self.assertTrue(result["firstReplyAllowed"])
        self.assertTrue(result["laterRepliesWellFormed"])
        self.assertTrue(result["laterAtMostOneVisible"])
        self.assertTrue(result["allPassed"])

    def test_evaluate_same_cluster_windows_accepts_synthetic_missing_middle_and_third(self):
        session_id = "ses_samecluster_demo"
        windows = [
            {"replyText": "NO_REPLY"},
            same_cluster_live.synthetic_no_reply_window(2, reason="no_receiver_window_within_timeout"),
            same_cluster_live.synthetic_no_reply_window(3, reason="no_receiver_window_within_timeout"),
        ]

        result = same_cluster_live.evaluate_same_cluster_windows(windows, session_id=session_id)

        self.assertTrue(result["firstReplyAllowed"])
        self.assertTrue(result["laterRepliesWellFormed"])
        self.assertTrue(result["laterAtMostOneVisible"])
        self.assertTrue(result["allPassed"])
        self.assertEqual(result["details"]["syntheticNoDeliveryOccurrences"], [2, 3])

    def test_evaluate_same_cluster_windows_rejects_two_visible_replies_after_first(self):
        session_id = "ses_samecluster_demo"
        windows = [
            {"replyText": "我刚看了下，这条目前还在跑。"},
            {"replyText": "这条现在已经完成了。"},
            {"replyText": "这条有净新增进展，已经完成了新一轮步骤。"},
        ]

        result = same_cluster_live.evaluate_same_cluster_windows(windows, session_id=session_id)

        self.assertTrue(result["firstReplyAllowed"])
        self.assertTrue(result["laterRepliesWellFormed"])
        self.assertFalse(result["laterAtMostOneVisible"])
        self.assertFalse(result["allPassed"])

    def test_evaluate_same_cluster_windows_rejects_explanatory_no_reply_text(self):
        session_id = "ses_samecluster_demo"
        windows = [
            {"replyText": "NO_REPLY"},
            {"replyText": "We should suppress this.\nNO_REPLY"},
        ]

        result = same_cluster_live.evaluate_same_cluster_windows(windows, session_id=session_id)

        self.assertTrue(result["firstReplyAllowed"])
        self.assertFalse(result["laterRepliesWellFormed"])
        self.assertFalse(result["allPassed"])


if __name__ == "__main__":
    unittest.main()
