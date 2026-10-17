from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from runtime.ai_runtime import chat_bridge as native
from runtime.ai_runtime.tasks import get_task_spec

PUBLIC_PREFIX = "convertible--"
TASK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,80}$")
MAX_CHUNK = 20000
MAX_WAITING_AGE_SECONDS = 24 * 60 * 60
CLAIMANT = "server-control-chat-bridge-v02"


def _runtime_root() -> Path:
    return Path(
        os.environ.get("ARBITRAGE_RUNTIME_ROOT", "/home/admin/projects/arbitrage-runtime")
    ).resolve()


def _data_root(root: Path | None = None) -> Path:
    runtime_root = root or _runtime_root()
    return Path(
        os.environ.get("RUNTIME_DATA_ROOT", str(runtime_root / "runtime_data"))
    ).resolve()


def _public_id(native_task_id: str) -> str:
    value = PUBLIC_PREFIX + native_task_id
    if len(value) > 80 or not TASK_ID_RE.fullmatch(value):
        raise ValueError("native task id cannot be represented by CHAT Bridge")
    return value


def _native_id(public_task_id: str) -> str:
    if not isinstance(public_task_id, str) or not TASK_ID_RE.fullmatch(public_task_id):
        raise ValueError("invalid task_id")
    if not public_task_id.startswith(PUBLIC_PREFIX):
        raise ValueError("task_id is not a convertible CHAT task")
    native_task_id = public_task_id[len(PUBLIC_PREFIX) :]
    if not native_task_id or not TASK_ID_RE.fullmatch(native_task_id):
        raise ValueError("invalid convertible native task_id")
    return native_task_id


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _is_fresh(task: dict[str, Any], now_utc: datetime | None = None) -> bool:
    created = _parse_time(task.get("created_at"))
    if created is None:
        return False
    current = now_utc or datetime.now(timezone.utc)
    age = (current - created).total_seconds()
    return -300 <= age <= MAX_WAITING_AGE_SECONDS


def _task_dir(data_root: Path, native_task_id: str) -> Path:
    return data_root / "chat_tasks" / native_task_id


def _load_task(data_root: Path, native_task_id: str) -> dict[str, Any]:
    return native.load_task(data_root, native_task_id)


