from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .deployment_gate import LOF_REQUIRED_CANONICAL_PATHS


SOURCE_REPOSITORY = "chenpeng005/obsidian-knowledge-base"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_head(root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def build_lof_knowledge_snapshot(
    *,
    knowledge_root: Path,
    data_root: Path,
    knowledge_commit_sha: str,
    source_repository: str = SOURCE_REPOSITORY,
    verify_git_head: bool = True,
) -> tuple[Path, str]:
    """Freeze LOF release Canonical at one exact Knowledge commit.

    The caller must provide a local checkout of the Knowledge repository.
    Production/release usage verifies that checkout HEAD equals the requested
    knowledge_commit_sha before copying any canonical file.
    """
    knowledge_root = knowledge_root.resolve()
    data_root = data_root.resolve()
    knowledge_commit_sha = knowledge_commit_sha.strip()

    if not knowledge_commit_sha:
        raise ValueError("knowledge_commit_sha is required")

    if verify_git_head:
        actual_head = _git_head(knowledge_root)
        if actual_head != knowledge_commit_sha:
            raise ValueError(
                "knowledge checkout HEAD does not match requested commit: "
                f"{actual_head} != {knowledge_commit_sha}"
            )

    target_dir = data_root / "knowledge_snapshots" / knowledge_commit_sha
    if target_dir.exists():
        raise FileExistsError(
            f"knowledge snapshot already exists: {target_dir}"
        )

    snapshot_parent = target_dir.parent
    snapshot_parent.mkdir(parents=True, exist_ok=True)

    temp_dir = Path(
        tempfile.mkdtemp(
            prefix=f".{knowledge_commit_sha}.",
            dir=str(snapshot_parent),
        )
    )

    try:
        files_dir = temp_dir / "files"
        files_dir.mkdir(parents=True)

        entries: list[dict[str, Any]] = []
        for canonical_path in LOF_REQUIRED_CANONICAL_PATHS:
            source_path = (knowledge_root / canonical_path).resolve()
            try:
                source_path.relative_to(knowledge_root)
            except ValueError as exc:
                raise ValueError(
                    f"canonical path escapes Knowledge root: {canonical_path}"
                ) from exc

            if not source_path.is_file():
                raise FileNotFoundError(
                    f"required LOF canonical missing: {canonical_path}"
                )

            relative_local = Path("files") / canonical_path
            destination = temp_dir / relative_local
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, destination)

            entries.append(
                {
                    "canonical_path": canonical_path,
                    "local_file": relative_local.as_posix(),
                    "sha256": _sha256(destination),
                    "byte_size": destination.stat().st_size,
                }
            )

        manifest = {
            "knowledge_commit_sha": knowledge_commit_sha,
            "source_repository": source_repository,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "release_profile": "lof-opportunity-runtime-v1",
            "files": entries,
        }
        manifest_path = temp_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        manifest_hash = _sha256(manifest_path)

        temp_dir.replace(target_dir)
        return target_dir / "manifest.json", manifest_hash
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build exact LOF Knowledge Snapshot from a pinned checkout."
    )
    parser.add_argument("--knowledge-root", required=True)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--knowledge-commit-sha", required=True)
    parser.add_argument(
        "--source-repository",
        default=SOURCE_REPOSITORY,
    )
    args = parser.parse_args(argv)

    manifest_path, manifest_hash = build_lof_knowledge_snapshot(
        knowledge_root=Path(args.knowledge_root),
        data_root=Path(args.data_root),
        knowledge_commit_sha=args.knowledge_commit_sha,
        source_repository=args.source_repository,
        verify_git_head=True,
    )
    print(
        json.dumps(
            {
                "status": "PASS",
                "manifest_path": str(manifest_path),
                "knowledge_snapshot_manifest_sha256": manifest_hash,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
