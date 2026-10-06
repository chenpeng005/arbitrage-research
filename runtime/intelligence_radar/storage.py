from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 4
ARCHIVE_SCHEMA_VERSION = 1
DEFAULT_EXTRACTOR_VERSION = "radar-v3-increment-ledger"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def radar_db_path(data_root: Path) -> Path:
    return data_root / "intelligence_radar" / "radar.sqlite3"


def radar_archive_path(data_root: Path, run_date: str) -> Path:
    year, month, _ = run_date.split("-")
    return (
        data_root
        / "intelligence_radar"
        / "archive"
        / year
        / month
        / f"{run_date}.json"
    )


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _schema_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute(
            "SELECT value FROM radar_meta WHERE key='schema_version'"
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    if row is None:
        return 0
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        return 0


def normalize_object_name(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    return re.sub(r"\s+", "", text)


def _object_id(object_name: str) -> str:
    normalized = normalize_object_name(object_name)
    return "OBJ_" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def ensure_object(
    conn: sqlite3.Connection,
    object_name: str,
    *,
    aliases: list[str] | None = None,
) -> str:
    name = str(object_name or "").strip()
    if not name:
        raise ValueError("object_name must not be empty")
    normalized = normalize_object_name(name)
    alias_row = conn.execute(
        "SELECT object_id FROM radar_object_alias WHERE normalized_alias=?",
        (normalized,),
    ).fetchone()
    if alias_row is not None:
        object_id = str(alias_row["object_id"])
        conn.execute(
            "UPDATE radar_object SET updated_at=? WHERE object_id=?",
            (now_utc(), object_id),
        )
    else:
        direct = conn.execute(
            "SELECT object_id FROM radar_object WHERE normalized_name=?",
            (normalized,),
        ).fetchone()
        if direct is not None:
            object_id = str(direct["object_id"])
        else:
            object_id = _object_id(name)
            timestamp = now_utc()
            conn.execute(
                """INSERT INTO radar_object(
                       object_id,object_name,normalized_name,created_at,updated_at
                   ) VALUES(?,?,?,?,?)""",
                (object_id, name, normalized, timestamp, timestamp),
            )
        conn.execute(
            """INSERT INTO radar_object_alias(
                   normalized_alias,alias,object_id,created_at
               ) VALUES(?,?,?,?)
               ON CONFLICT(normalized_alias) DO UPDATE SET
                   alias=excluded.alias,
                   object_id=excluded.object_id""",
            (normalized, name, object_id, now_utc()),
        )

    for alias in aliases or []:
        alias_text = str(alias or "").strip()
        if not alias_text:
            continue
        conn.execute(
            """INSERT INTO radar_object_alias(
                   normalized_alias,alias,object_id,created_at
               ) VALUES(?,?,?,?)
               ON CONFLICT(normalized_alias) DO UPDATE SET
                   alias=excluded.alias,
                   object_id=excluded.object_id""",
            (
                normalize_object_name(alias_text),
                alias_text,
                object_id,
                now_utc(),
            ),
        )
    return object_id


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
                run_mode TEXT NOT NULL DEFAULT 'LIVE',
                extractor_version TEXT,
                ai_metered INTEGER NOT NULL DEFAULT 0,
                ai_provider TEXT,
                ai_model TEXT,
                ai_request_count INTEGER NOT NULL DEFAULT 0,
                ai_prompt_tokens INTEGER NOT NULL DEFAULT 0,
                ai_completion_tokens INTEGER NOT NULL DEFAULT 0,
                ai_total_tokens INTEGER NOT NULL DEFAULT 0,
                ai_cache_hit_tokens INTEGER NOT NULL DEFAULT 0,
                ai_cache_miss_tokens INTEGER NOT NULL DEFAULT 0,
                ai_usage_json TEXT,
                PRIMARY KEY (run_date, source)
            );
            CREATE TABLE IF NOT EXISTS radar_object (
                object_id TEXT PRIMARY KEY,
                object_name TEXT NOT NULL,
                normalized_name TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS radar_object_alias (
                normalized_alias TEXT PRIMARY KEY,
                alias TEXT NOT NULL,
                object_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (object_id)
                    REFERENCES radar_object(object_id) ON DELETE CASCADE
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
                object_id TEXT,
                node_title TEXT,
                FOREIGN KEY (run_date, source)
                    REFERENCES daily_run(run_date, source) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS finding_evidence (
                evidence_id TEXT PRIMARY KEY,
                finding_id TEXT NOT NULL,
                source TEXT NOT NULL,
                author TEXT,
                published_at TEXT,
                source_title TEXT,
                source_url TEXT NOT NULL,
                locator_kind TEXT,
                locator_id TEXT,
                locator_url TEXT,
                excerpt TEXT,
                is_primary INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY (finding_id)
                    REFERENCES daily_finding(finding_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS source_item_version (
                source TEXT NOT NULL,
                item_type TEXT NOT NULL,
                item_id TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                parent_id TEXT,
                published_at TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                PRIMARY KEY (source,item_type,item_id,content_hash)
            );
            CREATE TABLE IF NOT EXISTS source_item_analysis (
                source TEXT NOT NULL,
                item_type TEXT NOT NULL,
                item_id TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                analysis_version TEXT NOT NULL,
                analyzed_at TEXT NOT NULL,
                PRIMARY KEY (source,item_type,item_id,content_hash,analysis_version),
                FOREIGN KEY (source,item_type,item_id,content_hash)
                    REFERENCES source_item_version(
                        source,item_type,item_id,content_hash
                    ) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS thread_context_capsule (
                source TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                title TEXT,
                capsule_text TEXT NOT NULL,
                capsule_hash TEXT NOT NULL,
                capsule_version TEXT NOT NULL,
                derived_from_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (source,thread_id)
            );
            CREATE INDEX IF NOT EXISTS idx_daily_finding_date
                ON daily_finding(run_date, source);
            CREATE INDEX IF NOT EXISTS idx_finding_evidence_finding
                ON finding_evidence(finding_id, is_primary DESC, published_at);
            CREATE INDEX IF NOT EXISTS idx_source_item_parent
                ON source_item_version(source,parent_id,published_at);
            CREATE INDEX IF NOT EXISTS idx_source_item_analysis_version
                ON source_item_analysis(source,analysis_version,analyzed_at);
            CREATE INDEX IF NOT EXISTS idx_thread_capsule_updated
                ON thread_context_capsule(source,updated_at);
            """
        )

        previous_version = _schema_version(conn)
        run_columns = _table_columns(conn, "daily_run")
        if "run_mode" not in run_columns:
            conn.execute(
                "ALTER TABLE daily_run ADD COLUMN run_mode TEXT NOT NULL DEFAULT 'LIVE'"
            )
        if "extractor_version" not in run_columns:
            conn.execute(
                "ALTER TABLE daily_run ADD COLUMN extractor_version TEXT"
            )
        usage_columns = {
            "ai_metered": "INTEGER NOT NULL DEFAULT 0",
            "ai_provider": "TEXT",
            "ai_model": "TEXT",
            "ai_request_count": "INTEGER NOT NULL DEFAULT 0",
            "ai_prompt_tokens": "INTEGER NOT NULL DEFAULT 0",
            "ai_completion_tokens": "INTEGER NOT NULL DEFAULT 0",
            "ai_total_tokens": "INTEGER NOT NULL DEFAULT 0",
            "ai_cache_hit_tokens": "INTEGER NOT NULL DEFAULT 0",
            "ai_cache_miss_tokens": "INTEGER NOT NULL DEFAULT 0",
            "ai_usage_json": "TEXT",
        }
        run_columns = _table_columns(conn, "daily_run")
        for column, sql_type in usage_columns.items():
            if column not in run_columns:
                conn.execute(f"ALTER TABLE daily_run ADD COLUMN {column} {sql_type}")

        finding_columns = _table_columns(conn, "daily_finding")
        if "object_id" not in finding_columns:
            conn.execute("ALTER TABLE daily_finding ADD COLUMN object_id TEXT")
        if "node_title" not in finding_columns:
            conn.execute("ALTER TABLE daily_finding ADD COLUMN node_title TEXT")

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_daily_finding_object "
            "ON daily_finding(object_id, run_date)"
        )

        if previous_version < 2:
            conn.execute(
                """UPDATE daily_run
                   SET run_mode=CASE
                       WHEN substr(scanned_at,1,10)=run_date THEN 'LIVE'
                       ELSE 'BACKFILL'
                   END"""
            )
            conn.execute(
                """UPDATE daily_run
                   SET extractor_version=COALESCE(extractor_version,'legacy-v1')"""
            )

        conn.execute(
            """INSERT INTO radar_meta(key,value) VALUES('schema_version',?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (str(SCHEMA_VERSION),),
        )
        conn.commit()


def _finding_id(
    run_date: str,
    source: str,
    item_key: str,
    finding_type: str,
    what_happened: str,
) -> str:
    raw = "|".join([run_date, source, item_key, finding_type, what_happened.strip()])
    return "RDF_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _evidence_id(finding_id: str, row: dict[str, Any]) -> str:
    raw = "|".join(
        [
            finding_id,
            str(row.get("source") or ""),
            str(row.get("locator_kind") or ""),
            str(row.get("locator_id") or ""),
            str(row.get("published_at") or ""),
            str(row.get("excerpt") or "")[:160],
        ]
    )
    return "EVD_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _fallback_evidence(
    source: str,
    row: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        {
            "source": source,
            "author": row.get("author"),
            "published_at": row.get("observed_at"),
            "source_title": row.get("title"),
            "source_url": row.get("url"),
            "locator_kind": "QUESTION",
            "locator_id": row.get("question_id"),
            "locator_url": row.get("url"),
            "excerpt": row.get("evidence_excerpt"),
            "is_primary": True,
        }
    ]


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
    run_mode: str = "LIVE",
    extractor_version: str = DEFAULT_EXTRACTOR_VERSION,
    ai_usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Replace one source/date snapshot atomically."""
    init_db(db_path)
    normalized_run_mode = str(run_mode or "LIVE").upper()
    if normalized_run_mode not in {"LIVE", "BACKFILL"}:
        raise ValueError("run_mode must be LIVE or BACKFILL")
    usage = dict(ai_usage or {})
    ai_metered = bool(usage.get("metered"))

    def _usage_int(key: str) -> int:
        try:
            return max(0, int(usage.get(key) or 0))
        except (TypeError, ValueError):
            return 0

    with connect(db_path) as conn:
        conn.execute(
            """INSERT INTO daily_run(
                   run_date,source,scan_status,scanned_at,completed_at,
                   candidate_count,finding_count,note,run_mode,extractor_version,
                   ai_metered,ai_provider,ai_model,ai_request_count,
                   ai_prompt_tokens,ai_completion_tokens,ai_total_tokens,
                   ai_cache_hit_tokens,ai_cache_miss_tokens,ai_usage_json
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(run_date,source) DO UPDATE SET
                   scan_status=excluded.scan_status,
                   scanned_at=excluded.scanned_at,
                   completed_at=excluded.completed_at,
                   candidate_count=excluded.candidate_count,
                   finding_count=excluded.finding_count,
                   note=excluded.note,
                   run_mode=excluded.run_mode,
                   extractor_version=excluded.extractor_version,
                   ai_metered=excluded.ai_metered,
                   ai_provider=excluded.ai_provider,
                   ai_model=excluded.ai_model,
                   ai_request_count=excluded.ai_request_count,
                   ai_prompt_tokens=excluded.ai_prompt_tokens,
                   ai_completion_tokens=excluded.ai_completion_tokens,
                   ai_total_tokens=excluded.ai_total_tokens,
                   ai_cache_hit_tokens=excluded.ai_cache_hit_tokens,
                   ai_cache_miss_tokens=excluded.ai_cache_miss_tokens,
                   ai_usage_json=excluded.ai_usage_json""",
            (
                run_date,
                source,
                scan_status,
                scanned_at,
                completed_at,
                int(candidate_count),
                len(findings),
                note,
                normalized_run_mode,
                extractor_version,
                1 if ai_metered else 0,
                usage.get("provider"),
                usage.get("model"),
                _usage_int("request_count"),
                _usage_int("prompt_tokens"),
                _usage_int("completion_tokens"),
                _usage_int("total_tokens"),
                _usage_int("cache_hit_tokens"),
                _usage_int("cache_miss_tokens"),
                json.dumps(usage, ensure_ascii=False, sort_keys=True)
                if ai_metered
                else None,
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
                    run_date,
                    source,
                    item_key,
                    str(row["finding_type"]),
                    str(row["what_happened"]),
                )
            )
            object_name = str(row.get("object_name") or "").strip()
            object_id = ensure_object(
                conn,
                object_name,
                aliases=[str(x) for x in row.get("object_aliases", [])],
            ) if object_name else None
            node_title = str(row.get("node_title") or row.get("title") or "").strip()
            conn.execute(
                """INSERT INTO daily_finding(
                       finding_id,run_date,source,item_key,question_id,title,url,
                       author,observed_at,finding_type,what_happened,
                       ai_understanding,current_judgment,worth_follow_up,
                       evidence_excerpt,created_at,object_id,node_title
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    fid,
                    run_date,
                    source,
                    item_key,
                    row.get("question_id"),
                    str(row["title"]),
                    str(row["url"]),
                    row.get("author"),
                    row.get("observed_at"),
                    str(row["finding_type"]),
                    str(row["what_happened"]),
                    str(row["ai_understanding"]),
                    str(row["current_judgment"]),
                    1 if row.get("worth_follow_up") else 0,
                    row.get("evidence_excerpt"),
                    created_at,
                    object_id,
                    node_title or None,
                ),
            )

            evidences = row.get("evidence")
            if not isinstance(evidences, list) or not evidences:
                evidences = _fallback_evidence(source, row)
            for idx, evidence in enumerate(evidences):
                evidence_row = dict(evidence)
                evidence_row["source"] = str(evidence_row.get("source") or source)
                evidence_row["source_title"] = str(
                    evidence_row.get("source_title") or row.get("title") or ""
                )
                evidence_row["source_url"] = str(
                    evidence_row.get("source_url") or row.get("url") or ""
                )
                evidence_row["locator_url"] = str(
                    evidence_row.get("locator_url")
                    or evidence_row.get("source_url")
                    or ""
                )
                evidence_row["is_primary"] = bool(
                    evidence_row.get("is_primary") or idx == 0
                )
                conn.execute(
                    """INSERT INTO finding_evidence(
                           evidence_id,finding_id,source,author,published_at,
                           source_title,source_url,locator_kind,locator_id,
                           locator_url,excerpt,is_primary,created_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        _evidence_id(fid, evidence_row),
                        fid,
                        evidence_row["source"],
                        evidence_row.get("author"),
                        evidence_row.get("published_at"),
                        evidence_row["source_title"],
                        evidence_row["source_url"],
                        evidence_row.get("locator_kind"),
                        evidence_row.get("locator_id"),
                        evidence_row["locator_url"],
                        evidence_row.get("excerpt"),
                        1 if evidence_row["is_primary"] else 0,
                        created_at,
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
        return {
            "date": None,
            "runs": [],
            "findings": [],
            "finding_count": 0,
            "status": "NO_DATA",
        }

    with connect(db_path) as conn:
        runs = [
            dict(row)
            for row in conn.execute(
                """SELECT run_date,source,scan_status,scanned_at,completed_at,
                          candidate_count,finding_count,note,run_mode,
                          extractor_version,ai_metered,ai_provider,ai_model,
                          ai_request_count,ai_prompt_tokens,ai_completion_tokens,
                          ai_total_tokens,ai_cache_hit_tokens,ai_cache_miss_tokens,
                          ai_usage_json
                   FROM daily_run WHERE run_date=? ORDER BY source""",
                (selected,),
            ).fetchall()
        ]
        for run in runs:
            run["ai_metered"] = bool(run.get("ai_metered"))
            raw_usage = run.get("ai_usage_json")
            if raw_usage:
                try:
                    run["ai_usage"] = json.loads(raw_usage)
                except json.JSONDecodeError:
                    run["ai_usage"] = None
            else:
                run["ai_usage"] = None
        findings = [
            dict(row)
            for row in conn.execute(
                """SELECT f.finding_id,f.run_date,f.source,f.item_key,
                          f.question_id,f.title,f.url,f.author,f.observed_at,
                          f.finding_type,f.what_happened,f.ai_understanding,
                          f.current_judgment,f.worth_follow_up,
                          f.evidence_excerpt,f.object_id,f.node_title,
                          o.object_name
                   FROM daily_finding f
                   LEFT JOIN radar_object o ON o.object_id=f.object_id
                   WHERE f.run_date=?
                   ORDER BY COALESCE(f.observed_at,'') DESC,f.finding_id""",
                (selected,),
            ).fetchall()
        ]
        evidence_rows = [
            dict(row)
            for row in conn.execute(
                """SELECT e.evidence_id,e.finding_id,e.source,e.author,
                          e.published_at,e.source_title,e.source_url,
                          e.locator_kind,e.locator_id,e.locator_url,e.excerpt,
                          e.is_primary
                   FROM finding_evidence e
                   JOIN daily_finding f ON f.finding_id=e.finding_id
                   WHERE f.run_date=?
                   ORDER BY e.finding_id,e.is_primary DESC,
                            COALESCE(e.published_at,''),e.evidence_id""",
                (selected,),
            ).fetchall()
        ]

    evidence_by_finding: dict[str, list[dict[str, Any]]] = {}
    for evidence in evidence_rows:
        evidence["is_primary"] = bool(evidence["is_primary"])
        evidence_by_finding.setdefault(str(evidence["finding_id"]), []).append(evidence)

    for row in findings:
        row["worth_follow_up"] = bool(row["worth_follow_up"])
        row["node_title"] = row.get("node_title") or row.get("title")
        row["evidence"] = evidence_by_finding.get(str(row["finding_id"]), [])

    statuses = {str(row["scan_status"]) for row in runs}
    overall = (
        "NO_DATA"
        if not runs
        else (
            "OK"
            if statuses == {"OK"}
            else ("FAILED" if "FAILED" in statuses else "PARTIAL")
        )
    )
    return {
        "date": selected,
        "runs": runs,
        "findings": findings,
        "finding_count": len(findings),
        "status": overall,
    }


def write_daily_archive(data_root: Path, run_date: str) -> Path:
    db_path = radar_db_path(data_root)
    view = get_daily_view(db_path, run_date)
    objects: dict[str, dict[str, str]] = {}
    for row in view["findings"]:
        object_id = row.get("object_id")
        object_name = row.get("object_name")
        if object_id and object_name:
            objects[str(object_id)] = {
                "object_id": str(object_id),
                "object_name": str(object_name),
            }

    payload = {
        "archive_schema_version": ARCHIVE_SCHEMA_VERSION,
        "radar_schema_version": SCHEMA_VERSION,
        "exported_at": now_utc(),
        "date": run_date,
        "status": view["status"],
        "runs": view["runs"],
        "objects": sorted(objects.values(), key=lambda item: item["object_id"]),
        "nodes": view["findings"],
    }
    path = radar_archive_path(data_root, run_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return path