def _read_optional_json(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _task_spec(root: Path, task: dict[str, Any]):
    task_type = str(task.get("task_type") or "")
    if not task_type:
        raise ValueError("convertible CHAT task has no task_type")
    return get_task_spec(task_type, root)


def _system_prompt(root: Path, data_root: Path, native_task_id: str, task: dict[str, Any]) -> str:
    snapshot = _task_dir(data_root, native_task_id) / "prompt_snapshot.md"
    if snapshot.is_file():
        return snapshot.read_text(encoding="utf-8")
    spec = _task_spec(root, task)
    return spec.prompt_path.read_text(encoding="utf-8")


def _messages(data_root: Path, native_task_id: str, task: dict[str, Any]) -> list[dict[str, str]]:
    tdir = _task_dir(data_root, native_task_id)
    input_payload = task.get("semantic_review_request")
    if not isinstance(input_payload, dict):
        input_payload = _read_optional_json(tdir / "input.json")
    prefetched = _read_optional_json(tdir / "engineering_prefetched_evidence.json")
    evidence_catalog = _read_optional_json(tdir / "evidence_catalog.json")
    initial = {
        "chat_instruction": task.get("chat_instruction"),
        "task_type": task.get("task_type"),
        "expected_output": task.get("expected_output"),
        "input": input_payload,
        "engineering_prefetched_evidence": prefetched,
        "evidence_catalog": evidence_catalog,
    }
    messages: list[dict[str, str]] = [
        {
            "role": "user",
            "content": json.dumps(initial, ensure_ascii=False, sort_keys=True),
        }
    ]

    tool_dir = tdir / "tool_calls"
    if tool_dir.is_dir():
        indexes: set[str] = set()
        for path in tool_dir.glob("*_request.json"):
            indexes.add(path.name.split("_", 1)[0])
        for idx in sorted(indexes):
            req = _read_optional_json(tool_dir / f"{idx}_request.json")
            result = _read_optional_json(tool_dir / f"{idx}_result.json")
            messages.append(
                {
                    "role": "tool",
                    "content": json.dumps(
                        {"request": req, "result": result},
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                }
            )
    return messages


def _summary(root: Path, data_root: Path, native_task_id: str, task: dict[str, Any]) -> dict[str, Any]:
    messages = _messages(data_root, native_task_id, task)
    spec = _task_spec(root, task)
    return {
        "task_id": _public_id(native_task_id),
        "scope": "convertible",
        "status": task.get("status"),
        "created_at": task.get("created_at"),
        "provider": "convertible-native-chat",
        "response_exists": task.get("status") == "PASS",
        "system_prompt_chars": len(_system_prompt(root, data_root, native_task_id, task)),
        "message_count": len(messages),
        "messages": [
            {"index": i, "role": row["role"], "content_chars": len(row["content"])}
            for i, row in enumerate(messages)
        ],
        "model_config": {"model": "chatgpt-chat"},
        "response_contract": {
            "structured_output": "object|null",
            "tool_calls": "array",
            "finish_reason": "string",
            "model": "string(optional)",
            "usage": "object(optional)",
        },
        "has_tools": bool(spec.allowed_tools),
        "has_output_schema": spec.output_schema is not None,
        "native_task_type": task.get("task_type"),
        "native_status": task.get("status"),
    }


def next_task(*, root: Path | None = None, data_root: Path | None = None) -> dict[str, Any]:
    runtime_root = (root or _runtime_root()).resolve()
    runtime_data = (data_root or _data_root(runtime_root)).resolve()
    rows: list[tuple[str, str, dict[str, Any]]] = []
    stale_skipped = 0
    current = datetime.now(timezone.utc)
    for task in native.list_tasks(runtime_data, status="WAITING_FOR_CHAT"):
        native_task_id = str(task.get("task_id") or "")
        if not native_task_id or not TASK_ID_RE.fullmatch(native_task_id):
            continue
        if not _is_fresh(task, current):
            stale_skipped += 1
            continue
        rows.append((str(task.get("created_at") or ""), native_task_id, task))
    if not rows:
        return {
            "ok": True,
            "action": "chat-task-next",
            "found": False,
            "scope": "convertible",
            "stale_skipped": stale_skipped,
        }
    rows.sort(key=lambda row: (row[0], row[1]))
    _, native_task_id, task = rows[0]
    return {
        "ok": True,
        "action": "chat-task-next",
        "found": True,
        "stale_skipped": stale_skipped,
        "task": _summary(runtime_root, runtime_data, native_task_id, task),
    }


def _chunk(text: str, offset: int, limit: int) -> dict[str, Any]:
    if not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be >= 0")
    if not isinstance(limit, int) or not 1 <= limit <= MAX_CHUNK:
        raise ValueError(f"limit must be 1..{MAX_CHUNK}")
    total = len(text)
    start = min(offset, total)
    end = min(total, start + limit)
    return {
        "offset": start,
        "limit": limit,
        "total_chars": total,
        "next_offset": end if end < total else None,
        "complete": end >= total,
        "content": text[start:end],
    }


def read_task(
    *,
    task_id: str,
    section: Literal["meta", "system_prompt", "message", "output_schema", "tools"] = "meta",
    message_index: int = 0,
    offset: int = 0,
    limit: int = MAX_CHUNK,
    root: Path | None = None,
    data_root: Path | None = None,
) -> dict[str, Any]:
    runtime_root = (root or _runtime_root()).resolve()
    runtime_data = (data_root or _data_root(runtime_root)).resolve()
    native_task_id = _native_id(task_id)
    task = _load_task(runtime_data, native_task_id)
    if section == "meta":
        return {
            "ok": True,
            "action": "chat-task-read",
            "section": "meta",
            "task": _summary(runtime_root, runtime_data, native_task_id, task),
        }
    spec = _task_spec(runtime_root, task)
    if section == "output_schema":
        return {
            "ok": True,
            "action": "chat-task-read",
            "section": section,
            "task_id": task_id,
            "value": spec.output_schema,
        }
    if section == "tools":
        return {
            "ok": True,
            "action": "chat-task-read",
            "section": section,
            "task_id": task_id,
            "value": spec.allowed_tools,
        }
    if section == "system_prompt":
        out = _chunk(_system_prompt(runtime_root, runtime_data, native_task_id, task), offset, limit)
        out.update({"ok": True, "action": "chat-task-read", "section": section, "task_id": task_id})
        return out
    if section != "message":
        raise ValueError("invalid section")
    messages = _messages(runtime_data, native_task_id, task)
    if not isinstance(message_index, int) or message_index < 0 or message_index >= len(messages):
        raise ValueError("message_index out of range")
    row = messages[message_index]
    out = _chunk(row["content"], offset, limit)
    out.update(
        {
            "ok": True,
            "action": "chat-task-read",
            "section": section,
            "task_id": task_id,
            "message_index": message_index,
            "role": row["role"],
        }
    )
    return out


def _canonical_response(response: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(response, dict):
        raise ValueError("response must be an object")
    allowed = {"structured_output", "tool_calls", "finish_reason", "model", "usage"}
    unknown = sorted(set(response) - allowed)
    if unknown:
        raise ValueError("unknown response fields: " + ", ".join(unknown))
    structured = response.get("structured_output")
    tool_calls = response.get("tool_calls", [])
    finish_reason = response.get("finish_reason")
    if structured is not None and not isinstance(structured, dict):
        raise ValueError("structured_output must be object or null")
    if not isinstance(tool_calls, list):
        raise ValueError("tool_calls must be an array")
    if not isinstance(finish_reason, str) or not finish_reason or len(finish_reason) > 80:
        raise ValueError("finish_reason must be a non-empty string <=80 chars")
    if structured is not None and tool_calls:
        raise ValueError("submit exactly one of structured_output or tool_calls")
    if structured is None and not tool_calls:
        raise ValueError("response must include structured_output or tool_calls")
    model = response.get("model")
    if model is not None and (not isinstance(model, str) or len(model) > 200):
        raise ValueError("model must be a string <=200 chars")
    usage = response.get("usage")
    if usage is not None and not isinstance(usage, dict):
        raise ValueError("usage must be an object")
    return {
        "structured_output": structured,
        "tool_calls": tool_calls,
        "finish_reason": finish_reason,
        **({"model": model} if model is not None else {}),
        **({"usage": usage} if usage is not None else {}),
    }


def _response_hash(response: dict[str, Any]) -> str:
    raw = json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _receipt_dir(data_root: Path, native_task_id: str) -> Path:
    path = _task_dir(data_root, native_task_id) / "server_control_adapter" / "receipts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _receipt_path(data_root: Path, native_task_id: str, response_hash: str) -> Path:
    return _receipt_dir(data_root, native_task_id) / f"{response_hash}.json"


def _ensure_claim(data_root: Path, native_task_id: str) -> str:
    active = native.read_active_claim(data_root, native_task_id)
    if active is not None:
        if active.get("claimant") != CLAIMANT:
            raise RuntimeError("convertible CHAT task is already claimed by another consumer")
        claim_id = str(active.get("claim_id") or "")
        if not claim_id:
            raise RuntimeError("active claim has no claim_id")
        native.require_claim(data_root, native_task_id, claim_id)
        return claim_id
    claimed = native.claim_task(data_root, native_task_id, claimant=CLAIMANT)
    claim = claimed.get("claim") or {}
    claim_id = str(claim.get("claim_id") or "")
    if not claim_id:
        raise RuntimeError("failed to claim convertible CHAT task")
    return claim_id


def _normalize_tool_call(call: Any) -> dict[str, Any]:
    if not isinstance(call, dict):
        raise ValueError("tool call must be an object")
    name = call.get("name")
    arguments = call.get("arguments", {})
    call_id = call.get("id") or call.get("tool_call_id")
    if not isinstance(name, str) or not name:
        raise ValueError("tool call name is required")
    if isinstance(arguments, str):
        arguments = json.loads(arguments)
    if not isinstance(arguments, dict):
        raise ValueError("tool call arguments must be an object")
    if call_id is not None and not isinstance(call_id, str):
        raise ValueError("tool call id must be a string")
    return {"id": call_id, "name": name, "arguments": arguments}


def submit_task(
    *,
    task_id: str,
    response: dict[str, Any],
    root: Path | None = None,
    data_root: Path | None = None,
) -> dict[str, Any]:
    runtime_root = (root or _runtime_root()).resolve()
    runtime_data = (data_root or _data_root(runtime_root)).resolve()
    native_task_id = _native_id(task_id)
    task = _load_task(runtime_data, native_task_id)
    if not _is_fresh(task):
        raise ValueError("convertible CHAT task is stale or has invalid created_at")

    normalized = _canonical_response(response)
    digest = _response_hash(normalized)
    receipt_path = _receipt_path(runtime_data, native_task_id, digest)
    if receipt_path.is_file():
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        result = dict(receipt.get("result") or {})
        result["idempotent"] = True
        return result

    if task.get("status") == "PASS":
        raise ValueError("convertible CHAT task is already complete with a different response")
    if task.get("status") not in {"WAITING_FOR_CHAT", "CLAIMED_BY_CHAT"}:
        raise ValueError("convertible CHAT task is not waiting for CHAT")

    spec = _task_spec(runtime_root, task)
    normalized_tool_calls: list[dict[str, Any]] = []
    if normalized["tool_calls"]:
        allowed = {str(tool.get("name") or "") for tool in spec.allowed_tools}
        for raw_call in normalized["tool_calls"]:
            call = _normalize_tool_call(raw_call)
            if call["name"] not in allowed:
                raise ValueError(f"tool is not allowed for this task: {call['name']}")
            normalized_tool_calls.append(call)

    claim_id = _ensure_claim(runtime_data, native_task_id)

    if normalized_tool_calls:
        tool_results: list[dict[str, Any]] = []
        for call in normalized_tool_calls:
            result = native.run_tool(
                data_root=runtime_data,
                task_id=native_task_id,
                claim_id=claim_id,
                name=call["name"],
                arguments=call["arguments"],
            )
            tool_results.append(
                {
                    "id": call["id"],
                    "name": call["name"],
                    "result": result,
                }
            )
        bridge_result: dict[str, Any] = {
            "ok": True,
            "action": "chat-task-submit",
            "task_id": task_id,
            "status": "WAITING_FOR_CHAT",
            "continuation": True,
            "tool_results": tool_results,
            "idempotent": False,
        }
    else:
        adapter_dir = _task_dir(runtime_data, native_task_id) / "server_control_adapter"
        adapter_dir.mkdir(parents=True, exist_ok=True)
        resolution_path = adapter_dir / f"resolution-{digest}.json"
        resolution_path.write_text(
            json.dumps(normalized["structured_output"], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        native_result = native.submit_resolution(
            data_root=runtime_data,
            task_id=native_task_id,
            claim_id=claim_id,
            resolution_file=resolution_path,
            auto_resume=True,
        )
        bridge_result = {
            "ok": True,
            "action": "chat-task-submit",
            "task_id": task_id,
            "status": native_result.get("status"),
            "validation": native_result.get("validation"),
            "resume": native_result.get("resume"),
            "continuation": native_result.get("status") != "PASS",
            "idempotent": False,
        }

    receipt_path.write_text(
        json.dumps(
            {"response_hash": digest, "response": normalized, "result": bridge_result},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return bridge_result


def handle_request(payload: dict[str, Any], *, root: Path | None = None, data_root: Path | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("request must be an object")
    action = payload.get("action")
    if action == "next":
        if set(payload) != {"action"}:
            raise ValueError("next accepts no additional fields")
        return next_task(root=root, data_root=data_root)
    if action == "read":
        allowed = {"action", "task_id", "section", "message_index", "offset", "limit"}
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ValueError("unknown read fields: " + ", ".join(unknown))
        return read_task(
            task_id=payload.get("task_id"),
            section=payload.get("section", "meta"),
            message_index=payload.get("message_index", 0),
            offset=payload.get("offset", 0),
            limit=payload.get("limit", MAX_CHUNK),
            root=root,
            data_root=data_root,
        )
    if action == "submit":
        if set(payload) != {"action", "task_id", "response"}:
            raise ValueError("submit requires exactly action, task_id and response")
        return submit_task(
            task_id=payload.get("task_id"),
            response=payload.get("response"),
            root=root,
            data_root=data_root,
        )
    raise ValueError("unsupported adapter action")


def main() -> None:
    raw = sys.stdin.read()
    payload = json.loads(raw or "{}")
    try:
        result = handle_request(payload)
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
    except Exception as exc:
        print(
            json.dumps(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            )
        )
        raise SystemExit(2)


if __name__ == "__main__":
    main()
