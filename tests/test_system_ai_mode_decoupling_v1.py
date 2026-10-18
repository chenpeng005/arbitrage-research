from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROD = ROOT / "scripts" / "production"


class SystemAiModeDecouplingTests(unittest.TestCase):
    def _resolve(self, helper: str, mode_file_env: str, state_line: str, mode_var: str) -> tuple[str, str]:
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "mode.env"
            state.write_text(state_line + "\n", encoding="utf-8")
            env = os.environ.copy()
            # Deliberately inject conflicting legacy/other-system values. A
            # system-specific resolver must ignore them.
            env["NIGHTLY_AI_MODE"] = "CHAT"
            env["RADAR_AI_MODE"] = "CHAT"
            env["CONVERTIBLE_AI_MODE"] = "CHAT"
            env[mode_file_env] = str(state)
            completed = subprocess.run(
                [
                    "bash",
                    "-lc",
                    f'source "{PROD / helper}"; printf "%s|%s" "${{{mode_var}}}" "$AI_EXECUTION_CHANNEL"',
                ],
                cwd=ROOT,
                env=env,
                check=True,
                text=True,
                capture_output=True,
            )
            return tuple(completed.stdout.split("|", 1))  # type: ignore[return-value]

    def test_convertible_resolver_ignores_global_and_radar_modes(self) -> None:
        self.assertEqual(
            self._resolve(
                "convertible-ai-mode-env.sh",
                "CONVERTIBLE_AI_MODE_FILE",
                "CONVERTIBLE_AI_MODE=API",
                "CONVERTIBLE_AI_MODE",
            ),
            ("API", "API"),
        )

    def test_radar_resolver_ignores_global_and_convertible_modes(self) -> None:
        self.assertEqual(
            self._resolve(
                "radar-ai-mode-env.sh",
                "RADAR_AI_MODE_FILE",
                "RADAR_AI_MODE=API",
                "RADAR_AI_MODE",
            ),
            ("API", "API"),
        )

    def test_formal_launchers_do_not_source_legacy_shared_mode(self) -> None:
        radar = (PROD / "radar-daily-run.sh").read_text(encoding="utf-8")
        information = (PROD / "information-runtime-run.sh").read_text(encoding="utf-8")
        pretrade = (PROD / "pretrade-unified-runtime-run.sh").read_text(encoding="utf-8")
        legacy_close = (PROD / "daily-close-runtime-run.sh").read_text(encoding="utf-8")

        self.assertIn("radar-ai-mode-env.sh", radar)
        self.assertIn("RADAR_AI_MODE", radar)
        self.assertNotIn("nightly-ai-mode-env.sh", radar)

        for text in (information, pretrade, legacy_close):
            self.assertIn("convertible-ai-mode-env.sh", text)
            self.assertIn("CONVERTIBLE_AI_MODE", text)
            self.assertNotIn("nightly-ai-mode-env.sh", text)

    def test_switches_own_distinct_state_files(self) -> None:
        convertible = (PROD / "convertible-ai-mode-switch.sh").read_text(encoding="utf-8")
        radar = (PROD / "radar-ai-mode-switch.sh").read_text(encoding="utf-8")
        self.assertIn("convertible-ai-mode.env", convertible)
        self.assertNotIn("radar-ai-mode.env", convertible)
        self.assertIn("radar-ai-mode.env", radar)
        self.assertNotIn("convertible-ai-mode.env", radar)


if __name__ == "__main__":
    unittest.main()
