from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, time as clock_time
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .fx import fetch_tencent_fx_daily, fetch_tencent_fx_quote, fx_close_on
from .r2a_holdings import HoldingsSnapshot, HoldingsStore
from .r2_fund_events import (
    DistributionSchedule,
    DistributionStore,
    cash_distribution_on,
    schedules_need_refresh,
)
from .snapshot_store import LofSnapshotStore


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q="
METHOD = "DISCLOSED_HOLDINGS_BASKET"
HKD_CNY_SYMBOL = "whHKDCNY"

PROFILES = {
    "501201": {
        "quality": "LOW",
        "name": "科创红土LOF",
        "research_group": "R2-A",
    },
    "501219": {
        "quality": "MEDIUM",
        "name": "智胜先锋LOF",
        "research_group": "R2-A",
    },
    "160127": {
        "quality": "LOW",
        "name": "南方消费LOF",
        "research_group": "R2-A",
    },
    "160133": {
        "quality": "MEDIUM",
        "name": "南方天元LOF",
        "research_group": "R2-A",
    },
    "160919": {
        "quality": "LOW",
        "name": "产业升级LOF",
        "research_group": "R2-A",
    },
    "163110": {
        "quality": "MEDIUM",
        "name": "申万量化LOF",
        "research_group": "R2-A",
    },
    "501085": {
        "quality": "LOW",
        "name": "财通科创LOF",
        "research_group": "R2-B1",
    },
    "162703": {
        "quality": "MEDIUM",
        "name": "广发小盘LOF",
        "research_group": "R2-B1",
    },
    "163417": {
        "quality": "MEDIUM",
        "name": "兴全合宜LOF",
        "research_group": "R2-B1",
    },
    "163406": {
        "quality": "MEDIUM",
        "name": "兴全合润LOF",
        "research_group": "R2-B1",
    },
    "506002": {
        "quality": "MEDIUM",
        "name": "易方达科创板",
        "research_group": "R2-B1",
    },
    "168401": {
        "quality": "LOW",
        "name": "红土创新精选LOF",
        "research_group": "R2-B1",
    },
    "501205": {
        "quality": "LOW",
        "name": "鹏华创新未来LOF",
        "research_group": "R2-B1",
    },
    "161903": {
        "quality": "MEDIUM",
        "name": "万家行业优选LOF",
        "research_group": "R2-B1",
    },
    "501096": {
        "quality": "LOW",
        "name": "国联安科创LOF",
        "research_group": "R2-B1",
    },
    "163415": {
        "quality": "MEDIUM",
        "name": "兴全商业模式LOF",
        "research_group": "R2-B1",
    },
    "162605": {
        "quality": "MEDIUM",
        "name": "景顺鼎益LOF",
        "research_group": "R2-B1",
    },
    "163402": {
        "quality": "MEDIUM",
        "name": "兴全趋势LOF",
        "research_group": "R2-B1",
    },
    "161005": {
        "quality": "MEDIUM",
        "name": "富国天惠LOF",
        "research_group": "R2-B1",
    },
    "501015": {
        "quality": "LOW",
        "name": "财通升级混合LOF",
        "research_group": "R2-B1",
    },
    "501026": {
        "quality": "LOW",
        "name": "财通福享混合LOF",
        "research_group": "R2-B1",
    },
    "501227": {
        "quality": "LOW",
        "name": "泓德红利优选LOF",
        "research_group": "R2-B1",
    },
    "161914": {
        "quality": "MEDIUM",
        "name": "创业板2年定开",
        "research_group": "R2-B1",
    },
    "161810": {
        "quality": "MEDIUM",
        "name": "银华内需LOF",
        "research_group": "R2-B1",
    },
    "166011": {
        "quality": "MEDIUM",
        "name": "中欧盛世LOF",
        "research_group": "R2-B1",
    },
    "169101": {
        "quality": "LOW",
        "name": "东方红睿丰LOF",
        "research_group": "R2-B1",
    },
    "162607": {
        "quality": "MEDIUM",
        "name": "景顺资源LOF",
        "research_group": "R2-B1",
    },
    "160505": {
        "quality": "MEDIUM",
        "name": "博时主题LOF",
        "research_group": "R2-B1",
    },
    "160314": {
        "quality": "MEDIUM",
        "name": "华夏行业LOF",
        "research_group": "R2-B1",
    },
}


