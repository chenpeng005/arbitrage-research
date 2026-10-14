from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.ai_runtime.chat_bridge import (
    claim_task,
    get_context,
    list_tasks,
    load_json,
    release_claim,
    submit_resolution,
    write_json,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _batch_id() -> str:
    return (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        + "-batch-"
        + uuid.uuid4().hex[:8]
    )


def _optional_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return load_json(path)
    except Exception:
        return None


def claim_export_batch(
    *,
    data_root: Path,
    task_type: str | None = None,
    limit: int = 20,
    claimant: str = "chatgpt-batch",
) -> dict[str, Any]:
    """Claim several native Chat tasks and export one self-contained bundle.

    This changes transport only. Every task keeps its own expected output,
    validator, claim, writeback mode, and eventual submit_resolution call.
    """
    candidates = list_tasks(data_root, status="WAITING_FOR_CHAT")
    if task_type:
        candidates = [
            task for task in candidates
            if str(task.get("task_type") or "") == task_type
        ]
    candidates = candidates[: max(1, int(limit))]

    batch_id = _batch_id()
    items: list[dict[str, Any]] = []
    for candidate in candidates:
        task_id = str(candidate["task_id"])
        try:
            claimed = claim_task(data_root, task_id, claimant)
        except RuntimeError:
            continue
        task = dict(claimed["task"])
        claim = dict(claimed["claim"])
        _, tdir, ctx = get_context(data_root, task_id)
        items.append(
            {
                "task_id": task_id,
                "claim_id": claim["claim_id"],
                "task_type": task.get("task_type"),
                "bond_code": task.get("bond_code"),
                "bond_name": task.get("bond_name"),
                "path_id": task.get("path_id"),
                "trigger_key": task.get("trigger_key"),
                "chat_instruction": task.get("chat_instruction"),
                "expected_output": task.get("expected_output"),
                "writeback_mode": task.get("writeback_mode"),
                "input": ctx.input_payload,
                "prefetched_evidence": _optional_json(
                    tdir / "engineering_prefetched_evidence.json"
                ),
                "evidence_catalog": _optional_json(tdir / "evidence_catalog.json"),
            }
        )

    bundle = {
        "batch_id": batch_id,
        "created_at": _now(),
        "task_type_filter": task_type,
        "claimant": claimant,
        "item_count": len(items),
        "instruction": (
            "Treat each item as an independent task. Return one resolution per "
            "task_id using that item's expected_output contract. Do not merge "
            "facts or conclusions across items."
        ),
        "items": items,
    }
    batch_root = data_root / "chat_batches" / batch_id
    batch_root.mkdir(parents=True, exist_ok=False)
    write_json(batch_root / "bundle.json", bundle)
    return bundle


def submit_batch(
    *,
    data_root: Path,
    response_file: Path,
    auto_resume: bool = False,
) -> dict[str, Any]:
    """Submit a batch response while preserving per-task validation/writeback."""
    payload = load_json(response_file)
    batch_id = str(payload.get("batch_id") or "adhoc")
    rows = list(payload.get("items") or [])
    results: list[dict[str, Any]] = []

    output_root = data_root / "chat_batches" / batch_id / "submissions"
    output_root.mkdir(parents=True, exist_ok=True)

    for row in rows:
        task_id = str(row.get("task_id") or "")
        claim_id = str(row.get("claim_id") or "")
        resolution = row.get("resolution")
        if not task_id or not claim_id or not isinstance(resolution, dict):
            results.append(
                {
                    "task_id": task_id,
                    "status": "INVALID_BATCH_ITEM",
                    "reason": "task_id, claim_id and object resolution are required",
                }
            )
            continue

        resolution_path = output_root / f"{task_id}.json"
        write_json(resolution_path, resolution)
        try:
            result = submit_resolution(
                data_root=data_root,
                task_id=task_id,
                claim_id=claim_id,
                resolution_file=resolution_path,
                auto_resume=auto_resume,
            )
        except Exception as exc:
            result = {
                "task_id": task_id,
                "status": "SUBMIT_ERROR",
                "error": f"{type(exc).__name__}: {exc}",
            }
        results.append(result)

    summary = {
        "batch_id": batch_id,
        "submitted_at": _now(),
        "item_count": len(rows),
        "pass_count": sum(1 for x in results if x.get("status") == "PASS"),
        "results": results,
    }
    write_json(data_root / "chat_batches" / batch_id / "submit_result.json", summary)
    return summary


def release_batch(*, data_root: Path, bundle_file: Path) -> dict[str, Any]:
    payload = load_json(bundle_file)
    results = []
    for row in payload.get("items") or []:
        task_id = str(row.get("task_id") or "")
        claim_id = str(row.get("claim_id") or "")
        if not task_id or not claim_id:
            continue
        try:
            results.append(release_claim(data_root, task_id, claim_id))
        except Exception as exc:
            results.append(
                {
                    "task_id": task_id,
                    "status": "RELEASE_ERROR",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return {"status": "PASS", "results": results}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", default="./runtime_data")
    sub = parser.add_subparsers(dest="command", required=True)

    p_claim = sub.add_parser("claim-export")
    p_claim.add_argument("--task-type")
    p_claim.add_argument("--limit", type=int, default=20)
    p_claim.add_argument("--claimant", default="chatgpt-batch")

    p_submit = sub.add_parser("submit")
    p_submit.add_argument("--response-file", required=True)
    p_submit.add_argument("--auto-resume", action="store_true")

    p_release = sub.add_parser("release")
    p_release.add_argument("--bundle-file", required=True)

    args = parser.parse_args()
    data_root = Path(args.data_root).resolve()
    if args.command == "claim-export":
        result = claim_export_batch(
            data_root=data_root,
            task_type=args.task_type,
            limit=args.limit,
            claimant=args.claimant,
        )
    elif args.command == "submit":
        result = submit_batch(
            data_root=data_root,
            response_file=Path(args.response_file).resolve(),
            auto_resume=args.auto_resume,
        )
    else:
        result = release_batch(
            data_root=data_root,
            bundle_file=Path(args.bundle_file).resolve(),
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
