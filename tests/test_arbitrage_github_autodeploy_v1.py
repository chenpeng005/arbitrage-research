from __future__ import annotations

import json
import os
import pathlib
import stat
import subprocess
import tempfile
import unittest


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "production" / "arbitrage-github-autodeploy.sh"


class ArbitrageGithubAutodeployContractTest(unittest.TestCase):
    def _write_executable(self, path: pathlib.Path, text: str) -> None:
        path.write_text(text, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def _fixture(self, remote_sha: str, current_sha: str):
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
            json.dumps({"application_commit_sha": current_sha}) + "\n",
            encoding="utf-8",
        )
        self._write_executable(
            bindir / "git",
            "#!/usr/bin/env bash\nprintf '%s\\trefs/heads/main\\n' '" + remote_sha + "'\n",
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
            "REPO_GIT_URL": "https://example.invalid/repo.git",
            "VALIDATION_RETRY_SECONDS": "300",
        })
        return td, root, state, curl_marker, env

    def test_head_discovery_avoids_rest_api_on_no_change(self):
        sha = "a" * 40
        td, root, state, curl_marker, env = self._fixture(sha, sha)
        with td:
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

    def test_validation_backoff_avoids_repeated_api_calls(self):
        remote = "b" * 40
        current = "a" * 40
        td, root, state, curl_marker, env = self._fixture(remote, current)
        with td:
            retry = state / f"validation-retry-{remote}.epoch"
            retry.write_text("4102444800\n", encoding="utf-8")
            proc = subprocess.run(
                ["bash", str(SCRIPT)],
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=10,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("backoff active", proc.stdout)
            self.assertFalse(curl_marker.exists(), proc.stdout)

    def test_contract_has_no_branch_rest_poll(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("git ls-remote", text)
        self.assertNotIn("$REPO_API/branches/main", text)
        self.assertIn("x-ratelimit-remaining", text)
        self.assertIn("validation-retry-", text)


if __name__ == "__main__":
    unittest.main()
