from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import json
import math
import os
from pathlib import Path
import tempfile
import time
from typing import Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
NAV_URL = "https://api.fund.eastmoney.com/f10/lsjz"
METHOD = "OFFICIAL_NAV_UPSIDE_BAND"
PROFILE_VERSION = "R2C_NAV_BAND_PROFILE_V2"
SNAPSHOT_VERSION = "R2C_NAV_BAND_SHADOW_V1"
WINDOW = 120
HISTORY_LIMIT = 300


@dataclass(frozen=True)
class NavPoint:
    day: date
    nav: float
    distribution: float = 0.0

    def to_dict(self) -> dict:
        return {
            "date": self.day.isoformat(),
            "nav": self.nav,
            "distribution": self.distribution,
        }

    @classmethod
    def from_dict(cls, value: dict) -> "NavPoint":
        return cls(
            day=date.fromisoformat(str(value["date"])),
            nav=float(value["nav"]),
            distribution=float(value.get("distribution") or 0.0),
        )


@dataclass(frozen=True)
class BondNavBandProfile:
    fund_code: str
    fetched_at: datetime
    history_end_date: date
    return_sample_count: int
    walk_forward_count: int
    mae_abs_return: float
    up95: float
    up99: float
    walk_forward_exceed95: float
    walk_forward_exceed99: float
    group: str
    reliability: str
    nav_history: tuple[NavPoint, ...] = ()

    def to_dict(self) -> dict:
        return {
            "fund_code": self.fund_code,
            "fetched_at": self.fetched_at.isoformat(),
            "history_end_date": self.history_end_date.isoformat(),
            "return_sample_count": self.return_sample_count,
            "walk_forward_count": self.walk_forward_count,
            "mae_abs_return": self.mae_abs_return,
            "up95": self.up95,
            "up99": self.up99,
            "walk_forward_exceed95": self.walk_forward_exceed95,
            "walk_forward_exceed99": self.walk_forward_exceed99,
            "group": self.group,
            "reliability": self.reliability,
            "nav_history": [
                point.to_dict() for point in self.nav_history
            ],
        }

    @classmethod
    def from_dict(cls, value: dict) -> "BondNavBandProfile":
        return cls(
            fund_code=str(value["fund_code"]),
            fetched_at=datetime.fromisoformat(str(value["fetched_at"])),
            history_end_date=date.fromisoformat(
                str(value["history_end_date"])
            ),
            return_sample_count=int(value["return_sample_count"]),
            walk_forward_count=int(value["walk_forward_count"]),
            mae_abs_return=float(value["mae_abs_return"]),
            up95=float(value["up95"]),
            up99=float(value["up99"]),
            walk_forward_exceed95=float(
                value["walk_forward_exceed95"]
            ),
            walk_forward_exceed99=float(
                value["walk_forward_exceed99"]
            ),
            group=str(value["group"]),
            reliability=str(value["reliability"]),
            nav_history=tuple(
                NavPoint.from_dict(x)
                for x in value.get("nav_history", [])
            ),
        )


def quantile(values: list[float], p: float) -> float:
    if not values:
        raise ValueError("quantile requires values")
    if p < 0 or p > 1:
        raise ValueError("p must be between 0 and 1")
    rows = sorted(values)
    k = (len(rows) - 1) * p
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return rows[lo]
    return rows[lo] * (hi - k) + rows[hi] * (k - lo)


def classify_group(up95: float) -> str:
    if up95 <= 0.10:
        return "C1_ULTRA_LOW"
    if up95 <= 0.25:
        return "C2_LOW"
    if up95 <= 0.50:
        return "C3_MODERATE"
    return "C4_HIGH"


def classify_reliability(
    group: str,
    exceed95: float,
    exceed99: float,
) -> str:
    if group not in {"C1_ULTRA_LOW", "C2_LOW"}:
        return "RESEARCH_ONLY"
    if exceed95 <= 7.5 and exceed99 <= 3.0:
        return "MEDIUM"
    return "LOW"


