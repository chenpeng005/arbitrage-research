from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROD = ROOT / "scripts" / "production"
RADAR_HELPER = PROD / "radar-ai-mode-env.sh"
CONVERTIBLE_HELPER = PROD / "convertible-ai-mode-env.sh"
RADAR = PROD / "radar-daily-run.sh"
CONVERTIBLE = PROD / "pretrade-unified-runtime-run.sh"


class PerSystemModeLaunchContractTest(unittest.TestCase):
    def _resolve(
        self,
        *,
        helper: Path,
        file_env: str,
        state_key: str,
        mode: str,
        conflicting_env: dict[str, str],
    ) -> list[str]:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as fh:
            fh.write(f"{state_key}={mode}\n")
            if mode == "CHAT":
                fh.write("CHAT_PROVIDER_ROOT=/tmp/nightly-chat-bridge\n")
            mode_file = fh.name
        try:
            env = dict(os.environ)
            env.update(conflicting_env)
            env[file_env] = mode_file
            proc = subprocess.run(
                [
                    "bash",
                    "-c",
                    f'source "{helper}"; printf "%s|%s|%s" "${{{state_key}}}" "$AI_EXECUTION_CHANNEL" "${{CHAT_PROVIDER_ROOT:-}}"',
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

    def test_radar_mode_file_selects_transport_independently(self) -> None:
        chat = self._resolve(
            helper=RADAR_HELPER,
            file_env="RADAR_AI_MODE_FILE",
            state_key="RADAR_AI_MODE",
            mode="CHAT",
            conflicting_env={"NIGHTLY_AI_MODE": "API", "CONVERTIBLE_AI_MODE": "API"},
        )
        self.assertEqual(chat, ["CHAT", "CHAT", "/tmp/nightly-chat-bridge"])

        api = self._resolve(
            helper=RADAR_HELPER,
            file_env="RADAR_AI_MODE_FILE",
            state_key="RADAR_AI_MODE",
            mode="API",
            conflicting_env={"NIGHTLY_AI_MODE": "CHAT", "CONVERTIBLE_AI_MODE": "CHAT"},
        )
        self.assertEqual(api[:2], ["API", "API"])

    def test_convertible_mode_file_selects_transport_independently(self) -> None:
        chat = self._resolve(
            helper=CONVERTIBLE_HELPER,
            file_env="CONVERTIBLE_AI_MODE_FILE",
            state_key="CONVERTIBLE_AI_MODE",
            mode="CHAT",
            conflicting_env={"NIGHTLY_AI_MODE": "API", "RADAR_AI_MODE": "API"},
        )
        self.assertEqual(chat, ["CHAT", "CHAT", "/tmp/nightly-chat-bridge"])

        api = self._resolve(
            helper=CONVERTIBLE_HELPER,
            file_env="CONVERTIBLE_AI_MODE_FILE",
            state_key="CONVERTIBLE_AI_MODE",
            mode="API",
            conflicting_env={"NIGHTLY_AI_MODE": "CHAT", "RADAR_AI_MODE": "CHAT"},
        )
        self.assertEqual(api[:2], ["API", "API"])

    def test_radar_daily_has_explicit_radar_owned_chat_branch(self) -> None:
        text = RADAR.read_text(encoding="utf-8")
        self.assertIn('source "$ROOT/scripts/production/radar-ai-mode-env.sh"', text)
        self.assertIn('if [[ "$RADAR_AI_MODE" == "CHAT" ]]', text)
        self.assertNotIn('nightly-ai-mode-env.sh', text)
        self.assertIn('runtime.intelligence_radar.chat_runner', text)
        self.assertIn('runtime.intelligence_radar.run_all --run-mode LIVE', text)

    def test_convertible_daily_maps_its_own_chat_mode(self) -> None:
        text = CONVERTIBLE.read_text(encoding="utf-8")
        self.assertIn('source "$ROOT/scripts/production/convertible-ai-mode-env.sh"', text)
        self.assertIn('if [[ "$CONVERTIBLE_AI_MODE" == "CHAT" ]]', text)
        self.assertNotIn('nightly-ai-mode-env.sh', text)
        self.assertIn('AI_EXECUTION_MODE="INTERACTIVE_CHAT"', text)
        self.assertIn('AI_EXECUTION_MODE="AUTO_API"', text)


if __name__ == "__main__":
    unittest.main()
