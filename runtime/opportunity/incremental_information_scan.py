"""Daily all-market announcement scan for Incremental Runtime.

One market-wide fetch per date, then filter to active CB issuers and only
Path/Risk-relevant notices.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, Callable

import akshare as ak
import pandas as pd

from runtime.opportunity.evidence_sources import (
    is_maturity_research_notice,
    is_revision_notice,
    maturity_notice_kind,
    revision_notice_kind,
)
from runtime.opportunity.incremental_storage import connect

INFORMATION_SCAN_VERSION = "incremental-information-scan-v1.1-empty-day-boundary"

def put_notice_kind(title: str) -> str | None:
    if "回售" not in title:
        return None
    if "回售结果" in title:
        return "PUT_RESULT"
    if re.search(r"预计(?:触发|满足).*回售|回售.*预计(?:触发|满足)", title):
        return "PUT_EXPECTED_TRIGGER"
    if (
        re.search(r"(?:已)?触发.*回售|回售.*(?:已)?触发", title)
        and "预计" not in title
    ):
        return "PUT_TRIGGER"
    return None

def classify_relevant_notice(title: str) -> str | None:
    put_kind = put_notice_kind(title)
    if put_kind:
        return put_kind
    if is_revision_notice(title):
        return revision_notice_kind(title)
    if is_maturity_research_notice(title):
        return maturity_notice_kind(title)
    return None

def _normalize_date(value: Any) -> str:
    try:
        return pd.Timestamp(value).strftime("%Y-%m-%d")
    except Exception:
        return str(value or "")

def _issuer_map(target_db: Path) -> dict[str, list[dict[str, str]]]:
    conn = connect(target_db)
    try:
        rows = conn.execute(
            """SELECT bond_code,bond_name,stock_code
               FROM bond_master
               WHERE active=1 AND stock_code IS NOT NULL AND stock_code<>''"""
        )
        out: dict[str, list[dict[str, str]]] = {}
        for row in rows:
            stock_code = str(row["stock_code"]).zfill(6)
            out.setdefault(stock_code, []).append(
                {
                    "bond_code": str(row["bond_code"]).zfill(6),
                    "bond_name": str(row["bond_name"]),
                }
            )
        return out
    finally:
        conn.close()

def scan_daily_relevant_notices(
    *,
    target_db: Path,
    date: str,
    fetcher: Callable[..., pd.DataFrame] | None = None,
) -> dict[str, Any]:
    if not re.fullmatch(r"\d{8}", date):
        raise ValueError("date must be YYYYMMDD")
    issuer_map = _issuer_map(target_db)
    fetcher = fetcher or ak.stock_notice_report
    source_empty_day_fallback = False
    try:
        frame = fetcher(symbol="全部", date=date).copy()
    except KeyError as exc:
        # AKShare/Eastmoney currently raises KeyError('代码') internally when
        # a requested date has zero notice rows. This exact boundary is an
        # empty official-notice day, not a source-integrity failure.
        if exc.args != ("代码",):
            raise
        frame = pd.DataFrame()
        source_empty_day_fallback = True

    if frame.empty:
        return {
            "information_scan_version": INFORMATION_SCAN_VERSION,
            "status": "PASS",
            "scan_date": date,
            "all_notice_count": 0,
            "active_issuer_count": len(issuer_map),
            "relevant_document_count": 0,
            "affected_bond_count": 0,
            "event_kind_summary": {},
            "documents": [],
            "source_empty_day_fallback": source_empty_day_fallback,
        }
    if "代码" not in frame.columns:
        raise RuntimeError(
            "announcement source returned non-empty data without required column: 代码"
        )

    documents: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for _, row in frame.iterrows():
        stock_code = str(row.get("代码") or "").zfill(6)
        affected_bonds = issuer_map.get(stock_code)
        if not affected_bonds:
            continue
        title = str(row.get("公告标题") or "")
        event_kind = classify_relevant_notice(title)
        if not event_kind or event_kind == "OTHER":
            continue
        url = str(row.get("网址") or "")
        notice_date = _normalize_date(row.get("公告日期"))
        dedupe_key = url or f"{stock_code}|{notice_date}|{title}"
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        documents.append(
            {
                "issuer_stock_code": stock_code,
                "issuer_name": str(row.get("名称") or ""),
                "notice_date": notice_date,
                "title": title,
                "notice_type": str(row.get("公告类型") or ""),
                "url": url,
                "event_kind": event_kind,
                "source": "AKShare/Eastmoney daily announcement index",
                "affected_bonds": affected_bonds,
            }
        )

    kinds = Counter(str(x["event_kind"]) for x in documents)
    affected_bond_codes = sorted(
        {
            bond["bond_code"]
            for doc in documents
            for bond in doc["affected_bonds"]
        }
    )
    return {
        "information_scan_version": INFORMATION_SCAN_VERSION,
        "status": "PASS",
        "scan_date": date,
        "all_notice_count": int(len(frame)),
        "active_issuer_count": len(issuer_map),
        "relevant_document_count": len(documents),
        "affected_bond_count": len(affected_bond_codes),
        "event_kind_summary": dict(sorted(kinds.items())),
        "documents": documents,
        "source_empty_day_fallback": source_empty_day_fallback,
    }
