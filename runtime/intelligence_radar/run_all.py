from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from runtime.intelligence_radar.daily import china_now, run_jisilu_daily
from runtime.intelligence_radar.xueqiu_daily import run_xueqiu_daily
from runtime.intelligence_radar.xueqiu_shadow_archive import run_shadow_accumulation


def run_all(*, data_root: Path, run_date: str, run_mode: str | None = None) -> dict[str, Any]:
    today = china_now().date().isoformat()
    shadow = None
    shadow_error = None
    if run_date == today and (run_mode is None or str(run_mode).upper() == "LIVE"):
        try:
            shadow = run_shadow_accumulation(data_root=data_root)
        except Exception as exc:
            shadow_error = f"{type(exc).__name__}: {exc}"
    jisilu = run_jisilu_daily(data_root=data_root, run_date=run_date, run_mode=run_mode)
    xueqiu = run_xueqiu_daily(data_root=data_root, run_date=run_date, run_mode=run_mode)
    return {
        "run_date": run_date,
        "status": "OK" if jisilu.get("status") == "OK" and xueqiu.get("status") == "OK" else "PARTIAL",
        "shadow_collection": shadow,
        "shadow_collection_error": shadow_error,
        "sources": {"jisilu": jisilu, "xueqiu": xueqiu},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run formal Radar across Jisilu + Xueqiu.")
    parser.add_argument("--date", default=china_now().date().isoformat())
    parser.add_argument("--data-root", default=os.environ.get("RUNTIME_DATA_ROOT", "runtime_data"))
    parser.add_argument("--run-mode", choices=["AUTO", "LIVE", "BACKFILL"], default="AUTO")
    args = parser.parse_args()
    result = run_all(
        data_root=Path(args.data_root),
        run_date=args.date,
        run_mode=None if args.run_mode == "AUTO" else args.run_mode,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
