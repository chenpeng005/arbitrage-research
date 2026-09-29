from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json
from pathlib import Path


@dataclass(frozen=True)
class CommodityProxyEntry:
    fund_code: str
    benchmark: str
    status: str
    commodity_history_symbol: str | None = None
    commodity_live_market: str | None = None
    commodity_live_code: str | None = None
    currency: str | None = None
    exposure_ratio: Decimal | None = None
    proxy_quality: str = "UNKNOWN"


def load_commodity_proxy_registry(
    path: str | Path,
) -> dict[str, CommodityProxyEntry]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    result: dict[str, CommodityProxyEntry] = {}
    for row in payload.get("entries") or []:
        exposure = row.get("exposure_ratio")
        entry = CommodityProxyEntry(
            fund_code=str(row["fund_code"]),
            benchmark=str(row.get("benchmark") or ""),
            status=str(row.get("status") or "UNRESOLVED"),
            commodity_history_symbol=row.get("commodity_history_symbol"),
            commodity_live_market=row.get("commodity_live_market"),
            commodity_live_code=row.get("commodity_live_code"),
            currency=row.get("currency"),
            exposure_ratio=(
                Decimal(str(exposure)) if exposure is not None else None
            ),
            proxy_quality=str(row.get("proxy_quality") or "UNKNOWN"),
        )
        result[entry.fund_code] = entry
    return result
