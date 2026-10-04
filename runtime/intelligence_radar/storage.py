from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def radar_db_path(data_root: Path) -> Path:
    return data_root / "intelligence_radar" / "radar.sqlite3"


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Path) -> None:
    with connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS radar_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS daily_run (
                run_date TEXT NOT NULL,
                source TEXT NOT NULL,
                scan_status TEXT NOT NULL,
                scanned_at TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                candidate_count INTEGER NOT NULL DEFAULT 0,
                finding_count INTEGER NOT NULL DEFAULT 0,
                note TEXT,
                PRIMARY KEY (run_date, source)
            );
            CREATE TABLE IF NOT EXISTS daily_finding (
                finding_id TEXT PRIMARY KEY,
                run_date TEXT NOT NULL,
                source TEXT NOT NULL,
                item_key TEXT NOT NULL,
                question_id TEXT,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                author TEXT,
                observed_at TEXT,
                finding_type TEXT NOT NULL,
                what_happened TEXT NOT NULL,
                ai_understanding TEXT NOT NULL,
                current_judgment TEXT NOT NULL,
                worth_follow_up INTEGER NOT NULL DEFAULT 0,
                evidence_excerpt TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (run_date, source)
                    REFERENCES daily_run(run_date, source) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_daily_finding_date
                ON daily_finding(run_date, source);
            """
        )
        conn.execute(
            """INSERT INTO radar_meta(key,value) VALUES('schema_version',?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (str(SCHEMA_VERSION),),
        )


def _finding_id(
    run_date: str,
    source: str,
    item_key: str,
    finding_type: str,
    what_happened: str,
) -> str:
    raw = "|".join([run_date, source, item_key, finding_type, what_happened.strip()])
    return "RDF_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def save_daily_result(
    db_path: Path,
    *,
    run_date: str,
    source: str,
    scan_status: str,
    scanned_at: str,
    completed_at: str,
    candidate_count: int,
    findings: list[dict[str, Any]],
    note: str | None = None,
) -> dict[str, Any]:
    """Replace one source/date snapshot atomically."""
    init_db(db_path)
    with connect(db_path) as conn:
        conn.execute(
            """INSERT INTO daily_run(
                   run_date,source,scan_status,scanned_at,completed_at,
                   candidate_count,finding_count,note
               ) VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(run_date,source) DO UPDATE SET
                   scan_status=excluded.scan_status,
                   scanned_at=excluded.scanned_at,
                   completed_at=excluded.completed_at,
                   candidate_count=excluded.candidate_count,
                   finding_count=excluded.finding_count,
                   note=excluded.note""",
            (
                run_date, source, scan_status, scanned_at, completed_at,
                int(candidate_count), len(findings), note,
            ),
        )
        conn.execute(
            "DELETE FROM daily_finding WHERE run_date=? AND source=?",
            (run_date, source),
        )
        created_at = now_utc()
        for row in findings:
            item_key = str(row.get("item_key") or row.get("question_id") or row["url"])
            fid = str(
                row.get("finding_id")
                or _finding_id(
                    run_date, source, item_key,
                    str(row["finding_type"]), str(row["what_happened"]),
                )
            )
            conn.execute(
                """INSERT INTO daily_finding(
                       finding_id,run_date,source,item_key,question_id,title,url,
                       author,observed_at,finding_type,what_happened,
                       ai_understanding,current_judgment,worth_follow_up,
                       evidence_excerpt,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    fid, run_date, source, item_key, row.get("question_id"),
                    str(row["title"]), str(row["url"]), row.get("author"),
                    row.get("observed_at"), str(row["finding_type"]),
                    str(row["what_happened"]), str(row["ai_understanding"]),
                    str(row["current_judgment"]),
                    1 if row.get("worth_follow_up") else 0,
                    row.get("evidence_excerpt"), created_at,
                ),
            )
        conn.commit()
    return get_daily_view(db_path, run_date)


def list_daily_dates(db_path: Path, limit: int = 90) -> list[str]:
    init_db(db_path)
    safe_limit = max(1, min(int(limit), 3660))
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT run_date FROM daily_run ORDER BY run_date DESC LIMIT ?",
            (safe_limit,),
        ).fetchall()
    return [str(row["run_date"]) for row in rows]


def latest_daily_date(db_path: Path) -> str | None:
    dates = list_daily_dates(db_path, 1)
    return dates[0] if dates else None


def get_daily_view(db_path: Path, run_date: str | None = None) -> dict[str, Any]:
    init_db(db_path)
    selected = run_date or latest_daily_date(db_path)
    if selected is None:
        return {"date": None, "runs": [], "findings": [], "finding_count": 0, "status": "NO_DATA"}

    with connect(db_path) as conn:
        runs = [
            dict(row) for row in conn.execute(
                """SELECT run_date,source,scan_status,scanned_at,completed_at,
                          candidate_count,finding_count,note
                   FROM daily_run WHERE run_date=? ORDER BY source""",
                (selected,),
            ).fetchall()
        ]
        findings = [
            dict(row) for row in conn.execute(
                """SELECT finding_id,run_date,source,item_key,question_id,title,url,
                          author,observed_at,finding_type,what_happened,
                          ai_understanding,current_judgment,worth_follow_up,
                          evidence_excerpt
                   FROM daily_finding WHERE run_date=?
                   ORDER BY COALESCE(observed_at,'') DESC,finding_id""",
                (selected,),
            ).fetchall()
        ]
    for row in findings:
        row["worth_follow_up"] = bool(row["worth_follow_up"])
    statuses = {str(row["scan_status"]) for row in runs}
    overall = "NO_DATA" if not runs else ("OK" if statuses == {"OK"} else ("FAILED" if "FAILED" in statuses else "PARTIAL"))
    return {
        "date": selected,
        "runs": runs,
        "findings": findings,
        "finding_count": len(findings),
        "status": overall,
    }
