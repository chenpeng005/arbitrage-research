from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime.web import app as web_app


class WebDeploymentManifestProjectionTest(unittest.TestCase):
    def test_snapshot_hash_and_gate_version_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "deployment_manifest.json"
            path.write_text(
                json.dumps(
                    {
                        "application_commit_sha": "app-sha",
                        "knowledge_commit_sha": "knowledge-sha",
                        "knowledge_snapshot_manifest_sha256": "snapshot-hash",
                        "deployment_gate_version": "runtime-deployment-gate-v1",
                        "deployed_at": "2026-09-28T00:00:00+00:00",
                        "deployment_method": "test",
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(web_app, "DEPLOYMENT_MANIFEST_PATH", path):
                loaded = web_app.load_deployment_manifest()

            self.assertEqual(
                loaded["knowledge_snapshot_manifest_sha256"],
                "snapshot-hash",
            )
            self.assertEqual(
                loaded["deployment_gate_version"],
                "runtime-deployment-gate-v1",
            )


if __name__ == "__main__":
    unittest.main()
