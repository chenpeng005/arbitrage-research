"""Recover abandoned Interactive-Chat tasks so AUTO_API can consume them."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import uuid
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.opportunity.incremental_storage import connect

RECOVERY_VERSION = "chat-to-api-recovery-v1"
SUPERSEDED_STATUS = "SUPERSEDED_BY_API"
ELIGIBLE_CHAT_STATUSES = {"WAITING_FOR_CHAT", "CLAIMED_BY_CHAT"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _claim_state(chat_dir: Path) -> str:
    path = chat_dir / "claim.json"
    if not path.exists():
        return "NONE"
    try:
        expires = _parse_time(_read(path).get("expires_at"))
    except Exception:
        return "INVALID"
    if expires is None:
        return "INVALID"
    return "ACTIVE" if expires > datetime.now(timezone.utc) else "EXPIRED"


def _validation_status(task: dict[str, Any]) -> str | None:
    direct = str(task.get("validation_status") or "").strip()
    if direct:
        return direct
    run_dir = Path(str(task.get("business_run_dir") or ""))
    if not str(run_dir):
        return None
    name = (
        "semantic_audit_validation.json"
        if task.get("task_type") == "EVENT_SEMANTIC_AUDIT"
        else "path_research_validation.json"
    )
    path = run_dir / name
    if not path.exists():
        return None
    try:
        return str(_read(path).get("status") or "").strip() or None
    except Exception:
        return "INVALID"


def _skip(task: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "chat_task_id": task.get("task_id"),
        "task_type": task.get("task_type"),
        "trigger_key": task.get("trigger_key"),
        "reason": reason,
    }


def _base_ok(task_path: Path, task: dict[str, Any]) -> tuple[bool, str | None, str]:
    if task.get("execution_mode") != "INTERACTIVE_CHAT":
        return False, "NOT_INTERACTIVE_CHAT", "NONE"
    if task.get("status") not in ELIGIBLE_CHAT_STATUSES:
        return False, f"CHAT_STATUS_{task.get('status')}", "NONE"
    if _validation_status(task) == "PASS":
        return False, "VALIDATION_ALREADY_PASS", "NONE"
    if task.get("writeback_status") == "PASS":
        return False, "WRITEBACK_ALREADY_PASS", "NONE"
    claim = _claim_state(task_path.parent)
    if claim == "ACTIVE":
        return False, "ACTIVE_CLAIM", claim
    if claim == "INVALID":
        return False, "INVALID_CLAIM_FILE", claim
    return True, None, claim


def build_recovery_plan(data_root: Path) -> dict[str, Any]:
    pending_path = data_root / "registry" / "pending_research_tasks.json"
    state_path = data_root / "registry" / "research_trigger_state.json"
    pending = _read(pending_path) if pending_path.exists() else {"pending_tasks": []}
    trigger_state = _read(state_path) if state_path.exists() else {"paths": {}}
    candidates: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    db = connect(data_root / "state" / "incremental_runtime.sqlite")
    try:
        for task_path in sorted((data_root / "chat_tasks").glob("*/chat_task.json")) if (data_root / "chat_tasks").exists() else []:
            try:
                task = _read(task_path)
            except Exception:
                skipped.append({"chat_task_path": str(task_path), "reason": "CHAT_TASK_INVALID_JSON"})
                continue
            kind = task.get("task_type")
            if kind not in {"EVENT_SEMANTIC_AUDIT", "PATH_RESEARCH"}:
                continue
            ok, reason, claim = _base_ok(task_path, task)
            if not ok:
                skipped.append(_skip(task, str(reason)))
                continue

            if kind == "EVENT_SEMANTIC_AUDIT":
                source_id = str(task.get("source_event_semantic_task_id") or "")
                source_path = data_root / "event_semantic_tasks" / f"{source_id}.json"
                if not source_id or not source_path.exists():
                    skipped.append(_skip(task, "SEMANTIC_SOURCE_TASK_MISSING"))
                    continue
                source = _read(source_path)
                trigger = str(task.get("trigger_key") or "")
                if source.get("task_id") != source_id or source.get("trigger_key") != trigger:
                    skipped.append(_skip(task, "SEMANTIC_SOURCE_IDENTITY_MISMATCH"))
                    continue
                row = db.execute(
                    "SELECT * FROM research_trigger_ledger WHERE trigger_key=?",
                    (trigger,),
                ).fetchone()
                if row is None:
                    skipped.append(_skip(task, "SEMANTIC_LEDGER_ROW_MISSING"))
                    continue
                ledger = dict(row)
                if ledger.get("task_kind") != "EVENT_SEMANTIC_AUDIT":
                    skipped.append(_skip(task, "SEMANTIC_LEDGER_KIND_MISMATCH"))
                    continue
                if ledger.get("status") != "WAITING_FOR_CHAT":
                    skipped.append(_skip(task, f"SEMANTIC_LEDGER_STATUS_{ledger.get('status')}"))
                    continue
                if str(ledger.get("task_id") or "") != source_id:
                    skipped.append(_skip(task, "SEMANTIC_LEDGER_TASK_ID_MISMATCH"))
                    continue
                candidates.append({
                    "kind": kind,
                    "source_key": f"SEMANTIC:{source_id}",
                    "chat_task_id": str(task.get("task_id") or task_path.parent.name),
                    "chat_task_path": str(task_path),
                    "source_task_id": source_id,
                    "trigger_key": trigger,
                    "bond_code": str(task.get("bond_code") or "").zfill(6),
                    "claim_state": claim,
                    "before": {"chat_status": task.get("status"), "ledger": ledger},
                })
                continue

            source_id = str(task.get("source_research_task_id") or "")
            source_path = data_root / "research_tasks" / f"{source_id}.json"
            trigger = str(task.get("trigger_key") or "")
            bond = str(task.get("bond_code") or "").zfill(6)
            path_id = str(task.get("path_id") or "")
            if not source_id or not source_path.exists():
                skipped.append(_skip(task, "PATH_SOURCE_TASK_MISSING"))
                continue
            source = _read(source_path)
            if (
                source.get("task_id") != source_id
                or source.get("trigger_key") != trigger
                or str(source.get("bond_code") or "").zfill(6) != bond
                or source.get("path_id") != path_id
            ):
                skipped.append(_skip(task, "PATH_SOURCE_IDENTITY_MISMATCH"))
                continue
            matches = [x for x in pending.get("pending_tasks", []) if x.get("trigger_key") == trigger]
            if len(matches) != 1:
                skipped.append(_skip(task, f"PATH_PENDING_MATCH_COUNT_{len(matches)}"))
                continue
            queue = matches[0]
            if queue.get("research_status") != "WAITING_FOR_CHAT":
                skipped.append(_skip(task, f"PATH_PENDING_STATUS_{queue.get('research_status')}"))
                continue
            if queue.get("chat_task_id") != task.get("task_id"):
                skipped.append(_skip(task, "PATH_PENDING_CHAT_TASK_MISMATCH"))
                continue
            state_key = f"{bond}:{path_id}"
            state = (trigger_state.get("paths") or {}).get(state_key)
            if not isinstance(state, dict):
                skipped.append(_skip(task, "PATH_TRIGGER_STATE_MISSING"))
                continue
            if state.get("last_trigger_key") != trigger:
                skipped.append(_skip(task, "PATH_TRIGGER_NOT_CURRENT"))
                continue
            if state.get("research_status") != "WAITING_FOR_CHAT":
                skipped.append(_skip(task, f"PATH_TRIGGER_STATUS_{state.get('research_status')}"))
                continue
            candidates.append({
                "kind": kind,
                "source_key": f"PATH:{source_id}",
                "chat_task_id": str(task.get("task_id") or task_path.parent.name),
                "chat_task_path": str(task_path),
                "source_task_id": source_id,
                "trigger_key": trigger,
                "bond_code": bond,
                "path_id": path_id,
                "claim_state": claim,
                "before": {"chat_status": task.get("status"), "pending_item": queue, "trigger_state": state},
            })
    finally:
        db.close()

    counts: dict[str, int] = {}
    for item in candidates:
        counts[item["source_key"]] = counts.get(item["source_key"], 0) + 1
    unique: list[dict[str, Any]] = []
    for item in candidates:
        if counts[item["source_key"]] == 1:
            unique.append(item)
        else:
            skipped.append({
                "chat_task_id": item["chat_task_id"],
                "task_type": item["kind"],
                "trigger_key": item["trigger_key"],
                "reason": "DUPLICATE_WAITING_CHAT_SOURCE",
            })
    semantic = sum(x["kind"] == "EVENT_SEMANTIC_AUDIT" for x in unique)
    path = sum(x["kind"] == "PATH_RESEARCH" for x in unique)
    deployment = {}
    manifest = data_root / "deployment_manifest.json"
    if manifest.exists():
        raw = _read(manifest)
        deployment = {
            "application_commit_sha": raw.get("application_commit_sha"),
            "knowledge_commit_sha": raw.get("knowledge_commit_sha"),
            "release_profile": raw.get("release_profile"),
        }
    return {
        "recovery_version": RECOVERY_VERSION,
        "generated_at": _now(),
        "deployment": deployment,
        "candidate_counts": {"semantic": semantic, "path": path, "total": len(unique)},
        "candidates": unique,
        "skipped": skipped,
    }


@contextmanager
def _lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise RuntimeError(f"recovery blocked by active lock: {path}") from exc
    try:
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _check_counts(plan: dict[str, Any], expect_semantic: int | None, expect_path: int | None, apply: bool) -> None:
    if apply and (expect_semantic is None or expect_path is None):
        raise RuntimeError("--apply requires --expect-semantic and --expect-path")
    actual = plan["candidate_counts"]
    if expect_semantic is not None and actual["semantic"] != expect_semantic:
        raise RuntimeError(f"semantic candidate count changed: expected={expect_semantic}, actual={actual['semantic']}")
    if expect_path is not None and actual["path"] != expect_path:
        raise RuntimeError(f"path candidate count changed: expected={expect_path}, actual={actual['path']}")


def _apply(data_root: Path, plan: dict[str, Any], reason: str) -> dict[str, Any]:
    recovery_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_chat_to_api_" + uuid.uuid4().hex[:8]
    audit_path = data_root / "recovery" / f"{recovery_id}.json"
    audit = {**plan, "recovery_id": recovery_id, "status": "PREPARED", "reason": reason, "prepared_at": _now()}
    _write(audit_path, audit)

    pending_path = data_root / "registry" / "pending_research_tasks.json"
    state_path = data_root / "registry" / "research_trigger_state.json"
    pending = _read(pending_path) if pending_path.exists() else {"pending_tasks": []}
    trigger_state = _read(state_path) if state_path.exists() else {"paths": {}}
    originals: dict[Path, str] = {}
    changed: list[Path] = []
    db = connect(data_root / "state" / "incremental_runtime.sqlite")
    try:
        db.execute("BEGIN IMMEDIATE")
        for item in plan["candidates"]:
            if item["kind"] != "EVENT_SEMANTIC_AUDIT":
                continue
            cur = db.execute(
                """UPDATE research_trigger_ledger
                   SET status='PENDING',task_id=NULL,updated_at=?
                   WHERE trigger_key=? AND task_kind='EVENT_SEMANTIC_AUDIT'
                     AND status='WAITING_FOR_CHAT' AND task_id=?""",
                (_now(), item["trigger_key"], item["source_task_id"]),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"semantic recovery lost compare-and-swap: {item['trigger_key']}")

        path_items = [x for x in plan["candidates"] if x["kind"] == "PATH_RESEARCH"]
        if path_items:
            originals[pending_path] = pending_path.read_text(encoding="utf-8")
            originals[state_path] = state_path.read_text(encoding="utf-8")
            for item in path_items:
                rows = [x for x in pending.get("pending_tasks", []) if x.get("trigger_key") == item["trigger_key"]]
                if len(rows) != 1 or rows[0].get("research_status") != "WAITING_FOR_CHAT" or rows[0].get("chat_task_id") != item["chat_task_id"]:
                    raise RuntimeError(f"path pending state changed: {item['trigger_key']}")
                rows[0]["research_status"] = "PENDING"
                rows[0].pop("chat_task_id", None)
                rows[0]["updated_at"] = _now()
                state_key = f"{item['bond_code']}:{item['path_id']}"
                row = (trigger_state.get("paths") or {}).get(state_key)
                if not isinstance(row, dict) or row.get("last_trigger_key") != item["trigger_key"] or row.get("research_status") != "WAITING_FOR_CHAT":
                    raise RuntimeError(f"path trigger state changed: {item['trigger_key']}")
                row["research_status"] = "PENDING"
                row["updated_at"] = _now()
            pending["updated_at"] = _now()
            trigger_state["updated_at"] = _now()
            _write(pending_path, pending); changed.append(pending_path)
            _write(state_path, trigger_state); changed.append(state_path)

        for item in plan["candidates"]:
            path = Path(item["chat_task_path"])
            originals[path] = path.read_text(encoding="utf-8")
            task = _read(path)
            if task.get("status") not in ELIGIBLE_CHAT_STATUSES:
                raise RuntimeError(f"chat task status changed: {item['chat_task_id']}")
            claim = _claim_state(path.parent)
            if claim in {"ACTIVE", "INVALID"}:
                raise RuntimeError(f"chat task claim changed: {item['chat_task_id']}={claim}")
            task["superseded_from_status"] = task.get("status")
            task["status"] = SUPERSEDED_STATUS
            task["superseded_at"] = _now()
            task["superseded_reason"] = reason
            task["recovery_id"] = recovery_id
            task.pop("claim", None)
            _write(path, task); changed.append(path)
        db.commit()
    except Exception:
        db.rollback()
        for path in reversed(changed):
            if path in originals:
                path.write_text(originals[path], encoding="utf-8")
        audit["status"] = "ROLLED_BACK"
        audit["failed_at"] = _now()
        _write(audit_path, audit)
        raise
    finally:
        db.close()

    audit["status"] = "APPLIED"
    audit["completed_at"] = _now()
    audit["applied_counts"] = plan["candidate_counts"]
    _write(audit_path, audit)
    _write(data_root / "registry" / "latest_chat_to_api_recovery.json", {
        "recovery_version": RECOVERY_VERSION,
        "recovery_id": recovery_id,
        "status": "APPLIED",
        "completed_at": audit["completed_at"],
        "audit_path": str(audit_path),
        "applied_counts": plan["candidate_counts"],
    })
    return {
        "recovery_version": RECOVERY_VERSION,
        "recovery_id": recovery_id,
        "status": "APPLIED",
        "audit_path": str(audit_path),
        "applied_counts": plan["candidate_counts"],
    }


def recover_chat_to_api(
    *,
    data_root: Path,
    apply: bool = False,
    expect_semantic: int | None = None,
    expect_path: int | None = None,
    reason: str = "NIGHTLY_MODE_SWITCHED_TO_API",
) -> dict[str, Any]:
    data_root = data_root.resolve()
    if not apply:
        plan = build_recovery_plan(data_root)
        _check_counts(plan, expect_semantic, expect_path, False)
        return {**plan, "status": "DRY_RUN"}
    with ExitStack() as stack:
        for path in (
            data_root / "registry" / "chat_to_api_recovery.lock",
            data_root / "registry" / "information_controller.lock",
            data_root / "registry" / "path_research_batch.lock",
        ):
            stack.enter_context(_lock(path))
        plan = build_recovery_plan(data_root)
        _check_counts(plan, expect_semantic, expect_path, True)
        return _apply(data_root, plan, reason)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recover abandoned Interactive-Chat tasks back to AUTO_API")
    parser.add_argument("--data-root", default=os.environ.get("RUNTIME_DATA_ROOT", "runtime_data"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expect-semantic", type=int)
    parser.add_argument("--expect-path", type=int)
    parser.add_argument("--reason", default="NIGHTLY_MODE_SWITCHED_TO_API")
    args = parser.parse_args(argv)
    try:
        result = recover_chat_to_api(
            data_root=Path(args.data_root),
            apply=args.apply,
            expect_semantic=args.expect_semantic,
            expect_path=args.expect_path,
            reason=args.reason,
        )
    except Exception as exc:
        print(json.dumps({"recovery_version": RECOVERY_VERSION, "status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
