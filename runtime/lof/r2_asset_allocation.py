from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import tempfile
import os
from urllib.request import Request, urlopen


SOURCE = "EASTMONEY_PINGZHONGDATA"
URL_TEMPLATE = "https://fund.eastmoney.com/pingzhongdata/{fund_code}.js"


@dataclass(frozen=True)
class AssetAllocationSnapshot:
    fund_code: str
    as_of_date: date
    fetched_at: datetime
    stock_weight: Decimal
    bond_weight: Decimal
    cash_weight: Decimal
    source: str = SOURCE

    def to_dict(self) -> dict:
        return {
            "fund_code": self.fund_code,
            "as_of_date": self.as_of_date.isoformat(),
            "fetched_at": self.fetched_at.isoformat(),
            "stock_weight": float(self.stock_weight),
            "bond_weight": float(self.bond_weight),
            "cash_weight": float(self.cash_weight),
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, value: dict) -> "AssetAllocationSnapshot":
        return cls(
            fund_code=str(value["fund_code"]),
            as_of_date=date.fromisoformat(str(value["as_of_date"])),
            fetched_at=datetime.fromisoformat(str(value["fetched_at"])),
            stock_weight=Decimal(str(value["stock_weight"])),
            bond_weight=Decimal(str(value["bond_weight"])),
            cash_weight=Decimal(str(value["cash_weight"])),
            source=str(value.get("source") or SOURCE),
        )


def _ratio(value) -> Decimal:
    try:
        result = Decimal(str(value)) / Decimal("100")
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"INVALID_ASSET_WEIGHT:{value}") from exc
    if result < 0:
        raise ValueError(f"NEGATIVE_ASSET_WEIGHT:{value}")
    return result


def parse_asset_allocation(
    text: str,
    *,
    fund_code: str,
    fetched_at: datetime,
) -> AssetAllocationSnapshot:
    match = re.search(
        r"var\s+Data_assetAllocation\s*=\s*(\{.*?\});/\*",
        text,
        flags=re.S,
    )
    if match is None:
        raise ValueError(f"ASSET_ALLOCATION_NOT_FOUND:{fund_code}")

    payload = json.loads(match.group(1))
    categories = payload.get("categories") or []
    series = payload.get("series") or []
    if not categories:
        raise ValueError(f"ASSET_ALLOCATION_DATE_MISSING:{fund_code}")
    index = len(categories) - 1

    values = {
        str(row.get("name") or ""): row.get("data") or []
        for row in series
        if isinstance(row, dict)
    }

    def latest(name: str):
        rows = values.get(name) or []
        if index >= len(rows):
            raise ValueError(
                f"ASSET_ALLOCATION_SERIES_MISSING:{fund_code}:{name}"
            )
        return rows[index]

    return AssetAllocationSnapshot(
        fund_code=fund_code,
        as_of_date=date.fromisoformat(str(categories[index])),
        fetched_at=fetched_at,
        stock_weight=_ratio(latest("股票占净比")),
        bond_weight=_ratio(latest("债券占净比")),
        cash_weight=_ratio(latest("现金占净比")),
    )


def fetch_asset_allocation(
    fund_code: str,
    *,
    now: datetime,
    timeout: int = 8,
) -> AssetAllocationSnapshot:
    request = Request(
        URL_TEMPLATE.format(fund_code=fund_code),
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": f"https://fund.eastmoney.com/{fund_code}.html",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        text = response.read().decode("utf-8", errors="replace")
    return parse_asset_allocation(
        text,
        fund_code=fund_code,
        fetched_at=now,
    )


def is_cash_heavy_candidate(
    snapshot: AssetAllocationSnapshot,
    *,
    min_stock_weight: Decimal = Decimal("0.70"),
    max_stock_weight: Decimal = Decimal("0.80"),
    max_bond_weight: Decimal = Decimal("0.05"),
    min_cash_weight: Decimal = Decimal("0.20"),
) -> bool:
    return (
        min_stock_weight <= snapshot.stock_weight < max_stock_weight
        and snapshot.bond_weight <= max_bond_weight
        and snapshot.cash_weight >= min_cash_weight
    )


class AssetAllocationStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.path = self.root / "r2b2_asset_allocation.json"

    def load(self) -> dict[str, AssetAllocationSnapshot]:
        if not self.path.is_file():
            return {}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        return {
            str(code): AssetAllocationSnapshot.from_dict(value)
            for code, value in (payload.get("rows") or {}).items()
        }

    def persist(
        self,
        rows: dict[str, AssetAllocationSnapshot],
        *,
        generated_at: datetime,
    ) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "R2B2_ASSET_ALLOCATION_V1",
            "generated_at": generated_at.isoformat(),
            "rows": {
                code: value.to_dict()
                for code, value in sorted(rows.items())
            },
        }
        text = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ) + "\n"
        fd, temp_name = tempfile.mkstemp(
            prefix=".r2b2_asset_allocation.",
            suffix=".tmp",
            dir=str(self.root),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
