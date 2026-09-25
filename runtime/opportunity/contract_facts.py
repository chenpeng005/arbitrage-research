"""Low-frequency contract facts for Opportunity Discovery.

Current V1 implements the maturity-cash branch. It prefers batch structured
sources and accepts explicit, provenance-bearing authoritative overrides only
for decision-sensitive exceptions.
"""

from __future__ import annotations

import calendar
import json
import re
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import akshare as ak
import pandas as pd
import requests

CONTRACT_FACTS_VERSION = "contract-facts-ingress-v1"
EASTMONEY_CB_LIST_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _text(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).replace("％", "%").strip()


def parse_coupon_rates(text: Any) -> list[float]:
    value = _text(text)
    rates = re.findall(
        r"第[一二三四五六七八九十0-9]+年[^0-9]{0,12}([0-9]+(?:\.[0-9]+)?)\s*%",
        value,
    )
    if rates:
        return [float(x) for x in rates]
    fallback = [float(x) for x in re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*%", value)]
    return fallback if 1 <= len(fallback) <= 10 else []


def maturity_clause_segment(text: Any) -> str:
    return re.split(r"(?:有条件赎回|条件赎回)", _text(text), maxsplit=1)[0]


def redemption_includes_final_interest(text: Any) -> bool:
    segment = maturity_clause_segment(text)
    patterns = (
        r"含最后一期(?:年度|计息年度|应计)?利息",
        r"含最后一年利息",
    )
    return any(re.search(pattern, segment) for pattern in patterns)


def parse_maturity_redemption(text: Any) -> tuple[float | None, str | None]:
    segment = maturity_clause_segment(text)
    patterns = (
        r"(?:到期|期满)[^。；;]{0,180}?(?:票面)?(?:面值|价值)(?:的)?\s*([0-9]+(?:\.[0-9]+)?)\s*%",
        r"(?:到期|期满)[^。；;]{0,180}?按\s*(?:每张)?\s*([0-9]+(?:\.[0-9]+)?)\s*元",
        r"(?:到期|期满)[^。；;]{0,180}?以\s*(?:每张)?\s*([0-9]+(?:\.[0-9]+)?)\s*元",
    )
    for pattern in patterns:
        match = re.search(pattern, segment)
        if match:
            return float(match.group(1)), "STRUCTURED_CLAUSE"

    uplift = re.search(
        r"(?:到期|期满)[^。；;]{0,180}?(?:票面)?面值上浮\s*([0-9]+(?:\.[0-9]+)?)\s*%",
        segment,
    )
    if uplift:
        amount = float(uplift.group(1))
        if amount <= 50:
            return 100.0 + amount, "STRUCTURED_CLAUSE_UPLIFT"

    return None, None


def fetch_eastmoney_contract_table(*, page_size: int = 500) -> pd.DataFrame:
    quote_columns = (
        "f2~01~CONVERT_STOCK_CODE~CONVERT_STOCK_PRICE,"
        "f235~10~SECURITY_CODE~TRANSFER_PRICE,"
        "f236~10~SECURITY_CODE~TRANSFER_VALUE,"
        "f2~10~SECURITY_CODE~CURRENT_BOND_PRICE,"
        "f237~10~SECURITY_CODE~TRANSFER_PREMIUM_RATIO,"
        "f239~10~SECURITY_CODE~RESALE_TRIG_PRICE,"
        "f240~10~SECURITY_CODE~REDEEM_TRIG_PRICE,"
        "f23~01~CONVERT_STOCK_CODE~PBV_RATIO"
    )
    common = {
        "reportName": "RPT_BOND_CB_LIST",
        "columns": "ALL",
        "quoteColumns": quote_columns,
        "quoteType": "0",
        "source": "WEB",
        "client": "WEB",
        "pageSize": page_size,
    }
    rows: list[dict[str, Any]] = []
    page = 1
    while True:
        params = {**common, "pageNumber": page}
        for attempt in range(1, 4):
            try:
                response = requests.get(EASTMONEY_CB_LIST_URL, params=params, timeout=(5, 20))
                response.raise_for_status()
                payload = response.json()
                result = payload.get("result") or {}
                data = result.get("data") or []
                pages = int(result.get("pages") or 1)
                rows.extend(data)
                break
            except Exception:
                if attempt == 3:
                    raise
        if page >= pages:
            break
        page += 1

    frame = pd.DataFrame(rows)
    if frame.empty or "SECURITY_CODE" not in frame.columns:
        raise RuntimeError("Eastmoney contract table is empty or missing SECURITY_CODE")
    frame["SECURITY_CODE"] = frame["SECURITY_CODE"].astype(str).str.zfill(6)
    return frame


def fetch_ths_contract_maturity() -> pd.DataFrame:
    frame = ak.bond_zh_cov_info_ths().copy()
    required = {"债券代码", "到期时间"}
    if frame.empty or not required.issubset(frame.columns):
        raise RuntimeError("THS maturity table is empty or incomplete")
    frame["债券代码"] = frame["债券代码"].astype(str).str.zfill(6)
    return frame


def load_overrides(path: Path | None) -> dict[tuple[str, str], dict[str, Any]]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for item in payload.get("overrides", []):
        code = str(item["bond_code"]).zfill(6)
        field = str(item["field"])
        mode = str(item.get("mode") or "exact")
        if "reason" not in item:
            raise ValueError(f"override {code}/{field} missing reason")
        if mode == "exact":
            for required in ("value", "source"):
                if required not in item:
                    raise ValueError(f"override {code}/{field} missing {required}")
        elif mode == "range":
            for required in ("min_value", "max_value", "sources"):
                if required not in item:
                    raise ValueError(f"override {code}/{field} missing {required}")
            if float(item["min_value"]) > float(item["max_value"]):
                raise ValueError(f"override {code}/{field} has invalid range")
        else:
            raise ValueError(f"override {code}/{field} has unsupported mode {mode}")
        normalized = dict(item)
        normalized["mode"] = mode
        result[(code, field)] = normalized
    return result


def _coupon_schedule(
    *,
    value_date: pd.Timestamp,
    pay_interest_day: str,
    rates: list[float],
    par_value: float,
    cutoff: pd.Timestamp,
    final_interest_in_redemption: bool,
) -> list[dict[str, Any]]:
    month, day = (int(x) for x in pay_interest_day.split("-"))
    result: list[dict[str, Any]] = []
    for year_no, rate in enumerate(rates, 1):
        year = value_date.year + year_no
        payment_day = min(day, calendar.monthrange(year, month)[1])
        payment_date = pd.Timestamp(date(year, month, payment_day))
        is_final = year_no == len(rates)
        if payment_date <= cutoff:
            continue
        if is_final and final_interest_in_redemption:
            continue
        result.append({
            "year_no": year_no,
            "payment_date": str(payment_date.date()),
            "rate": rate,
            "cash": par_value * rate / 100.0,
        })
    return result


def build_maturity_contract_facts(
    market_input: dict[str, Any],
    eastmoney: pd.DataFrame,
    ths: pd.DataFrame,
    *,
    overrides: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    overrides = overrides or {}
    codes = {str(row["bond_code"]).zfill(6) for row in market_input["rows"]}
    em = eastmoney[eastmoney["SECURITY_CODE"].isin(codes)].drop_duplicates("SECURITY_CODE").copy()
    th = ths[ths["债券代码"].isin(codes)].drop_duplicates("债券代码").copy()

    if set(em["SECURITY_CODE"]) != codes:
        missing = sorted(codes - set(em["SECURITY_CODE"]))
        raise RuntimeError(f"contract source missing codes: {missing[:20]}")
    if set(th["债券代码"]) != codes:
        missing = sorted(codes - set(th["债券代码"]))
        raise RuntimeError(f"maturity cross-check missing codes: {missing[:20]}")

    em_rows = em.set_index("SECURITY_CODE").to_dict("index")
    th_rows = th.set_index("债券代码").to_dict("index")
    cutoff = pd.Timestamp(market_input["market_cutoff"])
    rows = []

    for market_row in market_input["rows"]:
        code = str(market_row["bond_code"]).zfill(6)
        source = em_rows[code]
        rates = parse_coupon_rates(source.get("INTEREST_RATE_EXPLAIN"))
        par = pd.to_numeric(source.get("PAR_VALUE"), errors="coerce")
        value_date = pd.to_datetime(source.get("VALUE_DATE"), errors="coerce")
        maturity_date = pd.to_datetime(th_rows[code].get("到期时间"), errors="coerce")
        pay_day = _text(source.get("PAY_INTEREST_DAY"))
        redemption, redemption_source = parse_maturity_redemption(source.get("REDEEM_CLAUSE"))
        includes_final = redemption_includes_final_interest(source.get("REDEEM_CLAUSE"))
        redemption_min = redemption
        redemption_max = redemption
        override = overrides.get((code, "maturity_redemption_cash"))
        audit_notes: list[str] = []
        status = "READY"

        if override:
            mode = override.get("mode", "exact")
            includes_final = bool(override.get("includes_final_interest", True))
            if mode == "exact":
                redemption = float(override["value"])
                redemption_min = redemption
                redemption_max = redemption
                redemption_source = "AUTHORITATIVE_OVERRIDE"
                status = "READY_WITH_OVERRIDE"
                audit_notes.append(
                    f"maturity_redemption_cash exact override: {override['source']}"
                )
            elif mode == "range":
                redemption = None
                redemption_min = float(override["min_value"])
                redemption_max = float(override["max_value"])
                redemption_source = "AUTHORITATIVE_RANGE"
                status = "READY_RANGE"
                audit_notes.append(
                    "maturity_redemption_cash bounded conflict: "
                    + "; ".join(str(x) for x in override.get("sources", []))
                )

        required_ok = (
            rates
            and pd.notna(par)
            and pd.notna(value_date)
            and pd.notna(maturity_date)
            and bool(pay_day)
            and redemption_min is not None
            and redemption_max is not None
        )
        if not required_ok:
            rows.append({
                "bond_code": code,
                "bond_name": market_row["bond_name"],
                "status": "INSUFFICIENT_DATA",
                "missing": [
                    name for name, ok in (
                        ("coupon_rates", bool(rates)),
                        ("par_value", pd.notna(par)),
                        ("value_date", pd.notna(value_date)),
                        ("contract_maturity_date", pd.notna(maturity_date)),
                        ("pay_interest_day", bool(pay_day)),
                        ("maturity_redemption_cash", redemption_min is not None and redemption_max is not None),
                    ) if not ok
                ],
                "audit_notes": audit_notes,
            })
            continue

        coupons = _coupon_schedule(
            value_date=value_date,
            pay_interest_day=pay_day,
            rates=rates,
            par_value=float(par),
            cutoff=cutoff,
            final_interest_in_redemption=includes_final,
        )
        intermediate_cash = sum(float(item["cash"]) for item in coupons)
        c_min = float(redemption_min) + intermediate_cash
        c_max = float(redemption_max) + intermediate_cash
        exact_c = c_min if abs(c_max - c_min) < 1e-12 else None
        rows.append({
            "bond_code": code,
            "bond_name": market_row["bond_name"],
            "status": status,
            "contract_maturity_date": str(maturity_date.date()),
            "source_expire_date": _text(source.get("EXPIRE_DATE")),
            "value_date": str(value_date.date()),
            "pay_interest_day": pay_day,
            "par_value": float(par),
            "coupon_rates": rates,
            "remaining_intermediate_coupons": coupons,
            "maturity_redemption_cash": (
                float(redemption_min) if abs(float(redemption_max) - float(redemption_min)) < 1e-12 else None
            ),
            "maturity_redemption_cash_min": float(redemption_min),
            "maturity_redemption_cash_max": float(redemption_max),
            "maturity_redemption_source": redemption_source,
            "redemption_includes_final_interest": includes_final,
            "remaining_contract_cash_C": exact_c,
            "remaining_contract_cash_C_min": c_min,
            "remaining_contract_cash_C_max": c_max,
            "audit_notes": audit_notes,
        })

    return {
        "contract_facts_version": CONTRACT_FACTS_VERSION,
        "market_run_id": market_input["run_id"],
        "market_snapshot_id": market_input["market_snapshot_id"],
        "market_cutoff": market_input["market_cutoff"],
        "created_at": _now(),
        "rows": rows,
        "audit": {
            "universe": len(market_input["rows"]),
            "ready": sum(row["status"] in {"READY", "READY_WITH_OVERRIDE", "READY_RANGE"} for row in rows),
            "ready_with_override": sum(row["status"] == "READY_WITH_OVERRIDE" for row in rows),
            "ready_range": sum(row["status"] == "READY_RANGE" for row in rows),
            "insufficient_data": sum(row["status"] == "INSUFFICIENT_DATA" for row in rows),
        },
    }


def run_maturity_contract_facts(
    market_input_path: Path,
    data_root: Path,
    deployment: dict[str, Any],
    *,
    overrides_path: Path | None = None,
) -> dict[str, Any]:
    market_input = json.loads(market_input_path.read_text(encoding="utf-8"))
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_contract_" + uuid.uuid4().hex[:8]
    run_dir = data_root / "runs" / run_id
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=False)

    eastmoney = fetch_eastmoney_contract_table()
    ths = fetch_ths_contract_maturity()
    eastmoney.to_csv(raw_dir / "eastmoney_cb_contracts.csv", index=False)
    ths.to_csv(raw_dir / "ths_cb_maturity.csv", index=False)

    overrides = load_overrides(overrides_path)
    result = build_maturity_contract_facts(market_input, eastmoney, ths, overrides=overrides)
    result["run_id"] = run_id
    result["application_commit_sha"] = deployment.get("application_commit_sha")
    result["knowledge_commit_sha"] = deployment.get("knowledge_commit_sha")
    result["overrides_path"] = str(overrides_path) if overrides_path else None
    _write_json(run_dir / "maturity_contract_facts.json", result)
    _write_json(run_dir / "run_metadata.json", {
        "run_id": run_id,
        "unit": "MATURITY_CONTRACT_FACTS",
        "status": "PASS" if result["audit"]["insufficient_data"] == 0 else "INSUFFICIENT_DATA",
        "started_at": result["created_at"],
        "completed_at": _now(),
        "market_run_id": result["market_run_id"],
        "market_snapshot_id": result["market_snapshot_id"],
        "contract_facts_version": CONTRACT_FACTS_VERSION,
        "application_commit_sha": deployment.get("application_commit_sha"),
        "knowledge_commit_sha": deployment.get("knowledge_commit_sha"),
        "result_path": str(run_dir / "maturity_contract_facts.json"),
    })
    return result
