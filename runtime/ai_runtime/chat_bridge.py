from __future__ import annotations

import argparse
import base64
import json
import os
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import requests

from runtime.ai_runtime.tools.evidence import ToolContext, execute_tool
from runtime.market_map.semantic_resolution import validate_resolution


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def task_root(data_root: Path) -> Path:
    return data_root / "chat_tasks"


def task_dir(data_root: Path, task_id: str) -> Path:
    return task_root(data_root) / task_id


def task_path(data_root: Path, task_id: str) -> Path:
    return task_dir(data_root, task_id) / "chat_task.json"


CLAIM_TTL_SECONDS = 45 * 60


def claim_path(data_root: Path, task_id: str) -> Path:
    return task_dir(data_root, task_id) / "claim.json"


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _claim_is_active(payload: dict[str, Any]) -> bool:
    expires = _parse_time(payload.get("expires_at"))
    if expires is None:
        return False
    return expires > datetime.now(timezone.utc)


def read_active_claim(data_root: Path, task_id: str) -> dict[str, Any] | None:
    path = claim_path(data_root, task_id)
    if not path.exists():
        return None
    try:
        payload = load_json(path)
    except Exception:
        return None
    if _claim_is_active(payload):
        return payload
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    return None


def claim_task(
    data_root: Path,
    task_id: str,
    claimant: str = "chatgpt",
) -> dict[str, Any]:
    task = load_task(data_root, task_id)
    if task.get("status") not in {"WAITING_FOR_CHAT", "CLAIMED_BY_CHAT"}:
        raise RuntimeError(
            f"task is not claimable: status={task.get('status')}"
        )

    existing = read_active_claim(data_root, task_id)
    if existing is not None:
        raise RuntimeError(
            f"task is already claimed until {existing.get('expires_at')}"
        )

    now = datetime.now(timezone.utc)
    claim_id = uuid.uuid4().hex
    payload = {
        "task_id": task_id,
        "claim_id": claim_id,
        "claimant": claimant,
        "claimed_at": now.isoformat(),
        "expires_at": (
            now + timedelta(seconds=CLAIM_TTL_SECONDS)
        ).isoformat(),
    }

    path = claim_path(data_root, task_id)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        existing = read_active_claim(data_root, task_id)
        if existing is not None:
            raise RuntimeError(
                f"task was claimed concurrently until {existing.get('expires_at')}"
            )
        fd = os.open(path, flags, 0o600)

    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)

    task["status"] = "CLAIMED_BY_CHAT"
    task["claim"] = payload
    write_json(task_path(data_root, task_id), task)
    return {"task": task, "claim": payload}


def claim_next_task(
    data_root: Path,
    claimant: str = "chatgpt",
) -> dict[str, Any] | None:
    for task in list_tasks(data_root, status="WAITING_FOR_CHAT"):
        try:
            return claim_task(data_root, task["task_id"], claimant)
        except RuntimeError:
            continue
    return None


def require_claim(
    data_root: Path,
    task_id: str,
    claim_id: str,
) -> dict[str, Any]:
    claim = read_active_claim(data_root, task_id)
    if claim is None:
        raise RuntimeError("task has no active claim")
    if claim.get("claim_id") != claim_id:
        raise RuntimeError("claim_id does not own this task")

    now = datetime.now(timezone.utc)
    claim["expires_at"] = (
        now + timedelta(seconds=CLAIM_TTL_SECONDS)
    ).isoformat()
    write_json(claim_path(data_root, task_id), claim)
    return claim


def release_claim(
    data_root: Path,
    task_id: str,
    claim_id: str,
) -> dict[str, Any]:
    require_claim(data_root, task_id, claim_id)
    path = claim_path(data_root, task_id)
    if path.exists():
        path.unlink()
    task = load_task(data_root, task_id)
    if task.get("status") == "CLAIMED_BY_CHAT":
        task["status"] = "WAITING_FOR_CHAT"
        task.pop("claim", None)
        write_json(task_path(data_root, task_id), task)
    return {"task_id": task_id, "status": "WAITING_FOR_CHAT"}


def load_task(data_root: Path, task_id: str) -> dict[str, Any]:
    path = task_path(data_root, task_id)
    if not path.exists():
        raise FileNotFoundError(f"chat task not found: {task_id}")
    return load_json(path)


