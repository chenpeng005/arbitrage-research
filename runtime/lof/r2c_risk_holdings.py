[Reading 513 lines from start (total: 513 lines, 0 remaining)]

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import hashlib
import html
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .r2a_holdings import fetch_period_holdings


ARCHIVE_URL = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
STORE_VERSION = "R2C_RISK_HOLDINGS_V1"
CB_PREFIXES_SH = {"110", "111", "113", "118"}
CB_PREFIXES_SZ = {"123", "127", "128"}


@dataclass(frozen=True)
class RiskPosition:
    asset_type: str
    symbol: str
    security_code: str
    name: str
    nav_weight: Decimal

    def to_dict(self) -> dict:
        return {
            "asset_type": self.asset_type,
            "symbol": self.symbol,
            "security_code": self.security_code,
            "name": self.name,
            "nav_weight": float(self.nav_weight),
        }

    @classmethod
    def from_dict(cls, value: dict) -> "RiskPosition":
        return cls(
            asset_type=str(value["asset_type"]),
            symbol=str(value["symbol"]),
            security_code=str(value["security_code"]),
            name=str(value.get("name") or ""),
            nav_weight=Decimal(str(value["nav_weight"])),
        )


@dataclass(frozen=True)
class RiskHoldingsSnapshot:
    fund_code: str
    as_of_date: date
    first_seen_at: datetime
    fetched_at: datetime
    positions: tuple[RiskPosition, ...]
    stock_weight: Decimal
    cb_weight: Decimal
    total_risk_weight: Decimal
    identity: str

    def to_dict(self) -> dict:
        return {
            "fund_code": self.fund_code,
            "as_of_date": self.as_of_date.isoformat(),
            "first_seen_at": self.first_seen_at.isoformat(),
            "fetched_at": self.fetched_at.isoformat(),
            "positions": [x.to_dict() for x in self.positions],
            "stock_weight": float(self.stock_weight),
            "cb_weight": float(self.cb_weight),
            "total_risk_weight": float(self.total_risk_weight),
            "identity": self.identity,
        }

    @classmethod
    def from_dict(cls, value: dict) -> "RiskHoldingsSnapshot":
        return cls(
            fund_code=str(value["fund_code"]),
            as_of_date=date.fromisoformat(str(value["as_of_date"])),
            first_seen_at=datetime.fromisoformat(str(value["first_seen_at"])),
            fetched_at=datetime.fromisoformat(str(value["fetched_at"])),
            positions=tuple(
                RiskPosition.from_dict(x)
                for x in value.get("positions", [])
            ),
            stock_weight=Decimal(str(value["stock_weight"])),
            cb_weight=Decimal(str(value["cb_weight"])),
            total_risk_weight=Decimal(str(value["total_risk_weight"])),
            identity=str(value["identity"]),
        )


def _full_period_candidates(as_of: date, count: int = 6) -> list[date]:
    values: list[date] = []
    for year in range(as_of.year, as_of.year - 4, -1):
        for month, day in ((12, 31), (6, 30)):
            value = date(year, month, day)
            if value <= as_of:
                values.append(value)
    values.sort(reverse=True)
    return values[:count]


def _fetch_text(
    fund_code: str,
    *,
    period: date,
    timeout: int,
) -> str:
    url = ARCHIVE_URL + "?" + urlencode(
        {
            "type": "zqcc",
            "code": fund_code,
            "topline": "500",
            "year": str(period.year),
            "month": str(period.month),
        }
    )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            request = Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": (
                        f"https://fundf10.eastmoney.com/"
                        f"ccmx_{fund_code}.html"
                    ),
                },
            )
            with urlopen(request, timeout=timeout) as response:
                return response.read().decode(
                    "utf-8",
                    errors="replace",
                )
        except Exception as exc:
            last_error = exc
            time.sleep(0.5 * (attempt + 1))
    assert last_error is not None
    raise last_error


def _plain_text(value: str) -> str:
    return html.unescape(
        re.sub(r"<.*?>", "", value, flags=re.S)
    ).strip()


