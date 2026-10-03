from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json
from pathlib import Path
from typing import Literal


ProxyType = Literal[
    "DIRECT_INDEX",
    "ETF_SAME_INDEX",
    "ETF_HIGH_CORR",
    "UNRESOLVED",
]


@dataclass(frozen=True)
class QdiiProxyEntry:
    fund_code: str
    tracking_target: str
    proxy_type: ProxyType
    proxy_symbol: str | None
    history_symbol: str | None
    currency: str
    quality: str
    futures_overlay_market: str | None = None
    futures_overlay_code: str | None = None
    futures_overlay_quality: str | None = None
    exposure_ratio: Decimal | None = None
    unresolved_reason: str | None = None


def load_qdii_proxy_registry(path: str | Path) -> dict[str, QdiiProxyEntry]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    result: dict[str, QdiiProxyEntry] = {}
    for row in payload.get("entries") or []:
        entry = QdiiProxyEntry(
            fund_code=str(row["fund_code"]),
            tracking_target=str(row["tracking_target"]),
            proxy_type=row["proxy_type"],
            proxy_symbol=row.get("proxy_symbol"),
            history_symbol=row.get("history_symbol"),
            currency=str(row["currency"]),
            quality=str(row["quality"]),
            futures_overlay_market=row.get("futures_overlay_market"),
            futures_overlay_code=row.get("futures_overlay_code"),
            futures_overlay_quality=row.get("futures_overlay_quality"),
            exposure_ratio=(
                Decimal(str(row["exposure_ratio"]))
                if row.get("exposure_ratio") is not None
                else None
            ),
            unresolved_reason=row.get("unresolved_reason"),
        )
        result[entry.fund_code] = entry
    return result
