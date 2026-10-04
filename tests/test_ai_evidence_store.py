from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from runtime.ai_runtime.evidence_store import compact_completed_job_evidence


def _write_job(
    data_root: Path,
    job_id: str,
    *,
    status: str = "PASS",
    pdf: bytes = b"%PDF-same-evidence",
    text: str = "same evidence text",
) -> Path:
    job_dir = data_root / "ai_jobs" / job_id
    evidence_dir = job_dir / "evidence"
    evidence_dir.mkdir(parents=True)

    pdf_path = evidence_dir / "AN_TEST.pdf"
    txt_path = evidence_dir / "AN_TEST.txt"
    pdf_path.write_bytes(pdf)
    txt_path.write_text(text, encoding="utf-8")

    meta = {
        "evidence_id": "AN_TEST",
        "pdf_artifact": "evidence/AN_TEST.pdf",
        "text_artifact": "evidence/AN_TEST.txt",
        "pdf_sha256": hashlib.sha256(pdf).hexdigest(),
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }
    (evidence_dir / "AN_TEST.json").write_text(
        json.dumps(meta),
        encoding="utf-8",
    )
    (job_dir / "ai_job_metadata.json").write_text(
        json.dumps({"status": status}),
        encoding="utf-8",
    )
    return job_dir


def test_pass_jobs_share_content_addressed_evidence(tmp_path: Path) -> None:
    data_root = tmp_path / "runtime_data"
    job1 = _write_job(data_root, "job1")
    first = compact_completed_job_evidence(
        job_dir=job1,
        data_root=data_root,
    )

    job2 = _write_job(data_root, "job2")
    before_pdf_inode = (job2 / "evidence" / "AN_TEST.pdf").stat().st_ino
    before_txt_inode = (job2 / "evidence" / "AN_TEST.txt").stat().st_ino
    second = compact_completed_job_evidence(
        job_dir=job2,
        data_root=data_root,
    )

    assert first["status"] == "PASS"
    assert first["blob_links_created"] == 2
    assert second["deduplicated_files"] == 2
    assert second["estimated_bytes_saved"] > 0

    pdf1 = job1 / "evidence" / "AN_TEST.pdf"
    pdf2 = job2 / "evidence" / "AN_TEST.pdf"
    txt1 = job1 / "evidence" / "AN_TEST.txt"
    txt2 = job2 / "evidence" / "AN_TEST.txt"

    assert os.path.samefile(pdf1, pdf2)
    assert os.path.samefile(txt1, txt2)
    assert pdf2.stat().st_ino != before_pdf_inode
    assert txt2.stat().st_ino != before_txt_inode


def test_non_pass_job_is_never_compacted(tmp_path: Path) -> None:
    data_root = tmp_path / "runtime_data"
    job = _write_job(data_root, "review", status="NEEDS_REVIEW")
    original_inode = (job / "evidence" / "AN_TEST.pdf").stat().st_ino

    result = compact_completed_job_evidence(
        job_dir=job,
        data_root=data_root,
    )

    assert result == {"status": "SKIPPED", "reason": "JOB_NOT_PASS"}
    assert (job / "evidence" / "AN_TEST.pdf").stat().st_ino == original_inode
    assert not (data_root / "evidence_blobs").exists()
