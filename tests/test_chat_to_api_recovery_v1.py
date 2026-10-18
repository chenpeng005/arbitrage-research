from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from runtime.opportunity.chat_to_api_recovery import SUPERSEDED_STATUS, recover_chat_to_api
from runtime.opportunity.incremental_storage import connect, initialize_schema


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class ChatToApiRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.data_root = Path(self.temp.name) / "runtime_data"
        self.data_root.mkdir(parents=True)
        write_json(
            self.data_root / "deployment_manifest.json",
            {
                "application_commit_sha": "app-sha",
                "knowledge_commit_sha": "knowledge-sha",
                "release_profile": "convertible",
            },
        )
        initialize_schema(self.data_root / "state" / "incremental_runtime.sqlite")
        conn = connect(self.data_root / "state" / "incremental_runtime.sqlite")
        try:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """INSERT INTO bond_master(
                       bond_code,bond_name,stock_code,active,first_seen_at,updated_at
                   ) VALUES (?,?,?,?,?,?)""",
                ("110001", "测试转债", "600001", 1, now, now),
            )
            conn.execute(
                """INSERT INTO research_trigger_ledger(
                       trigger_key,bond_code,path_id,source_event_update_id,
                       research_action,task_kind,status,task_id,emitted_at,updated_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    "SEM:1", "110001", None, None, "SEMANTIC_AUDIT",
                    "EVENT_SEMANTIC_AUDIT", "WAITING_FOR_CHAT", "event_sem_1",
                    now, now,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        write_json(
            self.data_root / "event_semantic_tasks" / "event_sem_1.json",
            {"task_id": "event_sem_1", "trigger_key": "SEM:1", "bond_code": "110001"},
        )
        semantic_work = self.data_root / "event_semantic_work" / "event_sem_1"
        semantic_work.mkdir(parents=True)
        write_json(
            self.data_root / "chat_tasks" / "chat_sem_1" / "chat_task.json",
            {
                "task_id": "chat_sem_1",
                "task_type": "EVENT_SEMANTIC_AUDIT",
                "execution_mode": "INTERACTIVE_CHAT",
                "status": "WAITING_FOR_CHAT",
                "business_run_dir": str(semantic_work),
                "source_event_semantic_task_id": "event_sem_1",
                "bond_code": "110001",
                "trigger_key": "SEM:1",
                "writeback_mode": "FORMAL",
            },
        )

        write_json(
            self.data_root / "research_tasks" / "research_1.json",
            {
                "task_id": "research_1",
                "trigger_key": "PATH:1",
                "bond_code": "110001",
                "bond_name": "测试转债",
                "path_id": "MATURITY_CASH",
            },
        )
        path_work = self.data_root / "path_research_work" / "research_1"
        path_work.mkdir(parents=True)
        write_json(
            self.data_root / "chat_tasks" / "chat_path_1" / "chat_task.json",
            {
                "task_id": "chat_path_1",
                "task_type": "PATH_RESEARCH",
                "execution_mode": "INTERACTIVE_CHAT",
                "status": "WAITING_FOR_CHAT",
                "business_run_dir": str(path_work),
                "source_research_task_id": "research_1",
                "bond_code": "110001",
                "bond_name": "测试转债",
                "path_id": "MATURITY_CASH",
                "trigger_key": "PATH:1",
                "writeback_mode": "FORMAL",
            },
        )
        write_json(
            self.data_root / "registry" / "pending_research_tasks.json",
            {
                "pending_tasks": [{
                    "bond_code": "110001",
                    "bond_name": "测试转债",
                    "path_id": "MATURITY_CASH",
                    "trigger_key": "PATH:1",
                    "research_status": "WAITING_FOR_CHAT",
                    "chat_task_id": "chat_path_1",
                }]
            },
        )
        write_json(
            self.data_root / "registry" / "research_trigger_state.json",
            {
                "paths": {
                    "110001:MATURITY_CASH": {
                        "last_trigger_key": "PATH:1",
                        "research_status": "WAITING_FOR_CHAT",
                    }
                }
            },
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_dry_run_is_read_only(self) -> None:
        result = recover_chat_to_api(data_root=self.data_root, apply=False)
        self.assertEqual(result["status"], "DRY_RUN")
        self.assertEqual(result["candidate_counts"], {"semantic": 1, "path": 1, "total": 2})
        conn = connect(self.data_root / "state" / "incremental_runtime.sqlite")
        try:
            row = conn.execute("SELECT status FROM research_trigger_ledger WHERE trigger_key='SEM:1'").fetchone()
            self.assertEqual(row["status"], "WAITING_FOR_CHAT")
        finally:
            conn.close()
        pending = json.loads((self.data_root / "registry" / "pending_research_tasks.json").read_text(encoding="utf-8"))
        self.assertEqual(pending["pending_tasks"][0]["research_status"], "WAITING_FOR_CHAT")

    def test_apply_restores_api_runnable_state_and_supersedes_chat(self) -> None:
        result = recover_chat_to_api(
            data_root=self.data_root,
            apply=True,
            expect_semantic=1,
            expect_path=1,
            reason="TEST_SWITCH_TO_API",
        )
        self.assertEqual(result["status"], "APPLIED")
        conn = connect(self.data_root / "state" / "incremental_runtime.sqlite")
        try:
            row = conn.execute("SELECT status,task_id FROM research_trigger_ledger WHERE trigger_key='SEM:1'").fetchone()
            self.assertEqual(row["status"], "PENDING")
            self.assertIsNone(row["task_id"])
        finally:
            conn.close()
        pending = json.loads((self.data_root / "registry" / "pending_research_tasks.json").read_text(encoding="utf-8"))
        self.assertEqual(pending["pending_tasks"][0]["research_status"], "PENDING")
        self.assertNotIn("chat_task_id", pending["pending_tasks"][0])
        state = json.loads((self.data_root / "registry" / "research_trigger_state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["paths"]["110001:MATURITY_CASH"]["research_status"], "PENDING")
        for chat_id in ("chat_sem_1", "chat_path_1"):
            task = json.loads((self.data_root / "chat_tasks" / chat_id / "chat_task.json").read_text(encoding="utf-8"))
            self.assertEqual(task["status"], SUPERSEDED_STATUS)
            self.assertEqual(task["superseded_reason"], "TEST_SWITCH_TO_API")
        self.assertTrue(Path(result["audit_path"]).exists())

    def test_active_claim_and_needs_review_are_skipped(self) -> None:
        write_json(
            self.data_root / "chat_tasks" / "chat_path_1" / "claim.json",
            {
                "task_id": "chat_path_1",
                "claim_id": "claim-1",
                "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
            },
        )
        conn = connect(self.data_root / "state" / "incremental_runtime.sqlite")
        try:
            conn.execute("UPDATE research_trigger_ledger SET status='NEEDS_REVIEW' WHERE trigger_key='SEM:1'")
            conn.commit()
        finally:
            conn.close()
        result = recover_chat_to_api(data_root=self.data_root, apply=False)
        self.assertEqual(result["candidate_counts"]["total"], 0)
        reasons = {item["reason"] for item in result["skipped"]}
        self.assertIn("ACTIVE_CLAIM", reasons)
        self.assertIn("SEMANTIC_LEDGER_STATUS_NEEDS_REVIEW", reasons)

    def test_apply_fails_closed_if_expected_count_changes(self) -> None:
        with self.assertRaises(RuntimeError):
            recover_chat_to_api(
                data_root=self.data_root,
                apply=True,
                expect_semantic=20,
                expect_path=5,
            )


if __name__ == "__main__":
    unittest.main()
