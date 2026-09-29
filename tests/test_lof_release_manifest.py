from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from runtime.lof.deployment_gate import LOF_RELEASE_PROFILE
from runtime.lof.release_manifest import (
    prepare_lof_deployment_candidate,
    promote_lof_deployment_manifest,
)


class LofReleaseManifestTest(unittest.TestCase):
    def _snapshot(
        self,
        root: Path,
        knowledge_sha: str,
    ) -> Path:
        snapshot_dir = (
            root / "knowledge_snapshots" / knowledge_sha
        )
        snapshot_dir.mkdir(parents=True)
        manifest = {
            "knowledge_commit_sha": knowledge_sha,
            "release_profile": LOF_RELEASE_PROFILE,
            "source_repository": "chenpeng005/obsidian-knowledge-base",
            "files": [
                {
                    "canonical_path": "x",
                    "local_file": "files/x.md",
                    "sha256": "0" * 64,
                    "byte_size": 1,
                }
            ],
        }
        path = snapshot_dir / "manifest.json"
        path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def test_prepare_binds_snapshot_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_sha = "a" * 40
            knowledge_sha = "b" * 40
            snapshot = self._snapshot(root, knowledge_sha)

            path, payload = prepare_lof_deployment_candidate(
                data_root=root,
                application_commit_sha=app_sha,
                knowledge_commit_sha=knowledge_sha,
            )

            self.assertTrue(path.exists())
            self.assertEqual(
                payload["knowledge_snapshot_manifest_sha256"],
                hashlib.sha256(snapshot.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                payload["release_profile"],
                LOF_RELEASE_PROFILE,
            )

    def test_promote_requires_matching_preflight_app_sha(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_sha = "a" * 40
            knowledge_sha = "b" * 40
            self._snapshot(root, knowledge_sha)
            prepare_lof_deployment_candidate(
                data_root=root,
                application_commit_sha=app_sha,
                knowledge_commit_sha=knowledge_sha,
            )

            preflight = root / "preflight.json"
            preflight.write_text(
                json.dumps(
                    {
                        "preflight_version": "lof-production-source-preflight-v1",
                        "status": "PASS",
                        "checked_at": "2026-09-29T14:00:00+08:00",
                        "application_commit_sha": "c" * 40,
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                promote_lof_deployment_manifest(
                    data_root=root,
                    preflight_result_path=preflight,
                    deployment_method="test",
                )

    def test_promote_pass_creates_formal_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_sha = "a" * 40
            knowledge_sha = "b" * 40
            self._snapshot(root, knowledge_sha)
            prepare_lof_deployment_candidate(
                data_root=root,
                application_commit_sha=app_sha,
                knowledge_commit_sha=knowledge_sha,
            )

            preflight = root / "preflight.json"
            preflight.write_text(
                json.dumps(
                    {
                        "preflight_version": "lof-production-source-preflight-v1",
                        "status": "WARN",
                        "checked_at": "2026-09-29T14:00:00+08:00",
                        "application_commit_sha": app_sha,
                    }
                ),
                encoding="utf-8",
            )

            path, payload = promote_lof_deployment_manifest(
                data_root=root,
                preflight_result_path=preflight,
                deployment_method="test-release",
            )

            self.assertEqual(path, root / "deployment_manifest.json")
            self.assertEqual(payload["source_preflight_status"], "WARN")
            self.assertEqual(payload["application_commit_sha"], app_sha)
            self.assertEqual(payload["deployment_method"], "test-release")

    def test_failed_preflight_cannot_promote(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_sha = "a" * 40
            knowledge_sha = "b" * 40
            self._snapshot(root, knowledge_sha)
            prepare_lof_deployment_candidate(
                data_root=root,
                application_commit_sha=app_sha,
                knowledge_commit_sha=knowledge_sha,
            )

            preflight = root / "preflight.json"
            preflight.write_text(
                json.dumps(
                    {
                        "preflight_version": "lof-production-source-preflight-v1",
                        "status": "FAIL",
                        "checked_at": "2026-09-29T14:00:00+08:00",
                        "application_commit_sha": app_sha,
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                promote_lof_deployment_manifest(
                    data_root=root,
                    preflight_result_path=preflight,
                    deployment_method="test",
                )


if __name__ == "__main__":
    unittest.main()