def _profile_stats(
    fund_code: str,
    rows: list[tuple[date, float]],
    *,
    fetched_at: datetime,
    nav_history: tuple[NavPoint, ...] = (),
    window: int = WINDOW,
) -> BondNavBandProfile:
    if len(rows) < window + 30:
        raise ValueError(
            f"INSUFFICIENT_NAV_HISTORY:{fund_code}:{len(rows)}"
        )

    ordered = sorted(rows, key=lambda x: x[0])
    returns = [x[1] for x in ordered]
    current = returns[-window:]

    up95 = max(0.0, quantile(current, 0.95))
    up99 = max(0.0, quantile(current, 0.99))
    mae_abs = sum(abs(x) for x in current) / len(current)

    wf95 = 0
    wf99 = 0
    wf_count = 0
    for idx in range(window, len(returns)):
        history = returns[idx - window : idx]
        band95 = max(0.0, quantile(history, 0.95))
        band99 = max(0.0, quantile(history, 0.99))
        actual = returns[idx]
        wf95 += actual > band95
        wf99 += actual > band99
        wf_count += 1

    if wf_count <= 0:
        raise ValueError(
            f"INSUFFICIENT_WALK_FORWARD:{fund_code}"
        )

    exceed95 = wf95 / wf_count * 100
    exceed99 = wf99 / wf_count * 100
    group = classify_group(up95)

    return BondNavBandProfile(
        fund_code=fund_code,
        fetched_at=fetched_at,
        history_end_date=ordered[-1][0],
        return_sample_count=len(returns),
        walk_forward_count=wf_count,
        mae_abs_return=mae_abs,
        up95=up95,
        up99=up99,
        walk_forward_exceed95=exceed95,
        walk_forward_exceed99=exceed99,
        group=group,
        reliability=classify_reliability(
            group,
            exceed95,
            exceed99,
        ),
        nav_history=nav_history,
    )


def build_profile_from_returns(
    fund_code: str,
    rows: list[tuple[date, float]],
    *,
    fetched_at: datetime,
    window: int = WINDOW,
) -> BondNavBandProfile:
    return _profile_stats(
        fund_code,
        rows,
        fetched_at=fetched_at,
        window=window,
    )


def returns_from_nav_history(
    points: Iterable[NavPoint],
) -> list[tuple[date, float]]:
    ordered = sorted(points, key=lambda x: x.day)
    result: list[tuple[date, float]] = []
    for prior, current in zip(ordered, ordered[1:]):
        if prior.nav <= 0:
            continue
        total_return = (
            (current.nav + current.distribution)
            / prior.nav
            - 1.0
        ) * 100.0
        result.append((current.day, total_return))
    return result


def build_profile_from_nav_history(
    fund_code: str,
    points: Iterable[NavPoint],
    *,
    fetched_at: datetime,
) -> BondNavBandProfile:
    ordered = tuple(
        sorted(points, key=lambda x: x.day)[-HISTORY_LIMIT:]
    )
    rows = returns_from_nav_history(ordered)
    return _profile_stats(
        fund_code,
        rows,
        fetched_at=fetched_at,
        nav_history=ordered,
    )


def _fetch_nav_page(
    fund_code: str,
    *,
    page_index: int,
    timeout: int,
) -> list[NavPoint]:
    url = NAV_URL + "?" + urlencode(
        {
            "fundCode": fund_code,
            "pageIndex": str(page_index),
            "pageSize": "20",
            "startDate": "",
            "endDate": "",
        }
    )
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://fundf10.eastmoney.com/",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(
            response.read().decode("utf-8")
        )
    rows = ((payload.get("Data") or {}).get("LSJZList") or [])
    result: list[NavPoint] = []
    for row in rows:
        try:
            day = date.fromisoformat(str(row["FSRQ"]))
            nav = float(row["DWJZ"])
            raw_div = str(row.get("FHFCZ") or "").strip()
            distribution = float(raw_div) if raw_div else 0.0
        except Exception:
            continue
        if nav > 0:
            result.append(
                NavPoint(
                    day=day,
                    nav=nav,
                    distribution=distribution,
                )
            )
    return result


