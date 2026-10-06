from __future__ import annotations

import hashlib
import sqlite3
import unicodedata
from pathlib import Path
from typing import Any

from runtime.intelligence_radar.storage import connect, init_db, now_utc

DEFAULT_ANALYSIS_VERSION = "radar-broad-increment-v1"
CAPSULE_VERSION = "final-node-capsule-v1"
CAPSULE_MAX_CHARS = 2200


def content_hash(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(text or ""))
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _source_item(
    source: str,
    candidate: dict[str, Any],
    segment: dict[str, Any],
) -> dict[str, Any] | None:
    text = str(segment.get("text") or "").strip()
    if not text:
        return None
    item_type = str(segment.get("kind") or "ITEM").upper()
    item_id = str(segment.get("locator_id") or segment.get("segment_id") or "").strip()
    if not item_id:
        return None
    return {
        "source": source,
        "item_type": item_type,
        "item_id": item_id,
        "parent_id": str(candidate.get("question_id") or "") or None,
        "published_at": segment.get("published_at"),
        "content_hash": content_hash(text),
    }


def _register_seen(
    conn: sqlite3.Connection,
    item: dict[str, Any],
    seen_at: str,
) -> None:
    conn.execute(
        """INSERT INTO source_item_version(
               source,item_type,item_id,content_hash,parent_id,published_at,
               first_seen_at,last_seen_at
           ) VALUES(?,?,?,?,?,?,?,?)
           ON CONFLICT(source,item_type,item_id,content_hash) DO UPDATE SET
               parent_id=COALESCE(excluded.parent_id,source_item_version.parent_id),
               published_at=COALESCE(excluded.published_at,source_item_version.published_at),
               last_seen_at=excluded.last_seen_at""",
        (
            item["source"],
            item["item_type"],
            item["item_id"],
            item["content_hash"],
            item.get("parent_id"),
            item.get("published_at"),
            seen_at,
            seen_at,
        ),
    )


def _already_analyzed(
    conn: sqlite3.Connection,
    item: dict[str, Any],
    analysis_version: str,
) -> bool:
    row = conn.execute(
        """SELECT 1 FROM source_item_analysis
           WHERE source=? AND item_type=? AND item_id=? AND content_hash=?
             AND analysis_version=?""",
        (
            item["source"],
            item["item_type"],
            item["item_id"],
            item["content_hash"],
            analysis_version,
        ),
    ).fetchone()
    return row is not None


def prepare_incremental_candidates(
    db_path: Path,
    *,
    source: str,
    candidates: list[dict[str, Any]],
    analysis_version: str = DEFAULT_ANALYSIS_VERSION,
) -> dict[str, Any]:
    """Register observed item versions and keep only AI-unprocessed daily increments."""
    init_db(db_path)
    seen_at = now_utc()
    selected: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    observed_versions = 0
    new_versions = 0
    skipped_versions = 0
    capsule_hits = 0

    with connect(db_path) as conn:
        for candidate in candidates:
            observed_keys: set[tuple[str, str, str]] = set()
            # Register stable context versions too. They are not automatically treated
            # as daily increments, but version history lets future adapters detect edits.
            for segment in candidate.get("context_segments", []):
                item = _source_item(source, candidate, segment)
                if item is not None:
                    _register_seen(conn, item, seen_at)
                    key = (item["item_type"], item["item_id"], item["content_hash"])
                    if key not in observed_keys:
                        observed_versions += 1
                        observed_keys.add(key)

            new_daily: list[dict[str, Any]] = []
            candidate_refs: list[dict[str, Any]] = []
            for segment in candidate.get("daily_segments", []):
                item = _source_item(source, candidate, segment)
                if item is None:
                    continue
                _register_seen(conn, item, seen_at)
                key = (item["item_type"], item["item_id"], item["content_hash"])
                if key not in observed_keys:
                    observed_versions += 1
                    observed_keys.add(key)
                if _already_analyzed(conn, item, analysis_version):
                    skipped_versions += 1
                    continue
                new_daily.append(segment)
                candidate_refs.append(item)
                new_versions += 1

            if not new_daily:
                continue

            row = dict(candidate)
            row["daily_segments"] = new_daily
            capsule = conn.execute(
                """SELECT capsule_text,capsule_version,updated_at
                   FROM thread_context_capsule
                   WHERE source=? AND thread_id=?""",
                (source, str(candidate.get("question_id") or "")),
            ).fetchone()
            if capsule is not None and str(capsule["capsule_text"] or "").strip():
                row["context_capsule"] = str(capsule["capsule_text"])
                row["context_capsule_version"] = str(capsule["capsule_version"])
                capsule_hits += 1
            selected.append(row)
            refs.extend(candidate_refs)
        conn.commit()

    return {
        "candidates": selected,
        "item_refs": refs,
        "analysis_version": analysis_version,
        "observed_version_count": observed_versions,
        "new_version_count": new_versions,
        "skipped_version_count": skipped_versions,
        "ai_candidate_count": len(selected),
        "capsule_hit_count": capsule_hits,
    }


