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
from runtime.intelligence_radar.increments import (
    DEFAULT_ANALYSIS_VERSION,
    mark_item_versions_analyzed,
    prepare_incremental_candidates,
    refresh_thread_capsules,
)
from runtime.intelligence_radar.storage import (
    DEFAULT_EXTRACTOR_VERSION,
    get_daily_view,
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


def _author_lane_audit_path(data_root: Path, run_date: str) -> Path:
    year, month, _ = run_date.split("-")
    return (
        data_root
        / "intelligence_radar"
        / "author_lane"
        / year
        / month
        / f"{run_date}.json"
    )


def _write_author_lane_audit(
    data_root: Path,
    run_date: str,
    *,
    scan: dict[str, Any],
    broad_findings: list[dict[str, Any]],
    findings: list[dict[str, Any]],
) -> Path:
    def slim_candidate(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "question_id": row.get("question_id"),
            "title": row.get("title"),
            "discovery_paths": list(row.get("discovery_paths", [])),
            "author_lane_authors": list(row.get("author_lane_authors", [])),
            "author_lane_hits": list(row.get("author_lane_hits", [])),
        }

    def slim_finding(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "question_id": row.get("question_id"),
            "object_name": row.get("object_name"),
            "node_title": row.get("node_title"),
            "finding_type": row.get("finding_type"),
            "discovery_paths": list(row.get("discovery_paths", [])),
            "author_lane_authors": list(row.get("author_lane_authors", [])),
        }

    candidates = [
        slim_candidate(row)
        for row in scan.get("candidates", [])
        if "AUTHOR_LANE" in row.get("discovery_paths", [])
    ]
    broad = [
        slim_finding(row)
        for row in broad_findings
        if "AUTHOR_LANE" in row.get("discovery_paths", [])
    ]
    final = [
        slim_finding(row)
        for row in findings
        if "AUTHOR_LANE" in row.get("discovery_paths", [])
    ]
    payload = {
        "schema_version": 1,
        "run_date": run_date,
        "author_lane_meta": scan.get("author_lane_meta", {}),
        "candidate_count": len(candidates),
        "author_only_candidate_count": sum(
            1 for row in candidates if row.get("discovery_paths") == ["AUTHOR_LANE"]
        ),
        "broad_count": len(broad),
        "author_only_broad_count": sum(
            1 for row in broad if row.get("discovery_paths") == ["AUTHOR_LANE"]
        ),
        "final_count": len(final),
        "author_only_final_count": sum(
            1 for row in final if row.get("discovery_paths") == ["AUTHOR_LANE"]
        ),
        "candidates": candidates,
        "broad_findings": broad,
        "final_findings": final,
    }
    path = _author_lane_audit_path(data_root, run_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


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


def _finding_merge_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("question_id") or row.get("item_key") or ""),
        str(row.get("finding_type") or ""),
        str(row.get("node_title") or row.get("title") or ""),
        str(row.get("what_happened") or ""),
    )


