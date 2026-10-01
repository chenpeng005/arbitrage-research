from __future__ import annotations

import argparse
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
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .r2_fund_events import (
    DistributionSchedule,
    DistributionStore,
    cash_distribution_on,
    schedules_need_refresh,
)
from .r2a_shadow import fetch_live_quotes
from .snapshot_store import LofSnapshotStore


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
FUND_CODE = "164814"
FUND_NAME = "工银双债LOF"
METHOD = "LIVE_EQUITY_CONVERTIBLE_BASKET"
SOURCE = "EASTMONEY_FUND_F10"
ARCHIVE_URL = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"

MIN_TRADE_WEIGHT = Decimal("0.85")
MIN_LIVE_RATIO = Decimal("0.98")
MAX_HOLDINGS_AGE_DAYS = 130
MAX_DISTRIBUTION_AGE_SECONDS = 86400


@dataclass(frozen=True)
class TradeHolding:
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
    def from_dict(cls, value: dict) -> "TradeHolding":
        return cls(
            asset_type=str(value["asset_type"]),
            symbol=str(value["symbol"]),
            security_code=str(value["security_code"]),
            name=str(value.get("name") or ""),
            nav_weight=Decimal(str(value["nav_weight"])),
        )


@dataclass(frozen=True)
class TradeHoldingsSnapshot:
    fund_code: str
    as_of_date: date
    first_seen_at: datetime
    fetched_at: datetime
    holdings: tuple[TradeHolding, ...]
    stock_weight: Decimal
    convertible_weight: Decimal
    ordinary_bond_weight: Decimal
    trade_weight: Decimal
    identity: str

    def to_dict(self) -> dict:
        return {
            "fund_code": self.fund_code,
            "as_of_date": self.as_of_date.isoformat(),
            "first_seen_at": self.first_seen_at.isoformat(),
            "fetched_at": self.fetched_at.isoformat(),
            "holdings": [x.to_dict() for x in self.holdings],
            "stock_weight": float(self.stock_weight),
            "convertible_weight": float(self.convertible_weight),
            "ordinary_bond_weight": float(self.ordinary_bond_weight),
            "trade_weight": float(self.trade_weight),
            "identity": self.identity,
            "source": SOURCE,
        }

    @classmethod
    def from_dict(cls, value: dict) -> "TradeHoldingsSnapshot":
        return cls(
            fund_code=str(value["fund_code"]),
            as_of_date=date.fromisoformat(str(value["as_of_date"])),
            first_seen_at=datetime.fromisoformat(str(value["first_seen_at"])),
            fetched_at=datetime.fromisoformat(str(value["fetched_at"])),
            holdings=tuple(
                TradeHolding.from_dict(x)
                for x in value.get("holdings", [])
            ),
            stock_weight=Decimal(str(value["stock_weight"])),
            convertible_weight=Decimal(str(value["convertible_weight"])),
            ordinary_bond_weight=Decimal(str(value["ordinary_bond_weight"])),
            trade_weight=Decimal(str(value["trade_weight"])),
            identity=str(value["identity"]),
        )


