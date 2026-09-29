from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from runtime.opportunity.incremental_information_controller import (
    run_information_controller,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-date")
    parser.add_argument("--execution-mode", default="AUTO_API")
    parser.add_argument("--semantic-max-waves", type=int, default=3)
    parser.add_argument("--path-limit", type=int, default=5)
    args = parser.parse_args()

    root = Path(os.environ.get("RUNTIME_ROOT", Path.cwd()))
    data_root = Path(
        os.environ.get("RUNTIME_DATA_ROOT", root / "runtime_data")
    )
    deployment = json.loads(
        (data_root / "deployment_manifest.json").read_text(encoding="utf-8")
    )
    target_date = args.target_date or datetime.now(
        ZoneInfo("Asia/Shanghai")
    ).date().isoformat()
    provider = os.environ.get("AI_PROVIDER", "").strip()
    model = os.environ.get("AI_MODEL", "").strip()
    if not provider or not model:
        raise RuntimeError("AI provider/model is not configured")

    result = run_information_controller(
        root=root,
        data_root=data_root,
        target_date=target_date,
        deployment=deployment,
        execution_mode=args.execution_mode,
        provider_name=provider,
        model=model,
        semantic_max_waves=args.semantic_max_waves,
        path_limit=min(max(1, args.path_limit), 5),
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("status") in {"PASS", "WAITING_FOR_CHAT"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