def _merge_live_findings(
    existing: list[dict[str, Any]],
    new_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    order: list[tuple[str, str, str, str]] = []
    for row in list(existing) + list(new_rows):
        key = _finding_merge_key(row)
        if key not in merged:
            order.append(key)
        merged[key] = row
    return [merged[key] for key in order]


def _combine_ai_usage(
    existing_run: dict[str, Any] | None,
    current: dict[str, Any],
) -> dict[str, Any]:
    if not existing_run or not bool(existing_run.get("ai_metered")):
        return dict(current)
    total = dict(current)
    mapping = {
        "request_count": "ai_request_count",
        "prompt_tokens": "ai_prompt_tokens",
        "completion_tokens": "ai_completion_tokens",
        "total_tokens": "ai_total_tokens",
        "cache_hit_tokens": "ai_cache_hit_tokens",
        "cache_miss_tokens": "ai_cache_miss_tokens",
    }
    for key, existing_key in mapping.items():
        total[key] = int(current.get(key) or 0) + int(existing_run.get(existing_key) or 0)
    total["metered"] = True
    total["provider"] = current.get("provider") or existing_run.get("ai_provider")
    total["model"] = current.get("model") or existing_run.get("ai_model")
    return total


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
    analysis_version: str | None = None,
) -> dict[str, Any]:
    scanned_at = china_now().isoformat()
    effective_run_mode = _effective_run_mode(run_date, run_mode)
    note_parts: list[str] = []
    broad_findings: list[dict[str, Any]] = []
    effective_model = model or os.environ.get("AI_MODEL") or "deepseek-chat"
    default_provider_name = os.environ.get("AI_PROVIDER") or None
    effective_analysis_version = (
        analysis_version
        or os.environ.get("RADAR_ANALYSIS_VERSION")
        or DEFAULT_ANALYSIS_VERSION
    )
    db_path = radar_db_path(data_root)
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
        existing_failure_findings: list[dict[str, Any]] = []
        failure_usage = failed_usage
        if effective_run_mode == "LIVE":
            existing_failure_view = get_daily_view(db_path, run_date)
            existing_failure_findings = list(
                existing_failure_view.get("findings", [])
            )
            existing_run = (
                existing_failure_view.get("runs", [None])[0]
                if existing_failure_view.get("runs")
                else None
            )
            failure_usage = _combine_ai_usage(existing_run, failed_usage)
        save_daily_result(
            db_path,
            run_date=run_date,
            source="jisilu",
            scan_status="FAILED",
            scanned_at=scanned_at,
            completed_at=completed_at,
            candidate_count=0,
            findings=existing_failure_findings,
            note=f"采集失败：{type(exc).__name__}: {exc}",
            run_mode=effective_run_mode,
            extractor_version=extractor_version,
            ai_usage=failure_usage,
        )
        archive_path = write_daily_archive(data_root, run_date)
        return {
            "status": "FAILED",
            "run_date": run_date,
            "run_mode": effective_run_mode,
            "source": "jisilu",
            "candidate_count": 0,
            "broad_finding_count": 0,
            "finding_count": len(existing_failure_findings),
            "ai_usage": failure_usage,
            "run_ai_usage": failed_usage,
            "archive_path": str(archive_path),
            "error": f"{type(exc).__name__}: {exc}",
        }

    if scan["detail_error_count"]:
        note_parts.append(f"详情页失败 {scan['detail_error_count']} 条")
    if scan["coverage_warning_count"]:
        note_parts.append(f"回复覆盖警告 {scan['coverage_warning_count']} 条")
    if scan["truncated_question_count"]:
        note_parts.append(f"候选上限截断 {scan['truncated_question_count']} 条")
    author_lane_meta = scan.get("author_lane_meta", {})
    if author_lane_meta.get("error_count"):
        note_parts.append(
            f"重点作者Lane采集异常 {author_lane_meta['error_count']} 处"
        )

    raw_candidates = scan["candidates"]
    incremental_meta: dict[str, Any] = {
        "enabled": effective_run_mode == "LIVE",
        "analysis_version": effective_analysis_version,
        "observed_version_count": 0,
        "new_version_count": 0,
        "skipped_version_count": 0,
        "ai_candidate_count": len(raw_candidates),
        "capsule_hit_count": 0,
        "item_refs": [],
    }
    if effective_run_mode == "LIVE":
        incremental_meta = prepare_incremental_candidates(
            db_path,
            source="jisilu",
            candidates=raw_candidates,
            analysis_version=effective_analysis_version,
        )
        incremental_meta["enabled"] = True
        candidates = incremental_meta["candidates"]
        note_parts.append(
            "增量AI "
            f"新版本 {incremental_meta['new_version_count']} / "
            f"已处理跳过 {incremental_meta['skipped_version_count']}；"
            f"AI候选 {len(candidates)}"
        )
    else:
        candidates = raw_candidates

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

    new_findings: list[dict[str, Any]] = []
    if not errors and broad_findings:
        try:
            new_findings = select_final_findings(
                run_date=run_date,
                source="jisilu",
                broad_findings=broad_findings,
                provider=provider_instance,
                model=effective_model,
            )
        except Exception as exc:
            errors.append(f"FINAL_GATE {type(exc).__name__}: {exc}")

    note_parts.append(
        f"宽筛 {len(broad_findings)} 条 → 本轮新增发现 {len(new_findings)} 条"
    )
    if errors:
        note_parts.append(f"AI处理失败 {len(errors)} 处；最终结果不视为完整")

    status = "FAILED" if errors else "OK"
    findings_to_save = list(new_findings)
    existing_run: dict[str, Any] | None = None
    existing_findings: list[dict[str, Any]] = []
    if effective_run_mode == "LIVE":
        existing_view = get_daily_view(db_path, run_date)
        existing_findings = list(existing_view.get("findings", []))
        existing_run = (
            existing_view.get("runs", [None])[0]
            if existing_view.get("runs")
            else None
        )
        if status == "OK":
            findings_to_save = _merge_live_findings(existing_findings, new_findings)
        else:
            # A partial/failed incremental run must never erase a valid earlier snapshot.
            findings_to_save = existing_findings
        if existing_findings:
            note_parts.append(
                f"当日累计发现 {len(findings_to_save)} 条（保留既有 {len(existing_findings)} 条）"
            )
    completed_at = china_now().isoformat()
    run_ai_usage = (
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
    ai_usage = (
        _combine_ai_usage(existing_run, run_ai_usage)
        if effective_run_mode == "LIVE"
        else run_ai_usage
    )
    saved = save_daily_result(
        db_path,
        run_date=run_date,
        source="jisilu",
        scan_status=status,
        scanned_at=scanned_at,
        completed_at=completed_at,
        candidate_count=int(scan.get("candidate_count") or 0),
        findings=findings_to_save,
        note="；".join(note_parts) or None,
        run_mode=effective_run_mode,
        extractor_version=extractor_version,
        ai_usage=ai_usage,
    )

    ledger_marked_count = 0
    capsule_refresh_count = 0
    postprocess_errors: list[str] = []
    if status == "OK" and effective_run_mode == "LIVE":
        try:
            ledger_marked_count = mark_item_versions_analyzed(
                db_path,
                item_refs=incremental_meta.get("item_refs", []),
                analysis_version=effective_analysis_version,
            )
        except Exception as exc:
            postprocess_errors.append(
                f"LEDGER_MARK {type(exc).__name__}: {exc}"
            )
        try:
            capsule_refresh_count = refresh_thread_capsules(
                db_path,
                source="jisilu",
                thread_ids=[
                    str(row.get("question_id") or "") for row in new_findings
                ],
            )
        except Exception as exc:
            postprocess_errors.append(
                f"CAPSULE_REFRESH {type(exc).__name__}: {exc}"
            )

    archive_path = write_daily_archive(data_root, run_date)
    author_lane_audit_path = _write_author_lane_audit(
        data_root,
        run_date,
        scan=scan,
        broad_findings=broad_findings,
        findings=new_findings,
    )
    return {
        "status": status,
        "run_date": run_date,
        "run_mode": effective_run_mode,
        "source": "jisilu",
        "candidate_count": int(scan.get("candidate_count") or 0),
        "ai_candidate_count": len(candidates),
        "broad_finding_count": len(broad_findings),
        "new_finding_count": len(new_findings),
        "finding_count": len(findings_to_save),
        "analysis_version": effective_analysis_version,
        "incremental": {
            key: value
            for key, value in incremental_meta.items()
            if key not in {"candidates", "item_refs"}
        },
        "ledger_marked_count": ledger_marked_count,
        "capsule_refresh_count": capsule_refresh_count,
        "postprocess_errors": postprocess_errors,
        "question_ref_count": scan["question_ref_count"],
        "feed_meta": scan["feed_meta"],
        "author_lane_meta": scan.get("author_lane_meta", {}),
        "author_lane_audit_path": str(author_lane_audit_path),
        "detail_error_count": scan["detail_error_count"],
        "coverage_warning_count": scan["coverage_warning_count"],
        "errors": errors,
        "ai_usage": ai_usage,
        "run_ai_usage": run_ai_usage,
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
        "--analysis-version",
        default=os.environ.get("RADAR_ANALYSIS_VERSION") or DEFAULT_ANALYSIS_VERSION,
        help="Increment-ledger AI analysis version. Changing it allows intentional re-analysis.",
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
        analysis_version=args.analysis_version,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
