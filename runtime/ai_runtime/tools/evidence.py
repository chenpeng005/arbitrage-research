from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import akshare as ak
import pandas as pd
import requests
from pypdf import PdfReader

MAX_LOOKBACK_DAYS = 730
MAX_SEARCH_RESULTS = 20
MAX_MODEL_TEXT_CHARS = 40000


@dataclass
class ToolContext:
    business_run_dir: Path
    ai_job_dir: Path
    input_payload: dict[str, Any]


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", str(text or "")).strip()


def _request_map(ctx: ToolContext) -> dict[str, dict[str, Any]]:
    return {
        str(item["conflict_id"]): item
        for item in ctx.input_payload.get("requests", [])
    }


def _audit_row(ctx: ToolContext, bond_code: str) -> pd.Series:
    path = ctx.business_run_dir / "market_input_audit.csv"
    if not path.exists():
        raise RuntimeError("market_input_audit.csv is required for evidence tools")
    df = pd.read_csv(path, dtype={"bond_code": str, "stock_code": str})
    df["bond_code"] = df["bond_code"].astype(str).str.zfill(6)
    rows = df[df["bond_code"] == str(bond_code).zfill(6)]
    if len(rows) != 1:
        raise RuntimeError(f"bond_code={bond_code} is not unique in audit input")
    return rows.iloc[0]


def _bounded_dates(
    market_cutoff: str,
    start_date: str | None,
    end_date: str | None,
) -> tuple[str, str]:
    cutoff = pd.Timestamp(market_cutoff).normalize()
    end = pd.Timestamp(end_date).normalize() if end_date else cutoff
    start = (
        pd.Timestamp(start_date).normalize()
        if start_date
        else end - pd.Timedelta(days=180)
    )
    if end > cutoff:
        end = cutoff
    minimum = cutoff - pd.Timedelta(days=MAX_LOOKBACK_DAYS)
    if start < minimum:
        start = minimum
    if start > end:
        raise ValueError("start_date must not be later than end_date")
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def evidence_search(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    conflict_id = str(args.get("conflict_id") or "")
    req = _request_map(ctx).get(conflict_id)
    if req is None:
        raise ValueError("conflict_id is not part of this AI task")

    row = _audit_row(ctx, req["bond_code"])
    stock_code = str(row.get("stock_code") or "").split(".")[0].zfill(6)
    if len(stock_code) != 6 or not stock_code.isdigit():
        raise RuntimeError(f"invalid stock_code for {req['bond_code']}: {stock_code!r}")

    keyword = str(args.get("keyword") or "转债").strip()
    if not keyword:
        keyword = "转债"

    start_date, end_date = _bounded_dates(
        str(ctx.input_payload["market_cutoff"]),
        args.get("start_date"),
        args.get("end_date"),
    )

    df = ak.stock_zh_a_disclosure_report_cninfo(
        symbol=stock_code,
        market="沪深京",
        keyword=keyword,
        category="",
        start_date=start_date,
        end_date=end_date,
    )

    results: list[dict[str, Any]] = []
    for _, item in df.head(MAX_SEARCH_RESULTS).iterrows():
        detail_url = str(item.get("公告链接") or "")
        parsed = parse_qs(urlparse(detail_url).query)
        announcement_id = (parsed.get("announcementId") or [None])[0]
        published = pd.Timestamp(item.get("公告时间")).date().isoformat()
        if not announcement_id:
            continue
        pdf_url = (
            f"http://static.cninfo.com.cn/finalpage/"
            f"{published}/{announcement_id}.PDF"
        )
        results.append(
            {
                "evidence_id": str(announcement_id),
                "source_type": "cninfo_announcement",
                "stock_code": stock_code,
                "bond_code": str(req["bond_code"]),
                "bond_name": str(req.get("bond_name") or ""),
                "title": _strip_html(item.get("公告标题")),
                "published_at": published,
                "detail_url": detail_url,
                "pdf_url": pdf_url,
                "keyword": keyword,
            }
        )

    catalog_path = ctx.ai_job_dir / "evidence_catalog.json"
    catalog = {}
    if catalog_path.exists():
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    for item in results:
        catalog[item["evidence_id"]] = item
    catalog_path.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "conflict_id": conflict_id,
        "query": {
            "stock_code": stock_code,
            "keyword": keyword,
            "start_date": start_date,
            "end_date": end_date,
        },
        "count": len(results),
        "results": results,
    }


def evidence_fetch(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    evidence_id = str(args.get("evidence_id") or "")
    catalog_path = ctx.ai_job_dir / "evidence_catalog.json"
    if not catalog_path.exists():
        raise ValueError("evidence_fetch requires prior evidence_search")
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    item = catalog.get(evidence_id)
    if item is None:
        raise ValueError("evidence_id was not returned by evidence_search in this AI job")

    evidence_dir = ctx.ai_job_dir / "evidence"
    evidence_dir.mkdir(exist_ok=True)

    pdf_path = evidence_dir / f"{evidence_id}.pdf"
    txt_path = evidence_dir / f"{evidence_id}.txt"
    meta_path = evidence_dir / f"{evidence_id}.json"

    response = requests.get(item["pdf_url"], timeout=30)
    response.raise_for_status()
    if "pdf" not in (response.headers.get("content-type") or "").lower():
        raise RuntimeError("CNINFO evidence response is not a PDF")

    pdf_path.write_bytes(response.content)
    pdf_sha256 = hashlib.sha256(response.content).hexdigest()

    reader = PdfReader(str(pdf_path))
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    txt_path.write_text(text, encoding="utf-8")
    text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()

    meta = {
        **item,
        "pdf_artifact": str(pdf_path.relative_to(ctx.ai_job_dir)),
        "text_artifact": str(txt_path.relative_to(ctx.ai_job_dir)),
        "pdf_sha256": pdf_sha256,
        "text_sha256": text_sha256,
        "page_count": len(reader.pages),
        "text_chars": len(text),
    }
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        **meta,
        "text": text[:MAX_MODEL_TEXT_CHARS],
        "text_truncated": len(text) > MAX_MODEL_TEXT_CHARS,
    }


TOOL_HANDLERS = {
    "evidence_search": evidence_search,
    "evidence_fetch": evidence_fetch,
}


def execute_tool(
    name: str,
    ctx: ToolContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        raise ValueError(f"Tool is not allowed: {name}")
    return handler(ctx, arguments)
