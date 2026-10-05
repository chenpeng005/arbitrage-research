import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from runtime.intelligence_radar.storage import (
    get_daily_view,
    init_db,
    list_daily_dates,
    radar_archive_path,
    radar_db_path,
    save_daily_result,
    write_daily_archive,
)


class IntelligenceRadarStorageTest(unittest.TestCase):
    def test_daily_snapshot_replaces_same_source_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "radar.sqlite3"
            first = {
                "question_id": "1",
                "object_name": "跨境ETF申购套利",
                "node_title": "到账时间出现执行差异",
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
                "evidence": [
                    {
                        "source": "jisilu",
                        "author": "乙",
                        "published_at": "2026-10-04 10:00",
                        "source_title": "第一次",
                        "source_url": "https://example.com/1",
                        "locator_kind": "ANSWER",
                        "locator_id": "10",
                        "locator_url": "https://example.com/1#answer_list_10",
                        "excerpt": "实测片段",
                        "is_primary": True,
                    }
                ],
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
                run_mode="LIVE",
            )
            view = get_daily_view(db, "2026-10-04")
            self.assertEqual(view["finding_count"], 1)
            self.assertEqual(view["findings"][0]["object_name"], "跨境ETF申购套利")
            self.assertEqual(view["findings"][0]["node_title"], "到账时间出现执行差异")
            self.assertEqual(
                view["findings"][0]["evidence"][0]["locator_id"], "10"
            )
            self.assertEqual(view["runs"][0]["run_mode"], "LIVE")

            save_daily_result(
                db,
                run_date="2026-10-04",
                source="jisilu",
                scan_status="OK",
                scanned_at="2026-10-04T21:01:00+08:00",
                completed_at="2026-10-04T21:02:00+08:00",
                candidate_count=12,
                findings=[],
                run_mode="LIVE",
            )
            view = get_daily_view(db, "2026-10-04")
            self.assertEqual(view["finding_count"], 0)
            self.assertEqual(view["runs"][0]["candidate_count"], 12)
            self.assertEqual(list_daily_dates(db), ["2026-10-04"])

    def test_zero_finding_day_is_still_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "radar.sqlite3"
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
            self.assertEqual(view["status"], "OK")
            self.assertEqual(view["findings"], [])

    def test_v1_database_migrates_without_losing_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "radar.sqlite3"
            conn = sqlite3.connect(db)
            conn.executescript(
                """
                CREATE TABLE radar_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT INTO radar_meta VALUES('schema_version','1');
                CREATE TABLE daily_run (
                    run_date TEXT NOT NULL, source TEXT NOT NULL,
                    scan_status TEXT NOT NULL, scanned_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL, candidate_count INTEGER NOT NULL,
                    finding_count INTEGER NOT NULL, note TEXT,
                    PRIMARY KEY(run_date,source)
                );
                CREATE TABLE daily_finding (
                    finding_id TEXT PRIMARY KEY, run_date TEXT NOT NULL,
                    source TEXT NOT NULL, item_key TEXT NOT NULL,
                    question_id TEXT, title TEXT NOT NULL, url TEXT NOT NULL,
                    author TEXT, observed_at TEXT, finding_type TEXT NOT NULL,
                    what_happened TEXT NOT NULL, ai_understanding TEXT NOT NULL,
                    current_judgment TEXT NOT NULL,
                    worth_follow_up INTEGER NOT NULL DEFAULT 0,
                    evidence_excerpt TEXT, created_at TEXT NOT NULL
                );
                INSERT INTO daily_run VALUES(
                    '2026-10-04','jisilu','OK','2026-10-05T07:00:00+08:00',
                    '2026-10-05T07:01:00+08:00',52,3,'legacy'
                );
                INSERT INTO daily_finding VALUES(
                    'RDF_1','2026-10-04','jisilu','1','1','旧标题',
                    'https://example.com/1','甲','2026-10-04 20:00','REAL_TEST',
                    '旧事实','旧理解','旧判断',1,'旧证据','2026-10-05T00:00:00Z'
                );
                """
            )
            conn.commit()
            conn.close()

            init_db(db)
            view = get_daily_view(db, "2026-10-04")
            self.assertEqual(view["finding_count"], 1)
            self.assertEqual(view["runs"][0]["run_mode"], "BACKFILL")
            self.assertEqual(view["runs"][0]["extractor_version"], "legacy-v1")
            self.assertEqual(view["findings"][0]["node_title"], "旧标题")

    def test_daily_archive_is_small_rebuildable_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = radar_db_path(root)
            save_daily_result(
                db,
                run_date="2026-10-05",
                source="jisilu",
                scan_status="OK",
                scanned_at="2026-10-05T21:00:00+08:00",
                completed_at="2026-10-05T21:01:00+08:00",
                candidate_count=4,
                findings=[
                    {
                        "question_id": "1",
                        "object_name": "渝水转债",
                        "node_title": "下修触发价数据疑似错误",
                        "title": "原帖标题",
                        "url": "https://example.com/1",
                        "author": "甲",
                        "observed_at": "2026-10-05 10:00",
                        "finding_type": "DATA_ISSUE",
                        "what_happened": "平台数据疑似错误",
                        "ai_understanding": "可能导致下修判断偏差",
                        "current_judgment": "需核对公告",
                        "worth_follow_up": True,
                        "evidence_excerpt": "证据",
                    }
                ],
            )
            path = write_daily_archive(root, "2026-10-05")
            self.assertEqual(path, radar_archive_path(root, "2026-10-05"))
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["date"], "2026-10-05")
            self.assertEqual(payload["objects"][0]["object_name"], "渝水转债")
            self.assertEqual(payload["nodes"][0]["node_title"], "下修触发价数据疑似错误")
            self.assertIn("evidence", payload["nodes"][0])


if __name__ == "__main__":
    unittest.main()
