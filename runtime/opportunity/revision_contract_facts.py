"""Low-frequency mechanism and NAV facts for Downward Revision Discovery."""

from __future__ import annotations

import json
import re
from io import StringIO
from pathlib import Path
from typing import Any

import akshare as ak
import pandas as pd
import requests

REVISION_FACTS_VERSION = "revision-contract-facts-v1"
KZZDATA_REVISION_URL = "https://kzzdata.com/cb-xiaxiu"


def _text(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _normalize_revision_count(value: Any) -> tuple[str | None, str | None]:
    raw = _text(value)
    if not raw or raw == "--":
        return None, raw or None
    if re.fullmatch(r"\d+/\d+", raw):
        return raw, raw
    if re.fullmatch(r"还需\s*\d+/\d+", raw):
        # Source display means "minimum remaining days / threshold", not the
        # current accumulated trigger count. Preserve it only as raw evidence.
        return None, raw
    return None, raw


def fetch_revision_watch() -> pd.DataFrame:
    response = requests.get(
        KZZDATA_REVISION_URL,
        timeout=(5, 20),
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()
    tables = pd.read_html(StringIO(response.text))
    if len(tables) != 1:
        raise RuntimeError(f"unexpected revision-watch table count: {len(tables)}")
    frame = tables[0].copy()
    required = {"转债", "正股", "状态", "重算起始", "触发价", "转股价", "历史下修"}
    if frame.empty or not required.issubset(frame.columns):
        raise RuntimeError("revision-watch table is empty or incomplete")
    frame["bond_code"] = frame["转债"].astype(str).str.extract(r"(\d{6})\s*$")[0]
    frame["stock_code"] = frame["正股"].astype(str).str.extract(r"(\d{6})\s*$")[0]
    return frame


def fetch_latest_audited_nav(market_cutoff: str) -> pd.DataFrame:
    cutoff = pd.Timestamp(market_cutoff)
    report_year = cutoff.year - 1
    report_date = f"{report_year}1231"
    frame = ak.stock_yjbb_em(date=report_date).copy()
    required = {"股票代码", "每股净资产", "最新公告日期"}
    if frame.empty or not required.issubset(frame.columns):
        raise RuntimeError("annual NAV table is empty or incomplete")
    frame["股票代码"] = frame["股票代码"].astype(str).str.zfill(6)
    frame["每股净资产"] = pd.to_numeric(frame["每股净资产"], errors="coerce")
    frame["最新公告日期"] = pd.to_datetime(frame["最新公告日期"], errors="coerce")
    frame = frame[
        frame["最新公告日期"].notna()
        & (frame["最新公告日期"] <= cutoff)
        & frame["每股净资产"].notna()
    ].copy()
    frame = frame.sort_values("最新公告日期").drop_duplicates("股票代码", keep="last")
    frame["report_date"] = report_date
    return frame


def load_nav_clause_evidence(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    result: dict[str, dict[str, Any]] = {}
    for item in payload.get("evidence", []):
        code = str(item["bond_code"]).zfill(6)
        if "nav_floor_applicable" not in item or "source" not in item:
            raise ValueError(f"NAV clause evidence incomplete for {code}")
        result[code] = dict(item)
    return result


def build_revision_contract_facts(
    market_input: dict[str, Any],
    revision_watch: pd.DataFrame,
    nav_frame: pd.DataFrame,
    *,
    nav_clause_evidence: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    nav_clause_evidence = nav_clause_evidence or {}
    market_codes = {str(row["bond_code"]).zfill(6) for row in market_input["rows"]}

    watch = revision_watch[
        revision_watch["bond_code"].isin(market_codes)
    ].drop_duplicates("bond_code")
    watch_rows = watch.set_index("bond_code").to_dict("index")
    nav_rows = nav_frame.set_index("股票代码").to_dict("index")

    rows = []
    for market_row in market_input["rows"]:
        code = str(market_row["bond_code"]).zfill(6)
        stock_code = str(market_row["stock_code"]).zfill(6)
        watch_row = watch_rows.get(code)
        nav_row = nav_rows.get(stock_code)
        evidence = nav_clause_evidence.get(code)

        if watch_row is None:
            rows.append({
                "bond_code": code,
                "bond_name": market_row["bond_name"],
                "status": "INSUFFICIENT_DATA",
                "revision_clause_available": None,
                "permanent_revision_blocker": None,
                "reason": "REVISION_WATCH_MISSING",
            })
            continue

        reset_date = pd.to_datetime(watch_row.get("重算起始"), errors="coerce")
        maturity_date = pd.to_datetime(market_row.get("maturity_date"), errors="coerce")
        permanent_blocker = bool(
            pd.notna(reset_date)
            and pd.notna(maturity_date)
            and reset_date >= maturity_date
        )

        source_k = pd.to_numeric(watch_row.get("转股价"), errors="coerce")
        trigger = _text(watch_row.get("触发价"))
        clause_available = bool(pd.notna(source_k) and trigger)
        revision_count, revision_count_raw = _normalize_revision_count(
            watch_row.get("下修天计数")
        )

        latest_nav = (
            float(nav_row["每股净资产"])
            if nav_row is not None and pd.notna(nav_row.get("每股净资产"))
            else None
        )

        rows.append({
            "bond_code": code,
            "bond_name": market_row["bond_name"],
            "stock_code": stock_code,
            "status": "READY" if clause_available else "INSUFFICIENT_DATA",
            "revision_clause_available": clause_available,
            "permanent_revision_blocker": permanent_blocker,
            "permanent_revision_blocker_basis": (
                "RESET_START_ON_OR_AFTER_MATURITY"
                if permanent_blocker else "NO_PERMANENT_BLOCKER_OBSERVED"
            ),
            "revision_event_state": _text(watch_row.get("状态")),
            "revision_count": revision_count,
            "revision_count_raw": revision_count_raw,
            "minimum_days_needed": _text(watch_row.get("至少还需")),
            "reset_start": (
                str(reset_date.date()) if pd.notna(reset_date) else None
            ),
            "maturity_date": (
                str(maturity_date.date()) if pd.notna(maturity_date) else None
            ),
            "source_conversion_price": (
                float(source_k) if pd.notna(source_k) else None
            ),
            "trigger_price_text": trigger,
            "historical_revision": _text(watch_row.get("历史下修")),
            "latest_audited_nav_per_share": latest_nav,
            "nav_report_date": (
                str(nav_row.get("report_date")) if nav_row is not None else None
            ),
            "nav_announcement_date": (
                str(pd.Timestamp(nav_row["最新公告日期"]).date())
                if nav_row is not None and pd.notna(nav_row.get("最新公告日期"))
                else None
            ),
            "nav_floor_applicable": (
                bool(evidence["nav_floor_applicable"]) if evidence else None
            ),
            "nav_clause_evidence_source": evidence.get("source") if evidence else None,
            "nav_clause_evidence_reason": evidence.get("reason") if evidence else None,
        })

    return {
        "contract_facts_version": REVISION_FACTS_VERSION,
        "market_run_id": market_input["run_id"],
        "market_snapshot_id": market_input["market_snapshot_id"],
        "market_cutoff": market_input["market_cutoff"],
        "rows": rows,
        "audit": {
            "universe": len(market_input["rows"]),
            "watch_matched": sum(row.get("revision_clause_available") is not None for row in rows),
            "mechanism_ready": sum(row.get("revision_clause_available") is True for row in rows),
            "permanent_blockers": sum(row.get("permanent_revision_blocker") is True for row in rows),
            "nav_available": sum(row.get("latest_audited_nav_per_share") is not None for row in rows),
            "nav_clause_evidence": sum(row.get("nav_floor_applicable") is not None for row in rows),
            "insufficient_data": sum(row.get("status") == "INSUFFICIENT_DATA" for row in rows),
        },
    }