@dataclass(frozen=True)
class LiveQuote:
    symbol: str
    current: Decimal | None
    previous_close: Decimal | None
    quote_time: datetime | None
    error: str | None = None

    @property
    def available(self) -> bool:
        return (
            self.current is not None
            and self.previous_close is not None
            and self.current > 0
            and self.previous_close > 0
            and self.quote_time is not None
            and self.error is None
        )


def _decimal(value) -> Decimal | None:
    try:
        if value is None or value == "":
            return None
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _parse_time(value: str | None) -> datetime | None:
    raw = (value or "").strip()
    for fmt in (
        "%Y%m%d%H%M%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(raw, fmt).replace(
                tzinfo=SHANGHAI_TZ
            )
        except ValueError:
            pass
    return None


def parse_quote_response(text: str) -> dict[str, LiveQuote]:
    result: dict[str, LiveQuote] = {}
    for match in re.finditer(r'v_([^=]+)="([^"]*)"', text):
        symbol = match.group(1).strip()
        fields = match.group(2).split("~")
        if len(fields) < 31:
            result[symbol] = LiveQuote(
                symbol, None, None, None, "INVALID_QUOTE"
            )
            continue
        current = _decimal(fields[3])
        previous_close = _decimal(fields[4])
        quote_time = _parse_time(fields[30])
        error = None
        if (
            current is None
            or previous_close is None
            or current <= 0
            or previous_close <= 0
            or quote_time is None
        ):
            error = "INVALID_OR_MISSING_QUOTE"
        result[symbol] = LiveQuote(
            symbol=symbol,
            current=current,
            previous_close=previous_close,
            quote_time=quote_time,
            error=error,
        )
    return result


def _fetch_quote_batch(
    symbols: list[str],
    *,
    timeout: int,
) -> dict[str, LiveQuote]:
    request = Request(
        f"{TENCENT_QUOTE_URL}{','.join(symbols)}",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://gu.qq.com/",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        raw = response.read()
    text = raw.decode("gb18030", errors="replace")
    return parse_quote_response(text)


def fetch_live_quotes(
    symbols,
    *,
    timeout: int = 6,
    batch_size: int = 80,
) -> dict[str, LiveQuote]:
    unique = sorted(set(symbols))
    batches = [
        unique[i : i + batch_size]
        for i in range(0, len(unique), batch_size)
    ]
    found: dict[str, LiveQuote] = {}
    if not batches:
        return found

    with ThreadPoolExecutor(max_workers=min(4, len(batches))) as pool:
        futures = {
            pool.submit(_fetch_quote_batch, batch, timeout=timeout): batch
            for batch in batches
        }
        for future in as_completed(futures):
            batch = futures[future]
            try:
                parsed = future.result()
            except Exception as exc:
                for symbol in batch:
                    found[symbol] = LiveQuote(
                        symbol,
                        None,
                        None,
                        None,
                        f"FETCH_ERROR:{type(exc).__name__}",
                    )
                continue
            for symbol in batch:
                found[symbol] = parsed.get(
                    symbol,
                    LiveQuote(
                        symbol,
                        None,
                        None,
                        None,
                        "MISSING_FROM_SOURCE",
                    ),
                )
    return found


def _quote_age(quote_time: datetime | None, as_of: datetime) -> int | None:
    if quote_time is None:
        return None
    if quote_time.tzinfo is None:
        quote_time = quote_time.replace(tzinfo=SHANGHAI_TZ)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=SHANGHAI_TZ)
    return max(
        0,
        int(
            (
                as_of.astimezone(SHANGHAI_TZ)
                - quote_time.astimezone(SHANGHAI_TZ)
            ).total_seconds()
        ),
    )


