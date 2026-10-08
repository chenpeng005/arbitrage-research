from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from runtime.opportunity.incremental_storage import connect, initialize_schema
from runtime.opportunity.incremental_storage_parity import audit_json_sqlite_parity


class PendingRetainedBindingParityTest(unittest.TestCase):
    def _fixture(self, research_status: str | None) -> tuple[Path, Path]:
        tmp = Path(tempfile.mkdtemp(prefix="pending-binding-parity-"))
        data_root = tmp / "runtime_data"
        (data_root / "registry").mkdir(parents=True)
        (data_root / "runs" / "market_run").mkdir(parents=True)
        (data_root / "runs" / "discovery_run").mkdir(parents=True)

        snapshot_id = "market-map-test-20261008"
        market_cutoff = "2026-10-08"
        code = "113707"
        path_id = "DOWNWARD_REVISION"
        state_key = f"{code}:{path_id}"
        old_trigger = "REVISION_EVENT_NEAR_TRIGGER:old"
        pending_trigger = "REVISION_EVENT_CONDITION_MET:new"
        old_result = "pr_old"

        market = {
            "rows": [
                {
                    "bond_code": code,
                    "bond_name": "科博转债",
                    "stock_code": "603290",
                    "current_bond_price": 119.0,
                    "current_stock_price": 40.0,
                    "current_conversion_price": 45.56,
                    "current_conversion_value": 87.8,
                    "remaining_months": 60.0,
                    "remaining_size": 5.0,
                }
            ]
        }
        (data_root / "runs" / "market_run" / "discovery_market_input.json").write_text(
            json.dumps(market, ensure_ascii=False), encoding="utf-8"
        )

        registry = {
            "run_id": "discovery_run",
            "market_run_id": "market_run",
            "market_snapshot_id": snapshot_id,
            "market_cutoff": market_cutoff,
            "bonds_with_any_keep": 1,
            "bonds": [
                {
                    "bond_code": code,
                    "bond_name": "科博转债",
                    "paths": {
                        path_id: {
                            "economic_status": "KEEP",
                            "reason": "POSITIVE_REVISION_ECONOMIC_SPACE",
                        }
                    },
                }
            ],
        }
        registry_path = (
            data_root / "runs" / "discovery_run" / "economic_path_registry.json"
        )
        registry_path.write_text(
            json.dumps(registry, ensure_ascii=False), encoding="utf-8"
        )
        (data_root / "registry" / "latest_economic_path_registry.json").write_text(
            json.dumps(
                {
                    "result_path": str(registry_path),
                    "market_cutoff": market_cutoff,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        trigger = {
            "market_snapshot_id": snapshot_id,
            "paths": {
                state_key: {
                    "bond_code": code,
                    "bond_name": "科博转债",
                    "path_id": path_id,
                    "economic_status": "KEEP",
                    "current_event_state": "满足条件",
                    "current_conversion_price": 45.56,
                    "last_trigger_key": pending_trigger,
                    "last_trigger_reason": "REVISION_EVENT_CONDITION_MET",
                    "last_trigger_source": "STATE_TRIGGER",
                    "research_status": research_status,
                    "last_path_result_id": old_result,
                }
            },
        }
        (data_root / "registry" / "research_trigger_state.json").write_text(
            json.dumps(trigger, ensure_ascii=False), encoding="utf-8"
        )
        (data_root / "registry" / "research_ledger.json").write_text(
            json.dumps(
                {
                    "results": {
                        old_trigger: {
                            "bond_code": code,
                            "path_id": path_id,
                            "path_result_id": old_result,
                        }
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        db_path = data_root / "state.sqlite"
        initialize_schema(db_path)
        conn = connect(db_path)
        try:
            conn.execute(
                """INSERT INTO bond_master
                   (bond_code,bond_name,stock_code,active,first_seen_at,updated_at)
                   VALUES (?,?,?,?,?,?)""",
                (code, "科博转债", "603290", 1, market_cutoff, market_cutoff),
            )
            conn.execute(
                """INSERT INTO market_snapshot
                   (snapshot_id,market_cutoff,source_run_id,observed_at,status)
                   VALUES (?,?,?,?,?)""",
                (snapshot_id, market_cutoff, "market_run", market_cutoff, "FORMAL"),
            )
            conn.execute(
                """INSERT INTO market_observation
                   (snapshot_id,bond_code,market_order,bond_price,stock_price,
                    conversion_price,conversion_value,remaining_months,remaining_size,payload_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    snapshot_id,
                    code,
                    0,
                    119.0,
                    40.0,
                    45.56,
                    87.8,
                    60.0,
                    5.0,
                    "{}",
                ),
            )
            conn.execute(
                """INSERT INTO scope_state_current
                   (bond_code,scope_type,scope_id,economic_status,state_code,
                    state_version,state_hash,scope_order,research_state_version,
                    research_state_hash,source_snapshot_id,source_event_update_id,
                    payload_json,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    code,
                    "PATH",
                    path_id,
                    "KEEP",
                    "满足条件",
                    3,
                    "state-hash",
                    0,
                    3,
                    "research-hash",
                    snapshot_id,
                    None,
                    "{}",
                    market_cutoff,
                ),
            )
            conn.execute(
                """INSERT INTO research_result_index
                   (result_id,bond_code,path_id,task_id,trigger_key,result_version,
                    research_cutoff,review_ready,research_status,artifact_path,
                    artifact_sha256,judgment_signature,confidence,canonical_commit_sha,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    old_result,
                    code,
                    path_id,
                    "task_old",
                    old_trigger,
                    "V2",
                    "2026-09-29",
                    1,
                    "COMPLETED",
                    "/tmp/old.json",
                    None,
                    None,
                    None,
                    "knowledge-test",
                    "2026-09-29",
                ),
            )
            conn.execute(
                """INSERT INTO research_binding
                   (binding_id,bond_code,path_id,result_id,binding_type,validity_status,
                    reuse_reason,checked_event_watermark_hash,checked_candidate_watermark_hash,
                    checked_state_version,checked_research_state_version,validity_basis_json,
                    bound_at,superseded_at,is_current)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    "binding-old",
                    code,
                    path_id,
                    old_result,
                    "ORIGINAL",
                    "VALID",
                    None,
                    None,
                    None,
                    2,
                    2,
                    json.dumps({"source_trigger_key": old_trigger}),
                    "2026-09-29",
                    None,
                    1,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        return data_root, db_path

    def test_pending_trigger_may_retain_previous_binding(self) -> None:
        data_root, db_path = self._fixture("PENDING")
        result = audit_json_sqlite_parity(
            data_root=data_root,
            target_db=db_path,
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["metrics"]["json_research_results"], 0)
        self.assertEqual(result["metrics"]["json_pending_retained_bindings"], 1)
        self.assertEqual(result["metrics"]["json_expected_current_bindings"], 1)
        self.assertEqual(result["metrics"]["sqlite_current_bindings"], 1)

    def test_non_pending_unmatched_binding_still_fails_closed(self) -> None:
        data_root, db_path = self._fixture(None)
        result = audit_json_sqlite_parity(
            data_root=data_root,
            target_db=db_path,
        )
        self.assertEqual(result["status"], "FAIL")
        kinds = {row["kind"] for row in result["mismatch_samples"]}
        self.assertIn("UNEXPECTED_CURRENT_RESEARCH_BINDING", kinds)
        self.assertIn("RESEARCH_BINDING_COUNT", kinds)


if __name__ == "__main__":
    unittest.main()
