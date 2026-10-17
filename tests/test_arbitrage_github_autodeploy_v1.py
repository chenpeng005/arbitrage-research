from __future__ import annotations

import json
import os
import pathlib
import stat
import subprocess
import tempfile
import time
import unittest


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "production" / "arbitrage-github-autodeploy.sh"


class ArbitrageGithubAutodeployContractTest(unittest.TestCase):
    def _write_executable(self, path: pathlib.Path, text: str) -> None:
        path.write_text(text, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def _fixture(self, sha: str):
        td = tempfile.TemporaryDirectory()
        root = pathlib.Path(td.name)
        live = root / "live"
        state = root / "state"
        staging = root / "staging"
        bindir = root / "bin"
        (live / "runtime_data").mkdir(parents=True)
        state.mkdir()
        staging.mkdir()
        bindir.mkdir()
        (live / "runtime_data" / "deployment_manifest.json").write_text(
            json.dumps({"application_commit_sha": sha}) + "\n",
            encoding="utf-8",
        )
        curl_marker = root / "curl-called"
        self._write_executable(
            bindir / "curl",
            "#!/usr/bin/env bash\nprintf called > '" + str(curl_marker) + "'\nexit 99\n",
        )
        env = os.environ.copy()
        env.update({
            "PATH": str(bindir) + os.pathsep + env["PATH"],
            "LIVE_ROOT": str(live),
            "STATE_ROOT": str(state),
            "STAGING_ROOT": str(staging),
            "LOCK_DIR": str(root / "lock"),
        })
        return td, root, state, curl_marker, env

    def test_cached_head_skips_rest_between_polls(self):
        sha = "a" * 40
        td, root, state, curl_marker, env = self._fixture(sha)
        with td:
            (state / "last_remote_sha").write_text(sha + "\n", encoding="utf-8")
            (state / "head-next-check.epoch").write_text(
                str(int(time.time()) + 3600) + "\n", encoding="utf-8"
            )
            proc = subprocess.run(
                ["bash", str(SCRIPT)],
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=10,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout)
            self.assertFalse(curl_marker.exists(), proc.stdout)

    def test_contract_uses_throttled_rest_and_rate_limit_backoff(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("$REPO_API/branches/main", text)
        self.assertIn("HEAD_POLL_SECONDS", text)
        self.assertIn("head-next-check.epoch", text)
        self.assertIn("last_remote_sha", text)
        self.assertNotIn("git ls-remote", text)
        self.assertIn("x-ratelimit-remaining", text)
        self.assertIn("x-ratelimit-reset", text)
        self.assertIn("retry-after", text)


if __name__ == "__main__":
    unittest.main()
