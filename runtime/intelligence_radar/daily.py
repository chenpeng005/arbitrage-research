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
from runtime.intelligence_radar.storage import (
    DEFAULT_EXTRACTOR_VERSION,
    radar_db_path,
    save_daily_result,
    write_daily_archive,
)


def china_now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


class MeteredProvider:
    USAGE_KEYS = (
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
    )

    def __init__(self, delegate):
        self.delegate = delegate
        self.request_count = 0
        self.provider_name = None
        self.model_name = None
        self.totals = {key: 0 for key in self.USAGE_KEYS}

    def complete(self, **kwargs):
        self.request_count += 1
        response = self.delegate.complete(**kwargs)
        self.provider_name = response.provider or self.provider_name
        self.model_name = response.model or self.model_name
        usage = response.usage or {}
        for key in self.USAGE_KEYS:
            try:
                self.totals[key] += max(0, int(usage.get(key) or 0))
            except (TypeError, ValueError):
                continue
        return response

    def snapshot(
        self,
        *,
        default_provider: str | None = None,
        default_model: str | None = None,
    ) -> dict[str, Any]:
        return {
            "metered": True,
            "provider": self.provider_name or default_provider,
            "model": self.model_name or default_model,
            "request_count": self.request_count,
            "prompt_tokens": self.totals["prompt_tokens"],
            "completion_tokens": self.totals["completion_tokens"],
            "total_tokens": self.totals["total_tokens"],
            "cache_hit_tokens": self.totals["prompt_cache_hit_tokens"],
            "cache_miss_tokens": self.totals["prompt_cache_miss_tokens"],
        }


def _chunk(rows: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    safe = max(1, int(size))
    return [rows[i:i + safe] for i in range(0, len(rows), safe)]


def _effective_run_mode(run_date: str, requested: str | None) -> str:
    if requested:
        value = requested.upper()
        if value not in {"LIVE", "BACKFILL"}:
            raise ValueError("run_mode must be LIVE or BACKFILL")
        return value
    return "LIVE" if run_date == china_now().date().isoformat() else "BACKFILL"


def run_jisilu_daily(
    *,
    data_root: Path,
    run_date: str,
    max_pages: int = 8,
    max_questions: int = 120,
    batch_size: int = 12,
    provider=None,
    model: str | None = None,
    run_mode: str | None = None,
    extractor_version: str = DEFAULT_EXTRACTOR_VERSION,
) -> dict[str, Any]:
    scanned_at = china_now().isoformat()
    effective_run_mode = _effective_run_mode(run_date, run_mode)
    note_parts: list[str] = []
    broad_findings: list[dict[str, Any]] = []
    effective_model = model or os.environ.get("AI_MODEL") or "deepseek-chat"
    default_provider_name = os.environ.get("AI_PROVIDER") or None
    meter: MeteredProvider | None = None

    try:
        scan = collect_daily_candidates(
            run_date,
            max_pages=max_pages,
            max_questions=max_questions,
        )
    except Exception as exc:
        completed_at = china_now().isoformat()
        failed_usage = {
            "metered": True,
            "provider": default_provider_name,
            "model": effective_model,
            "request_count": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cache_hit_tokens": 0,
            "cache_miss_tokens": 0,
        }
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
            run_mode=effective_run_mode,
            extractor_version=extractor_version,
            ai_usage=failed_usage,
        )
        archive_path = write_daily_archive(data_root, run_date)
        return {
            "status": "FAILED",
            "run_date": run_date,
            "run_mode": effective_run_mode,
            "source": "jisilu",
            "candidate_count": 0,
            "broad_finding_count": 0,
            "finding_count": 0,
            "ai_usage": failed_usage,
            "archive_path": str(archive_path),
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

    if candidates:
        meter = MeteredProvider(provider or build_provider_from_env())
        provider_instance = meter
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
                provider=provider_instance,
                model=effective_model,
            )
        except Exception as exc:
            errors.append(f"FINAL_GATE {type(exc).__name__}: {exc}")

    note_parts.append(
        f"宽筛 {len(broad_findings)} 条 → 今日发现 {len(findings)} 条"
    )
    if errors:
        note_parts.append(f"AI处理失败 {len(errors)} 处；最终结果不视为完整")

    status = "FAILED" if errors else "OK"
    completed_at = china_now().isoformat()
    ai_usage = (
        meter.snapshot(
            default_provider=default_provider_name,
            default_model=effective_model,
        )
        if meter is not None
        else {
            "metered": True,
            "provider": default_provider_name,
            "model": effective_model,
            "request_count": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cache_hit_tokens": 0,
            "cache_miss_tokens": 0,
        }
    )
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
        run_mode=effective_run_mode,
        extractor_version=extractor_version,
        ai_usage=ai_usage,
    )
    archive_path = write_daily_archive(data_root, run_date)
    return {
        "status": status,
        "run_date": run_date,
        "run_mode": effective_run_mode,
        "source": "jisilu",
        "candidate_count": len(candidates),
        "broad_finding_count": len(broad_findings),
        "finding_count": len(findings),
        "question_ref_count": scan["question_ref_count"],
        "feed_meta": scan["feed_meta"],
        "detail_error_count": scan["detail_error_count"],
        "coverage_warning_count": scan["coverage_warning_count"],
        "errors": errors,
        "ai_usage": ai_usage,
        "saved": saved,
        "archive_path": str(archive_path),
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
        "--run-mode",
        choices=["AUTO", "LIVE", "BACKFILL"],
        default="AUTO",
        help="AUTO uses LIVE only for today's Asia/Shanghai date.",
    )
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
                {key: value for key, value in scan.items() if key != "candidates"},
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
        run_mode=None if args.run_mode == "AUTO" else args.run_mode,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
