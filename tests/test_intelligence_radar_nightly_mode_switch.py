from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from runtime.opportunity.incremental_storage import initialize_schema


ROOT = Path(__file__).resolve().parents[1]
SWITCH = ROOT / "scripts" / "production" / "nightly-ai-mode-switch.sh"


class NightlyModeSwitchTest(unittest.TestCase):
    def _run(self, mode: str) -> tuple[str, str]:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            state_dir = tmp_root / "state"
            data_root = tmp_root / "runtime_data"
            state_dir.mkdir(parents=True)
            data_root.mkdir(parents=True)
            initialize_schema(data_root / "state" / "incremental_runtime.sqlite")
            (data_root / "deployment_manifest.json").write_text(
                json.dumps(
                    {
                        "application_commit_sha": "test-app",
                        "knowledge_commit_sha": "test-knowledge",
                        "release_profile": "convertible",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            env = dict(os.environ)
            env.update(
                {
                    "NIGHTLY_AI_MODE_STATE_DIR": str(state_dir),
                    "ARBITRAGE_RUNTIME_ROOT": str(ROOT),
                    "NIGHTLY_AI_RECOVERY_DATA_ROOT": str(data_root),
                    "NIGHTLY_AI_RECOVERY_PY": sys.executable,
                    "PYTHONPATH": str(ROOT),
                }
            )
            proc = subprocess.run(
                ["bash", str(SWITCH), mode],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            state = (state_dir / "nightly-ai-mode.env").read_text(encoding="utf-8")
            return state, proc.stdout

    def test_chat_switch_writes_state_without_recovery(self) -> None:
        text, stdout = self._run("chat")
        self.assertIn("NIGHTLY_AI_MODE=CHAT", text)
        self.assertIn("CHAT_PROVIDER_ROOT=", text)
        self.assertNotIn("CHAT_TO_API_RECOVERY", stdout)

    def test_api_switch_runs_recovery_gate_then_writes_state(self) -> None:
        text, stdout = self._run("api")
        self.assertIn("NIGHTLY_AI_MODE=API", text)
        self.assertIn(
            "CHAT_TO_API_RECOVERY_DRY_RUN semantic=0 path=0 blockers=0",
            stdout,
        )
        self.assertIn("CHAT_TO_API_RECOVERY=NOOP", stdout)

    def test_switch_script_has_no_cron_mutation(self) -> None:
        text = SWITCH.read_text(encoding="utf-8")
        self.assertNotIn("crontab", text)
        self.assertNotIn("/var/spool/cron", text)


if __name__ == "__main__":
    unittest.main()
