from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from runtime.lof.deployment_gate import LOF_REQUIRED_CANONICAL_PATHS
from runtime.lof.knowledge_snapshot import build_lof_knowledge_snapshot


class LofKnowledgeSnapshotTest(unittest.TestCase):
    def _make_knowledge_tree(self, root: Path) -> None:
        for i, canonical in enumerate(LOF_REQUIRED_CANONICAL_PATHS):
            path = root / canonical
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                f"canonical-{i}\n",
                encoding="utf-8",
            )

    def test_build_snapshot_freezes_required_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            knowledge = base / "knowledge"
            knowledge.mkdir()
            self._make_knowledge_tree(knowledge)
            data_root = base / "runtime_data"
            sha = "a" * 40

            manifest_path, manifest_hash = build_lof_knowledge_snapshot(
                knowledge_root=knowledge,
                data_root=data_root,
                knowledge_commit_sha=sha,
                verify_git_head=False,
            )

            self.assertTrue(manifest_path.exists())
            self.assertEqual(
                hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                manifest_hash,
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["knowledge_commit_sha"], sha)
            self.assertEqual(
                len(manifest["files"]),
                len(LOF_REQUIRED_CANONICAL_PATHS),
            )
            for item in manifest["files"]:
                frozen = manifest_path.parent / item["local_file"]
                self.assertTrue(frozen.exists())
                self.assertEqual(
                    hashlib.sha256(frozen.read_bytes()).hexdigest(),
                    item["sha256"],
                )
                self.assertEqual(frozen.stat().st_size, item["byte_size"])

    def test_missing_required_canonical_fails_without_partial_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            knowledge = base / "knowledge"
            knowledge.mkdir()
            self._make_knowledge_tree(knowledge)
            missing = knowledge / LOF_REQUIRED_CANONICAL_PATHS[-1]
            missing.unlink()
            data_root = base / "runtime_data"
            sha = "b" * 40

            with self.assertRaises(FileNotFoundError):
                build_lof_knowledge_snapshot(
                    knowledge_root=knowledge,
                    data_root=data_root,
                    knowledge_commit_sha=sha,
                    verify_git_head=False,
                )

            self.assertFalse(
                (data_root / "knowledge_snapshots" / sha).exists()
            )

    def test_existing_snapshot_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            knowledge = base / "knowledge"
            knowledge.mkdir()
            self._make_knowledge_tree(knowledge)
            data_root = base / "runtime_data"
            sha = "c" * 40

            build_lof_knowledge_snapshot(
                knowledge_root=knowledge,
                data_root=data_root,
                knowledge_commit_sha=sha,
                verify_git_head=False,
            )

            with self.assertRaises(FileExistsError):
                build_lof_knowledge_snapshot(
                    knowledge_root=knowledge,
                    data_root=data_root,
                    knowledge_commit_sha=sha,
                    verify_git_head=False,
                )


if __name__ == "__main__":
    unittest.main()
