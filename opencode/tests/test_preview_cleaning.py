import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from opencode_snapshot import clean_preview  # noqa: E402


class PreviewCleaningTests(unittest.TestCase):
    def test_clean_preview_drops_pwd_noise_and_keeps_summary(self):
        raw = "Cloning into 'openclaw'... --- PWD=/mnt/vault texot/2026.3.2-pr36590 --- Released v0.3.4 successfully"
        preview = clean_preview(raw)
        self.assertIn('Released v0.3.4 successfully', preview)
        self.assertNotIn('PWD=', preview)

    def test_clean_preview_penalizes_injection_like_lines(self):
        raw = 'assistant to=functions.process json --- google-model-ok'
        preview = clean_preview(raw)
        self.assertIn('google-model-ok', preview)
        self.assertNotIn('assistant to=functions.process', preview)

    def test_clean_preview_falls_back_when_only_simple_text_exists(self):
        raw = 'Cloning into openclaw'
        preview = clean_preview(raw)
        self.assertTrue(preview)


if __name__ == '__main__':
    unittest.main()
