"""Controlled batch execution for pending PATH_RESEARCH tasks."""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.opportunity.path_research_runner import (
    create_path_research_chat_task,
    set_trigger_research_status,
)
from runtime.opportunity.research_queue import (
    pending_item_is_runnable,
    task_id_from_trigger,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _run_one_with_wall_timeout(
    *,
    root: Path,
    data_root: Path,
    task_id: str,
    provider_name: str,
    model: str,
    wall_timeout_seconds: int,
) -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "runtime.opportunity.path_research_worker",
        "--root",
        str(root),
        "--data-root",
        str(data_root),
        "--task-id",
        task_id,
        "--provider",
        provider_name,
        "--model",
        model,
    ]
    try:
        proc = subprocess.run(
            command,
            cwd=str(root),
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=max(1, int(wall_timeout_seconds)),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        set_trigger_research_status(data_root, task_id, "PENDING")
        return {
            "task_id": task_id,
            "status": "FAIL",
            "error": (
                f"TimeoutError: PATH_RESEARCH worker exceeded "
                f"{wall_timeout_seconds}s"
            ),
            "worker_exit_code": None,
            "stdout_tail": (exc.stdout or "")[-2000:] if isinstance(exc.stdout, str) else "",
            "stderr_tail": (exc.stderr or "")[-2000:] if isinstance(exc.stderr, str) else "",
        }

    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    result: dict[str, Any] | None = None
    if lines:
        try:
            parsed = json.loads(lines[-1])
            if isinstance(parsed, dict):
                result = parsed
        except Exception:
            result = None

    if result is None:
        runner_result = (
            data_root / "path_research_work" / task_id / "runner_result.json"
        )
        if runner_result.exists():
            try:
                parsed = _read_json(runner_result)
                if isinstance(parsed, dict):
                    result = parsed
            except Exception:
                result = None

    if result is None:
        set_trigger_research_status(data_root, task_id, "PENDING")
        return {
            "task_id": task_id,
            "status": "FAIL",
            "error": "Worker did not return parseable JSON result",
            "worker_exit_code": proc.returncode,
            "stdout_tail": proc.stdout[-2000:],
            "stderr_tail": proc.stderr[-2000:],
        }

    result["worker_exit_code"] = proc.returncode
    result["worker_stdout_tail"] = proc.stdout[-1000:]
    result["worker_stderr_tail"] = proc.stderr[-1000:]
    if result.get("status") != "PASS":
        set_trigger_research_status(data_root, task_id, "PENDING")
    return result

@contextmanager
def _exclusive_batch_lock(data_root: Path):
    lock_path = data_root / "registry" / "path_research_batch.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise RuntimeError("Another PATH_RESEARCH batch is already running") from exc
    try:
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _run_path_research_batch_unlocked(
    *,
    root: Path,
    data_root: Path,
    provider_name: str,
    model: str,
    execution_mode: str = "AUTO_API",
    full_runtime_run_id: str | None = None,
    limit: int = 5,
    path_id: str | None = None,
    retry_once: bool = False,
    wall_timeout_seconds: int = 180,
) -> dict[str, Any]:
    if execution_mode not in {"AUTO_API", "INTERACTIVE_CHAT"}:
        raise ValueError(f"unsupported execution_mode={execution_mode}")
    pending = _read_json(data_root / "registry" / "pending_research_tasks.json")
    all_items = list(pending.get("pending_tasks", []))
    if path_id:
        all_items = [x for x in all_items if x.get("path_id") == path_id]

    runnable_items = []
    held_items = []
    for item in all_items:
        runnable, queue_reason = pending_item_is_runnable(item, data_root)
        enriched = {**item, "queue_reason": queue_reason}
        if runnable:
            runnable_items.append(enriched)
        else:
            held_items.append(enriched)

    items = runnable_items[: max(0, int(limit))]

    batch_id = (
        datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        + "_path_research_batch"
    )
    batch_dir = data_root / "path_research_batches" / batch_id
    batch_dir.mkdir(parents=True, exist_ok=False)

    results: list[dict[str, Any]] = []
    for item in items:
        task_id = task_id_from_trigger(str(item["trigger_key"]))
        attempts: list[dict[str, Any]] = []

        if execution_mode == "INTERACTIVE_CHAT":
            chat_task = create_path_research_chat_task(
                root=root,
                data_root=data_root,
                task_id=task_id,
                writeback_mode="FORMAL",
                full_runtime_run_id=full_runtime_run_id,
            )
            first = {
                "task_id": task_id,
                "status": "WAITING_FOR_CHAT",
                "chat_task_id": chat_task["task_id"],
                "execution_mode": "INTERACTIVE_CHAT",
            }
            attempts.append(first)
        else:
            first = _run_one_with_wall_timeout(
                root=root,
                data_root=data_root,
                task_id=task_id,
                provider_name=provider_name,
                model=model,
                wall_timeout_seconds=wall_timeout_seconds,
            )
            attempts.append(first)

            timed_out = "wall timeout" in str(first.get("error") or "")
            should_retry = (
                retry_once
                and not timed_out
                and first.get("status") in {"FAIL", "NEEDS_REVIEW"}
            )
            if should_retry:
                second = _run_one_with_wall_timeout(
                    root=root,
                    data_root=data_root,
                    task_id=task_id,
                    provider_name=provider_name,
                    model=model,
                    wall_timeout_seconds=wall_timeout_seconds,
                )
                attempts.append(second)

        final = attempts[-1]
        results.append({
            "task_id": task_id,
            "bond_code": item.get("bond_code"),
            "bond_name": item.get("bond_name"),
            "path_id": item.get("path_id"),
            "attempts": len(attempts),
            "status": final.get("status"),
            "ledger": final.get("ledger"),
            "attempt_results": attempts,
        })
        _write_json(
            batch_dir / f"{task_id}.json",
            results[-1],
        )

    status_counts: dict[str, int] = {}
    for item in results:
        status = str(item.get("status") or "UNKNOWN")
        status_counts[status] = status_counts.get(status, 0) + 1

    remaining = _read_json(data_root / "registry" / "pending_research_tasks.json")
    summary = {
        "batch_id": batch_id,
        "unit": "PATH_RESEARCH_BATCH",
        "started_from_pending": len(pending.get("pending_tasks", [])),
        "runnable_before_limit": len(runnable_items),
        "held_waiting_evidence": len(held_items),
        "selected": len(items),
        "path_filter": path_id,
        "provider": provider_name,
        "model": model,
        "execution_mode": execution_mode,
        "full_runtime_run_id": full_runtime_run_id,
        "retry_once": retry_once,
        "wall_timeout_seconds": wall_timeout_seconds,
        "status_counts": status_counts,
        "remaining_pending": len(remaining.get("pending_tasks", [])),
        "completed_at": _now(),
        "results": results,
    }
    _write_json(batch_dir / "batch_result.json", summary)
    _write_json(
        data_root / "registry" / "latest_path_research_batch.json",
        {
            "batch_id": batch_id,
            "status_counts": status_counts,
            "selected": len(items),
            "runnable_before_limit": len(runnable_items),
            "held_waiting_evidence": len(held_items),
            "remaining_pending": summary["remaining_pending"],
            "result_path": str(batch_dir / "batch_result.json"),
        },
    )
    return summary


def run_path_research_batch(
    *,
    root: Path,
    data_root: Path,
    provider_name: str,
    model: str,
    execution_mode: str = "AUTO_API",
    full_runtime_run_id: str | None = None,
    limit: int = 5,
    path_id: str | None = None,
    retry_once: bool = False,
    wall_timeout_seconds: int = 180,
) -> dict[str, Any]:
    with _exclusive_batch_lock(data_root):
        return _run_path_research_batch_unlocked(
            root=root,
            data_root=data_root,
            provider_name=provider_name,
            model=model,
            execution_mode=execution_mode,
            full_runtime_run_id=full_runtime_run_id,
            limit=limit,
            path_id=path_id,
            retry_once=retry_once,
            wall_timeout_seconds=wall_timeout_seconds,
        )

[executed on device: iZ2vc3972s0n20m9kq0ns4Z (b3130143-0d28-448b-8a4c-d5f1482304ab)]