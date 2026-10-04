from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from runtime.intelligence_radar.filtering import build_provider_from_env, filter_batch
from runtime.intelligence_radar.final_gate import select_final_findings
from runtime.intelligence_radar.jisilu import collect_daily_candidates
from runtime.intelligence_radar.storage import radar_db_path, save_daily_result


def china_now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _chunk(rows: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    safe = max(1, int(size))
    return [rows[i:i + safe] for i in range(0, len(rows), safe)]


def run_jisilu_daily(
    *,
    data_root: Path,
    run_date: str,
    max_pages: int = 8,
    max_questions: int = 120,
    batch_size: int = 12,
    provider=None,
    model: str | None = None,
) -> dict[str, Any]:
    scanned_at = china_now().isoformat()
    note_parts: list[str] = []
    broad_findings: list[dict[str, Any]] = []

    try:
        scan = collect_daily_candidates(
            run_date,
            max_pages=max_pages,
            max_questions=max_questions,
        )
    except Exception as exc:
        completed_at = china_now().isoformat()
        save_daily_result(
            radar_db_path(data_root),
            run_date=run_date,
            source="jisilu",
            scan_status="FAILED",
            scanned_at=scanned_at,
            completed_at=completed_at,
            candidate_count=0,
            findings=[],
            note=f"采集失败：{type(exc).__name__}: {exc}",
        )
        return {
            "status": "FAILED",
            "run_date": run_date,
            "source": "jisilu",
            "candidate_count": 0,
            "broad_finding_count": 0,
            "finding_count": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }

    if scan["detail_error_count"]:
        note_parts.append(f"详情页失败 {scan['detail_error_count']} 条")
    if scan["coverage_warning_count"]:
        note_parts.append(f"回复覆盖警告 {scan['coverage_warning_count']} 条")
    if scan["truncated_question_count"]:
        note_parts.append(f"候选上限截断 {scan['truncated_question_count']} 条")

    candidates = scan["candidates"]
    errors: list[str] = []
    provider_instance = None
    effective_model = model or os.environ.get("AI_MODEL") or "deepseek-chat"

    if candidates:
        provider_instance = provider or build_provider_from_env()
        for idx, batch in enumerate(_chunk(candidates, batch_size), start=1):
            batch_id = f"{run_date}:jisilu:{idx:03d}"
            try:
                broad_findings.extend(
                    filter_batch(
                        run_date=run_date,
                        source="jisilu",
                        batch_id=batch_id,
                        candidates=batch,
                        provider=provider_instance,
                        model=effective_model,
                    )
                )
            except Exception as exc:
                errors.append(f"{batch_id} {type(exc).__name__}: {exc}")

    findings: list[dict[str, Any]] = []
    if not errors and broad_findings:
        try:
            findings = select_final_findings(
                run_date=run_date,
                source="jisilu",
                broad_findings=broad_findings,
                provider=provider_instance or provider or build_provider_from_env(),
                model=effective_model,
            )
        except Exception as exc:
            errors.append(
                f"FINAL_GATE {type(exc).__name__}: {exc}"
            )

    note_parts.append(
        f"宽筛 {len(broad_findings)} 条 → 今日发现 {len(findings)} 条"
    )
    if errors:
        note_parts.append(
            f"AI处理失败 {len(errors)} 处；最终结果不视为完整"
        )

    status = "FAILED" if errors else "OK"
    completed_at = china_now().isoformat()
    saved = save_daily_result(
        radar_db_path(data_root),
        run_date=run_date,
        source="jisilu",
        scan_status=status,
        scanned_at=scanned_at,
        completed_at=completed_at,
        candidate_count=len(candidates),
        findings=findings,
        note="；".join(note_parts) or None,
    )
    return {
        "status": status,
        "run_date": run_date,
        "source": "jisilu",
        "candidate_count": len(candidates),
        "broad_finding_count": len(broad_findings),
        "finding_count": len(findings),
        "question_ref_count": scan["question_ref_count"],
        "feed_meta": scan["feed_meta"],
        "detail_error_count": scan["detail_error_count"],
        "coverage_warning_count": scan["coverage_warning_count"],
        "errors": errors,
        "saved": saved,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run one manual daily intelligence-radar scan."
    )
    parser.add_argument(
        "--date",
        default=china_now().date().isoformat(),
        help="Asia/Shanghai date, YYYY-MM-DD",
    )
    parser.add_argument(
        "--data-root",
        default=os.environ.get("RUNTIME_DATA_ROOT", "runtime_data"),
    )
    parser.add_argument("--max-pages", type=int, default=8)
    parser.add_argument("--max-questions", type=int, default=120)
    parser.add_argument("--batch-size", type=int, default=12)
    parser.add_argument(
        "--collect-only",
        action="store_true",
        help="Only validate public Jisilu collection; do not call AI or persist.",
    )
    args = parser.parse_args()

    if args.collect_only:
        scan = collect_daily_candidates(
            args.date,
            max_pages=args.max_pages,
            max_questions=args.max_questions,
        )
        print(
            json.dumps(
                {
                    key: value
                    for key, value in scan.items()
                    if key != "candidates"
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    result = run_jisilu_daily(
        data_root=Path(args.data_root),
        run_date=args.date,
        max_pages=args.max_pages,
        max_questions=args.max_questions,
        batch_size=args.batch_size,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
