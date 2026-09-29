from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
import re
from urllib.request import Request, urlopen


FUND_F10_URL = "https://fundf10.eastmoney.com/jbgk_{code}.html"


@dataclass(frozen=True)
class ResolverMappingCandidate:
    fund_code: str
    tracking_target_name: str | None
    benchmark_text: str | None
    exposure_ratio_candidate: Decimal | None
    source: str
    error: str | None = None


class _CellParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_cell = False
        self._parts: list[str] = []
        self.cells: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() in {"th", "td"}:
            self._in_cell = True
            self._parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"th", "td"} and self._in_cell:
            text = "".join(self._parts)
            text = re.sub(r"\s+", " ", text).strip()
            self.cells.append(text)
            self._in_cell = False
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._parts.append(data)


def _field_after(cells: list[str], label: str) -> str | None:
    for i, cell in enumerate(cells[:-1]):
        if cell.strip() == label:
            value = cells[i + 1].strip()
            return value or None
    return None


def _exposure_from_benchmark(
    *,
    benchmark_text: str | None,
    tracking_target_name: str | None,
) -> Decimal | None:
    if not benchmark_text or not tracking_target_name:
        return None

    # Prefer a percentage attached to the explicit tracking target.
    escaped = re.escape(tracking_target_name)
    patterns = [
        rf"{escaped}(?:收益率)?\s*[×*]\s*(\d+(?:\.\d+)?)%",
        rf"{escaped}[^%]{{0,40}}?(\d+(?:\.\d+)?)%",
    ]
    for pattern in patterns:
        match = re.search(pattern, benchmark_text)
        if match:
            try:
                return Decimal(match.group(1)) / Decimal("100")
            except (InvalidOperation, ValueError):
                return None

    return None


def parse_f10_mapping(
    html_text: str,
    *,
    fund_code: str,
) -> ResolverMappingCandidate:
    parser = _CellParser()
    parser.feed(html_text)

    benchmark = _field_after(parser.cells, "业绩比较基准")
    tracking_target = _field_after(parser.cells, "跟踪标的")

    if not benchmark and not tracking_target:
        return ResolverMappingCandidate(
            fund_code=fund_code,
            tracking_target_name=None,
            benchmark_text=None,
            exposure_ratio_candidate=None,
            source="EASTMONEY_F10",
            error="NO_TRACKING_MAPPING_DATA",
        )

    return ResolverMappingCandidate(
        fund_code=fund_code,
        tracking_target_name=tracking_target,
        benchmark_text=benchmark,
        exposure_ratio_candidate=_exposure_from_benchmark(
            benchmark_text=benchmark,
            tracking_target_name=tracking_target,
        ),
        source="EASTMONEY_F10",
        error=None,
    )


def fetch_f10_mapping(
    fund_code: str,
    *,
    timeout: int = 15,
) -> ResolverMappingCandidate:
    request = Request(
        FUND_F10_URL.format(code=fund_code),
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urlopen(request, timeout=timeout) as response:
        raw = response.read()
    text = raw.decode("utf-8", errors="replace")
    return parse_f10_mapping(text, fund_code=fund_code)
