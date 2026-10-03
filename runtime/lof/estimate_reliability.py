from __future__ import annotations

from datetime import datetime, time as clock_time
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
CSI_COMPONENT_METHOD = "CSI_COMPONENT_WEIGHT_PREV_CLOSE"


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI_TZ)
    return parsed.astimezone(SHANGHAI_TZ)


def is_domestic_market_refresh_time(value: Any) -> bool:
    parsed = _datetime(value)
    if parsed is None or parsed.weekday() >= 5:
        return False
    current = parsed.time()
    return (
        clock_time(9, 25) <= current <= clock_time(11, 35)
        or clock_time(12, 55) <= current <= clock_time(15, 5)
    )


def is_reliable_available_estimate(row: dict) -> bool:
    status = str(row.get("estimated_nav_status") or "")
    nav = _decimal(row.get("estimated_nav"))
    if status != "AVAILABLE" or nav is None or nav <= 0:
        return False

    if row.get("estimated_nav_method") == CSI_COMPONENT_METHOD:
        # Historical CSI component reconstruction once stamped the calculation
        # clock as estimate time. That kept an end-of-day constituent basket
        # falsely AVAILABLE for hours after the A-share session. Component
        # observations qualify as reliable only inside the domestic refresh
        # window. New snapshots carry proxy_time; old ones fall back to
        # estimated_nav_time and are thereby cleaned during rebuild.
        effective_time = (
            row.get("estimated_nav_proxy_time")
            or row.get("estimated_nav_time")
        )
        if not is_domestic_market_refresh_time(effective_time):
            return False

    return True
