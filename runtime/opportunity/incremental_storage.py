"""Formal SQLite Schema V1 for Daily Incremental Runtime.

SQLite owns structured state, identity, relationships, watermarks, idempotency,
change history, and notification delivery state.

Immutable research/evidence artifacts remain files and are referenced by path/hash.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "incremental-runtime-sqlite-schema-v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


SCHEMA_SQL = r"""
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bond_master (
    bond_code TEXT PRIMARY KEY,
    bond_name TEXT NOT NULL,
    stock_code TEXT,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    first_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_snapshot (
    snapshot_id TEXT PRIMARY KEY,
    market_cutoff TEXT NOT NULL,
    source_run_id TEXT,
    observed_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'FORMAL'
);

CREATE TABLE IF NOT EXISTS market_observation (
    snapshot_id TEXT NOT NULL,
    bond_code TEXT NOT NULL,
    bond_price REAL,
    stock_price REAL,
    conversion_price REAL,
    conversion_value REAL,
    remaining_months REAL,
    remaining_size REAL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (snapshot_id, bond_code),
    FOREIGN KEY (snapshot_id) REFERENCES market_snapshot(snapshot_id),
    FOREIGN KEY (bond_code) REFERENCES bond_master(bond_code)
);

CREATE TABLE IF NOT EXISTS scope_state_current (
    bond_code TEXT NOT NULL,
    scope_type TEXT NOT NULL CHECK (scope_type IN ('PATH', 'RISK')),
    scope_id TEXT NOT NULL,
    economic_status TEXT,
    state_code TEXT,
    state_version INTEGER NOT NULL CHECK (state_version >= 1),
    state_hash TEXT NOT NULL,
    source_snapshot_id TEXT,
    source_event_update_id TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (bond_code, scope_type, scope_id),
    FOREIGN KEY (bond_code) REFERENCES bond_master(bond_code),
    FOREIGN KEY (source_snapshot_id) REFERENCES market_snapshot(snapshot_id)
);

CREATE TABLE IF NOT EXISTS evidence_document (
    evidence_id TEXT PRIMARY KEY,
    bond_code TEXT NOT NULL,
    published_at TEXT,
    title TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    url TEXT,
    content_sha256 TEXT,
    artifact_path TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (bond_code) REFERENCES bond_master(bond_code)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_evidence_url
ON evidence_document(url)
WHERE url IS NOT NULL AND url <> '';

CREATE UNIQUE INDEX IF NOT EXISTS uq_evidence_content_hash
ON evidence_document(bond_code, content_sha256)
WHERE content_sha256 IS NOT NULL AND content_sha256 <> '';

CREATE TABLE IF NOT EXISTS event_family (
    event_family_id TEXT PRIMARY KEY,
    bond_code TEXT NOT NULL,
    event_family TEXT NOT NULL,
    latest_confirmed_version INTEGER NOT NULL DEFAULT 0,
    confirmed_watermark TEXT NOT NULL DEFAULT 'EWM_EMPTY',
    semantic_candidate_watermark TEXT NOT NULL DEFAULT 'ECM_EMPTY',
    updated_at TEXT NOT NULL,
    UNIQUE (bond_code, event_family),
    FOREIGN KEY (bond_code) REFERENCES bond_master(bond_code)
);

CREATE TABLE IF NOT EXISTS event_update (
    event_update_id TEXT PRIMARY KEY,
    event_family_id TEXT NOT NULL,
    confirmation_status TEXT NOT NULL
        CHECK (confirmation_status IN ('CONFIRMED', 'SEMANTIC_CANDIDATE', 'REJECTED')),
    event_version INTEGER,
    candidate_version INTEGER,
    occurred_at TEXT,
    materiality_status TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (event_family_id) REFERENCES event_family(event_family_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_event_confirmed_version
ON event_update(event_family_id, event_version)
WHERE confirmation_status = 'CONFIRMED';

CREATE UNIQUE INDEX IF NOT EXISTS uq_event_candidate_version
ON event_update(event_family_id, candidate_version)
WHERE confirmation_status = 'SEMANTIC_CANDIDATE';

CREATE TABLE IF NOT EXISTS event_evidence_link (
    event_update_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    link_role TEXT NOT NULL DEFAULT 'SUPPORTING',
    PRIMARY KEY (event_update_id, evidence_id),
    FOREIGN KEY (event_update_id) REFERENCES event_update(event_update_id),
    FOREIGN KEY (evidence_id) REFERENCES evidence_document(evidence_id)
);

CREATE TABLE IF NOT EXISTS event_scope_impact (
    event_update_id TEXT NOT NULL,
    scope_type TEXT NOT NULL CHECK (scope_type IN ('PATH', 'RISK')),
    scope_id TEXT NOT NULL,
    impact TEXT NOT NULL,
    research_action TEXT NOT NULL,
    notification_hint TEXT NOT NULL,
    reason TEXT NOT NULL,
    route_version TEXT NOT NULL,
    PRIMARY KEY (event_update_id, scope_type, scope_id),
    FOREIGN KEY (event_update_id) REFERENCES event_update(event_update_id)
);

CREATE TABLE IF NOT EXISTS research_result_index (
    result_id TEXT PRIMARY KEY,
    bond_code TEXT NOT NULL,
    path_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    trigger_key TEXT NOT NULL,
    result_version TEXT NOT NULL,
    research_cutoff TEXT NOT NULL,
    review_ready INTEGER NOT NULL CHECK (review_ready IN (0, 1)),
    research_status TEXT NOT NULL,
    artifact_path TEXT NOT NULL,
    artifact_sha256 TEXT,
    judgment_signature TEXT,
    confidence TEXT,
    canonical_commit_sha TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (bond_code) REFERENCES bond_master(bond_code)
);

CREATE INDEX IF NOT EXISTS idx_research_result_path
ON research_result_index(bond_code, path_id, research_cutoff);

CREATE TABLE IF NOT EXISTS research_binding (
    binding_id TEXT PRIMARY KEY,
    bond_code TEXT NOT NULL,
    path_id TEXT NOT NULL,
    result_id TEXT NOT NULL,
    binding_type TEXT NOT NULL CHECK (binding_type IN ('ORIGINAL', 'REUSED')),
    validity_status TEXT NOT NULL
        CHECK (validity_status IN ('VALID', 'STALE', 'UPDATE_PENDING', 'INVALID')),
    reuse_reason TEXT,
    checked_event_watermark_hash TEXT,
    checked_candidate_watermark_hash TEXT,
    checked_state_version INTEGER,
    validity_basis_json TEXT NOT NULL DEFAULT '{}',
    bound_at TEXT NOT NULL,
    superseded_at TEXT,
    is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
    FOREIGN KEY (bond_code) REFERENCES bond_master(bond_code),
    FOREIGN KEY (result_id) REFERENCES research_result_index(result_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_current_research_binding
ON research_binding(bond_code, path_id)
WHERE is_current = 1;

CREATE TABLE IF NOT EXISTS research_trigger_ledger (
    trigger_key TEXT PRIMARY KEY,
    bond_code TEXT NOT NULL,
    path_id TEXT,
    source_event_update_id TEXT,
    research_action TEXT NOT NULL,
    task_kind TEXT NOT NULL,
    status TEXT NOT NULL,
    task_id TEXT,
    emitted_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (bond_code) REFERENCES bond_master(bond_code),
    FOREIGN KEY (source_event_update_id) REFERENCES event_update(event_update_id)
);

CREATE TABLE IF NOT EXISTS change_ledger (
    change_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL CHECK (source_type IN ('MARKET', 'EVENT', 'EVIDENCE', 'RESEARCH', 'CLOCK')),
    source_event_update_id TEXT,
    market_snapshot_id TEXT,
    bond_code TEXT NOT NULL,
    scope_type TEXT NOT NULL CHECK (scope_type IN ('BOND', 'PATH', 'RISK')),
    scope_id TEXT NOT NULL,
    change_type TEXT NOT NULL,
    impact TEXT NOT NULL,
    research_action TEXT,
    notification_level TEXT NOT NULL
        CHECK (notification_level IN ('SILENT', 'DAILY_DIGEST', 'IMMEDIATE')),
    previous_json TEXT,
    current_json TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    detected_at TEXT NOT NULL,
    FOREIGN KEY (bond_code) REFERENCES bond_master(bond_code),
    FOREIGN KEY (source_event_update_id) REFERENCES event_update(event_update_id),
    FOREIGN KEY (market_snapshot_id) REFERENCES market_snapshot(snapshot_id)
);

CREATE INDEX IF NOT EXISTS idx_change_bond_time
ON change_ledger(bond_code, detected_at);

CREATE TABLE IF NOT EXISTS notification_group (
    notification_group_id TEXT PRIMARY KEY,
    group_key TEXT NOT NULL UNIQUE,
    bond_code TEXT NOT NULL,
    level TEXT NOT NULL CHECK (level IN ('DAILY_DIGEST', 'IMMEDIATE')),
    status TEXT NOT NULL
        CHECK (status IN ('PENDING', 'SENT', 'SUPPRESSED', 'FAILED')),
    created_at TEXT NOT NULL,
    sent_at TEXT,
    FOREIGN KEY (bond_code) REFERENCES bond_master(bond_code)
);

CREATE TABLE IF NOT EXISTS notification_change_link (
    notification_group_id TEXT NOT NULL,
    change_id TEXT NOT NULL,
    PRIMARY KEY (notification_group_id, change_id),
    FOREIGN KEY (notification_group_id) REFERENCES notification_group(notification_group_id),
    FOREIGN KEY (change_id) REFERENCES change_ledger(change_id)
);

CREATE TABLE IF NOT EXISTS notification_delivery (
    delivery_id TEXT PRIMARY KEY,
    notification_group_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('PENDING', 'SENT', 'FAILED', 'SKIPPED')),
    attempted_at TEXT,
    sent_at TEXT,
    error_text TEXT,
    FOREIGN KEY (notification_group_id) REFERENCES notification_group(notification_group_id)
);

CREATE INDEX IF NOT EXISTS idx_notification_delivery_group
ON notification_delivery(notification_group_id);
"""


EXPECTED_TABLES = {
    "schema_meta",
    "bond_master",
    "market_snapshot",
    "market_observation",
    "scope_state_current",
    "evidence_document",
    "event_family",
    "event_update",
    "event_evidence_link",
    "event_scope_impact",
    "research_result_index",
    "research_binding",
    "research_trigger_ledger",
    "change_ledger",
    "notification_group",
    "notification_change_link",
    "notification_delivery",
}


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def initialize_schema(path: Path) -> dict[str, Any]:
    conn = connect(path)
    try:
        conn.executescript(SCHEMA_SQL)
        meta = {
            "schema_version": SCHEMA_VERSION,
            "initialized_at": _now(),
        }
        conn.executemany(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES (?, ?)",
            list(meta.items()),
        )
        conn.commit()
        return validate_schema(conn)
    finally:
        conn.close()


def validate_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    missing = sorted(EXPECTED_TABLES - tables)
    fk_enabled = conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "PASS" if not missing and fk_enabled else "FAIL",
        "foreign_keys_enabled": fk_enabled,
        "table_count": len(EXPECTED_TABLES),
        "missing_tables": missing,
        "tables": sorted(EXPECTED_TABLES & tables),
    }


def table_manifest() -> list[dict[str, str]]:
    return [
        {"table": "bond_master", "role": "稳定身份"},
        {"table": "market_snapshot", "role": "市场截面身份"},
        {"table": "market_observation", "role": "每日价格/市场覆盖层"},
        {"table": "scope_state_current", "role": "Path/Risk 当前状态"},
        {"table": "evidence_document", "role": "不可变证据元数据"},
        {"table": "event_family", "role": "业务事件族与双 watermark"},
        {"table": "event_update", "role": "已确认 Event / 语义候选版本"},
        {"table": "event_evidence_link", "role": "Event 与 Evidence 多对多"},
        {"table": "event_scope_impact", "role": "1 Event → N Path/Risk 路由"},
        {"table": "research_result_index", "role": "不可变 V2 Artifact 索引"},
        {"table": "research_binding", "role": "当前 State 绑定 ORIGINAL/REUSED Result"},
        {"table": "research_trigger_ledger", "role": "Event-driven Trigger 幂等"},
        {"table": "change_ledger", "role": "正式变化账本"},
        {"table": "notification_group", "role": "用户侧聚合提醒"},
        {"table": "notification_change_link", "role": "1提醒 ↔ N Changes"},
        {"table": "notification_delivery", "role": "渠道发送状态"},
    ]


def dump_schema_manifest(path: Path) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "database_path": str(path),
        "tables": table_manifest(),
        "storage_rule": {
            "sqlite": "state / identity / relationship / watermark / idempotency / change / notification",
            "artifact": "PDF / TXT / Task JSON / Evidence Pack / immutable V2 Result / audit snapshot",
            "github": "Canonical / Contract / Prompt / Validator / source code",
        },
    }


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)

[executed on device: iZ2vc3972s0n20m9kq0ns4Z (b3130143-0d28-448b-8a4c-d5f1482304ab)]