def _request_text(url: str, *, timeout: int) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": f"https://fundf10.eastmoney.com/ccmx_{FUND_CODE}.html",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _archive_url(
    *,
    data_type: str,
    year: str = "",
    month: str = "",
    topline: int = 500,
) -> str:
    params = {
        "type": data_type,
        "code": FUND_CODE,
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


def discover_periods(*, as_of: date, timeout: int = 6) -> list[date]:
    probes = [
        ("", ""),
        (str(as_of.year), ""),
        (str(as_of.year - 1), ""),
    ]
    errors: list[str] = []
    for year, month in probes:
        try:
            text = _request_text(
                _archive_url(
                    data_type="jjcc",
                    year=year,
                    month=month,
                    topline=20,
                ),
                timeout=timeout,
            )
        except Exception as exc:
            errors.append(f"{year or 'default'}:{type(exc).__name__}")
            continue
        periods = parse_periods(text)
        if periods:
            return periods
    raise ValueError(
        "NO_HOLDINGS_PERIOD:" + "|".join(errors)
    )


def _target_box(text: str, period: date) -> str:
    target = period.isoformat()
    for part in re.split(r"(?=<div class='box')", text):
        if target in part:
            return part
    return ""


def _weight(value: str) -> Decimal | None:
    try:
        result = (
            Decimal(value.replace("%", "").replace(",", "").strip())
            / Decimal("100")
        )
    except (InvalidOperation, ValueError):
        return None
    return result if result > 0 else None


def _stock_symbol(code: str) -> str:
    if code.startswith(("5", "6", "68")):
        return "sh" + code.zfill(6)
    return "sz" + code.zfill(6)


def _bond_symbol(code: str) -> str:
    if code.startswith(("110", "111", "113", "118", "132")):
        return "sh" + code.zfill(6)
    return "sz" + code.zfill(6)


def _is_convertible(name: str) -> bool:
    return bool(
        re.search(
            r"转债|转[0-9A-Za-z]*$|EB\d*|可转|交换债",
            name,
            flags=re.I,
        )
    )


def parse_stock_holdings(
    text: str,
    period: date,
) -> tuple[TradeHolding, ...]:
    rows: list[TradeHolding] = []
    box = _target_box(text, period)
    for tr in re.findall(r"<tr>(.*?)</tr>", box, flags=re.S):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.S)
        if len(tds) < 8:
            continue
        cells = [
            html.unescape(re.sub(r"<.*?>", "", x, flags=re.S).strip())
            for x in tds
        ]
        code = cells[1]
        weight = _weight(cells[-3])
        if weight is None or not re.fullmatch(r"\d{6}", code):
            continue
        rows.append(
            TradeHolding(
                asset_type="STOCK",
                symbol=_stock_symbol(code),
                security_code=code,
                name=cells[2],
                nav_weight=weight,
            )
        )
    rows.sort(key=lambda x: x.symbol)
    return tuple(rows)


def parse_bond_holdings(
    text: str,
    period: date,
) -> tuple[tuple[TradeHolding, ...], Decimal]:
    trade_rows: list[TradeHolding] = []
    ordinary_weight = Decimal("0")
    box = _target_box(text, period)
    for tr in re.findall(r"<tr>(.*?)</tr>", box, flags=re.S):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.S)
        if len(tds) < 5:
            continue
        cells = [
            html.unescape(re.sub(r"<.*?>", "", x, flags=re.S).strip())
            for x in tds
        ]
        code, name = cells[1], cells[2]
        weight = _weight(cells[3])
        if weight is None or not re.fullmatch(r"\d{6}", code):
            continue
        if _is_convertible(name):
            trade_rows.append(
                TradeHolding(
                    asset_type="CONVERTIBLE",
                    symbol=_bond_symbol(code),
                    security_code=code,
                    name=name,
                    nav_weight=weight,
                )
            )
        else:
            ordinary_weight += weight
    trade_rows.sort(key=lambda x: x.symbol)
    return tuple(trade_rows), ordinary_weight


def holdings_identity(
    period: date,
    holdings: tuple[TradeHolding, ...],
) -> str:
    payload = {
        "fund_code": FUND_CODE,
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


def fetch_snapshot_for_period(
    period: date,
    *,
    now: datetime,
    timeout: int = 6,
    previous: TradeHoldingsSnapshot | None = None,
) -> TradeHoldingsSnapshot:
    year = str(period.year)
    month = str(period.month)

    stock_text = _request_text(
        _archive_url(
            data_type="jjcc",
            year=year,
            month=month,
        ),
        timeout=timeout,
    )
    bond_text = _request_text(
        _archive_url(
            data_type="zqcc",
            year=year,
            month=month,
        ),
        timeout=timeout,
    )

    stocks = parse_stock_holdings(stock_text, period)
    convertibles, ordinary_weight = parse_bond_holdings(
        bond_text,
        period,
    )
    holdings = tuple(
        sorted(
            (*stocks, *convertibles),
            key=lambda x: (x.asset_type, x.symbol),
        )
    )

    stock_weight = sum(
        (x.nav_weight for x in stocks),
        Decimal("0"),
    )
    convertible_weight = sum(
        (x.nav_weight for x in convertibles),
        Decimal("0"),
    )
    trade_weight = stock_weight + convertible_weight
    identity = holdings_identity(period, holdings)

    first_seen_at = now
    if previous is not None and previous.identity == identity:
        first_seen_at = previous.first_seen_at

    return TradeHoldingsSnapshot(
        fund_code=FUND_CODE,
        as_of_date=period,
        first_seen_at=first_seen_at,
        fetched_at=now,
        holdings=holdings,
        stock_weight=stock_weight,
        convertible_weight=convertible_weight,
        ordinary_bond_weight=ordinary_weight,
        trade_weight=trade_weight,
        identity=identity,
    )


def fetch_latest_usable_holdings(
    *,
    now: datetime,
    timeout: int = 6,
    previous: TradeHoldingsSnapshot | None = None,
) -> TradeHoldingsSnapshot:
    periods = discover_periods(
        as_of=now.date(),
        timeout=timeout,
    )
    errors: list[str] = []
    for period in periods[:6]:
        try:
            row = fetch_snapshot_for_period(
                period,
                now=now,
                timeout=timeout,
                previous=previous,
            )
        except Exception as exc:
            errors.append(
                f"{period.isoformat()}:{type(exc).__name__}"
            )
            continue

        if row.trade_weight >= MIN_TRADE_WEIGHT:
            return row

        errors.append(
            f"{period.isoformat()}:LOW_TRADE_WEIGHT:"
            f"{row.trade_weight}"
        )

    raise ValueError(
        "NO_USABLE_TRADE_HOLDINGS:" + "|".join(errors)
    )


class TradeHoldingsStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.path = self.root / "r2c_live_holdings.json"

    def load(self) -> TradeHoldingsSnapshot | None:
        if not self.path.exists():
            return None
        payload = json.loads(
            self.path.read_text(encoding="utf-8")
        )
        return TradeHoldingsSnapshot.from_dict(payload)

    def refresh(
        self,
        *,
        now: datetime,
        timeout: int = 6,
    ) -> TradeHoldingsSnapshot:
        previous = self.load()
        try:
            row = fetch_latest_usable_holdings(
                now=now,
                timeout=timeout,
                previous=previous,
            )
        except Exception:
            if previous is not None:
                return previous
            raise
        self.persist(row)
        return row

    def persist(self, row: TradeHoldingsSnapshot) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            row.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
        ) + "\n"
        _atomic_write(self.path, payload)


