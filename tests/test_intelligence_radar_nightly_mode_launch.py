from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "production" / "nightly-ai-mode-env.sh"
RADAR = ROOT / "scripts" / "production" / "radar-daily-run.sh"
CONVERTIBLE = ROOT / "scripts" / "production" / "pretrade-unified-runtime-run.sh"


class NightlyModeLaunchContractTest(unittest.TestCase):
    def _resolve(self, mode: str) -> list[str]:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as fh:
            fh.write(f"NIGHTLY_AI_MODE={mode}\n")
            if mode == "CHAT":
                fh.write("CHAT_PROVIDER_ROOT=/tmp/nightly-chat-bridge\n")
            mode_file = fh.name
        try:
            env = dict(os.environ)
            env["NIGHTLY_AI_MODE_FILE"] = mode_file
            proc = subprocess.run(
                [
                    "bash",
                    "-c",
                    f'source "{HELPER}"; printf "%s|%s|%s" "$NIGHTLY_AI_MODE" "$AI_EXECUTION_CHANNEL" "${{CHAT_PROVIDER_ROOT:-}}"',
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            return proc.stdout.split("|")
        finally:
            Path(mode_file).unlink(missing_ok=True)

    def test_shared_mode_file_selects_chat_transport(self) -> None:
        self.assertEqual(
            self._resolve("CHAT"),
            ["CHAT", "CHAT", "/tmp/nightly-chat-bridge"],
        )

    def test_shared_mode_file_selects_api_transport(self) -> None:
        mode, channel, _ = self._resolve("API")
        self.assertEqual((mode, channel), ("API", "API"))

    def test_radar_daily_has_explicit_chat_runner_branch(self) -> None:
        text = RADAR.read_text(encoding="utf-8")
        self.assertIn('source "$ROOT/scripts/production/nightly-ai-mode-env.sh"', text)
        self.assertIn('runtime.intelligence_radar.chat_runner', text)
        self.assertIn('runtime.intelligence_radar.run_all --run-mode LIVE', text)

    def test_convertible_daily_maps_chat_to_interactive_chat(self) -> None:
        text = CONVERTIBLE.read_text(encoding="utf-8")
        self.assertIn('source "$ROOT/scripts/production/nightly-ai-mode-env.sh"', text)
        self.assertIn('AI_EXECUTION_MODE="INTERACTIVE_CHAT"', text)
        self.assertIn('AI_EXECUTION_MODE="AUTO_API"', text)


if __name__ == "__main__":
    unittest.main()
