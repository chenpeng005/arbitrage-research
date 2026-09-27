"""JSON Runtime ↔ SQLite shadow parity audit."""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any

from runtime.opportunity.incremental_storage import connect


PARITY_VERSION = "incremental-storage-parity-v1"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _num_equal(a: Any, b: Any, tol: float = 1e-9) -> bool:
    if a is None and b is None:
        return True
    try:
        return math.isclose(float(a), float(b), rel_tol=tol, abs_tol=tol)
    except (TypeError, ValueError):
        return a == b


def audit_json_sqlite_parity(
    *,
    data_root: Path,
    target_db: Path,
    max_mismatch_samples: int = 30,
) -> dict[str, Any]:
    data_root = data_root.resolve()
    target_db = target_db.resolve()

    latest = _read_json(data_root / "registry" / "latest_economic_path_registry.json")
    registry = _read_json(Path(latest["result_path"]))
    trigger = _read_json(data_root / "registry" / "research_trigger_state.json")
    ledger = _read_json(data_root / "registry" / "research_ledger.json")
    market = _read_json(
        data_root / "runs" / registry["market_run_id"] / "discovery_market_input.json"
    )

    conn = connect(target_db)
    mismatches: list[dict[str, Any]] = []

    def add(kind: str, **payload: Any) -> None:
        if len(mismatches) < max_mismatch_samples:
            mismatches.append({"kind": kind, **payload})

    try:
        schema = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        schema_version = schema["value"] if schema else None
        if schema_version != "incremental-runtime-sqlite-schema-v1.3":
            add(
                "SCHEMA_VERSION",
                expected="incremental-runtime-sqlite-schema-v1.3",
                actual=schema_version,
            )

        snapshot_id = registry["market_snapshot_id"]
        snapshot = conn.execute(
            "SELECT * FROM market_snapshot WHERE snapshot_id=?",
            (snapshot_id,),
        ).fetchone()
        if snapshot is None:
            add("MISSING_MARKET_SNAPSHOT", snapshot_id=snapshot_id)

        db_market = {
            row["bond_code"]: dict(row)
            for row in conn.execute(
                "SELECT * FROM market_observation WHERE snapshot_id=?",
                (snapshot_id,),
            )
        }
        for row in market.get("rows", []):
            code = str(row["bond_code"]).zfill(6)
            db = db_market.get(code)
            if db is None:
                add("MISSING_MARKET_OBSERVATION", bond_code=code)
                continue
            for json_key, db_key in [
                ("current_bond_price", "bond_price"),
                ("current_stock_price", "stock_price"),
                ("current_conversion_price", "conversion_price"),
                ("current_conversion_value", "conversion_value"),
                ("remaining_months", "remaining_months"),
                ("remaining_size", "remaining_size"),
            ]:
                if not _num_equal(row.get(json_key), db.get(db_key)):
                    add(
                        "MARKET_VALUE",
                        bond_code=code,
                        field=json_key,
                        expected=row.get(json_key),
                        actual=db.get(db_key),
                    )

        registry_bonds = {
            str(row["bond_code"]).zfill(6): row
            for row in registry.get("bonds", [])
        }
        db_states = {
            f"{row['bond_code']}:{row['scope_id']}": dict(row)
            for row in conn.execute(
                "SELECT * FROM scope_state_current WHERE scope_type='PATH'"
            )
        }
        for key, trigger_row in trigger.get("paths", {}).items():
            code = str(trigger_row["bond_code"]).zfill(6)
            path_id = str(trigger_row["path_id"])
            db = db_states.get(f"{code}:{path_id}")
            if db is None:
                add("MISSING_PATH_STATE", bond_code=code, path_id=path_id)
                continue
            expected_economic = registry_bonds[code]["paths"][path_id].get(
                "economic_status"
            )
            if db["economic_status"] != expected_economic:
                add(
                    "ECONOMIC_STATUS",
                    bond_code=code,
                    path_id=path_id,
                    expected=expected_economic,
                    actual=db["economic_status"],
                )
            if db["source_snapshot_id"] != snapshot_id:
                add(
                    "STATE_SNAPSHOT",
                    bond_code=code,
                    path_id=path_id,
                    expected=snapshot_id,
                    actual=db["source_snapshot_id"],
                )

        current_bindings = {
            f"{row['bond_code']}:{row['path_id']}": dict(row)
            for row in conn.execute(
                """SELECT bond_code,path_id,result_id,validity_status
                   FROM research_binding WHERE is_current=1"""
            )
        }
        for row in ledger.get("results", {}).values():
            key = f"{str(row['bond_code']).zfill(6)}:{row['path_id']}"
            db = current_bindings.get(key)
            if db is None:
                add(
                    "MISSING_RESEARCH_BINDING",
                    bond_code=row["bond_code"],
                    path_id=row["path_id"],
                )
                continue
            if db["result_id"] != row["path_result_id"]:
                add(
                    "RESEARCH_RESULT_BINDING",
                    bond_code=row["bond_code"],
                    path_id=row["path_id"],
                    expected=row["path_result_id"],
                    actual=db["result_id"],
                )

        metrics = {
            "json_market_rows": len(market.get("rows", [])),
            "sqlite_market_rows": len(db_market),
            "json_path_states": len(trigger.get("paths", {})),
            "sqlite_path_states": len(db_states),
            "json_research_results": len(ledger.get("results", {})),
            "sqlite_current_bindings": len(current_bindings),
            "json_opportunity_bonds": int(registry.get("bonds_with_any_keep", 0)),
            "sqlite_opportunity_bonds": conn.execute(
                """SELECT COUNT(DISTINCT bond_code) n
                   FROM scope_state_current
                   WHERE scope_type='PATH' AND economic_status='KEEP'"""
            ).fetchone()["n"],
            "json_keep_paths": sum(
                1
                for bond in registry.get("bonds", [])
                for path in bond.get("paths", {}).values()
                if path.get("economic_status") == "KEEP"
            ),
            "sqlite_keep_paths": conn.execute(
                """SELECT COUNT(*) n FROM scope_state_current
                   WHERE scope_type='PATH' AND economic_status='KEEP'"""
            ).fetchone()["n"],
        }

        for left, right, label in [
            ("json_market_rows", "sqlite_market_rows", "MARKET_ROW_COUNT"),
            ("json_path_states", "sqlite_path_states", "PATH_STATE_COUNT"),
            ("json_research_results", "sqlite_current_bindings", "RESEARCH_BINDING_COUNT"),
            ("json_opportunity_bonds", "sqlite_opportunity_bonds", "OPPORTUNITY_BOND_COUNT"),
            ("json_keep_paths", "sqlite_keep_paths", "KEEP_PATH_COUNT"),
        ]:
            if metrics[left] != metrics[right]:
                add(label, expected=metrics[left], actual=metrics[right])

        fk_issues = [dict(row) for row in conn.execute("PRAGMA foreign_key_check")]
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if fk_issues:
            add("FOREIGN_KEY_CHECK", issues=fk_issues[:5])
        if integrity != "ok":
            add("INTEGRITY_CHECK", actual=integrity)

        return {
            "parity_version": PARITY_VERSION,
            "status": "PASS" if not mismatches else "FAIL",
            "schema_version": schema_version,
            "market_snapshot_id": snapshot_id,
            "market_cutoff": registry["market_cutoff"],
            "metrics": metrics,
            "mismatch_count": len(mismatches),
            "mismatch_samples": mismatches,
            "foreign_key_issues": fk_issues,
            "integrity_check": integrity,
        }
    finally:
        conn.close()

[executed on device: iZ2vc3972s0n20m9kq0ns4Z (b3130143-0d28-448b-8a4c-d5f1482304ab)]