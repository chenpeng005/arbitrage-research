from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from runtime.deployment_gate import DeploymentGateError
from runtime.lof.deployment_gate import (
    LOF_RELEASE_PROFILE,
    LOF_REQUIRED_CANONICAL_PATHS,
    validate_lof_deployment_gate,
)


class LofDeploymentGateTest(unittest.TestCase):
    def _build_release(
        self,
        root: Path,
        *,
        omit_path: str | None = None,
        release_profile: str = LOF_RELEASE_PROFILE,
    ) -> tuple[Path, dict]:
        data_root = root / "lof_runtime_data"
        knowledge_sha = "1" * 40
        snapshot_dir = data_root / "knowledge_snapshots" / knowledge_sha
        files_dir = snapshot_dir / "files"
        files_dir.mkdir(parents=True)

        entries = []
        file_index = 0
        for canonical_path in LOF_REQUIRED_CANONICAL_PATHS:
            if canonical_path == omit_path:
                continue
            local_file = f"files/lof_{file_index}.md"
            file_index += 1
            payload = (canonical_path + "\n").encode("utf-8")
            path = snapshot_dir / local_file
            path.write_bytes(payload)
            entries.append(
                {
                    "canonical_path": canonical_path,
                    "local_file": local_file,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "byte_size": len(payload),
                }
            )

        manifest = {
            "knowledge_commit_sha": knowledge_sha,
            "source_repository": "chenpeng005/obsidian-knowledge-base",
            "files": entries,
        }
        manifest_path = snapshot_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()

        deployment = {
            "release_profile": release_profile,
            "application_commit_sha": "2" * 40,
            "knowledge_commit_sha": knowledge_sha,
            "knowledge_snapshot_manifest_sha256": manifest_hash,
        }
        (data_root / "deployment_manifest.json").write_text(
            json.dumps(deployment, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return data_root, deployment

    def test_valid_lof_release_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root, deployment = self._build_release(Path(tmp))
            result = validate_lof_deployment_gate(
                data_root=data_root,
                deployment=deployment,
                write_audit=False,
            )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["release_profile"], LOF_RELEASE_PROFILE)
        self.assertEqual(
            result["required_canonical_count"],
            len(LOF_REQUIRED_CANONICAL_PATHS),
        )

    def test_missing_lof_canonical_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = LOF_REQUIRED_CANONICAL_PATHS[-1]
            data_root, deployment = self._build_release(
                Path(tmp),
                omit_path=missing,
            )
            with self.assertRaises(DeploymentGateError):
                validate_lof_deployment_gate(
                    data_root=data_root,
                    deployment=deployment,
                    write_audit=False,
                )

    def test_wrong_release_profile_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root, deployment = self._build_release(
                Path(tmp),
                release_profile="convertible-bond-runtime",
            )
            with self.assertRaises(DeploymentGateError):
                validate_lof_deployment_gate(
                    data_root=data_root,
                    deployment=deployment,
                    write_audit=False,
                )


if __name__ == "__main__":
    unittest.main()