def holdings_need_refresh(
    row: TradeHoldingsSnapshot | None,
    *,
    now: datetime,
    refresh_seconds: int,
) -> bool:
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
    return age < 0 or age >= refresh_seconds


def _quote_age_seconds(
    quote_time: datetime | None,
    as_of: datetime,
) -> int | None:
    if quote_time is None:
        return None
    q = quote_time
    n = as_of
    if q.tzinfo is None:
        q = q.replace(tzinfo=SHANGHAI_TZ)
    if n.tzinfo is None:
        n = n.replace(tzinfo=SHANGHAI_TZ)
    return max(
        0,
        int(
            (
                n.astimezone(SHANGHAI_TZ)
                - q.astimezone(SHANGHAI_TZ)
            ).total_seconds()
        ),
    )


def _schedule_age_seconds(
    schedule: DistributionSchedule,
    as_of: datetime,
) -> int | None:
    return _quote_age_seconds(schedule.fetched_at, as_of)


def _decimal(value) -> Decimal | None:
    try:
        if value is None or value == "":
            return None
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _unavailable(
    *,
    market_row: dict | None,
    holdings: TradeHoldingsSnapshot | None,
    error: str,
) -> dict:
    return {
        "fund_code": FUND_CODE,
        "fund_name": (
            (market_row or {}).get("name")
            or FUND_NAME
        ),
        "method": METHOD,
        "status": "UNAVAILABLE",
        "quality": "RESEARCH_ONLY",
        "shadow_estimated_nav": None,
        "shadow_premium_rate": None,
        "estimated_return": None,
        "official_nav": (market_row or {}).get("official_nav"),
        "official_nav_date": (market_row or {}).get("official_nav_date"),
        "market_price": (market_row or {}).get("price"),
        "holdings_as_of_date": (
            holdings.as_of_date.isoformat()
            if holdings is not None
            else None
        ),
        "holdings_first_seen_at": (
            holdings.first_seen_at.isoformat()
            if holdings is not None
            else None
        ),
        "stock_weight": (
            float(holdings.stock_weight)
            if holdings is not None
            else None
        ),
        "convertible_weight": (
            float(holdings.convertible_weight)
            if holdings is not None
            else None
        ),
        "ordinary_bond_weight": (
            float(holdings.ordinary_bond_weight)
            if holdings is not None
            else None
        ),
        "trade_weight": (
            float(holdings.trade_weight)
            if holdings is not None
            else None
        ),
        "valid_quote_weight": None,
        "fresh_quote_weight": None,
        "live_coverage_ratio": None,
        "fresh_coverage_ratio": None,
        "cash_distribution_per_unit": None,
        "error": error,
    }