def _is_convertible_bond(code: str, name: str) -> bool:
    if any(
        marker in name
        for marker in ("转债", "转2", "可转债")
    ):
        return True
    if len(code) != 6:
        return False
    return code[:3] in (CB_PREFIXES_SH | CB_PREFIXES_SZ)


def _cb_symbol(code: str) -> str | None:
    prefix = code[:3]
    if prefix in CB_PREFIXES_SH:
        return "sh" + code
    if prefix in CB_PREFIXES_SZ:
        return "sz" + code
    return None


def parse_bond_tables(
    text: str,
) -> dict[date, tuple[RiskPosition, ...]]:
    dates = [
        date.fromisoformat(value)
        for value in re.findall(
            r"截止至：<font[^>]*>(\d{4}-\d{2}-\d{2})</font>",
            text,
        )
    ]
    tables = re.findall(
        r"<tbody>(.*?)</tbody>",
        text,
        flags=re.S,
    )
    result: dict[date, tuple[RiskPosition, ...]] = {}

    for period, table in zip(dates, tables):
        rows: list[RiskPosition] = []
        for tr in re.findall(r"<tr>(.*?)</tr>", table, flags=re.S):
            cells = [
                _plain_text(value)
                for value in re.findall(
                    r"<td[^>]*>(.*?)</td>",
                    tr,
                    flags=re.S,
                )
            ]
            if len(cells) < 5:
                continue
            code = cells[1].strip()
            name = cells[2].strip()
            if not _is_convertible_bond(code, name):
                continue
            symbol = _cb_symbol(code)
            if symbol is None:
                continue
            try:
                weight = (
                    Decimal(
                        cells[3]
                        .replace("%", "")
                        .replace(",", "")
                        .strip()
                    )
                    / Decimal("100")
                )
            except Exception:
                continue
            if weight <= 0:
                continue
            rows.append(
                RiskPosition(
                    asset_type="CB",
                    symbol=symbol,
                    security_code=code,
                    name=name,
                    nav_weight=weight,
                )
            )
        rows.sort(key=lambda x: x.symbol)
        result[period] = tuple(rows)

    return result


def _identity(
    fund_code: str,
    period: date,
    positions: Iterable[RiskPosition],
) -> str:
    payload = {
        "fund_code": fund_code,
        "as_of_date": period.isoformat(),
        "positions": [
            [
                x.asset_type,
                x.symbol,
                format(x.nav_weight, "f"),
            ]
            for x in positions
        ],
    }
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def fetch_risk_holdings_snapshot(
    fund_code: str,
    *,
    now: datetime,
    timeout: int = 6,
    previous: RiskHoldingsSnapshot | None = None,
) -> RiskHoldingsSnapshot:
    errors: list[str] = []

    for period in _full_period_candidates(now.date()):
        try:
            text = _fetch_text(
                fund_code,
                period=period,
                timeout=timeout,
            )
            bond_tables = parse_bond_tables(text)
        except Exception as exc:
            errors.append(
                f"{period.isoformat()}:BOND:{type(exc).__name__}"
            )
            continue

        if period not in bond_tables:
            errors.append(
                f"{period.isoformat()}:NO_EXACT_BOND_TABLE"
            )
            continue

        cb_positions = bond_tables[period]
        try:
            stock_rows = fetch_period_holdings(
                fund_code,
                period=period,
                timeout=timeout,
            )
        except Exception:
            stock_rows = ()

        stock_positions = tuple(
            RiskPosition(
                asset_type="STOCK",
                symbol=row.symbol,
                security_code=row.security_code,
                name=row.name,
                nav_weight=row.nav_weight,
            )
            for row in stock_rows
        )

        positions = tuple(
            sorted(
                stock_positions + cb_positions,
                key=lambda x: (x.asset_type, x.symbol),
            )
        )
        if not positions:
            errors.append(
                f"{period.isoformat()}:NO_RISK_POSITIONS"
            )
            continue

        stock_weight = sum(
            (
                x.nav_weight
                for x in positions
                if x.asset_type == "STOCK"
            ),
            Decimal("0"),
        )
        cb_weight = sum(
            (
                x.nav_weight
                for x in positions
                if x.asset_type == "CB"
            ),
            Decimal("0"),
        )
        total = stock_weight + cb_weight
        identity = _identity(
            fund_code,
            period,
            positions,
        )
        first_seen = now
        if (
            previous is not None
            and previous.identity == identity
        ):
            first_seen = previous.first_seen_at

        return RiskHoldingsSnapshot(
            fund_code=fund_code,
            as_of_date=period,
            first_seen_at=first_seen,
            fetched_at=now,
            positions=positions,
            stock_weight=stock_weight,
            cb_weight=cb_weight,
            total_risk_weight=total,
            identity=identity,
        )

    raise ValueError(
        f"NO_RISK_HOLDINGS:{fund_code}:"
        + "|".join(errors)
    )


