from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from runtime.ai_runtime import server_control_chat_adapter as adapter


REPO_ROOT = Path(__file__).resolve().parents[1]


class ServerControlChatAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.data_root = Path(self.tmp.name) / "runtime_data"
        self.task_root = self.data_root / "chat_tasks"
        self.task_root.mkdir(parents=True)
        self.native_id = "20261017_120000_path_chat_abcdef12"
        self.public_id = adapter._public_id(self.native_id)
        self.task_dir = self.task_root / self.native_id
        self.task_dir.mkdir()
        business_run = Path(self.tmp.name) / "business_run"
        business_run.mkdir()
        task = {
            "task_id": self.native_id,
            "task_type": "PATH_RESEARCH",
            "execution_mode": "INTERACTIVE_CHAT",
            "status": "WAITING_FOR_CHAT",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "business_run_dir": str(business_run),
            "source_research_task_id": "research-task-1",
            "bond_code": "110092",
            "bond_name": "synthetic",
            "path_id": "MATURITY_CASH",
            "trigger_key": "synthetic-trigger",
            "writeback_mode": "FORMAL",
            "full_runtime_run_id": "synthetic-full-run",
            "expected_output": "path_research_result.json",
            "chat_instruction": "Synthetic convertible CHAT task for adapter tests.",
        }
        self._write(self.task_dir / "chat_task.json", task)
        self._write(self.task_dir / "input.json", {"question": "synthetic input"})
        self._write(
            self.task_dir / "engineering_prefetched_evidence.json",
            {"evidence": [{"evidence_id": "ev1", "title": "synthetic"}]},
        )
        (self.task_dir / "prompt_snapshot.md").write_text(
            "Synthetic system prompt.", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def _write(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_next_and_fixed_reads_map_native_task(self) -> None:
        nxt = adapter.next_task(root=REPO_ROOT, data_root=self.data_root)
        self.assertTrue(nxt["found"])
        self.assertEqual(nxt["task"]["task_id"], self.public_id)
        self.assertEqual(nxt["task"]["scope"], "convertible")
        self.assertEqual(nxt["task"]["status"], "WAITING_FOR_CHAT")

        meta = adapter.read_task(
            task_id=self.public_id,
            section="meta",
            root=REPO_ROOT,
            data_root=self.data_root,
        )
        self.assertEqual(meta["task"]["native_task_type"], "PATH_RESEARCH")
        self.assertTrue(meta["task"]["has_tools"])
        self.assertTrue(meta["task"]["has_output_schema"])

        system = adapter.read_task(
            task_id=self.public_id,
            section="system_prompt",
            root=REPO_ROOT,
            data_root=self.data_root,
        )
        self.assertIn("Synthetic system prompt", system["content"])

        message = adapter.read_task(
            task_id=self.public_id,
            section="message",
            message_index=0,
            root=REPO_ROOT,
            data_root=self.data_root,
        )
        self.assertIn("synthetic input", message["content"])
        self.assertIn("ev1", message["content"])

        tools = adapter.read_task(
            task_id=self.public_id,
            section="tools",
            root=REPO_ROOT,
            data_root=self.data_root,
        )
        names = {row["name"] for row in tools["value"]}
        self.assertIn("path_evidence_search", names)
        self.assertIn("path_evidence_fetch", names)

        schema = adapter.read_task(
            task_id=self.public_id,
            section="output_schema",
            root=REPO_ROOT,
            data_root=self.data_root,
        )
        self.assertEqual(schema["value"]["type"], "object")
        self.assertIn("path_result_id", schema["value"]["required"])

    def test_stale_native_task_is_not_returned(self) -> None:
        task_path = self.task_dir / "chat_task.json"
        task = json.loads(task_path.read_text(encoding="utf-8"))
        task["created_at"] = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        self._write(task_path, task)
        nxt = adapter.next_task(root=REPO_ROOT, data_root=self.data_root)
        self.assertFalse(nxt["found"])
        self.assertEqual(nxt["stale_skipped"], 1)

    def test_disallowed_tool_is_rejected_without_claiming_task(self) -> None:
        response = {
            "structured_output": None,
            "tool_calls": [{"id": "tc1", "name": "shell", "arguments": {}}],
            "finish_reason": "tool_calls",
        }
        with self.assertRaisesRegex(ValueError, "tool is not allowed"):
            adapter.submit_task(
                task_id=self.public_id,
                response=response,
                root=REPO_ROOT,
                data_root=self.data_root,
            )
        self.assertFalse((self.task_dir / "claim.json").exists())

    def test_allowed_tool_call_is_idempotent(self) -> None:
        response = {
            "structured_output": None,
            "tool_calls": [
                {
                    "id": "tc1",
                    "name": "path_evidence_search",
                    "arguments": {"keyword": "synthetic"},
                }
            ],
            "finish_reason": "tool_calls",
        }
        calls: list[dict[str, object]] = []

        def fake_run_tool(**kwargs):
            calls.append(kwargs)
            tool_dir = self.task_dir / "tool_calls"
            tool_dir.mkdir(exist_ok=True)
            self._write(
                tool_dir / "0001_request.json",
                {"name": kwargs["name"], "arguments": kwargs["arguments"]},
            )
            self._write(tool_dir / "0001_result.json", {"status": "PASS", "items": []})
            return {"status": "PASS", "items": []}

        with patch.object(adapter.native, "run_tool", side_effect=fake_run_tool):
            first = adapter.submit_task(
                task_id=self.public_id,
                response=response,
                root=REPO_ROOT,
                data_root=self.data_root,
            )
            second = adapter.submit_task(
                task_id=self.public_id,
                response=response,
                root=REPO_ROOT,
                data_root=self.data_root,
            )

        self.assertFalse(first["idempotent"])
        self.assertTrue(first["continuation"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(len(calls), 1)

        meta = adapter.read_task(
            task_id=self.public_id,
            section="meta",
            root=REPO_ROOT,
            data_root=self.data_root,
        )
        self.assertEqual(meta["task"]["message_count"], 2)
        tool_message = adapter.read_task(
            task_id=self.public_id,
            section="message",
            message_index=1,
            root=REPO_ROOT,
            data_root=self.data_root,
        )
        self.assertIn("path_evidence_search", tool_message["content"])

    def test_final_structured_submit_uses_native_validation_writeback_and_is_idempotent(self) -> None:
        response = {
            "structured_output": {"synthetic": True},
            "tool_calls": [],
            "finish_reason": "stop",
        }
        native_calls: list[dict[str, object]] = []

        def fake_submit_resolution(**kwargs):
            native_calls.append(kwargs)
            task_path = self.task_dir / "chat_task.json"
            task = json.loads(task_path.read_text(encoding="utf-8"))
            task["status"] = "PASS"
            task_path.write_text(json.dumps(task), encoding="utf-8")
            return {
                "task_id": self.native_id,
                "status": "PASS",
                "validation": {"status": "PASS"},
                "resume": {"status": "RUNNING"},
            }

        with patch.object(
            adapter.native, "submit_resolution", side_effect=fake_submit_resolution
        ):
            first = adapter.submit_task(
                task_id=self.public_id,
                response=response,
                root=REPO_ROOT,
                data_root=self.data_root,
            )
            second = adapter.submit_task(
                task_id=self.public_id,
                response=response,
                root=REPO_ROOT,
                data_root=self.data_root,
            )

        self.assertEqual(first["status"], "PASS")
        self.assertFalse(first["idempotent"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(len(native_calls), 1)
        self.assertTrue(native_calls[0]["auto_resume"])

    def test_request_surface_has_no_path_arguments(self) -> None:
        with self.assertRaisesRegex(ValueError, "additional fields"):
            adapter.handle_request(
                {"action": "next", "path": "/tmp/escape"},
                root=REPO_ROOT,
                data_root=self.data_root,
            )
        with self.assertRaisesRegex(ValueError, "unknown read fields"):
            adapter.handle_request(
                {"action": "read", "task_id": self.public_id, "path": "/tmp/escape"},
                root=REPO_ROOT,
                data_root=self.data_root,
            )


if __name__ == "__main__":
    unittest.main()
