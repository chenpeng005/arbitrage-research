"""Structured evidence sources for Path Research Evidence Pack V1."""

from __future__ import annotations

import re
from typing import Any

import akshare as ak
import pandas as pd


def _num(value: Any) -> float | None:
    value = pd.to_numeric(value, errors="coerce")
    return None if pd.isna(value) else float(value)


def statement_date_for_cutoff(cutoff: str) -> str:
    value = pd.Timestamp(cutoff)
    if (value.month, value.day) >= (8, 31):
        return f"{value.year}0630"
    if (value.month, value.day) >= (4, 30):
        return f"{value.year}0331"
    return f"{value.year - 1}1231"


def fetch_bulk_financial(statement_date: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    balance = ak.stock_zcfz_em(date=statement_date).copy()
    cashflow = ak.stock_xjll_em(date=statement_date).copy()
    balance["股票代码"] = balance["股票代码"].astype(str).str.zfill(6)
    cashflow["股票代码"] = cashflow["股票代码"].astype(str).str.zfill(6)
    return balance, cashflow


def compact_financial_fact(
    stock_code: str,
    statement_date: str,
    balance_index: dict[str, dict[str, Any]],
    cashflow_index: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    b = balance_index.get(stock_code)
    c = cashflow_index.get(stock_code)

    def yi(row: dict[str, Any] | None, key: str) -> float | None:
        if not row:
            return None
        value = _num(row.get(key))
        return None if value is None else value / 1e8

    return {
        "statement_date": statement_date,
        "scope": "CONSOLIDATED",
        "balance_sheet_matched": b is not None,
        "cash_flow_matched": c is not None,
        "money_funds_yi": yi(b, "资产-货币资金"),
        "total_assets_yi": yi(b, "资产-总资产"),
        "total_liabilities_yi": yi(b, "负债-总负债"),
        "asset_liability_ratio_pct": _num(b.get("资产负债率")) if b else None,
        "operating_cash_flow_yi": yi(c, "经营性现金流-现金流量净额"),
        "financing_cash_flow_yi": yi(c, "融资性现金流-现金流量净额"),
        "net_cash_flow_yi": yi(c, "净现金流-净现金流"),
        "balance_notice_date": str(b.get("公告日期")) if b else None,
        "cashflow_notice_date": str(c.get("公告日期")) if c else None,
        "source": "AKShare/Eastmoney bulk financial statements",
    }


def fetch_structured_rating(bond_code: str) -> dict[str, Any]:
    try:
        frame = ak.bond_zh_cov_info(symbol=bond_code, indicator="基本信息")
        if frame.empty:
            raise RuntimeError("empty bond basic info")
        row = frame.iloc[0]
        rating = row.get("RATING")
        return {
            "matched": True,
            "rating": None if pd.isna(rating) else str(rating),
            "source": "AKShare/Eastmoney bond basic info",
        }
    except Exception as exc:
        return {
            "matched": False,
            "rating": None,
            "source": "AKShare/Eastmoney bond basic info",
            "error": f"{type(exc).__name__}: {exc}",
        }


def revision_notice_kind(title: str) -> str:
    if "不向下修正" in title or "不下修" in title:
        return "NO_REVISION"
    if "预计触发" in title and ("修正" in title or "下修" in title):
        return "EXPECTED_TRIGGER"
    if "触发" in title and ("修正" in title or "下修" in title):
        return "TRIGGER"
    if "向下修正" in title or "下修" in title:
        return "REVISION_ACTION"
    if "转股价格" in title and ("修正" in title or "调整" in title):
        return "CONVERSION_PRICE_EVENT"
    return "OTHER"


def is_revision_notice(title: str) -> bool:
    return bool(re.search(
        r"不向下修正|下修|向下修正|预计触发.*修正|触发.*修正|"
        r"转股价格.*修正|修正.*转股价格|转股价格.*调整",
        title,
    ))


def fetch_revision_notice_index(
    stock_code: str,
    begin_date: str,
    end_date: str,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    frame = ak.stock_individual_notice_report(
        security=stock_code,
        symbol="全部",
        begin_date=begin_date,
        end_date=end_date,
    ).copy()
    selected: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        title = str(row.get("公告标题", ""))
        if not is_revision_notice(title):
            continue
        selected.append({
            "notice_date": str(row.get("公告日期")),
            "title": title,
            "notice_type": str(row.get("公告类型", "")),
            "url": str(row.get("网址", "")),
            "event_kind": revision_notice_kind(title),
            "source": "AKShare/Eastmoney official-announcement index",
        })
    selected.sort(key=lambda x: x["notice_date"])
    return frame, selected



def maturity_notice_kind(title: str) -> str:
    if "半年度报告" in title or "年度报告" in title:
        return "FINANCIAL_REPORT"
    if "跟踪评级" in title or "评级报告" in title:
        return "RATING_REPORT"
    if "受托管理" in title:
        return "TRUSTEE_REPORT"
    if re.search(r"逾期|违约|冻结|重整|预重整|破产|持续经营", title):
        return "HARD_CREDIT_EVENT"
    if re.search(r"授信|借款|融资|发行.*债券|债券.*发行", title):
        return "FINANCING_SUPPORT"
    if re.search(r"担保|资产出售|资产转让|增资|控股股东.*支持", title):
        return "SUPPORT_OR_ASSET"
    return "OTHER"


def is_maturity_research_notice(title: str) -> bool:
    return bool(re.search(
        r"半年度报告|年度报告|跟踪评级|评级报告|受托管理|"
        r"逾期|违约|冻结|重整|预重整|破产|持续经营|"
        r"授信|借款|融资|发行.*债券|债券.*发行|担保|资产出售|资产转让|增资",
        title,
    ))


def fetch_maturity_notice_index(
    stock_code: str,
    begin_date: str,
    end_date: str,
    max_items: int = 40,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    frame = ak.stock_individual_notice_report(
        security=stock_code,
        symbol="全部",
        begin_date=begin_date,
        end_date=end_date,
    ).copy()
    selected: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        title = str(row.get("公告标题", ""))
        if not is_maturity_research_notice(title):
            continue
        selected.append({
            "notice_date": str(row.get("公告日期")),
            "title": title,
            "notice_type": str(row.get("公告类型", "")),
            "url": str(row.get("网址", "")),
            "event_kind": maturity_notice_kind(title),
            "source": "AKShare/Eastmoney official-announcement index",
        })
    selected.sort(key=lambda x: x["notice_date"], reverse=True)
    return frame, selected[:max_items]