def _unavailable(
    code: str,
    *,
    market_row: dict | None,
    holdings: HoldingsSnapshot | None,
    error: str,
) -> dict:
    profile = PROFILES[code]
    return {
        "fund_code": code,
        "fund_name": (
            (market_row or {}).get("name")
            or profile["name"]
        ),
        "method": METHOD,
        "status": "UNAVAILABLE",
        "quality_candidate": profile["quality"],
        "research_group": profile["research_group"],
        "shadow_estimated_nav": None,
        "shadow_premium_rate": None,
        "estimated_return": None,
        "official_nav": (market_row or {}).get("official_nav"),
        "official_nav_date": (market_row or {}).get("official_nav_date"),
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
        "holdings_identity": (
            holdings.identity if holdings is not None else None
        ),
        "disclosed_weight": (
            float(holdings.total_weight)
            if holdings is not None
            else None
        ),
        "live_coverage_ratio": None,
        "fresh_coverage_ratio": None,
        "cash_distribution_per_unit": None,
        "distribution_ex_date": None,
        "distribution_schedule_fetched_at": None,
        "error": error,
    }


def calculate_rows(
    *,
    main_snapshot: dict,
    holdings_by_fund: dict[str, HoldingsSnapshot],
    distributions_by_fund: dict[str, DistributionSchedule],
    quotes: dict[str, LiveQuote],
    as_of: datetime,
    fx_current: Decimal | None = None,
    fx_quote_time: datetime | None = None,
    fx_anchor_by_nav_date: dict[date, Decimal] | None = None,
    max_quote_age_seconds: int = 120,
    min_disclosed_weight: Decimal = Decimal("0.80"),
    min_live_ratio: Decimal = Decimal("0.98"),
    max_holdings_age_days: int = 130,
    max_distribution_schedule_age_seconds: int = 86400,
) -> list[dict]:
    fx_anchor_by_nav_date = fx_anchor_by_nav_date or {}
    market = {
        str(row.get("code")): row
        for row in (main_snapshot.get("rows") or [])
    }
    result: list[dict] = []

    for code, profile in PROFILES.items():
        row = market.get(code)
        holdings = holdings_by_fund.get(code)
        if row is None:
            result.append(
                _unavailable(
                    code,
                    market_row=None,
                    holdings=holdings,
                    error="MISSING_MAIN_MARKET_ROW",
                )
            )
            continue
        if holdings is None:
            result.append(
                _unavailable(
                    code,
                    market_row=row,
                    holdings=None,
                    error="MISSING_HOLDINGS_SNAPSHOT",
                )
            )
            continue

        distribution_schedule = distributions_by_fund.get(code)
        if distribution_schedule is None:
            result.append(
                _unavailable(
                    code,
                    market_row=row,
                    holdings=holdings,
                    error="DISTRIBUTION_SCHEDULE_UNAVAILABLE",
                )
            )
            continue
        schedule_age = _quote_age(
            distribution_schedule.fetched_at,
            as_of,
        )
        if (
            schedule_age is None
            or schedule_age > max_distribution_schedule_age_seconds
        ):
            result.append(
                _unavailable(
                    code,
                    market_row=row,
                    holdings=holdings,
                    error="DISTRIBUTION_SCHEDULE_STALE",
                )
            )
            continue
        cash_distribution = cash_distribution_on(
            distribution_schedule,
            as_of.date(),
        )

        nav = _decimal(row.get("official_nav"))
        try:
            nav_date = date.fromisoformat(
                str(row.get("official_nav_date"))
            )
        except Exception:
            nav_date = None
        if nav is None or nav <= 0 or nav_date is None:
            result.append(
                _unavailable(
                    code,
                    market_row=row,
                    holdings=holdings,
                    error="OFFICIAL_NAV_UNAVAILABLE",
                )
            )
            continue

        if row.get("official_nav_lag_label") != "T-1":
            result.append(
                _unavailable(
                    code,
                    market_row=row,
                    holdings=holdings,
                    error="OFFICIAL_NAV_NOT_T1",
                )
            )
            continue

        age_days = (as_of.date() - holdings.as_of_date).days
        if age_days < 0 or age_days > max_holdings_age_days:
            result.append(
                _unavailable(
                    code,
                    market_row=row,
                    holdings=holdings,
                    error=f"HOLDINGS_TOO_OLD:{age_days}",
                )
            )
            continue

        disclosed = holdings.total_weight
        if disclosed < min_disclosed_weight:
            result.append(
                _unavailable(
                    code,
                    market_row=row,
                    holdings=holdings,
                    error=f"DISCLOSED_WEIGHT_TOO_LOW:{disclosed}",
                )
            )
            continue

        has_hk = any(x.asset_type == "HK" for x in holdings.holdings)
        fx_anchor = fx_anchor_by_nav_date.get(nav_date)
        if has_hk and (
            fx_current is None
            or fx_current <= 0
            or fx_quote_time is None
            or fx_anchor is None
            or fx_anchor <= 0
        ):
            result.append(
                _unavailable(
                    code,
                    market_row=row,
                    holdings=holdings,
                    error="HKD_CNY_UNAVAILABLE",
                )
            )
            continue

        estimated_return = Decimal("0")
        valid_weight = Decimal("0")
        fresh_weight = Decimal("0")
        used_times: list[datetime] = []

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
            if item.asset_type == "HK":
                assert fx_current is not None
                assert fx_anchor is not None
                asset_return = (
                    (quote.current / quote.previous_close)
                    * (fx_current / fx_anchor)
                    - Decimal("1")
                )

            estimated_return += item.nav_weight * asset_return
            valid_weight += item.nav_weight
            used_times.append(quote.quote_time)
            age = _quote_age(quote.quote_time, as_of)
            if age is not None and age <= max_quote_age_seconds:
                fresh_weight += item.nav_weight

        live_ratio = valid_weight / disclosed
        fresh_ratio = fresh_weight / disclosed
        if live_ratio < min_live_ratio:
            bad = _unavailable(
                code,
                market_row=row,
                holdings=holdings,
                error=f"LIVE_COVERAGE_TOO_LOW:{live_ratio}",
            )
            bad["live_coverage_ratio"] = float(live_ratio)
            bad["fresh_coverage_ratio"] = float(fresh_ratio)
            result.append(bad)
            continue

        status = "AVAILABLE"
        if fresh_ratio < min_live_ratio:
            status = "STALE"
        if has_hk:
            fx_age = _quote_age(fx_quote_time, as_of)
            if fx_age is None or fx_age > max_quote_age_seconds:
                status = "STALE"

        estimated_nav = (
            nav * (Decimal("1") + estimated_return)
            - cash_distribution
        )
        if estimated_nav <= 0:
            result.append(
                _unavailable(
                    code,
                    market_row=row,
                    holdings=holdings,
                    error="NON_POSITIVE_ESTIMATED_NAV",
                )
            )
            continue
        market_price = _decimal(row.get("price"))
        premium = None
        if (
            market_price is not None
            and market_price > 0
            and estimated_nav > 0
        ):
            premium = (
                market_price / estimated_nav
                - Decimal("1")
            ) * Decimal("100")

        result.append(
            {
                "fund_code": code,
                "fund_name": row.get("name") or profile["name"],
                "method": METHOD,
                "status": status,
                "quality_candidate": profile["quality"],
                "research_group": profile["research_group"],
                "shadow_estimated_nav": float(estimated_nav),
                "shadow_premium_rate": (
                    float(premium) if premium is not None else None
                ),
                "estimated_return": float(estimated_return),
                "official_nav": float(nav),
                "official_nav_date": nav_date.isoformat(),
                "holdings_as_of_date": holdings.as_of_date.isoformat(),
                "holdings_first_seen_at": holdings.first_seen_at.isoformat(),
                "holdings_identity": holdings.identity,
                "disclosed_weight": float(disclosed),
                "live_coverage_ratio": float(live_ratio),
                "fresh_coverage_ratio": float(fresh_ratio),
                "cash_distribution_per_unit": float(cash_distribution),
                "distribution_ex_date": (
                    as_of.date().isoformat()
                    if cash_distribution > 0
                    else None
                ),
                "distribution_schedule_fetched_at": (
                    distribution_schedule.fetched_at.isoformat()
                ),
                "quote_time_min": (
                    min(used_times).isoformat()
                    if used_times
                    else None
                ),
                "quote_time_max": (
                    max(used_times).isoformat()
                    if used_times
                    else None
                ),
                "error": None,
            }
        )

    return result


