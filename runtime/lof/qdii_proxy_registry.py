from __future__ import annotations

from dataclasses import dataclass
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
        )
        result[entry.fund_code] = entry
    return result
