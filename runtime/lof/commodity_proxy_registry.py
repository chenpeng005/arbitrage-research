from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json
from pathlib import Path


@dataclass(frozen=True)
class CommodityProxyComponent:
    history_symbol: str
    live_market: str
    live_code: str
    weight: Decimal


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
    anchor_mode: str = "HISTORY_CLOSE"
    resolver_method: str | None = None
    evidence_as_of: str | None = None
    components: tuple[CommodityProxyComponent, ...] = ()
    unresolved_reason: str | None = None


def load_commodity_proxy_registry(
    path: str | Path,
) -> dict[str, CommodityProxyEntry]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    result: dict[str, CommodityProxyEntry] = {}
    for row in payload.get("entries") or []:
        exposure = row.get("exposure_ratio")
        components = tuple(
            CommodityProxyComponent(
                history_symbol=str(component["history_symbol"]),
                live_market=str(component["live_market"]),
                live_code=str(component["live_code"]),
                weight=Decimal(str(component["weight"])),
            )
            for component in (row.get("components") or [])
        )
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
            anchor_mode=str(row.get("anchor_mode") or "HISTORY_CLOSE"),
            resolver_method=row.get("resolver_method"),
            evidence_as_of=row.get("evidence_as_of"),
            components=components,
            unresolved_reason=row.get("unresolved_reason"),
        )
        result[entry.fund_code] = entry
    return result
