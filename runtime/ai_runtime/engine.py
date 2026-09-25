from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .deepseek_provider import DeepSeekProvider
from .mock_provider import MockProvider
from .provider import AIProvider
from .tasks import get_task_spec
from .tools.evidence import ToolContext, execute_tool
from .validation import run_validator


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

    if provider_name == "deepseek":
        return DeepSeekProvider(
            api_key=str(provider_config.get("api_key") or os.environ.get("AI_API_KEY") or ""),
            base_url=str(provider_config.get("base_url") or os.environ.get("AI_BASE_URL") or ""),
            timeout_seconds=int(provider_config.get("timeout_seconds") or 90),
            max_retries=int(provider_config.get("max_retries") or 2),
        )

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
        "requested_model": model_config.get("model"),
        "provider_model": None,
        "validator": spec.validator,
        "tool_rounds": 0,
        "error": None,
    }
    write_json(job_dir / "ai_job_metadata.json", metadata)

    try:
        provider = build_provider(provider_name, provider_config)
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": json.dumps(input_payload, ensure_ascii=False),
            }
        ]
        tool_ctx = ToolContext(
            business_run_dir=business_run_dir,
            ai_job_dir=job_dir,
            input_payload=input_payload,
        )

        provider_round_dir = job_dir / "provider_rounds"
        provider_round_dir.mkdir()
        tool_call_index = 0
        tool_rounds = 0
        final_response = None

        for provider_round in range(1, spec.max_tool_rounds + 2):
            response = provider.complete(
                system_prompt=prompt,
                messages=messages,
                tools=spec.allowed_tools,
                output_schema=spec.output_schema,
                model_config=model_config,
            )
            final_response = response
            write_json(
                provider_round_dir / f"{provider_round:04d}.json",
                response.raw_response,
            )

            if response.tool_calls:
                if tool_rounds >= spec.max_tool_rounds:
                    metadata["status"] = "NEEDS_REVIEW"
                    metadata["error"] = "AI Runtime reached max_tool_rounds"
                    metadata["tool_rounds"] = tool_rounds
                    metadata["completed_at"] = now_utc()
                    write_json(job_dir / "ai_job_metadata.json", metadata)
                    return metadata

                tool_rounds += 1
                messages.append(
                    {
                        "role": "assistant",
                        "tool_calls": response.tool_calls,
                    }
                )

                for call in response.tool_calls:
                    tool_call_index += 1
                    call_id = str(call.get("id") or f"tool-{tool_call_index}")
                    name = str(call.get("name") or "")
                    arguments = call.get("arguments") or {}
                    if isinstance(arguments, str):
                        arguments = json.loads(arguments)
                    if not isinstance(arguments, dict):
                        raise ValueError(f"Tool arguments must be an object: {name}")

                    req_payload = {
                        "tool_call_id": call_id,
                        "name": name,
                        "arguments": arguments,
                        "requested_at": now_utc(),
                    }
                    write_json(
                        job_dir / "tool_calls" / f"{tool_call_index:04d}_request.json",
                        req_payload,
                    )

                    result_payload = execute_tool(
                        name,
                        tool_ctx,
                        arguments,
                    )
                    write_json(
                        job_dir / "tool_calls" / f"{tool_call_index:04d}_result.json",
                        result_payload,
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "name": name,
                            "content": json.dumps(
                                result_payload,
                                ensure_ascii=False,
                            ),
                        }
                    )

                metadata["tool_rounds"] = tool_rounds
                write_json(job_dir / "ai_job_metadata.json", metadata)
                continue

            if response.structured_output is None:
                metadata["status"] = "FAIL"
                metadata["error"] = "Provider returned neither tool_calls nor structured_output"
                metadata["completed_at"] = now_utc()
                write_json(job_dir / "ai_job_metadata.json", metadata)
                return metadata

            structured_path = job_dir / "structured_output.json"
            write_json(structured_path, response.structured_output)
            break
        else:
            metadata["status"] = "NEEDS_REVIEW"
            metadata["error"] = "AI Runtime provider loop exhausted"
            metadata["completed_at"] = now_utc()
            write_json(job_dir / "ai_job_metadata.json", metadata)
            return metadata

        if final_response is None:
            raise RuntimeError("Provider loop produced no response")

        write_json(job_dir / "raw_provider_response.json", final_response.raw_response)

        evidence_items: list[dict[str, Any]] = []
        evidence_dir = job_dir / "evidence"
        if evidence_dir.exists():
            for meta_path in sorted(evidence_dir.glob("*.json")):
                evidence_items.append(
                    json.loads(meta_path.read_text(encoding="utf-8"))
                )

        evidence_manifest = {
            "ai_job_id": ai_job_id,
            "task_type": task_type,
            "generated_at": now_utc(),
            "ai_job_dir": str(job_dir),
            "evidence_count": len(evidence_items),
            "evidence": evidence_items,
        }
        write_json(job_dir / "evidence_manifest.json", evidence_manifest)
        write_json(
            business_run_dir / "semantic_evidence_manifest.json",
            evidence_manifest,
        )

        metadata["status"] = "VALIDATING"
        metadata["provider_request_id"] = final_response.request_id
        metadata["provider_model"] = final_response.model
        metadata["finish_reason"] = final_response.finish_reason
        metadata["usage"] = final_response.usage
        metadata["tool_rounds"] = tool_rounds
        metadata["evidence_count"] = len(evidence_items)
        write_json(job_dir / "ai_job_metadata.json", metadata)

        validation = run_validator(
            spec.validator,
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