def holdings_need_refresh(
    holdings_by_fund: dict[str, HoldingsSnapshot],
    *,
    now: datetime,
    refresh_seconds: int,
) -> bool:
    if set(PROFILES) - set(holdings_by_fund):
        return True
    for code in PROFILES:
        row = holdings_by_fund.get(code)
        if row is None:
            return True
        fetched_at = row.fetched_at
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=SHANGHAI_TZ)
        current = now
        if current.tzinfo is None:
            current = current.replace(tzinfo=SHANGHAI_TZ)
        age = (
            current.astimezone(SHANGHAI_TZ)
            - fetched_at.astimezone(SHANGHAI_TZ)
        ).total_seconds()
        if age < 0 or age >= refresh_seconds:
            return True
    return False


def _main_snapshot_has_fresh_market(snapshot: dict) -> bool:
    quality = snapshot.get("quality_summary") or {}
    return int(quality.get("quote_fresh_count") or 0) > 0


def _market_probe_window(now: datetime) -> bool:
    local = now
    if local.tzinfo is None:
        local = local.replace(tzinfo=SHANGHAI_TZ)
    local = local.astimezone(SHANGHAI_TZ)
    if local.weekday() >= 5:
        return False
    value = local.time()
    return (
        clock_time(9, 25) <= value <= clock_time(11, 35)
        or clock_time(12, 55) <= value <= clock_time(15, 5)
    )


