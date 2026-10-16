from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SWITCH = ROOT / "scripts" / "production" / "nightly-ai-mode-switch.sh"


class NightlyModeSwitchTest(unittest.TestCase):
    def _run(self, mode: str) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ)
            env["NIGHTLY_AI_MODE_STATE_DIR"] = tmp
            subprocess.run(
                ["bash", str(SWITCH), mode],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            return (Path(tmp) / "nightly-ai-mode.env").read_text(encoding="utf-8")

    def test_chat_switch_writes_state_only_contract(self) -> None:
        text = self._run("chat")
        self.assertIn("NIGHTLY_AI_MODE=CHAT", text)
        self.assertIn("CHAT_PROVIDER_ROOT=", text)

    def test_api_switch_writes_state_only_contract(self) -> None:
        text = self._run("api")
        self.assertIn("NIGHTLY_AI_MODE=API", text)

    def test_switch_script_has_no_cron_mutation(self) -> None:
        text = SWITCH.read_text(encoding="utf-8")
        self.assertNotIn("crontab", text)
        self.assertNotIn("/var/spool/cron", text)


if __name__ == "__main__":
    unittest.main()
