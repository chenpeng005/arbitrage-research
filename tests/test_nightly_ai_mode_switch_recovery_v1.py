from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from runtime.opportunity.incremental_storage import connect, initialize_schema


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class NightlyAiModeSwitchRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(__file__).resolve().parents[1]
        self.data_root = Path(self.temp.name) / "runtime_data"
        self.state_dir = Path(self.temp.name) / "state"
        self.data_root.mkdir(parents=True)
        self.state_dir.mkdir(parents=True)
        initialize_schema(self.data_root / "state" / "incremental_runtime.sqlite")
        write_json(
            self.data_root / "deployment_manifest.json",
            {
                "application_commit_sha": "app-sha",
                "knowledge_commit_sha": "knowledge-sha",
                "release_profile": "convertible",
            },
        )
        self.script = self.root / "scripts" / "production" / "nightly-ai-mode-switch.sh"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "NIGHTLY_AI_MODE_STATE_DIR": str(self.state_dir),
                "CHAT_PROVIDER_ROOT": str(self.state_dir / "chat"),
                "ARBITRAGE_RUNTIME_ROOT": str(self.root),
                "NIGHTLY_AI_RECOVERY_DATA_ROOT": str(self.data_root),
                "NIGHTLY_AI_RECOVERY_PY": sys.executable,
                "PYTHONPATH": str(self.root),
            }
        )
        return env

    def _run(self, mode: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(self.script), mode],
            cwd=self.root,
            env=self._env(),
            text=True,
            capture_output=True,
            check=False,
        )

    def _seed_semantic_waiting(self, *, active_claim: bool) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn = connect(self.data_root / "state" / "incremental_runtime.sqlite")
        try:
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
        work_dir = self.data_root / "event_semantic_work" / "event_sem_1"
        work_dir.mkdir(parents=True)
        chat_dir = self.data_root / "chat_tasks" / "chat_sem_1"
        write_json(
            chat_dir / "chat_task.json",
            {
                "task_id": "chat_sem_1",
                "task_type": "EVENT_SEMANTIC_AUDIT",
                "execution_mode": "INTERACTIVE_CHAT",
                "status": "WAITING_FOR_CHAT",
                "business_run_dir": str(work_dir),
                "source_event_semantic_task_id": "event_sem_1",
                "bond_code": "110001",
                "trigger_key": "SEM:1",
                "writeback_mode": "FORMAL",
            },
        )
        if active_claim:
            write_json(
                chat_dir / "claim.json",
                {
                    "task_id": "chat_sem_1",
                    "claim_id": "live-claim",
                    "expires_at": (
                        datetime.now(timezone.utc) + timedelta(minutes=30)
                    ).isoformat(),
                },
            )

    def test_api_switch_noop_recovery_then_writes_mode(self) -> None:
        proc = self._run("api")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(
            "CHAT_TO_API_RECOVERY_DRY_RUN semantic=0 path=0 blockers=0",
            proc.stdout,
        )
        self.assertIn("CHAT_TO_API_RECOVERY=NOOP", proc.stdout)
        text = (self.state_dir / "nightly-ai-mode.env").read_text(encoding="utf-8")
        self.assertIn("NIGHTLY_AI_MODE=API", text)

    def test_chat_switch_does_not_require_recovery(self) -> None:
        env = self._env()
        env["NIGHTLY_AI_RECOVERY_DATA_ROOT"] = str(Path(self.temp.name) / "missing")
        proc = subprocess.run(
            ["bash", str(self.script), "chat"],
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("CHAT_TO_API_RECOVERY", proc.stdout)
        text = (self.state_dir / "nightly-ai-mode.env").read_text(encoding="utf-8")
        self.assertIn("NIGHTLY_AI_MODE=CHAT", text)

    def test_api_switch_blocks_active_chat_claim_and_preserves_old_mode(self) -> None:
        self._seed_semantic_waiting(active_claim=True)
        (self.state_dir / "nightly-ai-mode.env").write_text(
            "NIGHTLY_AI_MODE=CHAT\n", encoding="utf-8"
        )
        proc = self._run("api")
        self.assertEqual(proc.returncode, 4)
        self.assertIn("CHAT_TO_API_RECOVERY_BLOCKED", proc.stderr)
        text = (self.state_dir / "nightly-ai-mode.env").read_text(encoding="utf-8")
        self.assertIn("NIGHTLY_AI_MODE=CHAT", text)
        conn = connect(self.data_root / "state" / "incremental_runtime.sqlite")
        try:
            row = conn.execute(
                "SELECT status FROM research_trigger_ledger WHERE trigger_key='SEM:1'"
            ).fetchone()
            self.assertEqual(row["status"], "WAITING_FOR_CHAT")
        finally:
            conn.close()

    def test_api_switch_recovers_waiting_semantic_before_writing_mode(self) -> None:
        self._seed_semantic_waiting(active_claim=False)
        proc = self._run("api")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("CHAT_TO_API_RECOVERY=APPLIED semantic=1 path=0", proc.stdout)
        conn = connect(self.data_root / "state" / "incremental_runtime.sqlite")
        try:
            row = conn.execute(
                "SELECT status,task_id FROM research_trigger_ledger WHERE trigger_key='SEM:1'"
            ).fetchone()
            self.assertEqual(row["status"], "PENDING")
            self.assertIsNone(row["task_id"])
        finally:
            conn.close()
        chat = json.loads(
            (self.data_root / "chat_tasks" / "chat_sem_1" / "chat_task.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(chat["status"], "SUPERSEDED_BY_API")
        text = (self.state_dir / "nightly-ai-mode.env").read_text(encoding="utf-8")
        self.assertIn("NIGHTLY_AI_MODE=API", text)


if __name__ == "__main__":
    unittest.main()
