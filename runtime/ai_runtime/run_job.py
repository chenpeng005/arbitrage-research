from __future__ import annotations

import argparse
import json
from pathlib import Path

from .engine import run_ai_job


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--data-root", default="./runtime_data")
    parser.add_argument("--task-type", required=True)
    parser.add_argument("--input-file", required=True)
    parser.add_argument("--business-run-dir", required=True)
    parser.add_argument("--provider", default="mock")
    parser.add_argument("--model", default="mock-semantic-audit-v0")
    parser.add_argument("--mock-response-file")
    args = parser.parse_args()

    provider_config = {}
    if args.provider == "mock":
        provider_config["response_file"] = args.mock_response_file

    result = run_ai_job(
        root=Path(args.root).resolve(),
        data_root=Path(args.data_root).resolve(),
        task_type=args.task_type,
        input_file=Path(args.input_file).resolve(),
        business_run_dir=Path(args.business_run_dir).resolve(),
        provider_name=args.provider,
        provider_config=provider_config,
        model_config={"model": args.model},
    )
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if result.get("status") not in {"PASS", "NEEDS_REVIEW"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
