from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import html
import json
import os
from pathlib import Path
import re
import time
import tempfile
from typing import Iterable
from urllib.request import Request, urlopen


SOURCE = "EASTMONEY_FUND_F10_DIVIDEND"
PAGE_URL = "https://fundf10.eastmoney.com/fhsp_{fund_code}.html"


@dataclass(frozen=True)
class CashDistribution:
    record_date: date | None
    ex_date: date
    cash_per_unit: Decimal
    payment_date: date | None
    raw_text: str

    def to_dict(self) -> dict:
        return {
            "record_date": (
                self.record_date.isoformat()
                if self.record_date is not None
                else None
            ),
            "ex_date": self.ex_date.isoformat(),
            "cash_per_unit": float(self.cash_per_unit),
            "payment_date": (
                self.payment_date.isoformat()
                if self.payment_date is not None
                else None
            ),
            "raw_text": self.raw_text,
        }

    @classmethod
    def from_dict(cls, value: dict) -> "CashDistribution":
        record = value.get("record_date")
        payment = value.get("payment_date")
        return cls(
            record_date=(
                date.fromisoformat(str(record))
                if record
                else None
            ),
            ex_date=date.fromisoformat(str(value["ex_date"])),
            cash_per_unit=Decimal(
                str(value["cash_per_unit"])
            ),
            payment_date=(
                date.fromisoformat(str(payment))
                if payment
                else None
            ),
            raw_text=str(value.get("raw_text") or ""),
        )


@dataclass(frozen=True)
class DistributionSchedule:
    fund_code: str
    fetched_at: datetime
    events: tuple[CashDistribution, ...]
    source: str = SOURCE

    def to_dict(self) -> dict:
        return {
            "fund_code": self.fund_code,
            "fetched_at": self.fetched_at.isoformat(),
            "events": [x.to_dict() for x in self.events],
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, value: dict) -> "DistributionSchedule":
        return cls(
            fund_code=str(value["fund_code"]),
            fetched_at=datetime.fromisoformat(
                str(value["fetched_at"])
            ),
            events=tuple(
                CashDistribution.from_dict(x)
                for x in value.get("events", [])
            ),
            source=str(value.get("source") or SOURCE),
        )


def _strip_html(value: str) -> str:
    return html.unescape(
        re.sub(r"<.*?>", "", value, flags=re.S)
    ).strip()


def _parse_date(value: str) -> date | None:
    raw = value.strip()
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _cash_per_unit(value: str) -> Decimal | None:
    text = value.replace(",", "").strip()
    match = re.search(
        r"每10份(?:基金份额)?派现金([0-9]+(?:\.[0-9]+)?)元",
        text,
    )
    if match is None:
        return None
    try:
        cash_per_ten = Decimal(match.group(1))
    except (InvalidOperation, ValueError):
        return None
    result = cash_per_ten / Decimal("10")
    return result if result > 0 else None


def parse_distribution_page(
    text: str,
) -> tuple[CashDistribution, ...]:
    events: list[CashDistribution] = []
    for tr in re.findall(r"<tr>(.*?)</tr>", text, flags=re.S):
        cells = [
            _strip_html(x)
            for x in re.findall(
                r"<td[^>]*>(.*?)</td>",
                tr,
                flags=re.S,
            )
        ]
        if len(cells) < 5:
            continue

        record_date = _parse_date(cells[1])
        ex_date = _parse_date(cells[2])
        cash = _cash_per_unit(cells[3])
        payment_date = _parse_date(cells[4])

        if ex_date is None or cash is None:
            continue

        events.append(
            CashDistribution(
                record_date=record_date,
                ex_date=ex_date,
                cash_per_unit=cash,
                payment_date=payment_date,
                raw_text=cells[3],
            )
        )

    events.sort(
        key=lambda x: (
            x.ex_date,
            x.cash_per_unit,
        ),
        reverse=True,
    )
    return tuple(events)


def fetch_distribution_schedule(
    fund_code: str,
    *,
    now: datetime,
    timeout: int = 6,
) -> DistributionSchedule:
    request = Request(
        PAGE_URL.format(fund_code=fund_code),
        headers={"User-Agent": "Mozilla/5.0"},
    )
    last_error = None
    text = None
    for attempt in range(3):
        try:
            with urlopen(request, timeout=timeout) as response:
                text = response.read().decode(
                    "utf-8",
                    errors="replace",
                )
            break
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
    if text is None:
        assert last_error is not None
        raise last_error

    return DistributionSchedule(
        fund_code=fund_code,
        fetched_at=now,
        events=parse_distribution_page(text),
    )


def cash_distribution_on(
    schedule: DistributionSchedule,
    target_date: date,
) -> Decimal:
    return sum(
        (
            event.cash_per_unit
            for event in schedule.events
            if event.ex_date == target_date
        ),
        Decimal("0"),
    )


def schedules_need_refresh(
    schedules: dict[str, DistributionSchedule],
    fund_codes: Iterable[str],
    *,
    now: datetime,
    refresh_seconds: int,
) -> bool:
    codes = set(fund_codes)
    if codes - set(schedules):
        return True

    for code in codes:
        row = schedules.get(code)
        if row is None:
            return True
        fetched = row.fetched_at
        current = now
        if fetched.tzinfo is None and current.tzinfo is not None:
            fetched = fetched.replace(
                tzinfo=current.tzinfo
            )
        if current.tzinfo is None and fetched.tzinfo is not None:
            current = current.replace(
                tzinfo=fetched.tzinfo
            )
        try:
            age = (current - fetched).total_seconds()
        except TypeError:
            return True
        if age < 0 or age >= refresh_seconds:
            return True

    return False


class DistributionStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.path = (
            self.root / "r2_distribution_snapshot.json"
        )

    def load(self) -> dict[str, DistributionSchedule]:
        if not self.path.exists():
            return {}
        payload = json.loads(
            self.path.read_text(encoding="utf-8")
        )
        return {
            code: DistributionSchedule.from_dict(value)
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
        dict[str, DistributionSchedule],
        dict[str, str],
    ]:
        previous = self.load()
        result = dict(previous)
        errors: dict[str, str] = {}
        codes = sorted(set(fund_codes))

        with ThreadPoolExecutor(
            max_workers=1
        ) as pool:
            futures = {
                pool.submit(
                    fetch_distribution_schedule,
                    code,
                    now=now,
                    timeout=timeout,
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
        rows: dict[str, DistributionSchedule],
        *,
        generated_at: datetime,
        refresh_errors: dict[str, str] | None = None,
    ) -> None:
        payload = {
            "version": "R2_DISTRIBUTION_SNAPSHOT_V1",
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
            prefix=".r2_distribution.",
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