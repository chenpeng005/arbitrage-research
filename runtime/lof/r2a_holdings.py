from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
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


ARCHIVE_URL = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
SOURCE = "EASTMONEY_FUND_F10"


@dataclass(frozen=True)
class Holding:
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
    def from_dict(cls, value: dict) -> "Holding":
        return cls(
            asset_type=str(value["asset_type"]),
            symbol=str(value["symbol"]),
            security_code=str(value["security_code"]),
            name=str(value.get("name") or ""),
            nav_weight=Decimal(str(value["nav_weight"])),
        )


@dataclass(frozen=True)
class HoldingsSnapshot:
    fund_code: str
    as_of_date: date
    first_seen_at: datetime
    fetched_at: datetime
    holdings: tuple[Holding, ...]
    total_weight: Decimal
    identity: str

    def to_dict(self) -> dict:
        return {
            "fund_code": self.fund_code,
            "as_of_date": self.as_of_date.isoformat(),
            "first_seen_at": self.first_seen_at.isoformat(),
            "fetched_at": self.fetched_at.isoformat(),
            "holdings": [x.to_dict() for x in self.holdings],
            "total_weight": float(self.total_weight),
            "identity": self.identity,
            "source": SOURCE,
        }

    @classmethod
    def from_dict(cls, value: dict) -> "HoldingsSnapshot":
        return cls(
            fund_code=str(value["fund_code"]),
            as_of_date=date.fromisoformat(str(value["as_of_date"])),
            first_seen_at=datetime.fromisoformat(str(value["first_seen_at"])),
            fetched_at=datetime.fromisoformat(str(value["fetched_at"])),
            holdings=tuple(
                Holding.from_dict(x)
                for x in value.get("holdings", [])
            ),
            total_weight=Decimal(str(value["total_weight"])),
            identity=str(value["identity"]),
        )


def _request_text(url: str, *, timeout: int, fund_code: str) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": f"https://fundf10.eastmoney.com/ccmx_{fund_code}.html",
        },
    )
    last_error = None
    for attempt in range(2):
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except Exception as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(0.2)
    assert last_error is not None
    raise last_error


def _url(
    fund_code: str,
    *,
    topline: int,
    year: str = "",
    month: str = "",
) -> str:
    params = {
        "type": "jjcc",
        "code": fund_code,
        "topline": str(topline),
        "year": year,
        "month": month,
    }
    return ARCHIVE_URL + "?" + urlencode(params)


def parse_periods(text: str) -> list[date]:
    periods = {
        date.fromisoformat(value)
        for value in re.findall(
            r"截止至：<font[^>]*>(\d{4}-\d{2}-\d{2})</font>",
            text,
        )
    }
    return sorted(periods, reverse=True)


def discover_periods(
    fund_code: str,
    *,
    as_of: date,
    timeout: int = 6,
) -> list[date]:
    probes = [
        ("", ""),
        (str(as_of.year), ""),
        (str(as_of.year - 1), ""),
    ]
    errors: list[str] = []
    for year, month in probes:
        try:
            text = _request_text(
                _url(
                    fund_code,
                    topline=20,
                    year=year,
                    month=month,
                ),
                timeout=timeout,
                fund_code=fund_code,
            )
        except Exception as exc:
            errors.append(
                f"{year or 'default'}:{type(exc).__name__}"
            )
            continue
        periods = parse_periods(text)
        if periods:
            return periods
    raise ValueError(
        f"NO_HOLDINGS_PERIOD:{fund_code}:" + "|".join(errors)
    )


def _weight(value: str) -> Decimal | None:
    try:
        result = (
            Decimal(
                value.replace("%", "").replace(",", "").strip()
            )
            / Decimal("100")
        )
    except (InvalidOperation, ValueError):
        return None
    return result if result > 0 else None


