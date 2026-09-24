from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.market_map.semantic_resolution import validate_resolution

from .mock_provider import MockProvider
from .provider import AIProvider
from .tasks import get_task_spec


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def create_ai_job_id(task_type: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = uuid.uuid4().hex[:6]
    prefix = "semantic" if task_type == "MARKET_MAP_SEMANTIC_AUDIT" else "ai"
    return f"{ts}_{prefix}_{suffix}"


def build_provider(provider_name: str, provider_config: dict[str, Any]) -> AIProvider:
    if provider_name == "mock":
        response_file = provider_config.get("response_file")
        if not response_file:
            raise ValueError("Mock provider requires response_file")
        return MockProvider(Path(response_file))
    raise ValueError(f"Unsupported AI provider: {provider_name}")


def run_ai_job(
    *,
    root: Path,
    data_root: Path,
    task_type: str,
    input_file: Path,
    business_run_dir: Path,
    provider_name: str,
    provider_config: dict[str, Any],
    model_config: dict[str, Any],
) -> dict[str, Any]:
    spec = get_task_spec(task_type, root)
    ai_job_id = create_ai_job_id(task_type)
    job_dir = data_root / "ai_jobs" / ai_job_id
    job_dir.mkdir(parents=True, exist_ok=False)
    (job_dir / "tool_calls").mkdir()

    input_payload = json.loads(input_file.read_text(encoding="utf-8"))
    prompt = spec.prompt_path.read_text(encoding="utf-8")

    shutil.copy2(input_file, job_dir / "input.json")
    (job_dir / "prompt_snapshot.md").write_text(prompt, encoding="utf-8")
    write_json(
        job_dir / "tool_policy.json",
        {
            "task_type": task_type,
            "allowed_tools": spec.allowed_tools,
            "max_tool_rounds": spec.max_tool_rounds,
            "timeout_seconds": spec.timeout_seconds,
        },
    )

    metadata = {
        "ai_job_id": ai_job_id,
        "task_type": task_type,
        "status": "RUNNING",
        "created_at": now_utc(),
        "completed_at": None,
        "business_run_dir": str(business_run_dir),
        "input_ref": str(input_file),
        "prompt_ref": "prompt_snapshot.md",
        "prompt_version": spec.prompt_path.stem,
        "provider": provider_name,
        "model": model_config.get("model"),
        "validator": spec.validator,
        "tool_rounds": 0,
        "error": None,
    }
    write_json(job_dir / "ai_job_metadata.json", metadata)

    try:
        provider = build_provider(provider_name, provider_config)
        response = provider.complete(
            system_prompt=prompt,
            messages=[
                {
                    "role": "user",
                    "content": json.dumps(input_payload, ensure_ascii=False),
                }
            ],
            tools=spec.allowed_tools,
            output_schema=spec.output_schema,
            model_config=model_config,
        )

        write_json(job_dir / "raw_provider_response.json", response.raw_response)

        if response.structured_output is None:
            metadata["status"] = "FAIL"
            metadata["error"] = "Provider returned no structured_output"
            metadata["completed_at"] = now_utc()
            write_json(job_dir / "ai_job_metadata.json", metadata)
            return metadata

        structured_path = job_dir / "structured_output.json"
        write_json(structured_path, response.structured_output)

        metadata["status"] = "VALIDATING"
        metadata["provider_request_id"] = response.request_id
        metadata["finish_reason"] = response.finish_reason
        metadata["usage"] = response.usage
        write_json(job_dir / "ai_job_metadata.json", metadata)

        validation = validate_resolution(
            business_run_dir,
            structured_path,
        )
        write_json(job_dir / "validation_result.json", validation)

        if validation.get("status") == "PASS":
            metadata["status"] = "PASS"
        else:
            metadata["status"] = "NEEDS_REVIEW"

        metadata["completed_at"] = now_utc()
        metadata["validation_status"] = validation.get("status")
        write_json(job_dir / "ai_job_metadata.json", metadata)
        return metadata

    except Exception as exc:
        metadata["status"] = "FAIL"
        metadata["error"] = f"{type(exc).__name__}: {exc}"
        metadata["completed_at"] = now_utc()
        write_json(job_dir / "ai_job_metadata.json", metadata)
        return metadata
