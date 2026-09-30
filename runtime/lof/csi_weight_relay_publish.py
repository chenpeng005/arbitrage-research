from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


RELAY_VERSION = "lof-csi-component-weight-relay-v1"
CSI_INDEX_DETAILS_URL = (
    "https://www.csindex.com.cn/csindex-home/"
    "indexInfo/index-details-data?fileLang=1&indexCode={index_code}"
)

# R1 equity-index codes that do not have a stable direct quote path on the
# production server. Keep this list deliberately narrow; adding an index
# requires a successful official-weight probe and runtime validation.
DEFAULT_INDEX_CODES = (
    "930620",
    "930641",
    "930713",
    "930719",
    "930720",
    "930721",
    "930743",
    "930790",
    "930791",
    "930820",
    "930875",
    "930997",
    "931068",
    "931069",
    "931136",
    "H30094",
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/154 Safari/537.36"
    ),
    "Referer": "https://www.csindex.com.cn/",
    "Accept": "application/json,text/plain,*/*",
}


def _fetch_bytes(url: str, *, timeout: int) -> bytes:
    request = Request(url, headers=_HEADERS)
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def _fetch_json(url: str, *, timeout: int) -> dict[str, Any]:
    return json.loads(_fetch_bytes(url, timeout=timeout).decode("utf-8"))


