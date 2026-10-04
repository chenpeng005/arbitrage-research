from pathlib import Path

from runtime.intelligence_radar.storage import (
    get_daily_view,
    list_daily_dates,
    save_daily_result,
)


def test_daily_snapshot_replaces_same_source_date(tmp_path: Path) -> None:
    db = tmp_path / "radar.sqlite3"
    first = {
        "question_id": "1",
        "title": "第一次",
        "url": "https://example.com/1",
        "author": "A",
        "observed_at": "2026-10-04 10:00",
        "finding_type": "REAL_TEST",
        "what_happened": "第一次实测出现差异",
        "ai_understanding": "执行差异可能影响收益",
        "current_judgment": "值得继续跟踪",
        "worth_follow_up": True,
        "evidence_excerpt": "实测片段",
    }
    save_daily_result(
        db,
        run_date="2026-10-04",
        source="jisilu",
        scan_status="OK",
        scanned_at="2026-10-04T10:01:00+08:00",
        completed_at="2026-10-04T10:02:00+08:00",
        candidate_count=8,
        findings=[first],
    )
    assert get_daily_view(db, "2026-10-04")["finding_count"] == 1

    save_daily_result(
        db,
        run_date="2026-10-04",
        source="jisilu",
        scan_status="OK",
        scanned_at="2026-10-04T21:01:00+08:00",
        completed_at="2026-10-04T21:02:00+08:00",
        candidate_count=12,
        findings=[],
    )
    view = get_daily_view(db, "2026-10-04")
    assert view["finding_count"] == 0
    assert view["runs"][0]["candidate_count"] == 12
    assert list_daily_dates(db) == ["2026-10-04"]


def test_zero_finding_day_is_still_persisted(tmp_path: Path) -> None:
    db = tmp_path / "radar.sqlite3"
    save_daily_result(
        db,
        run_date="2026-10-05",
        source="jisilu",
        scan_status="OK",
        scanned_at="2026-10-05T21:00:00+08:00",
        completed_at="2026-10-05T21:01:00+08:00",
        candidate_count=4,
        findings=[],
    )
    view = get_daily_view(db, "2026-10-05")
    assert view["status"] == "OK"
    assert view["findings"] == []