def _load_or_refresh_low_frequency(
    *,
    data_root: str | Path,
    now: datetime,
    timeout: int,
    holdings_refresh_seconds: int,
    distribution_refresh_seconds: int,
) -> tuple[
    dict[str, HoldingsSnapshot],
    dict[str, DistributionSchedule],
    dict[str, str],
    dict[str, str],
]:
    holdings_store = HoldingsStore(data_root)
    holdings = holdings_store.load()
    holdings_errors: dict[str, str] = {}
    if holdings_need_refresh(
        holdings,
        now=now,
        refresh_seconds=holdings_refresh_seconds,
    ):
        holdings, holdings_errors = holdings_store.refresh(
            PROFILES.keys(),
            now=now,
            timeout=timeout,
        )

    distribution_store = DistributionStore(data_root)
    distributions = distribution_store.load()
    distribution_errors: dict[str, str] = {}
    if schedules_need_refresh(
        distributions,
        PROFILES.keys(),
        now=now,
        refresh_seconds=distribution_refresh_seconds,
    ):
        distributions, distribution_errors = distribution_store.refresh(
            PROFILES.keys(),
            now=now,
            timeout=timeout,
        )

    return (
        holdings,
        distributions,
        holdings_errors,
        distribution_errors,
    )


def collect_once(
    *,
    data_root: str | Path,
    now: datetime | None = None,
    timeout: int = 6,
    max_quote_age_seconds: int = 120,
    holdings_refresh_seconds: int = 21600,
    distribution_refresh_seconds: int = 21600,
) -> dict:
    now = now or datetime.now(SHANGHAI_TZ)
    main_snapshot = LofSnapshotStore(data_root).load_latest()
    if main_snapshot is None:
        raise RuntimeError("MAIN_SNAPSHOT_UNAVAILABLE")

    (
        holdings,
        distributions,
        holdings_errors,
        distribution_errors,
    ) = _load_or_refresh_low_frequency(
        data_root=data_root,
        now=now,
        timeout=timeout,
        holdings_refresh_seconds=holdings_refresh_seconds,
        distribution_refresh_seconds=distribution_refresh_seconds,
    )

    symbols = {
        item.symbol
        for snapshot in holdings.values()
        for item in snapshot.holdings
    }
    quotes = fetch_live_quotes(
        symbols,
        timeout=timeout,
        batch_size=80,
    )

    has_hk = any(
        item.asset_type == "HK"
        for snapshot in holdings.values()
        for item in snapshot.holdings
    )
    fx_current = None
    fx_quote_time = None
    fx_anchor_by_nav_date: dict[date, Decimal] = {}
    if has_hk:
        fx_quote = fetch_tencent_fx_quote(
            HKD_CNY_SYMBOL,
            timeout=timeout,
        )
        fx_current = fx_quote.current
        fx_quote_time = fx_quote.quote_time
        fx_rows = fetch_tencent_fx_daily(
            HKD_CNY_SYMBOL,
            count=30,
            timeout=timeout,
        )
        for market_row in main_snapshot.get("rows") or []:
            if str(market_row.get("code")) not in PROFILES:
                continue
            try:
                nav_date = date.fromisoformat(
                    str(market_row.get("official_nav_date"))
                )
            except Exception:
                continue
            value = fx_close_on(fx_rows, nav_date)
            if value is not None:
                fx_anchor_by_nav_date[nav_date] = value

    rows = calculate_rows(
        main_snapshot=main_snapshot,
        holdings_by_fund=holdings,
        distributions_by_fund=distributions,
        quotes=quotes,
        as_of=now,
        fx_current=fx_current,
        fx_quote_time=fx_quote_time,
        fx_anchor_by_nav_date=fx_anchor_by_nav_date,
        max_quote_age_seconds=max_quote_age_seconds,
    )
    summary = {
        "available_count": sum(x["status"] == "AVAILABLE" for x in rows),
        "stale_count": sum(x["status"] == "STALE" for x in rows),
        "unavailable_count": sum(x["status"] == "UNAVAILABLE" for x in rows),
    }
    snapshot = {
        "contract_version": "R2A_SHADOW_V2",
        "snapshot_id": "r2a-shadow-" + now.strftime("%Y%m%dT%H%M%S"),
        "generated_at": now.isoformat(),
        "source_market_snapshot_id": main_snapshot.get("snapshot_id"),
        "method": METHOD,
        "summary": summary,
        "holdings_refresh_errors": holdings_errors,
        "distribution_refresh_errors": distribution_errors,
        "rows": rows,
    }
    persist_snapshot(data_root, snapshot)
    return snapshot