def calculate_row(
    *,
    main_snapshot: dict,
    holdings: TradeHoldingsSnapshot,
    distribution_schedule: DistributionSchedule,
    quotes: dict,
    as_of: datetime,
    max_quote_age_seconds: int = 120,
) -> dict:
    market_row = next(
        (
            row
            for row in (main_snapshot.get("rows") or [])
            if str(row.get("code")) == FUND_CODE
        ),
        None,
    )
    if market_row is None:
        return _unavailable(
            market_row=None,
            holdings=holdings,
            error="MISSING_MAIN_MARKET_ROW",
        )

    schedule_age = _schedule_age_seconds(
        distribution_schedule,
        as_of,
    )
    if (
        schedule_age is None
        or schedule_age > MAX_DISTRIBUTION_AGE_SECONDS
    ):
        return _unavailable(
            market_row=market_row,
            holdings=holdings,
            error="DISTRIBUTION_SCHEDULE_STALE",
        )

    nav = _decimal(market_row.get("official_nav"))
    try:
        nav_date = date.fromisoformat(
            str(market_row.get("official_nav_date"))
        )
    except Exception:
        nav_date = None

    if nav is None or nav <= 0 or nav_date is None:
        return _unavailable(
            market_row=market_row,
            holdings=holdings,
            error="OFFICIAL_NAV_UNAVAILABLE",
        )

    if market_row.get("official_nav_lag_label") != "T-1":
        return _unavailable(
            market_row=market_row,
            holdings=holdings,
            error="OFFICIAL_NAV_NOT_T1",
        )

    holdings_age = (
        as_of.date() - holdings.as_of_date
    ).days
    if (
        holdings_age < 0
        or holdings_age > MAX_HOLDINGS_AGE_DAYS
    ):
        return _unavailable(
            market_row=market_row,
            holdings=holdings,
            error=f"HOLDINGS_TOO_OLD:{holdings_age}",
        )

    if holdings.trade_weight < MIN_TRADE_WEIGHT:
        return _unavailable(
            market_row=market_row,
            holdings=holdings,
            error=f"TRADE_WEIGHT_TOO_LOW:{holdings.trade_weight}",
        )

    estimated_return = Decimal("0")
    valid_weight = Decimal("0")
    fresh_weight = Decimal("0")
    quote_times: list[datetime] = []

    for item in holdings.holdings:
        quote = quotes.get(item.symbol)
        if quote is None or not quote.available:
            continue
        assert quote.current is not None
        assert quote.previous_close is not None
        assert quote.quote_time is not None

        asset_return = (
            quote.current / quote.previous_close
            - Decimal("1")
        )
        estimated_return += (
            item.nav_weight * asset_return
        )
        valid_weight += item.nav_weight
        quote_times.append(quote.quote_time)

        age = _quote_age_seconds(
            quote.quote_time,
            as_of,
        )
        if (
            age is not None
            and age <= max_quote_age_seconds
        ):
            fresh_weight += item.nav_weight

    live_ratio = valid_weight / holdings.trade_weight
    fresh_ratio = fresh_weight / holdings.trade_weight

    if live_ratio < MIN_LIVE_RATIO:
        row = _unavailable(
            market_row=market_row,
            holdings=holdings,
            error=f"LIVE_COVERAGE_TOO_LOW:{live_ratio}",
        )
        row["valid_quote_weight"] = float(valid_weight)
        row["fresh_quote_weight"] = float(fresh_weight)
        row["live_coverage_ratio"] = float(live_ratio)
        row["fresh_coverage_ratio"] = float(fresh_ratio)
        return row

    cash_distribution = cash_distribution_on(
        distribution_schedule,
        as_of.date(),
    )
    estimated_nav = (
        nav * (Decimal("1") + estimated_return)
        - cash_distribution
    )
    if estimated_nav <= 0:
        return _unavailable(
            market_row=market_row,
            holdings=holdings,
            error="NON_POSITIVE_ESTIMATED_NAV",
        )

    status = "AVAILABLE"
    if fresh_ratio < MIN_LIVE_RATIO:
        status = "STALE"

    market_price = _decimal(market_row.get("price"))
    premium = None
    if (
        market_price is not None
        and market_price > 0
    ):
        premium = (
            market_price / estimated_nav
            - Decimal("1")
        ) * Decimal("100")

    return {
        "fund_code": FUND_CODE,
        "fund_name": market_row.get("name") or FUND_NAME,
        "method": METHOD,
        "status": status,
        "quality": "RESEARCH_ONLY",
        "shadow_estimated_nav": float(estimated_nav),
        "shadow_premium_rate": (
            float(premium)
            if premium is not None
            else None
        ),
        "estimated_return": float(estimated_return),
        "official_nav": float(nav),
        "official_nav_date": nav_date.isoformat(),
        "market_price": (
            float(market_price)
            if market_price is not None
            else None
        ),
        "holdings_as_of_date": holdings.as_of_date.isoformat(),
        "holdings_first_seen_at": holdings.first_seen_at.isoformat(),
        "stock_weight": float(holdings.stock_weight),
        "convertible_weight": float(holdings.convertible_weight),
        "ordinary_bond_weight": float(holdings.ordinary_bond_weight),
        "trade_weight": float(holdings.trade_weight),
        "valid_quote_weight": float(valid_weight),
        "fresh_quote_weight": float(fresh_weight),
        "live_coverage_ratio": float(live_ratio),
        "fresh_coverage_ratio": float(fresh_ratio),
        "quote_time_min": (
            min(quote_times).isoformat()
            if quote_times
            else None
        ),
        "quote_time_max": (
            max(quote_times).isoformat()
            if quote_times
            else None
        ),
        "cash_distribution_per_unit": float(cash_distribution),
        "distribution_ex_date": (
            as_of.date().isoformat()
            if cash_distribution > 0
            else None
        ),
        "distribution_schedule_fetched_at": (
            distribution_schedule.fetched_at.isoformat()
        ),
        "error": None,
    }