def mark_item_versions_analyzed(
    db_path: Path,
    *,
    item_refs: list[dict[str, Any]],
    analysis_version: str,
) -> int:
    if not item_refs:
        return 0
    init_db(db_path)
    analyzed_at = now_utc()
    inserted = 0
    with connect(db_path) as conn:
        for item in item_refs:
            before = conn.total_changes
            conn.execute(
                """INSERT OR IGNORE INTO source_item_analysis(
                       source,item_type,item_id,content_hash,analysis_version,analyzed_at
                   ) VALUES(?,?,?,?,?,?)""",
                (
                    item["source"],
                    item["item_type"],
                    item["item_id"],
                    item["content_hash"],
                    analysis_version,
                    analyzed_at,
                ),
            )
            if conn.total_changes > before:
                inserted += 1
        conn.commit()
    return inserted


def refresh_thread_capsules(
    db_path: Path,
    *,
    source: str,
    thread_ids: list[str],
) -> int:
    """Build cheap reusable context from retained Final nodes; no extra AI call."""
    unique_ids = sorted({str(x) for x in thread_ids if str(x).strip()})
    if not unique_ids:
        return 0
    init_db(db_path)
    updated = 0
    with connect(db_path) as conn:
        for thread_id in unique_ids:
            rows = conn.execute(
                """SELECT f.run_date,f.title,f.node_title,f.what_happened,
                          f.ai_understanding,f.current_judgment,o.object_name
                   FROM daily_finding f
                   LEFT JOIN radar_object o ON o.object_id=f.object_id
                   WHERE f.source=? AND f.question_id=?
                   ORDER BY f.run_date DESC,COALESCE(f.observed_at,'') DESC
                   LIMIT 6""",
                (source, thread_id),
            ).fetchall()
            if not rows:
                continue
            ordered = list(reversed(rows))
            title = str(rows[0]["title"] or "")
            lines = [f"主题：{title}", "已保留的重要历史节点："]
            for row in ordered:
                object_name = str(row["object_name"] or "对象")
                node_title = str(row["node_title"] or "")
                what = str(row["what_happened"] or "")
                judgment = str(row["current_judgment"] or "")
                line = f"- {row['run_date']}｜{object_name}｜{node_title}：{what}"
                if judgment:
                    line += f"；当前判断：{judgment}"
                lines.append(line)
            capsule = "\n".join(lines)
            if len(capsule) > CAPSULE_MAX_CHARS:
                capsule = capsule[-CAPSULE_MAX_CHARS:]
                capsule = "历史节点摘要（截取最近部分）：\n" + capsule
            capsule_digest = content_hash(capsule)
            timestamp = now_utc()
            conn.execute(
                """INSERT INTO thread_context_capsule(
                       source,thread_id,title,capsule_text,capsule_hash,
                       capsule_version,derived_from_count,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(source,thread_id) DO UPDATE SET
                       title=excluded.title,
                       capsule_text=excluded.capsule_text,
                       capsule_hash=excluded.capsule_hash,
                       capsule_version=excluded.capsule_version,
                       derived_from_count=excluded.derived_from_count,
                       updated_at=excluded.updated_at""",
                (
                    source,
                    thread_id,
                    title,
                    capsule,
                    capsule_digest,
                    CAPSULE_VERSION,
                    len(rows),
                    timestamp,
                    timestamp,
                ),
            )
            updated += 1
        conn.commit()
    return updated
