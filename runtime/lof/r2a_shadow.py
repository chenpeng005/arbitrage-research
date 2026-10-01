from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .fx import fetch_tencent_fx_daily, fetch_tencent_fx_quote, fx_close_on
from .r2a_holdings import HoldingsSnapshot, HoldingsStore
from .snapshot_store import LofSnapshotStore


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q="
METHOD = "DISCLOSED_HOLDINGS_BASKET"
HKD_CNY_SYMBOL = "whHKDCNY"

PROFILES = {
    "501201": {"quality": "LOW", "name": "科创红土LOF"},
    "501219": {"quality": "MEDIUM", "name": "智胜先锋LOF"},
    "160127": {"quality": "LOW", "name": "南方消费LOF"},
    "160133": {"quality": "MEDIUM", "name": "南方天元LOF"},
    "160919": {"quality": "LOW", "name": "产业升级LOF"},
    "163110": {"quality": "MEDIUM", "name": "申万量化LOF"},
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
        "error": error,
    }


def calculate_rows(
    *,
    main_snapshot: dict,
    holdings_by_fund: dict[str, HoldingsSnapshot],
    quotes: dict[str, LiveQuote],
    as_of: datetime,
    fx_current: Decimal | None = None,
    fx_quote_time: datetime | None = None,
    fx_anchor_by_nav_date: dict[date, Decimal] | None = None,
    max_quote_age_seconds: int = 120,
    min_disclosed_weight: Decimal = Decimal("0.80"),
    min_live_ratio: Decimal = Decimal("0.98"),
    max_holdings_age_days: int = 130,
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

        estimated_nav = nav * (Decimal("1") + estimated_return)
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


def collect_once(
    *,
    data_root: str | Path,
    now: datetime | None = None,
    timeout: int = 6,
    max_quote_age_seconds: int = 120,
) -> dict:
    now = now or datetime.now(SHANGHAI_TZ)
    main_snapshot = LofSnapshotStore(data_root).load_latest()
    if main_snapshot is None:
        raise RuntimeError("MAIN_SNAPSHOT_UNAVAILABLE")

    holdings_store = HoldingsStore(data_root)
    holdings, holdings_errors = holdings_store.refresh(
        PROFILES.keys(),
        now=now,
        timeout=timeout,
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
        "contract_version": "R2A_SHADOW_V1",
        "snapshot_id": "r2a-shadow-" + now.strftime("%Y%m%dT%H%M%S"),
        "generated_at": now.isoformat(),
        "source_market_snapshot_id": main_snapshot.get("snapshot_id"),
        "method": METHOD,
        "summary": summary,
        "holdings_refresh_errors": holdings_errors,
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--timeout", type=int, default=6)
    parser.add_argument("--max-quote-age-seconds", type=int, default=120)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    snapshot = collect_once(
        data_root=args.data_root,
        timeout=args.timeout,
        max_quote_age_seconds=args.max_quote_age_seconds,
    )
    print(json.dumps(
        {
            "snapshot_id": snapshot["snapshot_id"],
            "source_market_snapshot_id": snapshot["source_market_snapshot_id"],
            "summary": snapshot["summary"],
            "holdings_refresh_errors": snapshot["holdings_refresh_errors"],
        },
        ensure_ascii=False,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
