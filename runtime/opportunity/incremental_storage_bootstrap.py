"""Bootstrap current JSON Runtime state into Formal SQLite Schema V1.

This is a one-time baseline importer. It does not invent historical Events.
Event/Evidence/Change/Notification tables start from the formal incremental
activation boundary unless explicit audited history is migrated later.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from runtime.opportunity.incremental_storage import (
    SCHEMA_VERSION,
    connect,
    initialize_schema,
    json_text,
)


BOOTSTRAP_VERSION = "incremental-storage-bootstrap-v1"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _stable_hash(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _judgment_signature(result: dict[str, Any]) -> str:
    return _stable_hash(
        {
            "summary": result.get("summary"),
            "judgments": result.get("judgments"),
            "key_risks": result.get("key_risks"),
            "failure_conditions": result.get("failure_conditions"),
            "unknown_b": result.get("unknown_b"),
        }
    )


def bootstrap_current_runtime(
    *,
    data_root: Path,
    target_db: Path,
    overwrite: bool = False,
) -> dict[str, Any]:
    data_root = data_root.resolve()
    target_db = target_db.resolve()

    if target_db.exists():
        if not overwrite:
            raise FileExistsError(f"target db already exists: {target_db}")
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(target_db) + suffix)
            if p.exists():
                p.unlink()

    initialize_schema(target_db)
    conn = connect(target_db)
    try:
        latest_registry = _read_json(
            data_root / "registry" / "latest_economic_path_registry.json"
        )
        economic_registry_path = Path(latest_registry["result_path"])
        registry = _read_json(economic_registry_path)

        market_input_path = (
            data_root
            / "runs"
            / registry["market_run_id"]
            / "discovery_market_input.json"
        )
        market_input = _read_json(market_input_path)

        trigger_state = _read_json(
            data_root / "registry" / "research_trigger_state.json"
        )
        research_ledger = _read_json(
            data_root / "registry" / "research_ledger.json"
        )

        if trigger_state.get("market_snapshot_id") != registry.get(
            "market_snapshot_id"
        ):
            raise RuntimeError(
                "trigger state market snapshot does not match economic registry"
            )

        started_at = registry.get("completed_at") or registry.get(
            "started_at"
        ) or latest_registry.get("market_cutoff")

        # ---------------- bond + market baseline ----------------
        market_rows = market_input.get("rows", [])
        bond_names: dict[str, tuple[str, str | None]] = {}
        for row in market_rows:
            code = str(row["bond_code"]).zfill(6)
            bond_names[code] = (
                str(row.get("bond_name") or code),
                str(row.get("stock_code") or "") or None,
            )

        conn.executemany(
            """INSERT INTO bond_master
               (bond_code,bond_name,stock_code,active,first_seen_at,updated_at)
               VALUES (?,?,?,?,?,?)""",
            [
                (
                    code,
                    name,
                    stock_code,
                    1,
                    str(registry["market_cutoff"]),
                    str(registry["market_cutoff"]),
                )
                for code, (name, stock_code) in sorted(bond_names.items())
            ],
        )

        conn.execute(
            """INSERT INTO market_snapshot
               (snapshot_id,market_cutoff,source_run_id,observed_at,status)
               VALUES (?,?,?,?,?)""",
            (
                registry["market_snapshot_id"],
                registry["market_cutoff"],
                registry["market_run_id"],
                str(started_at),
                "FORMAL_BOOTSTRAP",
            ),
        )

        conn.executemany(
            """INSERT INTO market_observation
               (snapshot_id,bond_code,bond_price,stock_price,conversion_price,
                conversion_value,remaining_months,remaining_size,payload_json)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            [
                (
                    registry["market_snapshot_id"],
                    str(row["bond_code"]).zfill(6),
                    row.get("current_bond_price"),
                    row.get("current_stock_price"),
                    row.get("current_conversion_price"),
                    row.get("current_conversion_value"),
                    row.get("remaining_months"),
                    row.get("remaining_size"),
                    json_text(row),
                )
                for row in market_rows
            ],
        )

        # ---------------- current Path state baseline ----------------
        registry_bonds = {
            str(row["bond_code"]).zfill(6): row
            for row in registry.get("bonds", [])
        }
        state_rows = []
        for key, trigger_row in sorted(
            trigger_state.get("paths", {}).items()
        ):
            code = str(trigger_row["bond_code"]).zfill(6)
            path_id = str(trigger_row["path_id"])
            economic_path = registry_bonds[code]["paths"][path_id]

            payload = {
                "bootstrap_version": BOOTSTRAP_VERSION,
                "economic_path": economic_path,
                "trigger_state": trigger_row,
            }
            state_code = trigger_row.get("current_event_state")
            if state_code is None:
                state_code = economic_path.get("reason")

            state_rows.append(
                (
                    code,
                    "PATH",
                    path_id,
                    economic_path.get("economic_status"),
                    state_code,
                    1,
                    _stable_hash(payload),
                    registry["market_snapshot_id"],
                    None,
                    json_text(payload),
                    trigger_row.get("updated_at")
                    or str(registry["market_cutoff"]),
                )
            )

        conn.executemany(
            """INSERT INTO scope_state_current
               (bond_code,scope_type,scope_id,economic_status,state_code,
                state_version,state_hash,source_snapshot_id,source_event_update_id,
                payload_json,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            state_rows,
        )

        # ---------------- immutable research baseline ----------------
        result_rows = []
        binding_rows = []
        for trigger_key, ledger_row in sorted(
            research_ledger.get("results", {}).items()
        ):
            result_path = Path(ledger_row["result_path"])
            result = _read_json(result_path)
            task_path = (
                data_root
                / "research_tasks"
                / f"{ledger_row['task_id']}.json"
            )
            task = _read_json(task_path) if task_path.exists() else {}

            result_id = str(ledger_row["path_result_id"])
            code = str(ledger_row["bond_code"]).zfill(6)
            path_id = str(ledger_row["path_id"])
            review_ready = bool(ledger_row.get("review_ready"))
            research_status = str(ledger_row.get("research_status") or "")

            result_rows.append(
                (
                    result_id,
                    code,
                    path_id,
                    ledger_row["task_id"],
                    trigger_key,
                    "V2",
                    result.get("research_cutoff")
                    or registry["market_cutoff"],
                    1 if review_ready else 0,
                    research_status,
                    str(result_path),
                    _sha256_file(result_path),
                    _judgment_signature(result),
                    None,
                    result.get("knowledge_commit_sha")
                    or task.get("knowledge_commit_sha"),
                    ledger_row.get("completed_at")
                    or result.get("research_cutoff")
                    or registry["market_cutoff"],
                )
            )

            validity_status = (
                "VALID"
                if review_ready and research_status == "COMPLETED"
                else "UPDATE_PENDING"
            )
            binding_rows.append(
                (
                    f"bootstrap:{code}:{path_id}:{result_id}",
                    code,
                    path_id,
                    result_id,
                    "ORIGINAL",
                    validity_status,
                    None,
                    None,
                    None,
                    1,
                    json_text(
                        {
                            "bootstrap_version": BOOTSTRAP_VERSION,
                            "baseline_market_snapshot_id": registry[
                                "market_snapshot_id"
                            ],
                            "event_watermark_status": "UNKNOWN_PRE_EVENT_LEDGER",
                            "note": (
                                "Formal V2 baseline imported from JSON Runtime. "
                                "Future automatic REUSE requires incremental "
                                "Event watermarks to be established."
                            ),
                        }
                    ),
                    ledger_row.get("completed_at")
                    or registry["market_cutoff"],
                    None,
                    1,
                )
            )

        conn.executemany(
            """INSERT INTO research_result_index
               (result_id,bond_code,path_id,task_id,trigger_key,result_version,
                research_cutoff,review_ready,research_status,artifact_path,
                artifact_sha256,judgment_signature,confidence,canonical_commit_sha,
                created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            result_rows,
        )

        conn.executemany(
            """INSERT INTO research_binding
               (binding_id,bond_code,path_id,result_id,binding_type,validity_status,
                reuse_reason,checked_event_watermark_hash,
                checked_candidate_watermark_hash,checked_state_version,
                validity_basis_json,bound_at,superseded_at,is_current)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            binding_rows,
        )

        # Pre-incremental Trigger history is not backfilled into the event-driven
        # trigger ledger, because it does not have Event Update identity.
        # Event/Evidence/Change/Notification history is likewise not fabricated.

        conn.executemany(
            "INSERT OR REPLACE INTO schema_meta(key,value) VALUES (?,?)",
            [
                ("schema_version", SCHEMA_VERSION),
                ("bootstrap_version", BOOTSTRAP_VERSION),
                (
                    "baseline_market_snapshot_id",
                    str(registry["market_snapshot_id"]),
                ),
                ("baseline_market_cutoff", str(registry["market_cutoff"])),
                (
                    "legacy_economic_registry_path",
                    str(economic_registry_path),
                ),
                (
                    "legacy_research_ledger_path",
                    str(
                        data_root
                        / "registry"
                        / "research_ledger.json"
                    ),
                ),
            ],
        )

        conn.commit()

        row_counts = {
            table: conn.execute(
                f"SELECT COUNT(*) AS n FROM {table}"
            ).fetchone()["n"]
            for table in [
                "bond_master",
                "market_snapshot",
                "market_observation",
                "scope_state_current",
                "evidence_document",
                "event_family",
                "event_update",
                "event_scope_impact",
                "research_result_index",
                "research_binding",
                "research_trigger_ledger",
                "change_ledger",
                "notification_group",
            ]
        }

        expected = {
            "bond_master": len(market_rows),
            "market_snapshot": 1,
            "market_observation": len(market_rows),
            "scope_state_current": len(
                trigger_state.get("paths", {})
            ),
            "research_result_index": len(
                research_ledger.get("results", {})
            ),
            "research_binding": len(
                research_ledger.get("results", {})
            ),
        }
        mismatches = {
            key: {"expected": value, "actual": row_counts[key]}
            for key, value in expected.items()
            if row_counts[key] != value
        }

        fk_issues = [
            dict(row) for row in conn.execute("PRAGMA foreign_key_check")
        ]
        integrity = conn.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]

        completed = conn.execute(
            """SELECT COUNT(*) AS n
               FROM research_binding
               WHERE validity_status='VALID' AND is_current=1"""
        ).fetchone()["n"]
        pending = conn.execute(
            """SELECT COUNT(*) AS n
               FROM research_binding
               WHERE validity_status='UPDATE_PENDING' AND is_current=1"""
        ).fetchone()["n"]

        keep_paths = conn.execute(
            """SELECT COUNT(*) AS n
               FROM scope_state_current
               WHERE scope_type='PATH' AND economic_status='KEEP'"""
        ).fetchone()["n"]

        status = (
            "PASS"
            if not mismatches
            and not fk_issues
            and integrity == "ok"
            else "FAIL"
        )

        return {
            "bootstrap_version": BOOTSTRAP_VERSION,
            "schema_version": SCHEMA_VERSION,
            "status": status,
            "target_db": str(target_db),
            "baseline_market_snapshot_id": registry[
                "market_snapshot_id"
            ],
            "baseline_market_cutoff": registry["market_cutoff"],
            "row_counts": row_counts,
            "expected_counts": expected,
            "count_mismatches": mismatches,
            "research_binding_summary": {
                "VALID": completed,
                "UPDATE_PENDING": pending,
            },
            "economic_keep_paths": keep_paths,
            "foreign_key_issues": fk_issues,
            "integrity_check": integrity,
            "historical_backfill_policy": {
                "event_history": "NOT_FABRICATED",
                "evidence_history": "NOT_FABRICATED",
                "change_history": "NOT_FABRICATED",
                "notification_history": "NOT_FABRICATED",
                "event_driven_trigger_history": "NOT_FABRICATED",
            },
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

[executed on device: iZ2vc3972s0n20m9kq0ns4Z (b3130143-0d28-448b-8a4c-d5f1482304ab)]