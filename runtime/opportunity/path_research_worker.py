"""CLI worker for exactly one PATH_RESEARCH task."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from runtime.opportunity.path_research_runner import run_one_path_research


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--data-root", default="./runtime_data")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--provider")
    parser.add_argument("--model")
    args = parser.parse_args()

    provider = args.provider or os.environ.get("AI_PROVIDER") or "deepseek"
    model = args.model or os.environ.get("AI_MODEL") or "deepseek-chat"

    result = run_one_path_research(
        root=Path(args.root).resolve(),
        data_root=Path(args.data_root).resolve(),
        task_id=args.task_id,
        provider_name=provider,
        model=model,
    )
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if result.get("status") not in {"PASS", "NEEDS_REVIEW"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
