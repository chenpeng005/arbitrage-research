from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .provider import ProviderResponse


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


class FileChatProvider:
    """Filesystem handoff provider for a human-triggered ChatGPT session.

    It never performs an external model/API request. The caller blocks until the
    active ChatGPT session writes response.json for the emitted task.
    """

    name = "chatgpt-chat"

    def __init__(
        self,
        *,
        root: str | Path | None = None,
        scope: str | None = None,
        timeout_seconds: int | None = None,
        poll_seconds: float = 0.5,
    ) -> None:
        self.root = Path(
            root
            or os.environ.get("CHAT_PROVIDER_ROOT")
            or "/home/admin/projects/server-control/state/nightly-chat-bridge"
        )
        self.scope = str(scope or os.environ.get("CHAT_PROVIDER_SCOPE") or "generic")
        self.timeout_seconds = int(
            timeout_seconds
            or os.environ.get("CHAT_PROVIDER_TIMEOUT_SECONDS")
            or 14400
        )
        self.poll_seconds = poll_seconds
        self.root.mkdir(parents=True, exist_ok=True)

    def complete(
        self,
        *,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        output_schema: dict[str, Any],
        model_config: dict[str, Any],
    ) -> ProviderResponse:
        task_id = (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            + "-"
            + uuid.uuid4().hex[:10]
        )
        task_dir = self.root / "tasks" / task_id
        task_dir.mkdir(parents=True, exist_ok=False)
        task = {
            "task_id": task_id,
            "status": "WAITING_FOR_CHAT",
            "scope": self.scope,
            "created_at": _now(),
            "provider": self.name,
            "system_prompt": system_prompt,
            "messages": messages,
            "tools": tools,
            "output_schema": output_schema,
            "model_config": model_config,
            "response_contract": {
                "structured_output": "object|null",
                "tool_calls": "array",
                "finish_reason": "string",
                "model": "string(optional)",
                "usage": "object(optional)",
            },
        }
        _atomic_json(task_dir / "task.json", task)
        _atomic_json(
            self.root / "latest_waiting.json",
            {"task_id": task_id, "scope": self.scope, "created_at": task["created_at"]},
        )

        response_path = task_dir / "response.json"
        deadline = time.monotonic() + self.timeout_seconds
        while not response_path.exists():
            if time.monotonic() >= deadline:
                task["status"] = "TIMEOUT"
                task["finished_at"] = _now()
                _atomic_json(task_dir / "task.json", task)
                raise TimeoutError(f"Chat response timeout task_id={task_id}")
            time.sleep(self.poll_seconds)

        data = json.loads(response_path.read_text(encoding="utf-8"))
        if str(data.get("status") or "").upper() == "ERROR":
            raise RuntimeError(str(data.get("error") or "Chat response returned ERROR"))
        structured_output = data.get("structured_output")
        tool_calls = list(data.get("tool_calls") or [])
        if structured_output is not None and not isinstance(structured_output, dict):
            raise RuntimeError("Chat structured_output must be an object or null")
        if structured_output is None and not tool_calls:
            raise RuntimeError("Chat response must include structured_output or tool_calls")

        task["status"] = "COMPLETED"
        task["finished_at"] = _now()
        task["response_file"] = str(response_path)
        _atomic_json(task_dir / "task.json", task)
        return ProviderResponse(
            provider=self.name,
            model=str(data.get("model") or "GPT-5.6-Sol-Chat"),
            request_id=task_id,
            finish_reason=str(
                data.get("finish_reason")
                or ("tool_calls" if tool_calls else "stop")
            ),
            structured_output=structured_output,
            tool_calls=tool_calls,
            usage=dict(data.get("usage") or {}),
            raw_response={"task_id": task_id, "scope": self.scope},
        )
