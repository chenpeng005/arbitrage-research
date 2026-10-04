from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


HASH_KEYS = {
    "pdf_artifact": "pdf_sha256",
    "text_artifact": "text_sha256",
}


def _is_sha256(value: Any) -> bool:
    text = str(value or "").lower()
    return len(text) == 64 and all(ch in "0123456789abcdef" for ch in text)


def _artifact_path(job_dir: Path, ref: str) -> Path | None:
    raw = Path(str(ref or ""))
    path = raw if raw.is_absolute() else job_dir / raw
    try:
        resolved = path.resolve(strict=True)
        root = job_dir.resolve(strict=True)
    except FileNotFoundError:
        return None
    if not resolved.is_relative_to(root) or not resolved.is_file():
        return None
    return resolved


def compact_completed_job_evidence(
    *,
    job_dir: Path,
    data_root: Path,
) -> dict[str, Any]:
    """Deduplicate immutable evidence artifacts for a completed PASS AI job.

    The business-facing artifact paths stay unchanged. Identical evidence files
    share disk blocks through hard links to a content-addressed blob store.
    Any unsupported filesystem or linking error is fail-open for storage only:
    the original job artifact remains untouched.
    """
    metadata_path = job_dir / "ai_job_metadata.json"
    if not metadata_path.exists():
        return {"status": "SKIPPED", "reason": "NO_JOB_METADATA"}

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("status") != "PASS":
        return {"status": "SKIPPED", "reason": "JOB_NOT_PASS"}

    evidence_dir = job_dir / "evidence"
    if not evidence_dir.exists():
        return {
            "status": "PASS",
            "blob_links_created": 0,
            "deduplicated_files": 0,
            "estimated_bytes_saved": 0,
        }

    blob_root = data_root / "evidence_blobs" / "sha256"
    blob_links_created = 0
    deduplicated_files = 0
    estimated_bytes_saved = 0
    skipped_files = 0
    seen_paths: set[Path] = set()

    for meta_path in sorted(evidence_dir.glob("*.json")):
        try:
            evidence_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            skipped_files += 1
            continue

        for artifact_key, hash_key in HASH_KEYS.items():
            artifact_ref = evidence_meta.get(artifact_key)
            sha256 = str(evidence_meta.get(hash_key) or "").lower()
            if not artifact_ref or not _is_sha256(sha256):
                continue

            artifact = _artifact_path(job_dir, str(artifact_ref))
            if artifact is None or artifact in seen_paths:
                continue
            seen_paths.add(artifact)

            suffix = artifact.suffix.lower()
            blob = blob_root / sha256[:2] / f"{sha256}{suffix}"
            blob.parent.mkdir(parents=True, exist_ok=True)

            if not blob.exists():
                try:
                    os.link(artifact, blob)
                    blob_links_created += 1
                except FileExistsError:
                    pass
                except OSError:
                    skipped_files += 1
                    continue

            try:
                artifact_stat = artifact.stat()
                blob_stat = blob.stat()
            except FileNotFoundError:
                skipped_files += 1
                continue

            if artifact_stat.st_dev != blob_stat.st_dev:
                skipped_files += 1
                continue
            if artifact_stat.st_size != blob_stat.st_size:
                skipped_files += 1
                continue
            if os.path.samefile(artifact, blob):
                continue

            tmp_link = artifact.with_name(
                f".{artifact.name}.dedupe-{os.getpid()}"
            )
            try:
                if tmp_link.exists():
                    tmp_link.unlink()
                os.link(blob, tmp_link)
                os.replace(tmp_link, artifact)
                deduplicated_files += 1
                estimated_bytes_saved += artifact_stat.st_size
            except OSError:
                skipped_files += 1
                try:
                    if tmp_link.exists():
                        tmp_link.unlink()
                except OSError:
                    pass

    return {
        "status": "PASS",
        "blob_links_created": blob_links_created,
        "deduplicated_files": deduplicated_files,
        "estimated_bytes_saved": estimated_bytes_saved,
        "skipped_files": skipped_files,
        "blob_root": str(blob_root),
    }