def persist_snapshot(data_root: str | Path, snapshot: dict) -> Path:
    root = Path(data_root)
    archive_dir = root / "r2a_shadow_snapshots"
    archive_dir.mkdir(parents=True, exist_ok=True)
    root.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        snapshot,
        ensure_ascii=False,
        separators=(",", ":"),
    ) + "\n"
    latest = root / "r2a_shadow_snapshot.json"
    archive = archive_dir / f"{snapshot['snapshot_id']}.json"
    for path in (archive, latest):
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
    return archive


def run_loop(
    *,
    data_root: str | Path,
    timeout: int = 6,
    max_quote_age_seconds: int = 120,
    holdings_refresh_seconds: int = 21600,
    distribution_refresh_seconds: int = 21600,
    quote_interval_seconds: float = 30.0,
    off_hours_interval_seconds: float = 300.0,
) -> int:
    main_store = LofSnapshotStore(data_root)
    while True:
        cycle_started = time.monotonic()
        now = datetime.now(SHANGHAI_TZ)
        interval = off_hours_interval_seconds
        try:
            main_snapshot = main_store.load_latest()
            market_fresh = (
                main_snapshot is not None
                and _main_snapshot_has_fresh_market(main_snapshot)
            )
            if market_fresh or _market_probe_window(now):
                interval = quote_interval_seconds

            if market_fresh:
                snapshot = collect_once(
                    data_root=data_root,
                    now=now,
                    timeout=timeout,
                    max_quote_age_seconds=max_quote_age_seconds,
                    holdings_refresh_seconds=holdings_refresh_seconds,
                    distribution_refresh_seconds=distribution_refresh_seconds,
                )
                print(
                    json.dumps(
                        {
                            "event": "r2a_shadow_persisted",
                            "time": now.isoformat(),
                            "snapshot_id": snapshot["snapshot_id"],
                            "summary": snapshot["summary"],
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            else:
                (
                    _holdings,
                    _distributions,
                    holdings_errors,
                    distribution_errors,
                ) = _load_or_refresh_low_frequency(
                    data_root=data_root,
                    now=now,
                    timeout=timeout,
                    holdings_refresh_seconds=holdings_refresh_seconds,
                    distribution_refresh_seconds=distribution_refresh_seconds,
                )
                print(
                    json.dumps(
                        {
                            "event": "r2a_shadow_idle",
                            "time": now.isoformat(),
                            "reason": "MAIN_MARKET_NOT_FRESH",
                            "market_probe_window": _market_probe_window(now),
                            "holdings_refresh_errors": holdings_errors,
                            "distribution_refresh_errors": distribution_errors,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "event": "r2a_shadow_failed",
                        "time": now.isoformat(),
                        "error": (
                            f"{type(exc).__name__}:{str(exc)[:240]}"
                        ),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

        elapsed = time.monotonic() - cycle_started
        time.sleep(max(0.0, interval - elapsed))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--timeout", type=int, default=6)
    parser.add_argument("--max-quote-age-seconds", type=int, default=120)
    parser.add_argument("--holdings-refresh-seconds", type=int, default=21600)
    parser.add_argument("--distribution-refresh-seconds", type=int, default=21600)
    parser.add_argument("--quote-interval-seconds", type=float, default=30.0)
    parser.add_argument("--off-hours-interval-seconds", type=float, default=300.0)
    parser.add_argument("--loop", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.loop:
        return run_loop(
            data_root=args.data_root,
            timeout=args.timeout,
            max_quote_age_seconds=args.max_quote_age_seconds,
            holdings_refresh_seconds=args.holdings_refresh_seconds,
            distribution_refresh_seconds=args.distribution_refresh_seconds,
            quote_interval_seconds=args.quote_interval_seconds,
            off_hours_interval_seconds=args.off_hours_interval_seconds,
        )

    snapshot = collect_once(
        data_root=args.data_root,
        timeout=args.timeout,
        max_quote_age_seconds=args.max_quote_age_seconds,
        holdings_refresh_seconds=args.holdings_refresh_seconds,
        distribution_refresh_seconds=args.distribution_refresh_seconds,
    )
    print(json.dumps(
        {
            "snapshot_id": snapshot["snapshot_id"],
            "source_market_snapshot_id": snapshot["source_market_snapshot_id"],
            "summary": snapshot["summary"],
            "holdings_refresh_errors": snapshot["holdings_refresh_errors"],
            "distribution_refresh_errors": snapshot["distribution_refresh_errors"],
        },
        ensure_ascii=False,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())