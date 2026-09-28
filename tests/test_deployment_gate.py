from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from runtime.deployment_gate import (
    DeploymentGateError,
    REQUIRED_CANONICAL_PATHS,
    validate_deployment_gate,
)


class DeploymentGateTest(unittest.TestCase):
    def _build_valid_release(self, root: Path) -> tuple[Path, dict]:
        data_root = root / "runtime_data"
        knowledge_sha = "a" * 40
        snapshot_dir = data_root / "knowledge_snapshots" / knowledge_sha
        files_dir = snapshot_dir / "files"
        files_dir.mkdir(parents=True)

        entries = []
        for index, canonical_path in enumerate(REQUIRED_CANONICAL_PATHS):
            local_file = f"files/canonical_{index}.md"
            path = snapshot_dir / local_file
            payload = f"canonical {index}\n".encode("utf-8")
            path.write_bytes(payload)
            entries.append(
                {
                    "canonical_path": canonical_path,
                    "local_file": local_file,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "byte_size": len(payload),
                }
            )

        snapshot_manifest = {
            "knowledge_commit_sha": knowledge_sha,
            "source_repository": "example/knowledge",
            "files": entries,
        }
        snapshot_manifest_path = snapshot_dir / "manifest.json"
        snapshot_manifest_path.write_text(
            json.dumps(snapshot_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        manifest_hash = hashlib.sha256(snapshot_manifest_path.read_bytes()).hexdigest()
        deployment = {
            "application_commit_sha": "b" * 40,
            "knowledge_commit_sha": knowledge_sha,
            "knowledge_snapshot_manifest_sha256": manifest_hash,
        }
        (data_root / "deployment_manifest.json").write_text(
            json.dumps(deployment, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return data_root, deployment

    def test_valid_release_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root, deployment = self._build_valid_release(Path(tmp))
            result = validate_deployment_gate(
                data_root=data_root,
                deployment=deployment,
                write_audit=False,
            )
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(
                result["required_canonical_count"],
                len(REQUIRED_CANONICAL_PATHS),
            )

    def test_missing_snapshot_fails_before_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root, deployment = self._build_valid_release(Path(tmp))
            snapshot_dir = (
                data_root
                / "knowledge_snapshots"
                / deployment["knowledge_commit_sha"]
            )
            for child in sorted(snapshot_dir.rglob("*"), reverse=True):
                if child.is_file():
                    child.unlink()
                elif child.is_dir():
                    child.rmdir()
            snapshot_dir.rmdir()

            with self.assertRaises(DeploymentGateError):
                validate_deployment_gate(
                    data_root=data_root,
                    deployment=deployment,
                    write_audit=False,
                )

    def test_manifest_hash_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root, deployment = self._build_valid_release(Path(tmp))
            deployment["knowledge_snapshot_manifest_sha256"] = "0" * 64
            with self.assertRaises(DeploymentGateError):
                validate_deployment_gate(
                    data_root=data_root,
                    deployment=deployment,
                    write_audit=False,
                )


if __name__ == "__main__":
    unittest.main()