def list_tasks(data_root: Path, status: str | None = None) -> list[dict[str, Any]]:
    root = task_root(data_root)
    if not root.exists():
        return []

    items: list[dict[str, Any]] = []
    for path in sorted(root.glob("*/chat_task.json")):
        try:
            task = load_json(path)
        except Exception:
            continue

        business_run_dir = Path(task.get("business_run_dir") or "")
        validation_path = business_run_dir / "semantic_resolution_validation.json"
        if validation_path.exists():
            try:
                validation = load_json(validation_path)
                if validation.get("status") == "PASS":
                    task["status"] = "PASS"
                    task["validation_status"] = "PASS"
                else:
                    task["validation_status"] = validation.get("status")
            except Exception:
                task["validation_status"] = "INVALID"

        active_claim = read_active_claim(data_root, task.get("task_id", ""))
        if active_claim is not None:
            task["status"] = "CLAIMED_BY_CHAT"
            task["claim"] = active_claim
        elif task.get("status") == "CLAIMED_BY_CHAT":
            task["status"] = "WAITING_FOR_CHAT"
            task.pop("claim", None)
            write_json(path, task)

        if status and task.get("status") != status:
            continue

        items.append(task)

    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return items


def get_context(data_root: Path, task_id: str) -> tuple[dict[str, Any], Path, ToolContext]:
    task = load_task(data_root, task_id)
    tdir = task_dir(data_root, task_id)
    business_run_dir = Path(task["business_run_dir"])
    input_payload = task.get("semantic_review_request")
    if not isinstance(input_payload, dict):
        input_payload = load_json(tdir / "input.json")

    ctx = ToolContext(
        business_run_dir=business_run_dir,
        ai_job_dir=tdir,
        input_payload=input_payload,
    )
    return task, tdir, ctx


def next_tool_index(tdir: Path) -> int:
    tool_dir = tdir / "tool_calls"
    tool_dir.mkdir(exist_ok=True)
    existing = list(tool_dir.glob("*_request.json"))
    if not existing:
        return 1
    indexes = []
    for p in existing:
        try:
            indexes.append(int(p.name.split("_", 1)[0]))
        except Exception:
            continue
    return (max(indexes) if indexes else 0) + 1


