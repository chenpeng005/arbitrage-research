"""Controlled batch execution for pending PATH_RESEARCH tasks."""

from __future__ import annotations

import hashlib
import json
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.opportunity.path_research_runner import run_one_path_research


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _task_id_from_trigger(trigger_key: str) -> str:
    digest = hashlib.sha256(trigger_key.encode("utf-8")).hexdigest()[:16]
    return f"research_{digest}"


def _run_one_with_wall_timeout(
    *,
    root: Path,
    data_root: Path,
    task_id: str,
    provider_name: str,
    model: str,
    wall_timeout_seconds: int,
) -> dict[str, Any]:
    previous_handler = signal.getsignal(signal.SIGALRM)

    def _timeout_handler(signum: int, frame: Any) -> None:
        raise TimeoutError(
            f"PATH_RESEARCH wall timeout after {wall_timeout_seconds}s"
        )

    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(max(1, int(wall_timeout_seconds)))
    try:
        return run_one_path_research(
            root=root,
            data_root=data_root,
            task_id=task_id,
            provider_name=provider_name,
            model=model,
        )
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)


def run_path_research_batch(
    *,
    root: Path,
    data_root: Path,
    provider_name: str,
    model: str,
    limit: int = 5,
    path_id: str | None = None,
    retry_once: bool = True,
    wall_timeout_seconds: int = 180,
) -> dict[str, Any]:
    pending = _read_json(data_root / "registry" / "pending_research_tasks.json")
    items = list(pending.get("pending_tasks", []))
    if path_id:
        items = [x for x in items if x.get("path_id") == path_id]
    items = items[: max(0, int(limit))]

    batch_id = (
        datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        + "_path_research_batch"
    )
    batch_dir = data_root / "path_research_batches" / batch_id
    batch_dir.mkdir(parents=True, exist_ok=False)

    results: list[dict[str, Any]] = []
    for item in items:
        task_id = _task_id_from_trigger(str(item["trigger_key"]))
        attempts: list[dict[str, Any]] = []

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
        "selected": len(items),
        "path_filter": path_id,
        "provider": provider_name,
        "model": model,
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
            "remaining_pending": summary["remaining_pending"],
            "result_path": str(batch_dir / "batch_result.json"),
        },
    )
    return summary
