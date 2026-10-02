from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PROFILE_FILE = "r2c_nav_band_profiles.json"


def _compact_profile(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "mae_abs_return": value.get("mae_abs_return"),
        "up95": value.get("up95"),
        "up99": value.get("up99"),
        "group": value.get("group"),
        "reliability": value.get("reliability"),
        "history_end_date": value.get("history_end_date"),
        "return_sample_count": value.get("return_sample_count"),
        "walk_forward_count": value.get("walk_forward_count"),
        "walk_forward_exceed95": value.get("walk_forward_exceed95"),
        "walk_forward_exceed99": value.get("walk_forward_exceed99"),
    }


def load_r2c_t1_profile_summary(data_root: str | Path) -> dict[str, Any]:
    path = Path(data_root) / PROFILE_FILE
    if not path.is_file():
        return {
            "status": "UNAVAILABLE",
            "version": None,
            "generated_at": None,
            "row_count": 0,
            "rows": {},
        }

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "status": "INVALID",
            "version": None,
            "generated_at": None,
            "row_count": 0,
            "rows": {},
        }

    source_rows = payload.get("rows") or {}
    rows = {
        str(code): _compact_profile(value)
        for code, value in source_rows.items()
        if isinstance(value, dict)
    }
    return {
        "status": "PASS" if rows else "EMPTY",
        "version": payload.get("version"),
        "generated_at": payload.get("generated_at"),
        "row_count": len(rows),
        "rows": rows,
    }
