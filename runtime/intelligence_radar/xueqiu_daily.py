from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from runtime.intelligence_radar.daily import (
    MeteredProvider,
    _chunk,
    _combine_ai_usage,
    _effective_run_mode,
    _merge_live_findings,
    china_now,
)
from runtime.intelligence_radar.filtering import build_provider_from_env, filter_batch
from runtime.intelligence_radar.final_gate import select_final_findings
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
from runtime.intelligence_radar.xueqiu import build_increment_candidates

SOURCE = "xueqiu"


def shadow_archive_path(data_root: Path, run_date: str) -> Path:
    year, month, _ = run_date.split("-", 2)
    return (
        data_root
        / "intelligence_radar"
        / "xueqiu_shadow"
        / year
        / month
        / f"{run_date}.json"
    )


def load_shadow_candidates(data_root: Path, run_date: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = shadow_archive_path(data_root, run_date)
    if not path.exists():
        raise FileNotFoundError(f"Xueqiu shadow archive not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("source") != SOURCE:
        raise ValueError(f"invalid Xueqiu shadow archive: {path}")

    items: list[dict[str, Any]] = []
    for row in payload.get("items", []):
        if not isinstance(row, dict):
            continue
        parent_context = None
        parent_excerpt = str(row.get("parent_excerpt") or "").strip()
        parent_id = str(row.get("parent_id") or "").strip()
        if parent_excerpt and parent_id:
            parent_context = {
                "status_id": parent_id,
                "author_id": None,
                "author_name": None,
                "published_at": None,
                "edited_at": None,
                "content": parent_excerpt,
                "target": None,
            }
        items.append(
            {
                "source": SOURCE,
                "item_type": row.get("item_type"),
                "item_id": str(row.get("item_id") or ""),
                "status_id": str(row.get("status_id") or row.get("item_id") or ""),
                "comment_id": row.get("comment_id"),
                "parent_id": parent_id or None,
                "author_id": row.get("author_id"),
                "author_name": row.get("author_name"),
                "published_at": row.get("published_at"),
                "edited_at": row.get("edited_at"),
                "content": str(row.get("excerpt") or ""),
                "title": str(row.get("title") or ""),
                "locator_url": row.get("locator_url"),
                "parent_context": parent_context,
                "discovery_paths": list(row.get("discovery_paths", [])),
                "watch_author_id": row.get("watch_author_id"),
                "watch_author_label": row.get("watch_author_label"),
            }
        )
    return build_increment_candidates(items), payload


def _source_existing(view: dict[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    run = next((row for row in view.get("runs", []) if row.get("source") == SOURCE), None)
    findings = [row for row in view.get("findings", []) if row.get("source") == SOURCE]
    return run, findings


def run_xueqiu_daily(
    *,
    data_root: Path,
    run_date: str,
    batch_size: int = 35,
    provider=None,
    model: str | None = None,
    run_mode: str | None = None,
    extractor_version: str = DEFAULT_EXTRACTOR_VERSION,
    analysis_version: str | None = None,
) -> dict[str, Any]:
    scanned_at = china_now().isoformat()
    effective_run_mode = _effective_run_mode(run_date, run_mode)
    effective_model = model or os.environ.get("AI_MODEL") or "deepseek-chat"
    default_provider_name = os.environ.get("AI_PROVIDER") or None
    effective_analysis_version = (
        analysis_version
        or os.environ.get("RADAR_ANALYSIS_VERSION")
        or DEFAULT_ANALYSIS_VERSION
    )
    db_path = radar_db_path(data_root)
    note_parts: list[str] = []
    broad_findings: list[dict[str, Any]] = []
    meter: MeteredProvider | None = None

    try:
        raw_candidates, archive = load_shadow_candidates(data_root, run_date)
    except Exception as exc:
        completed_at = china_now().isoformat()
        existing_run = None
        existing_findings: list[dict[str, Any]] = []
        if effective_run_mode == "LIVE":
            existing_run, existing_findings = _source_existing(get_daily_view(db_path, run_date))
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
        usage = _combine_ai_usage(existing_run, failed_usage) if existing_run else failed_usage
        save_daily_result(
            db_path,
            run_date=run_date,
            source=SOURCE,
            scan_status="FAILED",
            scanned_at=scanned_at,
            completed_at=completed_at,
            candidate_count=0,
            findings=existing_findings,
            note=f"雪球采集归档读取失败：{type(exc).__name__}: {exc}",
            run_mode=effective_run_mode,
            extractor_version=extractor_version,
            ai_usage=usage,
        )
        archive_path = write_daily_archive(data_root, run_date)
        return {
            "status": "FAILED",
            "run_date": run_date,
            "source": SOURCE,
            "candidate_count": 0,
            "finding_count": len(existing_findings),
            "error": f"{type(exc).__name__}: {exc}",
            "archive_path": str(archive_path),
        }

    note_parts.append(
        f"雪球Hot/Author积累 {int(archive.get('item_count') or 0)} 条；"
        "Hot Exploration受平台推荐机制影响，不代表雪球全站覆盖"
    )

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
    candidates = raw_candidates
    if effective_run_mode == "LIVE":
        incremental_meta = prepare_incremental_candidates(
            db_path,
            source=SOURCE,
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

    errors: list[str] = []
    provider_instance = None
    if candidates:
        meter = MeteredProvider(provider or build_provider_from_env())
        provider_instance = meter
        for idx, batch in enumerate(_chunk(candidates, batch_size), start=1):
            batch_id = f"{run_date}:xueqiu:{idx:03d}"
            try:
                broad_findings.extend(
                    filter_batch(
                        run_date=run_date,
                        source=SOURCE,
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
                source=SOURCE,
                broad_findings=broad_findings,
                provider=provider_instance,
                model=effective_model,
            )
        except Exception as exc:
            errors.append(f"FINAL_GATE {type(exc).__name__}: {exc}")

    note_parts.append(f"宽筛 {len(broad_findings)} 条 → 本轮新增发现 {len(new_findings)} 条")
    if errors:
        note_parts.append(f"AI处理失败 {len(errors)} 处；最终结果不视为完整")
    status = "FAILED" if errors else "OK"

    existing_run = None
    existing_findings: list[dict[str, Any]] = []
    findings_to_save = list(new_findings)
    if effective_run_mode == "LIVE":
        existing_run, existing_findings = _source_existing(get_daily_view(db_path, run_date))
        if status == "OK":
            findings_to_save = _merge_live_findings(existing_findings, new_findings)
        else:
            findings_to_save = existing_findings
        if existing_findings:
            note_parts.append(
                f"当日累计发现 {len(findings_to_save)} 条（保留既有 {len(existing_findings)} 条）"
            )

    completed_at = china_now().isoformat()
    run_ai_usage = (
        meter.snapshot(default_provider=default_provider_name, default_model=effective_model)
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
    ai_usage = _combine_ai_usage(existing_run, run_ai_usage) if existing_run else run_ai_usage

    saved = save_daily_result(
        db_path,
        run_date=run_date,
        source=SOURCE,
        scan_status=status,
        scanned_at=scanned_at,
        completed_at=completed_at,
        candidate_count=len(raw_candidates),
        findings=findings_to_save,
        note="；".join(note_parts),
        run_mode=effective_run_mode,
        extractor_version=extractor_version,
        ai_usage=ai_usage,
    )

    ledger_marked_count = 0
    capsule_refresh_count = 0
    if status == "OK" and effective_run_mode == "LIVE":
        ledger_marked_count = mark_item_versions_analyzed(
            db_path,
            item_refs=incremental_meta.get("item_refs", []),
            analysis_version=effective_analysis_version,
        )
        capsule_refresh_count = refresh_thread_capsules(
            db_path,
            source=SOURCE,
            thread_ids=[str(row.get("question_id") or "") for row in new_findings],
        )

    archive_path = write_daily_archive(data_root, run_date)
    return {
        "status": status,
        "run_date": run_date,
        "run_mode": effective_run_mode,
        "source": SOURCE,
        "candidate_count": len(raw_candidates),
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
        "errors": errors,
        "ai_usage": ai_usage,
        "run_ai_usage": run_ai_usage,
        "saved": saved,
        "archive_path": str(archive_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run formal Xueqiu intelligence-radar daily scan from accumulated public Shadow observations.")
    parser.add_argument("--date", default=china_now().date().isoformat())
    parser.add_argument("--data-root", default=os.environ.get("RUNTIME_DATA_ROOT", "runtime_data"))
    parser.add_argument("--batch-size", type=int, default=35)
    parser.add_argument("--run-mode", choices=["AUTO", "LIVE", "BACKFILL"], default="AUTO")
    parser.add_argument(
        "--analysis-version",
        default=os.environ.get("RADAR_ANALYSIS_VERSION") or DEFAULT_ANALYSIS_VERSION,
    )
    args = parser.parse_args()
    result = run_xueqiu_daily(
        data_root=Path(args.data_root),
        run_date=args.date,
        batch_size=args.batch_size,
        run_mode=None if args.run_mode == "AUTO" else args.run_mode,
        analysis_version=args.analysis_version,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
