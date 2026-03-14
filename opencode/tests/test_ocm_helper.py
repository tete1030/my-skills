import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
OCM_PATH = REPO_ROOT / ".local" / "opencode-manager" / "ocm"


class OcmHelperTests(unittest.TestCase):
    def test_continue_refuses_implicit_local_route_defaults(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            defaults_path = tmp_path / "local-defaults.env"
            runtime_dir = tmp_path / "runtime"
            defaults_path.write_text(
                "\n".join(
                    [
                        "OPENCODE_BASE_URL='http://127.0.0.1:4096'",
                        "OPENCODE_WORKSPACE='/tmp/demo-workspace'",
                        "OPENCLAW_SESSION_KEY='agent:main:telegram:group:-100123:topic:4029'",
                        "OPENCLAW_DELIVERY_TARGET='telegram:-100123:topic:4029'",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["OPENCODE_LOCAL_DEFAULTS_FILE"] = str(defaults_path)
            env["OPENCODE_LOCAL_RUNTIME_DIR"] = str(runtime_dir)

            proc = subprocess.run(
                ["bash", str(OCM_PATH), "continue", "ses_demo", "--prompt", "please continue"],
                capture_output=True,
                text=True,
                env=env,
            )

            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("Refusing to use local OPENCLAW_SESSION_KEY/OPENCLAW_DELIVERY_TARGET defaults implicitly", proc.stderr)


if __name__ == "__main__":
    unittest.main()