def parse_holdings(text: str) -> tuple[Holding, ...]:
    rows: list[Holding] = []
    for tr in re.findall(r"<tr>(.*?)</tr>", text, flags=re.S):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.S)
        if len(tds) < 8:
            continue
        market_match = re.search(
            r"unify/r/([0-9]+)\.([0-9A-Za-z]+)",
            tr,
        )
        if market_match is None:
            continue
        market, code = market_match.groups()
        cells = [
            html.unescape(
                re.sub(r"<.*?>", "", x, flags=re.S).strip()
            )
            for x in tds
        ]
        nav_weight = _weight(cells[-3])
        if nav_weight is None:
            continue

        if market == "116":
            asset_type = "HK"
            symbol = "hk" + code.zfill(5)
        elif market == "1":
            asset_type = "A"
            symbol = "sh" + code.zfill(6)
        elif market == "0":
            asset_type = "A"
            symbol = "sz" + code.zfill(6)
        else:
            continue

        rows.append(
            Holding(
                asset_type=asset_type,
                symbol=symbol,
                security_code=code,
                name=cells[2] if len(cells) > 2 else "",
                nav_weight=nav_weight,
            )
        )

    rows.sort(key=lambda x: (x.asset_type, x.symbol))
    return tuple(rows)


def fetch_period_holdings(
    fund_code: str,
    *,
    period: date,
    timeout: int = 6,
) -> tuple[Holding, ...]:
    text = _request_text(
        _url(
            fund_code,
            topline=500,
            year=str(period.year),
            month=str(period.month),
        ),
        timeout=timeout,
        fund_code=fund_code,
    )
    rows = parse_holdings(text)
    if not rows:
        raise ValueError(
            f"EMPTY_HOLDINGS:{fund_code}:{period.isoformat()}"
        )
    return rows


def snapshot_identity(
    fund_code: str,
    period: date,
    holdings: Iterable[Holding],
) -> str:
    payload = {
        "fund_code": fund_code,
        "period": period.isoformat(),
        "holdings": [
            [
                x.asset_type,
                x.symbol,
                format(x.nav_weight, "f"),
            ]
            for x in holdings
        ],
    }
    raw = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def fetch_latest_full_snapshot(
    fund_code: str,
    *,
    now: datetime,
    timeout: int = 6,
    min_total_weight: Decimal = Decimal("0.75"),
    previous: HoldingsSnapshot | None = None,
) -> HoldingsSnapshot:
    errors: list[str] = []
    periods = discover_periods(
        fund_code,
        as_of=now.date(),
        timeout=timeout,
    )
    for period in periods[:6]:
        try:
            holdings = fetch_period_holdings(
                fund_code,
                period=period,
                timeout=timeout,
            )
        except Exception as exc:
            errors.append(
                f"{period.isoformat()}:{type(exc).__name__}"
            )
            continue

        total = sum(
            (x.nav_weight for x in holdings),
            Decimal("0"),
        )
        if total < min_total_weight:
            errors.append(
                f"{period.isoformat()}:LOW_WEIGHT:{total}"
            )
            continue

        identity = snapshot_identity(
            fund_code,
            period,
            holdings,
        )
        first_seen = now
        if (
            previous is not None
            and previous.identity == identity
        ):
            first_seen = previous.first_seen_at

        return HoldingsSnapshot(
            fund_code=fund_code,
            as_of_date=period,
            first_seen_at=first_seen,
            fetched_at=now,
            holdings=holdings,
            total_weight=total,
            identity=identity,
        )

    raise ValueError(
        f"NO_USABLE_FULL_HOLDINGS:{fund_code}:"
        + "|".join(errors)
    )


class HoldingsStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.path = (
            self.root / "r2a_holdings_snapshot.json"
        )

    def load(self) -> dict[str, HoldingsSnapshot]:
        if not self.path.exists():
            return {}
        payload = json.loads(
            self.path.read_text(encoding="utf-8")
        )
        return {
            code: HoldingsSnapshot.from_dict(value)
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
        dict[str, HoldingsSnapshot],
        dict[str, str],
    ]:
        previous = self.load()
        result = dict(previous)
        errors: dict[str, str] = {}
        codes = sorted(set(fund_codes))

        with ThreadPoolExecutor(
            max_workers=min(3, len(codes))
        ) as pool:
            futures = {
                pool.submit(
                    fetch_latest_full_snapshot,
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
                        f"{str(exc)[:180]}"
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
        rows: dict[str, HoldingsSnapshot],
        *,
        generated_at: datetime,
        refresh_errors: dict[str, str] | None = None,
    ) -> None:
        payload = {
            "version": "R2A_HOLDINGS_SNAPSHOT_V1",
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

        fd, tmp = tempfile.mkstemp(
            prefix=".r2a_holdings.",
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
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