def snapshots_need_refresh(
    rows: dict[str, RiskHoldingsSnapshot],
    fund_codes: Iterable[str],
    *,
    now: datetime,
    refresh_seconds: int,
) -> bool:
    codes = set(fund_codes)
    if codes - set(rows):
        return True
    for code in codes:
        row = rows.get(code)
        if row is None:
            return True
        fetched = row.fetched_at
        current = now
        if fetched.tzinfo is None and current.tzinfo is not None:
            fetched = fetched.replace(tzinfo=current.tzinfo)
        if current.tzinfo is None and fetched.tzinfo is not None:
            current = current.replace(tzinfo=fetched.tzinfo)
        try:
            age = (current - fetched).total_seconds()
        except TypeError:
            return True
        if age < 0 or age >= refresh_seconds:
            return True
    return False


class RiskHoldingsStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.path = self.root / "risk_holdings_snapshot.json"

    def load(self) -> dict[str, RiskHoldingsSnapshot]:
        if not self.path.exists():
            return {}
        payload = json.loads(
            self.path.read_text(encoding="utf-8")
        )
        return {
            code: RiskHoldingsSnapshot.from_dict(value)
            for code, value
            in (payload.get("rows") or {}).items()
        }

    def refresh(
        self,
        fund_codes: Iterable[str],
        *,
        now: datetime,
        timeout: int = 6,
    ) -> tuple[
        dict[str, RiskHoldingsSnapshot],
        dict[str, str],
    ]:
        previous = self.load()
        result = dict(previous)
        errors: dict[str, str] = {}
        codes = sorted(set(fund_codes))

        with ThreadPoolExecutor(max_workers=1) as pool:
            futures = {
                pool.submit(
                    fetch_risk_holdings_snapshot,
                    code,
                    now=now,
                    timeout=timeout,
                    previous=previous.get(code),
                ): code
                for code in codes
            }
            for future in as_completed(futures):
                code = futures[future]
                try:
                    result[code] = future.result()
                except Exception as exc:
                    errors[code] = (
                        f"{type(exc).__name__}:"
                        f"{str(exc)[:220]}"
                    )

        selected = {
            code: result[code]
            for code in codes
            if code in result
        }
        self.persist(
            selected,
            generated_at=now,
            refresh_errors=errors,
        )
        return selected, errors

    def persist(
        self,
        rows: dict[str, RiskHoldingsSnapshot],
        *,
        generated_at: datetime,
        refresh_errors: dict[str, str] | None = None,
    ) -> None:
        payload = {
            "version": STORE_VERSION,
            "generated_at": generated_at.isoformat(),
            "rows": {
                code: row.to_dict()
                for code, row in sorted(rows.items())
            },
            "refresh_errors": refresh_errors or {},
        }
        self.root.mkdir(
            parents=True,
            exist_ok=True,
        )
        text = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ) + "\n"
        fd, temp_name = tempfile.mkstemp(
            prefix=".risk_holdings.",
            suffix=".tmp",
            dir=str(self.root),
        )
        try:
            with os.fdopen(
                fd,
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

[executed on device: iZ2vc3972s0n20m9kq0ns4Z (b3130143-0d28-448b-8a4c-d5f1482304ab)]