def _atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def persist_snapshot(
    state_root: str | Path,
    snapshot: dict,
) -> Path:
    root = Path(state_root)
    archive_dir = root / "snapshots"
    archive = archive_dir / f"{snapshot['snapshot_id']}.json"
    latest = root / "r2c_live_overlay_shadow.json"
    payload = json.dumps(
        snapshot,
        ensure_ascii=False,
        separators=(",", ":"),
    ) + "\n"
    _atomic_write(archive, payload)
    _atomic_write(latest, payload)
    return archive


def _main_snapshot_has_fresh_market(
    snapshot: dict,
) -> bool:
    quality = snapshot.get("quality_summary") or {}
    return int(
        quality.get("quote_fresh_count") or 0
    ) > 0


def collect_once(
    *,
    main_data_root: str | Path,
    state_root: str | Path,
    now: datetime | None = None,
    timeout: int = 6,
    max_quote_age_seconds: int = 120,
    holdings_refresh_seconds: int = 21600,
    distribution_refresh_seconds: int = 21600,
) -> dict:
    now = now or datetime.now(SHANGHAI_TZ)
    main_snapshot = LofSnapshotStore(
        main_data_root
    ).load_latest()
    if main_snapshot is None:
        raise RuntimeError("MAIN_SNAPSHOT_UNAVAILABLE")

    holdings_store = TradeHoldingsStore(state_root)
    holdings = holdings_store.load()
    if holdings_need_refresh(
        holdings,
        now=now,
        refresh_seconds=holdings_refresh_seconds,
    ):
        holdings = holdings_store.refresh(
            now=now,
            timeout=timeout,
        )
    if holdings is None:
        raise RuntimeError("HOLDINGS_UNAVAILABLE")

    distribution_store = DistributionStore(state_root)
    distributions = distribution_store.load()
    if schedules_need_refresh(
        distributions,
        [FUND_CODE],
        now=now,
        refresh_seconds=distribution_refresh_seconds,
    ):
        distributions, distribution_errors = (
            distribution_store.refresh(
                [FUND_CODE],
                now=now,
                timeout=timeout,
            )
        )
    else:
        distribution_errors = {}

    schedule = distributions.get(FUND_CODE)
    if schedule is None:
        row = _unavailable(
            market_row=next(
                (
                    x
                    for x in (
                        main_snapshot.get("rows") or []
                    )
                    if str(x.get("code")) == FUND_CODE
                ),
                None,
            ),
            holdings=holdings,
            error="DISTRIBUTION_SCHEDULE_UNAVAILABLE",
        )
    else:
        quotes = fetch_live_quotes(
            [x.symbol for x in holdings.holdings],
            timeout=timeout,
            batch_size=80,
        )
        row = calculate_row(
            main_snapshot=main_snapshot,
            holdings=holdings,
            distribution_schedule=schedule,
            quotes=quotes,
            as_of=now,
            max_quote_age_seconds=max_quote_age_seconds,
        )

    snapshot = {
        "contract_version": "R2C_LIVE_OVERLAY_V0",
        "snapshot_id": (
            "r2c-live-overlay-"
            + now.strftime("%Y%m%dT%H%M%S")
        ),
        "generated_at": now.isoformat(),
        "source_market_snapshot_id": (
            main_snapshot.get("snapshot_id")
        ),
        "method": METHOD,
        "distribution_refresh_errors": distribution_errors,
        "summary": {
            "available_count": int(
                row["status"] == "AVAILABLE"
            ),
            "stale_count": int(
                row["status"] == "STALE"
            ),
            "unavailable_count": int(
                row["status"] == "UNAVAILABLE"
            ),
        },
        "rows": [row],
    }
    persist_snapshot(state_root, snapshot)
    return snapshot


