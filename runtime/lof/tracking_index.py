from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
import json
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .universe import LofIdentity


EASTMONEY_BASEINFO_URL = (
    "https://fund.eastmoney.com/data/FundChannelData_BaseInfoHandler.ashx"
)


@dataclass(frozen=True)
class TrackingIndexRecord:
    fund_code: str
    fund_name: str | None
    tracking_index_code: str | None
    tracking_index_name: str | None
    market_bucket: str
    source: str
    fetched_at: datetime
    error: str | None = None

    @property
    def available(self) -> bool:
        return (
            bool(self.tracking_index_code)
            and bool(self.tracking_index_name)
            and self.error is None
        )


def _get_json(
    params: dict[str, Any],
    *,
    timeout: int,
) -> dict[str, Any]:
    request = Request(
        f"{EASTMONEY_BASEINFO_URL}?{urlencode(params)}",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://fund.eastmoney.com/data/jcxx_zhishu.html",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json,text/javascript,*/*;q=0.01",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_tracking_index_rows(
    rows: list[dict[str, Any]],
    *,
    market_bucket: str,
    fetched_at: datetime | None = None,
) -> list[TrackingIndexRecord]:
    fetched_at = fetched_at or datetime.now(timezone.utc)
    result: list[TrackingIndexRecord] = []

    for row in rows:
        code = str(row.get("FCode") or "").strip()
        if not code:
            continue

        index_code = str(row.get("StandarIndexCode") or "").strip() or None
        index_name = str(row.get("IndexName") or "").strip() or None
        error = None
        if not index_code or not index_name:
            error = "MISSING_TRACKING_INDEX"

        result.append(
            TrackingIndexRecord(
                fund_code=code.zfill(6),
                fund_name=(str(row.get("ShortName") or "").strip() or None),
                tracking_index_code=index_code,
                tracking_index_name=index_name,
                market_bucket=market_bucket,
                source="EASTMONEY_FUND_BASEINFO",
                fetched_at=fetched_at,
                error=error,
            )
        )

    return result


def fetch_tracking_index_category(
    *,
    market: str,
    fund_type: str,
    timeout: int = 15,
    sleep_seconds: float = 0.04,
) -> list[TrackingIndexRecord]:
    rows: list[dict[str, Any]] = []
    page = 1
    total = 1

    while len(rows) < total:
        payload = _get_json(
            {
                "r": int(time.time() * 1000),
                "m": market,
                "pageIndex": page,
                "sName": "FCode",
                "s": "asc",
                "t": fund_type,
            },
            timeout=timeout,
        )

        if page == 1:
            total = int(payload.get("TotalCount") or 0)

        page_rows = payload.get("Datas") or []
        if not page_rows and len(rows) < total:
            raise RuntimeError(
                f"tracking index pagination stopped early: {market=} {fund_type=}"
            )

        rows.extend(page_rows)
        page += 1
        if page > 600:
            raise RuntimeError("tracking index pagination exceeded safety limit")
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    return parse_tracking_index_rows(
        rows,
        market_bucket=f"{market}:{fund_type}",
    )


def load_tracking_index_fixture(
    path: str | Path,
) -> dict[str, TrackingIndexRecord]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    fetched_at_raw = payload.get("fetched_at")
    fetched_at = (
        datetime.fromisoformat(str(fetched_at_raw))
        if fetched_at_raw
        else datetime.now(timezone.utc)
    )
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)

    result: dict[str, TrackingIndexRecord] = {}
    for row in payload.get("rows") or []:
        code = str(row.get("fund_code") or "").strip()
        if not code:
            continue
        index_code = str(row.get("tracking_index_code") or "").strip() or None
        index_name = str(row.get("tracking_index_name") or "").strip() or None
        error = None if index_code and index_name else "MISSING_TRACKING_INDEX"
        result[code] = TrackingIndexRecord(
            fund_code=code,
            fund_name=(str(row.get("fund_name") or "").strip() or None),
            tracking_index_code=index_code,
            tracking_index_name=index_name,
            market_bucket=str(row.get("market_bucket") or "fixture"),
            source=str(row.get("source") or "TRACKING_INDEX_FIXTURE"),
            fetched_at=fetched_at,
            error=error,
        )

    if not result:
        raise ValueError("tracking-index fixture is empty")
    return result


def fetch_active_tracking_index_map(
    universe: Iterable[LofIdentity],
    *,
    timeout: int = 15,
) -> dict[str, TrackingIndexRecord]:
    """Return mapping only for active LOF universe members.

    Domestic index-fund dataset supplies the main LOF mapping.
    QDII all-fund dataset supplements active QDII LOFs after universe
    intersection. This avoids assuming Eastmoney's category membership equals
    the exchange active universe.
    """
    active_codes = {row.code for row in universe}

    domestic = fetch_tracking_index_category(
        market="zs",
        fund_type="lof",
        timeout=timeout,
    )
    qdii = fetch_tracking_index_category(
        market="qdii",
        fund_type="all",
        timeout=timeout,
    )

    mapping: dict[str, TrackingIndexRecord] = {}
    for row in [*domestic, *qdii]:
        if row.fund_code in active_codes:
            # Prefer an available record over a missing-index record.
            current = mapping.get(row.fund_code)
            if current is None or (row.available and not current.available):
                mapping[row.fund_code] = row

    return mapping