def _normalize_code(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{int(value):06d}"
    text = str(value or "").strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text.zfill(6) if text.isdigit() and len(text) <= 6 else text


def _normalize_weight(value: Any) -> Decimal:
    text = str(value or "").strip().replace("%", "")
    return Decimal(text)


def _normalize_date(value: Any, *, datemode: int) -> str:
    import xlrd

    if isinstance(value, (int, float)):
        return xlrd.xldate_as_datetime(value, datemode).date().isoformat()
    text = str(value or "").strip()
    digits = text.replace("-", "").replace("/", "")
    if len(digits) == 8 and digits.isdigit():
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return text


def _header_map(sheet) -> dict[str, int]:
    aliases = {
        "date": ("日期", "date"),
        "index_code": ("指数代码", "indexcode"),
        "constituent_code": ("成份券代码", "成分券代码", "constituentcode"),
        "constituent_name": ("成份券名称", "成分券名称", "constituentname"),
        "exchange": ("交易所", "exchange"),
        "weight": ("权重", "weight"),
    }
    for row_idx in range(min(sheet.nrows, 8)):
        values = [str(sheet.cell_value(row_idx, col)).strip() for col in range(sheet.ncols)]
        normalized = [x.lower().replace(" ", "") for x in values]
        result: dict[str, int] = {}
        for key, candidates in aliases.items():
            for col, value in enumerate(normalized):
                if any(candidate.lower() in value for candidate in candidates):
                    result[key] = col
                    break
        if len(result) == len(aliases):
            result["_row"] = row_idx
            return result
    raise ValueError("CSI closeweight header not recognized")


def parse_closeweight_xls(
    raw: bytes,
    *,
    expected_index_code: str,
) -> dict[str, Any]:
    import xlrd

    book = xlrd.open_workbook(file_contents=raw)
    sheet = book.sheet_by_index(0)
    columns = _header_map(sheet)
    rows: list[dict[str, Any]] = []

    for row_idx in range(columns["_row"] + 1, sheet.nrows):
        constituent_code = _normalize_code(
            sheet.cell_value(row_idx, columns["constituent_code"])
        )
        if not constituent_code:
            continue
        index_code = _normalize_code(
            sheet.cell_value(row_idx, columns["index_code"])
        )
        if index_code != expected_index_code:
            continue
        weight = _normalize_weight(
            sheet.cell_value(row_idx, columns["weight"])
        )
        if weight <= 0:
            continue
        rows.append(
            {
                "date": _normalize_date(
                    sheet.cell_value(row_idx, columns["date"]),
                    datemode=book.datemode,
                ),
                "index_code": index_code,
                "constituent_code": constituent_code,
                "constituent_name": str(
                    sheet.cell_value(row_idx, columns["constituent_name"])
                ).strip(),
                "exchange": str(
                    sheet.cell_value(row_idx, columns["exchange"])
                ).strip(),
                "weight": str(weight),
            }
        )

    if len(rows) < 10:
        raise ValueError(
            f"CSI closeweight constituent count too small: "
            f"{expected_index_code} -> {len(rows)}"
        )
    dates = {row["date"] for row in rows}
    if len(dates) != 1:
        raise ValueError(
            f"CSI closeweight has mixed dates: "
            f"{expected_index_code} -> {sorted(dates)}"
        )
    total = sum(Decimal(row["weight"]) for row in rows)
    if total < Decimal("98") or total > Decimal("102"):
        raise ValueError(
            f"CSI closeweight sum outside sanity range: "
            f"{expected_index_code} -> {total}"
        )
    return {
        "index_code": expected_index_code,
        "weight_date": next(iter(dates)),
        "constituent_count": len(rows),
        "weight_sum": str(total),
        "constituents": rows,
    }


def fetch_one_index(
    index_code: str,
    *,
    timeout: int,
) -> dict[str, Any]:
    details = _fetch_json(
        CSI_INDEX_DETAILS_URL.format(index_code=index_code),
        timeout=timeout,
    )
    data = details.get("data") or {}
    files = data.get("样本权重") or []
    if not files:
        raise ValueError(f"CSI weight file missing: {index_code}")
    file_url = str(files[0].get("filePath") or "").strip()
    if not file_url:
        raise ValueError(f"CSI weight file URL missing: {index_code}")
    raw = _fetch_bytes(file_url, timeout=timeout)
    parsed = parse_closeweight_xls(
        raw,
        expected_index_code=index_code,
    )
    parsed["weight_file_url"] = file_url.split("?", 1)[0]
    parsed["weight_file_sha256"] = hashlib.sha256(raw).hexdigest()
    return parsed


def publish_weight_relay(
    *,
    output_dir: Path,
    index_codes: tuple[str, ...],
    timeout: int = 20,
) -> dict[str, Any]:
    fetched_at = datetime.now(timezone.utc)
    indices = [
        fetch_one_index(code, timeout=timeout)
        for code in sorted(set(index_codes))
    ]
    if len(indices) != len(set(index_codes)):
        raise ValueError("CSI weight relay index count mismatch")

    payload = {
        "relay_version": RELAY_VERSION,
        "source": "CSI_OFFICIAL",
        "fetched_at": fetched_at.isoformat(),
        "index_count": len(indices),
        "indices": indices,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    payload_path = output_dir / "csi_weights.json"
    data = (
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    payload_path.write_bytes(data)
    payload_hash = hashlib.sha256(data).hexdigest()

    manifest = {
        "relay_version": RELAY_VERSION,
        "source": "CSI_OFFICIAL",
        "fetched_at": fetched_at.isoformat(),
        "index_count": len(indices),
        "file": {
            "filename": "csi_weights.json",
            "sha256": payload_hash,
        },
        "weight_dates": {
            row["index_code"]: row["weight_date"]
            for row in indices
        },
        "constituent_counts": {
            row["index_code"]: row["constituent_count"]
            for row in indices
        },
    }
    manifest_data = (
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    (output_dir / "manifest.json").write_bytes(manifest_data)
    return {
        **manifest,
        "manifest_sha256": hashlib.sha256(manifest_data).hexdigest(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build official CSI component-weight relay for LOF R1."
    )
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument(
        "--index-code",
        action="append",
        dest="index_codes",
        help="Optional repeated CSI index code; defaults to the R1 registry.",
    )
    args = parser.parse_args(argv)
    codes = tuple(args.index_codes or DEFAULT_INDEX_CODES)
    result = publish_weight_relay(
        output_dir=Path(args.output_dir),
        index_codes=codes,
        timeout=args.timeout,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