def run_loop(
    *,
    main_data_root: str | Path,
    state_root: str | Path,
    timeout: int = 6,
    max_quote_age_seconds: int = 120,
    holdings_refresh_seconds: int = 21600,
    distribution_refresh_seconds: int = 21600,
    quote_interval_seconds: float = 30.0,
    off_hours_interval_seconds: float = 300.0,
) -> int:
    main_store = LofSnapshotStore(main_data_root)
    while True:
        started = time.monotonic()
        now = datetime.now(SHANGHAI_TZ)
        interval = off_hours_interval_seconds
        try:
            main_snapshot = main_store.load_latest()
            if (
                main_snapshot is not None
                and _main_snapshot_has_fresh_market(
                    main_snapshot
                )
            ):
                snapshot = collect_once(
                    main_data_root=main_data_root,
                    state_root=state_root,
                    now=now,
                    timeout=timeout,
                    max_quote_age_seconds=max_quote_age_seconds,
                    holdings_refresh_seconds=holdings_refresh_seconds,
                    distribution_refresh_seconds=distribution_refresh_seconds,
                )
                interval = quote_interval_seconds
                print(
                    json.dumps(
                        {
                            "event": "r2c_live_overlay_persisted",
                            "time": now.isoformat(),
                            "snapshot_id": snapshot["snapshot_id"],
                            "summary": snapshot["summary"],
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            else:
                print(
                    json.dumps(
                        {
                            "event": "r2c_live_overlay_idle",
                            "time": now.isoformat(),
                            "reason": "MAIN_MARKET_NOT_FRESH",
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "event": "r2c_live_overlay_failed",
                        "time": now.isoformat(),
                        "error": (
                            f"{type(exc).__name__}:"
                            f"{str(exc)[:240]}"
                        ),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

        elapsed = time.monotonic() - started
        time.sleep(
            max(0.0, interval - elapsed)
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--main-data-root", required=True)
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--timeout", type=int, default=6)
    parser.add_argument(
        "--max-quote-age-seconds",
        type=int,
        default=120,
    )
    parser.add_argument(
        "--holdings-refresh-seconds",
        type=int,
        default=21600,
    )
    parser.add_argument(
        "--distribution-refresh-seconds",
        type=int,
        default=21600,
    )
    parser.add_argument(
        "--quote-interval-seconds",
        type=float,
        default=30.0,
    )
    parser.add_argument(
        "--off-hours-interval-seconds",
        type=float,
        default=300.0,
    )
    parser.add_argument("--loop", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.loop:
        return run_loop(
            main_data_root=args.main_data_root,
            state_root=args.state_root,
            timeout=args.timeout,
            max_quote_age_seconds=args.max_quote_age_seconds,
            holdings_refresh_seconds=args.holdings_refresh_seconds,
            distribution_refresh_seconds=args.distribution_refresh_seconds,
            quote_interval_seconds=args.quote_interval_seconds,
            off_hours_interval_seconds=args.off_hours_interval_seconds,
        )

    snapshot = collect_once(
        main_data_root=args.main_data_root,
        state_root=args.state_root,
        timeout=args.timeout,
        max_quote_age_seconds=args.max_quote_age_seconds,
        holdings_refresh_seconds=args.holdings_refresh_seconds,
        distribution_refresh_seconds=args.distribution_refresh_seconds,
    )
    print(
        json.dumps(
            {
                "snapshot_id": snapshot["snapshot_id"],
                "source_market_snapshot_id": (
                    snapshot["source_market_snapshot_id"]
                ),
                "summary": snapshot["summary"],
                "row": snapshot["rows"][0],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