def fetch_nav_history(
    fund_code: str,
    *,
    timeout: int = 6,
    previous: BondNavBandProfile | None = None,
    max_pages: int = 15,
) -> tuple[NavPoint, ...]:
    existing = {
        point.day: point
        for point in (
            previous.nav_history
            if previous is not None
            else ()
        )
    }
    cutoff = (
        previous.history_end_date
        if previous is not None and previous.nav_history
        else None
    )

    for page in range(1, max_pages + 1):
        rows = _fetch_nav_page(
            fund_code,
            page_index=page,
            timeout=timeout,
        )
        if not rows:
            break
        for point in rows:
            existing[point.day] = point

        if cutoff is not None and any(
            point.day <= cutoff
            for point in rows
        ):
            break

    ordered = tuple(
        sorted(existing.values(), key=lambda x: x.day)[
            -HISTORY_LIMIT:
        ]
    )
    if len(ordered) < WINDOW + 31:
        raise ValueError(
            f"INSUFFICIENT_NAV_POINTS:{fund_code}:{len(ordered)}"
        )
    return ordered


def fetch_profile(
    fund_code: str,
    *,
    now: datetime,
    timeout: int = 6,
    previous: BondNavBandProfile | None = None,
) -> BondNavBandProfile:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            history = fetch_nav_history(
                fund_code,
                timeout=timeout,
                previous=previous,
            )
            return build_profile_from_nav_history(
                fund_code,
                history,
                fetched_at=now,
            )
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
    assert last_error is not None
    raise last_error


class BondBandProfileStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.path = self.root / "r2c_nav_band_profiles.json"

    def load(self) -> dict[str, BondNavBandProfile]:
        if not self.path.exists():
            return {}
        payload = json.loads(
            self.path.read_text(encoding="utf-8")
        )
        return {
            code: BondNavBandProfile.from_dict(value)
            for code, value in (payload.get("rows") or {}).items()
        }

    def refresh(
        self,
        fund_codes: Iterable[str],
        *,
        now: datetime,
        timeout: int = 6,
    ) -> tuple[
        dict[str, BondNavBandProfile],
        dict[str, str],
    ]:
        previous = self.load()
        result = dict(previous)
        errors: dict[str, str] = {}
        codes = sorted(set(fund_codes))

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {
                pool.submit(
                    fetch_profile,
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

        self.persist(
            result,
            generated_at=now,
            refresh_errors=errors,
        )
        return result, errors

    def persist(
        self,
        rows: dict[str, BondNavBandProfile],
        *,
        generated_at: datetime,
        refresh_errors: dict[str, str] | None = None,
    ) -> None:
        payload = {
            "version": PROFILE_VERSION,
            "generated_at": generated_at.isoformat(),
            "rows": {
                code: row.to_dict()
                for code, row in sorted(rows.items())
            },
            "refresh_errors": refresh_errors or {},
        }
        self.root.mkdir(parents=True, exist_ok=True)
        text = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ) + "\n"
        fd, temp_name = tempfile.mkstemp(
            prefix=".r2c_nav_band_profiles.",
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


def _parse_date(value) -> date | None:
    try:
        if not value:
            return None
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _bond_rows(main_snapshot: dict) -> list[dict]:
    return [
        row
        for row in (main_snapshot.get("rows") or [])
        if row.get("resolver_class") == "R2_DOMESTIC_OTHER"
        and row.get("lof_type") == "BOND"
        and row.get("subscription_status") in {"OPEN", "LIMITED"}
    ]


def profiles_requiring_refresh(
    main_snapshot: dict,
    profiles: dict[str, BondNavBandProfile],
) -> list[str]:
    codes: list[str] = []
    for row in _bond_rows(main_snapshot):
        code = str(row.get("code"))
        profile = profiles.get(code)
        nav_date = _parse_date(row.get("official_nav_date"))
        if profile is None:
            codes.append(code)
            continue
        if (
            nav_date is not None
            and profile.history_end_date < nav_date
        ):
            codes.append(code)
    return sorted(set(codes))


def calculate_band_rows(
    *,
    main_snapshot: dict,
    profiles: dict[str, BondNavBandProfile],
) -> list[dict]:
    result: list[dict] = []

    for row in _bond_rows(main_snapshot):
        code = str(row.get("code"))
        profile = profiles.get(code)

        base = {
            "fund_code": code,
            "fund_name": row.get("name") or code,
            "method": METHOD,
            "subscription_status": row.get("subscription_status"),
            "quality": None,
            "band_group": None,
            "official_nav": row.get("official_nav"),
            "official_nav_date": row.get("official_nav_date"),
            "market_price": row.get("price"),
            "nav_upper_95": None,
            "nav_upper_99": None,
            "premium_floor_95": None,
            "premium_floor_99": None,
            "up95": None,
            "up99": None,
            "walk_forward_exceed95": None,
            "walk_forward_exceed99": None,
            "profile_history_end_date": None,
            "status": "UNAVAILABLE",
            "error": None,
        }

        if profile is None:
            base["error"] = "PROFILE_UNAVAILABLE"
            result.append(base)
            continue

        base.update(
            {
                "quality": profile.reliability,
                "band_group": profile.group,
                "up95": profile.up95,
                "up99": profile.up99,
                "walk_forward_exceed95": (
                    profile.walk_forward_exceed95
                ),
                "walk_forward_exceed99": (
                    profile.walk_forward_exceed99
                ),
                "profile_history_end_date": (
                    profile.history_end_date.isoformat()
                ),
            }
        )

        if profile.group not in {
            "C1_ULTRA_LOW",
            "C2_LOW",
        }:
            base["error"] = "BAND_TOO_WIDE"
            result.append(base)
            continue

        if row.get("official_nav_lag_label") != "T-1":
            base["error"] = "OFFICIAL_NAV_NOT_T1"
            result.append(base)
            continue

        try:
            nav = float(row.get("official_nav"))
            price = float(row.get("price"))
        except (TypeError, ValueError):
            base["error"] = "NAV_OR_PRICE_UNAVAILABLE"
            result.append(base)
            continue
        if nav <= 0 or price <= 0:
            base["error"] = "NAV_OR_PRICE_UNAVAILABLE"
            result.append(base)
            continue

        nav_date = _parse_date(row.get("official_nav_date"))
        if nav_date is None:
            base["error"] = "OFFICIAL_NAV_DATE_UNAVAILABLE"
            result.append(base)
            continue

        upper95 = nav * (1.0 + profile.up95 / 100.0)
        upper99 = nav * (1.0 + profile.up99 / 100.0)
        floor95 = (price / upper95 - 1.0) * 100.0
        floor99 = (price / upper99 - 1.0) * 100.0

        base.update(
            {
                "nav_upper_95": upper95,
                "nav_upper_99": upper99,
                "premium_floor_95": floor95,
                "premium_floor_99": floor99,
                "status": "AVAILABLE",
                "error": None,
            }
        )

        if profile.history_end_date < nav_date:
            base["status"] = "STALE"
            base["error"] = "PROFILE_LAGGED"
        elif row.get("quote_status") != "FRESH":
            base["status"] = "STALE"
            base["error"] = "MARKET_QUOTE_STALE"

        result.append(base)

    return result


def persist_shadow(
    data_root: str | Path,
    snapshot: dict,
) -> Path:
    root = Path(data_root)
    archive_dir = root / "r2c_nav_band_snapshots"
    root.mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)

    payload = json.dumps(
        snapshot,
        ensure_ascii=False,
        separators=(",", ":"),
    ) + "\n"
    latest = root / "r2c_nav_band_shadow.json"
    archive = archive_dir / f"{snapshot['snapshot_id']}.json"

    for path in (archive, latest):
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    return archive


def build_shadow_snapshot(
    *,
    main_snapshot: dict,
    profiles: dict[str, BondNavBandProfile],
    as_of: datetime,
    refresh_errors: dict[str, str] | None = None,
) -> dict:
    rows = calculate_band_rows(
        main_snapshot=main_snapshot,
        profiles=profiles,
    )
    summary = {
        "available_count": sum(
            x["status"] == "AVAILABLE" for x in rows
        ),
        "stale_count": sum(
            x["status"] == "STALE" for x in rows
        ),
        "unavailable_count": sum(
            x["status"] == "UNAVAILABLE" for x in rows
        ),
        "c1_count": sum(
            x.get("band_group") == "C1_ULTRA_LOW"
            for x in rows
        ),
        "c2_count": sum(
            x.get("band_group") == "C2_LOW"
            for x in rows
        ),
    }
    return {
        "contract_version": SNAPSHOT_VERSION,
        "snapshot_id": (
            "r2c-band-" + as_of.strftime("%Y%m%dT%H%M%S")
        ),
        "generated_at": as_of.isoformat(),
        "source_market_snapshot_id": main_snapshot.get(
            "snapshot_id"
        ),
        "method": METHOD,
        "profile_refresh_errors": refresh_errors or {},
        "summary": summary,
        "rows": rows,
    }


def _market_is_fresh(snapshot: dict) -> bool:
    quality = snapshot.get("quality_summary") or {}
    return int(quality.get("quote_fresh_count") or 0) > 0


def run_once(
    *,
    data_root: str | Path,
    timeout: int = 6,
    now: datetime | None = None,
) -> dict:
    from .snapshot_store import LofSnapshotStore

    now = now or datetime.now(SHANGHAI_TZ)
    main_snapshot = LofSnapshotStore(data_root).load_latest()
    if main_snapshot is None:
        raise RuntimeError("MAIN_SNAPSHOT_UNAVAILABLE")

    store = BondBandProfileStore(data_root)
    profiles = store.load()
    needs = profiles_requiring_refresh(
        main_snapshot,
        profiles,
    )
    errors: dict[str, str] = {}
    if needs:
        profiles, errors = store.refresh(
            needs,
            now=now,
            timeout=timeout,
        )

    snapshot = build_shadow_snapshot(
        main_snapshot=main_snapshot,
        profiles=profiles,
        as_of=now,
        refresh_errors=errors,
    )
    persist_shadow(data_root, snapshot)
    return snapshot


def run_loop(
    *,
    data_root: str | Path,
    timeout: int = 6,
    quote_interval_seconds: float = 30.0,
    off_hours_interval_seconds: float = 300.0,
) -> int:
    from .snapshot_store import LofSnapshotStore

    main_store = LofSnapshotStore(data_root)
    profile_store = BondBandProfileStore(data_root)

    while True:
        started = time.monotonic()
        now = datetime.now(SHANGHAI_TZ)
        interval = off_hours_interval_seconds
        try:
            main_snapshot = main_store.load_latest()
            if main_snapshot is None:
                raise RuntimeError("MAIN_SNAPSHOT_UNAVAILABLE")

            profiles = profile_store.load()
            needs = profiles_requiring_refresh(
                main_snapshot,
                profiles,
            )
            refresh_errors: dict[str, str] = {}
            if needs:
                profiles, refresh_errors = profile_store.refresh(
                    needs,
                    now=now,
                    timeout=timeout,
                )

            if _market_is_fresh(main_snapshot):
                snapshot = build_shadow_snapshot(
                    main_snapshot=main_snapshot,
                    profiles=profiles,
                    as_of=now,
                    refresh_errors=refresh_errors,
                )
                path = persist_shadow(
                    data_root,
                    snapshot,
                )
                interval = quote_interval_seconds
                print(
                    json.dumps(
                        {
                            "event": "r2c_nav_band_persisted",
                            "time": now.isoformat(),
                            "snapshot_id": snapshot["snapshot_id"],
                            "summary": snapshot["summary"],
                            "path": str(path),
                            "profile_refresh_errors": refresh_errors,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            else:
                print(
                    json.dumps(
                        {
                            "event": "r2c_nav_band_idle",
                            "time": now.isoformat(),
                            "reason": "MAIN_MARKET_NOT_FRESH",
                            "profile_refresh_errors": refresh_errors,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "event": "r2c_nav_band_failed",
                        "time": now.isoformat(),
                        "error": (
                            f"{type(exc).__name__}:{str(exc)[:240]}"
                        ),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

        elapsed = time.monotonic() - started
        time.sleep(max(0.0, interval - elapsed))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--timeout", type=int, default=6)
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
            data_root=args.data_root,
            timeout=args.timeout,
            quote_interval_seconds=args.quote_interval_seconds,
            off_hours_interval_seconds=args.off_hours_interval_seconds,
        )

    snapshot = run_once(
        data_root=args.data_root,
        timeout=args.timeout,
    )
    print(
        json.dumps(
            {
                "snapshot_id": snapshot["snapshot_id"],
                "summary": snapshot["summary"],
                "profile_refresh_errors": (
                    snapshot["profile_refresh_errors"]
                ),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
