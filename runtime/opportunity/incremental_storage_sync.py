"""Shadow dual-write mirror from current JSON Runtime into SQLite Schema V1.1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from runtime.opportunity.incremental_storage import connect, json_text
from runtime.opportunity.incremental_storage_bootstrap import (
    _current_state_identity,
    _read_json,
    _research_state_identity,
    _sha256_file,
    _stable_hash,
    _judgment_signature,
)


SYNC_VERSION = "incremental-storage-shadow-sync-v1"

ECONOMIC_ONLY_DROP_REASONS = {
    "MATURITY_CASH": {
        "NO_POSITIVE_CASH_SPREAD",
        "NO_POSITIVE_CASH_SPREAD_UNDER_ALL_BRANCHES",
    },
    "PUT": {
        "NO_POSITIVE_PUT_SPREAD",
    },
    "DOWNWARD_REVISION": {
        "NO_INCREMENTAL_REVISION_VALUE",
        "NO_POSITIVE_ECONOMIC_SPACE_AT_CV100",
        "NO_POSITIVE_ECONOMIC_SPACE",
    },
}


def _is_economic_only_drop(path_id: str, economic_path: dict[str, Any]) -> bool:
    return (
        economic_path.get("economic_status") == "DROP"
        and economic_path.get("reason") in ECONOMIC_ONLY_DROP_REASONS.get(path_id, set())
    )


def _load_current_runtime(data_root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    latest = _read_json(data_root / "registry" / "latest_economic_path_registry.json")
    registry = _read_json(Path(latest["result_path"]))
    market_input = _read_json(
        data_root / "runs" / registry["market_run_id"] / "discovery_market_input.json"
    )
    trigger_state = _read_json(data_root / "registry" / "research_trigger_state.json")
    research_ledger = _read_json(data_root / "registry" / "research_ledger.json")
    return registry, market_input, trigger_state, research_ledger


def _current_identity(
    path_id: str,
    economic_path: dict[str, Any],
    trigger_row: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    research_identity = _research_state_identity(path_id, economic_path, trigger_row)
    current_identity = {
        **_current_state_identity(economic_path, trigger_row),
        "path_state": research_identity,
    }
    return current_identity, research_identity


def shadow_sync_current_runtime(
    *,
    data_root: Path,
    target_db: Path,
) -> dict[str, Any]:
    data_root = data_root.resolve()
    target_db = target_db.resolve()
    if not target_db.exists():
        raise FileNotFoundError(target_db)

    registry, market_input, trigger_state, research_ledger = _load_current_runtime(data_root)
    conn = connect(target_db)

    stats = {
        "bond_upserts": 0,
        "snapshot_inserts": 0,
        "observation_inserts": 0,
        "state_updates": 0,
        "state_version_increments": 0,
        "research_state_version_increments": 0,
        "research_result_inserts": 0,
        "research_binding_updates": 0,
    }

    try:
        schema_version = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        if not schema_version or schema_version["value"] != "incremental-runtime-sqlite-schema-v1.3":
            raise RuntimeError(f"unexpected schema_version={schema_version['value'] if schema_version else None}")

        # Bond + snapshot + observations.
        for row in market_input.get("rows", []):
            code = str(row["bond_code"]).zfill(6)
            conn.execute(
                """INSERT INTO bond_master
                   (bond_code,bond_name,stock_code,active,first_seen_at,updated_at)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(bond_code) DO UPDATE SET
                     bond_name=excluded.bond_name,
                     stock_code=excluded.stock_code,
                     active=1,
                     updated_at=excluded.updated_at""",
                (
                    code,
                    str(row.get("bond_name") or code),
                    str(row.get("stock_code") or "") or None,
                    1,
                    registry["market_cutoff"],
                    registry["market_cutoff"],
                ),
            )
            stats["bond_upserts"] += 1

        before = conn.total_changes
        conn.execute(
            """INSERT OR IGNORE INTO market_snapshot
               (snapshot_id,market_cutoff,source_run_id,observed_at,status)
               VALUES (?,?,?,?,?)""",
            (
                registry["market_snapshot_id"],
                registry["market_cutoff"],
                registry["market_run_id"],
                registry.get("completed_at") or registry["market_cutoff"],
                "FORMAL_SHADOW",
            ),
        )
        stats["snapshot_inserts"] = conn.total_changes - before

        for market_order, row in enumerate(market_input.get("rows", [])):
            before = conn.total_changes
            conn.execute(
                """INSERT OR IGNORE INTO market_observation
                   (snapshot_id,bond_code,market_order,bond_price,stock_price,conversion_price,
                    conversion_value,remaining_months,remaining_size,payload_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    registry["market_snapshot_id"],
                    str(row["bond_code"]).zfill(6),
                    market_order,
                    row.get("current_bond_price"),
                    row.get("current_stock_price"),
                    row.get("current_conversion_price"),
                    row.get("current_conversion_value"),
                    row.get("remaining_months"),
                    row.get("remaining_size"),
                    json_text(row),
                ),
            )
            stats["observation_inserts"] += conn.total_changes - before

        registry_bonds = {
            str(row["bond_code"]).zfill(6): row
            for row in registry.get("bonds", [])
        }
        path_order_by_pair = {
            (str(bond["bond_code"]).zfill(6), path_id): path_order
            for bond in registry.get("bonds", [])
            for path_order, path_id in enumerate((bond.get("paths") or {}).keys())
        }

        for key, trigger_row in sorted(trigger_state.get("paths", {}).items()):
            code = str(trigger_row["bond_code"]).zfill(6)
            path_id = str(trigger_row["path_id"])
            economic_path = registry_bonds[code]["paths"][path_id]
            existing = conn.execute(
                """SELECT state_version,state_hash,research_state_version,research_state_hash,
                          payload_json
                   FROM scope_state_current
                   WHERE bond_code=? AND scope_type='PATH' AND scope_id=?""",
                (code, path_id),
            ).fetchone()

            current_identity, research_identity = _current_identity(
                path_id, economic_path, trigger_row
            )

            # Legacy JSON Trigger clears current_event_state when a Path becomes
            # Economic DROP.  For price/model-only DROP reasons, that is not a
            # research invalidation. Preserve the previous research fingerprint.
            preserved_research_identity = None
            if existing is not None and _is_economic_only_drop(path_id, economic_path):
                try:
                    old_payload = json.loads(existing["payload_json"] or "{}")
                    preserved_research_identity = old_payload.get("research_state_identity")
                except Exception:
                    preserved_research_identity = None
                if preserved_research_identity:
                    research_identity = preserved_research_identity
                    current_identity["path_state"] = research_identity

            new_state_hash = _stable_hash(current_identity)
            new_research_hash = _stable_hash(research_identity)
            state_code = trigger_row.get("current_event_state") or economic_path.get("reason")
            payload = json_text(
                {
                    "sync_version": SYNC_VERSION,
                    "economic_path": economic_path,
                    "trigger_state": trigger_row,
                    "current_state_identity": current_identity,
                    "research_state_identity": research_identity,
                    "research_state_preserved_on_economic_drop": bool(
                        preserved_research_identity
                    ),
                }
            )

            if existing is None:
                conn.execute(
                    """INSERT INTO scope_state_current
                       (bond_code,scope_type,scope_id,economic_status,state_code,
                        state_version,state_hash,scope_order,research_state_version,research_state_hash,
                        source_snapshot_id,source_event_update_id,payload_json,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        code,"PATH",path_id,economic_path.get("economic_status"),state_code,
                        1,new_state_hash,path_order_by_pair[(code, path_id)],1,new_research_hash,
                        registry["market_snapshot_id"],None,payload,
                        trigger_row.get("updated_at") or registry["market_cutoff"],
                    ),
                )
                stats["state_updates"] += 1
                stats["state_version_increments"] += 1
                stats["research_state_version_increments"] += 1
                continue

            state_changed = existing["state_hash"] != new_state_hash
            research_changed = existing["research_state_hash"] != new_research_hash
            state_version = int(existing["state_version"]) + (1 if state_changed else 0)
            research_version = int(existing["research_state_version"]) + (1 if research_changed else 0)

            conn.execute(
                """UPDATE scope_state_current SET
                   economic_status=?,state_code=?,state_version=?,state_hash=?,scope_order=?,
                   research_state_version=?,research_state_hash=?,source_snapshot_id=?,
                   payload_json=?,updated_at=?
                   WHERE bond_code=? AND scope_type='PATH' AND scope_id=?""",
                (
                    economic_path.get("economic_status"),state_code,state_version,new_state_hash,
                    path_order_by_pair[(code, path_id)],research_version,new_research_hash,
                    registry["market_snapshot_id"],payload,
                    trigger_row.get("updated_at") or registry["market_cutoff"],code,path_id,
                ),
            )
            stats["state_updates"] += 1
            stats["state_version_increments"] += 1 if state_changed else 0
            stats["research_state_version_increments"] += 1 if research_changed else 0

        # Mirror immutable research results + current binding.
        for trigger_key, ledger_row in sorted(research_ledger.get("results", {}).items()):
            result_id = str(ledger_row["path_result_id"])
            result_path = Path(ledger_row["result_path"])
            result = _read_json(result_path)
            task_path = data_root / "research_tasks" / f"{ledger_row['task_id']}.json"
            task = _read_json(task_path) if task_path.exists() else {}
            code = str(ledger_row["bond_code"]).zfill(6)
            path_id = str(ledger_row["path_id"])

            if conn.execute(
                "SELECT 1 FROM research_result_index WHERE result_id=?",
                (result_id,),
            ).fetchone() is None:
                conn.execute(
                    """INSERT INTO research_result_index
                       (result_id,bond_code,path_id,task_id,trigger_key,result_version,
                        research_cutoff,review_ready,research_status,artifact_path,
                        artifact_sha256,judgment_signature,confidence,canonical_commit_sha,
                        created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        result_id,code,path_id,ledger_row["task_id"],trigger_key,"V2",
                        result.get("research_cutoff") or registry["market_cutoff"],
                        1 if ledger_row.get("review_ready") else 0,
                        ledger_row.get("research_status") or "",
                        str(result_path),_sha256_file(result_path),_judgment_signature(result),None,
                        result.get("knowledge_commit_sha") or task.get("knowledge_commit_sha"),
                        ledger_row.get("completed_at") or registry["market_cutoff"],
                    ),
                )
                stats["research_result_inserts"] += 1

            current = conn.execute(
                """SELECT binding_id,result_id FROM research_binding
                   WHERE bond_code=? AND path_id=? AND is_current=1""",
                (code,path_id),
            ).fetchone()
            if current and current["result_id"] == result_id:
                continue

            if current:
                conn.execute(
                    "UPDATE research_binding SET is_current=0,superseded_at=? WHERE binding_id=?",
                    (registry["market_cutoff"],current["binding_id"]),
                )

            state_row = conn.execute(
                """SELECT state_version,research_state_version
                   FROM scope_state_current
                   WHERE bond_code=? AND scope_type='PATH' AND scope_id=?""",
                (code,path_id),
            ).fetchone()
            validity = (
                "VALID"
                if ledger_row.get("review_ready") and ledger_row.get("research_status") == "COMPLETED"
                else "UPDATE_PENDING"
            )
            conn.execute(
                """INSERT INTO research_binding
                   (binding_id,bond_code,path_id,result_id,binding_type,validity_status,
                    reuse_reason,checked_event_watermark_hash,checked_candidate_watermark_hash,
                    checked_state_version,checked_research_state_version,validity_basis_json,
                    bound_at,superseded_at,is_current)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    f"shadow:{code}:{path_id}:{result_id}",code,path_id,result_id,"ORIGINAL",
                    validity,None,None,None,
                    state_row["state_version"] if state_row else None,
                    state_row["research_state_version"] if state_row else None,
                    json_text({"sync_version":SYNC_VERSION,"event_watermark_status":"UNKNOWN_PRE_EVENT_LEDGER"}),
                    ledger_row.get("completed_at") or registry["market_cutoff"],None,1,
                ),
            )
            stats["research_binding_updates"] += 1

        conn.commit()

        parity = {
            "opportunity_bonds": conn.execute(
                """SELECT COUNT(DISTINCT bond_code) n FROM scope_state_current
                   WHERE scope_type='PATH' AND economic_status='KEEP'"""
            ).fetchone()["n"],
            "keep_paths": conn.execute(
                """SELECT COUNT(*) n FROM scope_state_current
                   WHERE scope_type='PATH' AND economic_status='KEEP'"""
            ).fetchone()["n"],
            "research_current": conn.execute(
                "SELECT COUNT(*) n FROM research_binding WHERE is_current=1"
            ).fetchone()["n"],
            "valid_research": conn.execute(
                """SELECT COUNT(*) n FROM research_binding
                   WHERE is_current=1 AND validity_status='VALID'"""
            ).fetchone()["n"],
            "update_pending": conn.execute(
                """SELECT COUNT(*) n FROM research_binding
                   WHERE is_current=1 AND validity_status='UPDATE_PENDING'"""
            ).fetchone()["n"],
        }

        return {
            "sync_version": SYNC_VERSION,
            "status": "PASS",
            "market_snapshot_id": registry["market_snapshot_id"],
            "market_cutoff": registry["market_cutoff"],
            "stats": stats,
            "parity": parity,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

[executed on device: iZ2vc3972s0n20m9kq0ns4Z (b3130143-0d28-448b-8a4c-d5f1482304ab)]