def run_tool(
    *,
    data_root: Path,
    task_id: str,
    claim_id: str,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    require_claim(data_root, task_id, claim_id)
    task, tdir, ctx = get_context(data_root, task_id)
    index = next_tool_index(tdir)
    req = {
        "tool_call_id": f"chat-{index}",
        "name": name,
        "arguments": arguments,
        "requested_at": now_utc(),
        "execution_mode": "INTERACTIVE_CHAT",
    }
    write_json(tdir / "tool_calls" / f"{index:04d}_request.json", req)
    result = execute_tool(name, ctx, arguments)
    write_json(tdir / "tool_calls" / f"{index:04d}_result.json", result)

    task["last_tool_at"] = now_utc()
    task["tool_call_count"] = index
    task["status"] = "CLAIMED_BY_CHAT"
    task["claim"] = read_active_claim(data_root, task_id)
    write_json(tdir / "chat_task.json", task)
    return result


def build_evidence_manifest(
    *,
    task: dict[str, Any],
    tdir: Path,
) -> dict[str, Any]:
    evidence_dir = tdir / "evidence"
    evidence: list[dict[str, Any]] = []
    if evidence_dir.exists():
        for meta_path in sorted(evidence_dir.glob("*.json")):
            try:
                evidence.append(load_json(meta_path))
            except Exception:
                continue

    manifest = {
        "ai_job_id": task["task_id"],
        "task_type": task["task_type"],
        "execution_mode": "INTERACTIVE_CHAT",
        "generated_at": now_utc(),
        "ai_job_dir": str(tdir.resolve()),
        "evidence_count": len(evidence),
        "evidence": evidence,
    }
    write_json(tdir / "evidence_manifest.json", manifest)

    business_run_dir = Path(task["business_run_dir"])
    write_json(
        business_run_dir / "semantic_evidence_manifest.json",
        manifest,
    )
    return manifest


def resume_pipeline(task: dict[str, Any]) -> dict[str, Any]:
    pipeline_job_id = task.get("pipeline_job_id")
    if not pipeline_job_id:
        return {
            "status": "SKIPPED",
            "reason": "task has no pipeline_job_id",
        }

    user = os.environ.get("RUNTIME_USER")
    password = os.environ.get("RUNTIME_PASSWORD")
    if not user or not password:
        return {
            "status": "SKIPPED",
            "reason": "Runtime Basic Auth is not available in environment",
        }

    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    url = (
        "http://127.0.0.1:8010"
        f"/api/market-map-runs/{pipeline_job_id}/resume-after-chat"
    )
    response = requests.post(
        url,
        headers={"Authorization": f"Basic {token}"},
        timeout=20,
    )
    try:
        body = response.json()
    except Exception:
        body = {"text": response.text[:1000]}

    if response.status_code >= 400:
        return {
            "status": "FAIL",
            "http_status": response.status_code,
            "response": body,
        }
    return {
        "status": "RUNNING",
        "http_status": response.status_code,
        "response": body,
    }


def submit_resolution(
    *,
    data_root: Path,
    task_id: str,
    claim_id: str,
    resolution_file: Path,
    auto_resume: bool,
) -> dict[str, Any]:
    require_claim(data_root, task_id, claim_id)
    task, tdir, _ = get_context(data_root, task_id)
    business_run_dir = Path(task["business_run_dir"])

    manifest = build_evidence_manifest(task=task, tdir=tdir)

    structured = load_json(resolution_file)
    write_json(tdir / "structured_output.json", structured)

    validation = validate_resolution(
        business_run_dir,
        tdir / "structured_output.json",
    )
    write_json(tdir / "validation_result.json", validation)

    task["validation_status"] = validation.get("status")
    task["completed_at"] = now_utc()

    if validation.get("status") == "PASS":
        task["status"] = "PASS"
        cp = claim_path(data_root, task_id)
        if cp.exists():
            cp.unlink()
        task.pop("claim", None)
    else:
        task["status"] = "CLAIMED_BY_CHAT"
        task["claim"] = read_active_claim(data_root, task_id)

    task["evidence_count"] = manifest.get("evidence_count", 0)
    write_json(tdir / "chat_task.json", task)

    resume_result = None
    if auto_resume and validation.get("status") == "PASS":
        resume_result = resume_pipeline(task)
        task["resume_result"] = resume_result
        write_json(tdir / "chat_task.json", task)

    return {
        "task_id": task_id,
        "status": task["status"],
        "validation": validation,
        "resume": resume_result,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", default="./runtime_data")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list")
    p_list.add_argument("--status", default="WAITING_FOR_CHAT")

    p_claim_next = sub.add_parser("claim-next")
    p_claim_next.add_argument("--claimant", default="chatgpt")

    p_claim = sub.add_parser("claim")
    p_claim.add_argument("--task-id", required=True)
    p_claim.add_argument("--claimant", default="chatgpt")

    p_release = sub.add_parser("release")
    p_release.add_argument("--task-id", required=True)
    p_release.add_argument("--claim-id", required=True)

    p_show = sub.add_parser("show")
    p_show.add_argument("--task-id", required=True)

    p_search = sub.add_parser("search")
    p_search.add_argument("--task-id", required=True)
    p_search.add_argument("--claim-id", required=True)
    p_search.add_argument("--conflict-id", required=True)
    p_search.add_argument("--keyword", default="转债")
    p_search.add_argument("--start-date")
    p_search.add_argument("--end-date")

    p_fetch = sub.add_parser("fetch")
    p_fetch.add_argument("--task-id", required=True)
    p_fetch.add_argument("--claim-id", required=True)
    p_fetch.add_argument("--evidence-id", required=True)

    p_submit = sub.add_parser("submit")
    p_submit.add_argument("--task-id", required=True)
    p_submit.add_argument("--claim-id", required=True)
    p_submit.add_argument("--resolution-file", required=True)
    p_submit.add_argument("--no-resume", action="store_true")

    args = parser.parse_args()
    data_root = Path(args.data_root).resolve()

    if args.command == "list":
        result = list_tasks(data_root, status=args.status or None)
    elif args.command == "claim-next":
        result = claim_next_task(data_root, claimant=args.claimant)
        if result is None:
            result = {"status": "NO_PENDING_CHAT_TASK"}
    elif args.command == "claim":
        result = claim_task(
            data_root,
            args.task_id,
            claimant=args.claimant,
        )
    elif args.command == "release":
        result = release_claim(
            data_root,
            args.task_id,
            args.claim_id,
        )
    elif args.command == "show":
        result = load_task(data_root, args.task_id)
    elif args.command == "search":
        result = run_tool(
            data_root=data_root,
            task_id=args.task_id,
            claim_id=args.claim_id,
            name="evidence_search",
            arguments={
                "conflict_id": args.conflict_id,
                "keyword": args.keyword,
                "start_date": args.start_date,
                "end_date": args.end_date,
            },
        )
    elif args.command == "fetch":
        result = run_tool(
            data_root=data_root,
            task_id=args.task_id,
            claim_id=args.claim_id,
            name="evidence_fetch",
            arguments={"evidence_id": args.evidence_id},
        )
    elif args.command == "submit":
        result = submit_resolution(
            data_root=data_root,
            task_id=args.task_id,
            claim_id=args.claim_id,
            resolution_file=Path(args.resolution_file).resolve(),
            auto_resume=not args.no_resume,
        )
    else:
        raise RuntimeError(args.command)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
