"""Opportunity View Contract V1.

Pure presentation adapter from lossless Opportunity Record to a Chinese-first
user view. It must not change Economic KEEP or Path research conclusions.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

VIEW_CONTRACT_VERSION = "opportunity-view-v2"

PATH_LABELS = {
    "MATURITY_CASH": "到期现金",
    "PUT": "回售",
    "DOWNWARD_REVISION": "下修",
}

STATE_LABELS = {
    "COMPLETED": "已完成深研",
    "HOLD_WAITING_EVIDENCE": "等待补充证据",
    "NOT_TRIGGERED": "机会已发现，尚未到深研节点",
    "PENDING": "等待研究",
    "IN_PROGRESS": "研究中",
}

EVENT_STATE_LABELS = {
    "T_LE_1M": "距到期 1 个月内",
    "T_LE_3M": "距到期 3 个月内",
    "T_LE_6M": "距到期 6 个月内",
    "T_LE_12M": "距到期 1 年内",
    "T_GT_12M": "距到期 1 年以上",
    "BEFORE_PUT_WINDOW": "尚未进入普通回售期",
    "满足条件": "已满足下修触发条件",
    "临近触发": "接近下修触发条件",
    "未进入": "尚未进入下修触发阶段",
}

FIELD_OVERRIDES = {
    "payment_stability": "支付稳定性",
    "payment_stability_rationale": "支付稳定性理由",
    "economic_vs_stability_separation": "经济空间与兑现风险的区分",
    "hard_credit_interpretation": "硬信用事件解读",
    "time_axis": "时间轴判断",
    "rating_semantic": "评级报告解读",
    "revision_and_put_note": "下修与回售影响",
    "right_formation_degree": "回售权形成程度",
    "core_judgment": "核心判断",
    "downward_revision_escape_k": "下修避免回售所需转股价",
    "current_reality_event_state": "当前真实事件状态",
    "issuer_primary_objective": "发行人当前首要目标",
    "governance_bottleneck": "治理瓶颈",
    "final_success_assessment": "最终成功成立程度",
    "realistic_revision_depth": "现实下修深度",
    "economic_translation": "经济结果翻译",
    "put_interaction": "与回售路径的相互作用",
    "uncertainty_statement": "不确定性说明",
}
TOKEN_LABELS = {
    "current": "当前", "price": "价格", "bond": "转债", "stock": "正股",
    "conversion": "转股", "value": "价值", "remaining": "剩余", "size": "规模",
    "maturity": "到期", "date": "日期", "cash": "现金", "money": "货币",
    "funds": "资金", "restricted": "受限", "unrestricted": "可自由动用",
    "parent": "母公司", "company": "公司", "group": "集团", "entity": "主体",
    "total": "总", "assets": "资产", "liabilities": "负债", "debt": "债务",
    "ratio": "比率", "operating": "经营", "financing": "融资", "flow": "现金流",
    "net": "净", "profit": "利润", "rating": "评级", "outlook": "展望",
    "audit": "审计", "opinion": "意见", "credit": "信用", "external": "外部",
    "internal": "内部", "generation": "生成", "closure": "补洞", "gap": "缺口",
    "available": "可用", "liquidity": "流动性", "assessment": "判断",
    "judgment": "判断", "reason": "原因", "note": "说明", "status": "状态",
    "state": "状态", "rule": "规则", "trigger": "触发", "line": "线",
    "window": "窗口", "start": "开始", "expected": "预计", "ordinary": "普通",
    "put": "回售", "revision": "下修", "clause": "条款", "count": "计数",
    "reset": "重置", "reference": "参考", "spread": "价差", "economic": "经济",
    "model": "模型", "zone": "区域", "floor": "底价", "hard": "硬约束",
    "nav": "每股净资产", "applicable": "是否适用", "decision": "决策",
    "sensitive": "敏感", "ceiling": "上限", "basis": "依据", "neutral": "中性",
    "discovery": "发现", "realistic": "现实", "behavior": "行为",
    "history": "历史", "issuer": "发行人", "governance": "治理",
    "success": "成功", "confidence": "可信度", "evidence": "证据",
    "source": "来源", "report": "报告", "notice": "公告", "event": "事件",
    "scope": "口径", "consolidated": "合并", "shareholder": "股东",
    "support": "支持", "overdue": "逾期", "default": "违约",
    "effective": "有效", "maximum": "最大", "minimum": "最小",
    "months": "月数", "month": "月", "days": "天", "day": "日",
    "per": "每", "unit": "单位", "annual": "年度", "year": "年",
    "interest": "利息", "redemption": "赎回", "responsibility": "责任",
}
SOURCE_TYPE_LABELS = {
    "structured_contract_fact": "结构化合同条款",
    "structured_path_fact": "结构化路径事实",
    "bulk_financial_statement": "财务报表",
    "structured_bond_rating": "结构化评级",
    "eastmoney_announcement": "公司公告",
    "formal_trustee_report_republication": "受托管理报告",
    "company_half_year_report": "公司半年度报告",
    "rating_report_republication": "评级报告",
}

CONFIDENCE_LABELS = {
    "HIGH": "高", "MEDIUM": "中", "LOW": "低",
    "high": "高", "medium": "中", "low": "低",
}

LOGIC_STATE_LABELS = {
    "KNOWN": "事实已确认",
    "DERIVED": "由事实推导",
    "MIXED": "证据有正有反",
    "NOT_MATERIAL": "对当前结论无需继续深挖",
    "UNKNOWN_A": "未来天然不确定",
    "UNKNOWN_B": "当前仍待查证",
}

FACT_KEY_LABELS = {
    "current_bond_price": "当前转债价格",
    "current_price": "当前转债价格",
    "current_price_P": "当前转债价格",
    "current_stock_price": "正股价格",
    "current_conversion_price": "当前转股价",
    "current_conversion_value": "当前转股价值",
    "current_cv": "当前转股价值",
    "remaining_months": "剩余期限",
    "remaining_size": "剩余规模",
    "maturity_date": "到期日",
    "contract_maturity_date": "到期日",
    "revision_event_state": "当前下修状态",
    "current_event_state": "当前事件状态",
    "revision_count": "当前触发计数",
    "minimum_days_needed": "距满足条件尚需交易日",
    "reset_start": "本轮计数起点",
    "ordinary_put_clause_exists": "普通回售条款",
    "put_mechanism_still_available": "普通回售机制",
    "put_window_start": "普通回售窗口起点",
    "current_put_trigger_count": "当前回售触发计数",
    "money_funds_yi": "合并口径货币资金",
    "total_liabilities_yi": "总负债",
    "asset_liability_ratio_pct": "资产负债率",
    "operating_cash_flow_yi": "经营活动现金流净额",
    "financing_cash_flow_yi": "筹资活动现金流净额",
    "net_cash_flow_yi": "现金净增加额",
    "remaining_size_yi": "剩余转债规模",
    "put_reference_cash_per_100": "每100元面值回售参考现金",
    "cash_pressure_yi": "集中回售现金压力",
    "neutral_reference": "模型中性参考价",
    "reference_minus_current_price": "相对当前价空间",
    "path_availability.status": "正常到期路径状态",
    "normal_maturity_path_available": "正常到期路径",
    "reason": "判断原因",
    "redeem_status": "强赎状态",
    "redeem_counter": "强赎计数",
    "last_trade_date": "最后交易日",
    "scope": "财务口径",
    "financial_scope": "财务口径",
    "maturity_redemption_source": "到期赎回条款来源",
    "value_date": "条款基准日期",
    "source_conversion_price": "当前转股价基准",
    "historical_revision": "历史下修记录",
    "cross_path_maturity.contract_fact": "同券到期现金事实",
    "cross_path_revision.contract_fact": "同券下修事实",
    "remaining_contract_cash_C": "剩余合同现金",
    "cash_pressure_yi": "集中现金压力",
    "put_reference": "回售参考现金",
    "put_window_start": "普通回售窗口起点",
    "earliest_right_formation": "最早可能形成回售权",
}

FACT_VALUE_LABELS = {
    "READY": "当前可用",
    "NO_ACTIVE_EARLY_REDEMPTION_SIGNAL": "未发现正在生效的提前赎回信号",
    "BEFORE_PUT_WINDOW": "尚未进入普通回售适用期",
    "FUTURE_ORDINARY_PUT_WINDOW_REMAINS": "未来仍保留普通回售窗口",
    "CONSOLIDATED": "合并口径",
    "STRUCTURED_CLAUSE": "结构化条款已核验",
    "True": "存在 / 可用",
    "False": "不存在 / 不可用",
    "true": "存在 / 可用",
    "false": "不存在 / 不可用",
    "None": "暂无",
    "null": "暂无",
    "NaT": "暂无有效日期",
    "--": "暂无计数",
}


def humanize_key(key: str) -> str:
    if key in FIELD_OVERRIDES:
        return FIELD_OVERRIDES[key]
    if key in FACT_KEY_LABELS:
        return FACT_KEY_LABELS[key]
    if key in {"fact_spine", "judgments"}:
        return {"fact_spine": "关键事实链", "judgments": "研究判断"}[key]
    parts = str(key).replace("-", "_").split("_")
    translated = [TOKEN_LABELS.get(part, "") for part in parts]
    if translated and all(translated):
        return "".join(translated)
    return str(key)


def _format_fact_value(key: str, value: str) -> str:
    raw = str(value or "").strip().rstrip("。")
    if raw in FACT_VALUE_LABELS:
        return FACT_VALUE_LABELS[raw]
    if not raw:
        return "暂无明确状态"
    if key == "redeem_counter" and "|" in raw:
        left, right = [x.strip() for x in raw.split("|", 1)]
        return f"{left}（观察窗口 {right} 日）"
    numeric = re.fullmatch(r"(-?\d+(?:\.\d+)?)(?:\s*(元|亿元|天|个月|%))?", raw)
    if numeric:
        number = float(numeric.group(1))
        unit = numeric.group(2) or ""
        if key in {
            "remaining_size", "remaining_size_yi", "money_funds_yi",
            "total_liabilities_yi", "operating_cash_flow_yi",
            "financing_cash_flow_yi", "net_cash_flow_yi",
            "cash_pressure_yi",
        }:
            shown = f"{number:.2f}"
        elif abs(number) >= 100:
            shown = f"{number:.2f}".rstrip("0").rstrip(".")
        elif abs(number) >= 10:
            shown = f"{number:.2f}".rstrip("0").rstrip(".")
        else:
            shown = f"{number:.3f}".rstrip("0").rstrip(".")
        if not unit:
            if key == "remaining_months":
                unit = "个月"
            elif key in {"remaining_size", "remaining_size_yi", "money_funds_yi",
                         "total_liabilities_yi", "operating_cash_flow_yi",
                         "financing_cash_flow_yi", "net_cash_flow_yi",
                         "cash_pressure_yi"}:
                unit = "亿元"
            elif key == "minimum_days_needed":
                unit = "天"
            elif key == "asset_liability_ratio_pct":
                unit = "%"
            elif key in {
                "current_bond_price", "current_price", "current_price_P",
                "current_stock_price", "current_conversion_price",
                "current_conversion_value", "current_cv", "neutral_reference",
                "reference_minus_current_price",
            }:
                unit = "元"
        if unit == "%":
            return f"{shown}%"
        return f"{shown}{(' ' + unit) if unit else ''}"
    return FACT_VALUE_LABELS.get(raw, raw)


def _humanize_key_value_clause(clause: str) -> str:
    text = clause.strip().strip("。")
    if "=" not in text:
        if text == "redeem_status 为空":
            return "强赎状态：当前无强赎信号"
        return text
    key, value = text.split("=", 1)
    key = key.strip()
    value = value.strip()
    return f"{humanize_key(key)}：{_format_fact_value(key, value)}"


def _humanize_prose(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    replacements = (
        ("Engineering Anchor", "当前状态锚"),
        ("Evidence Pack", "证据包"),
        ("Economic KEEP", "存在正向经济空间"),
        ("现实 CV → 正式估值网格债价 → 经济空间", "不同转股价值情景下的债价与经济空间"),
        (" Path ", "路径"),
        (" Path", "路径"),
        ("Path ", "路径"),
        ("path availability", "到期路径可用性"),
    )
    for old, new in replacements:
        text = text.replace(old, new)
    text = text.replace("T_GT_12M", "距到期 12 个月以上")
    text = text.replace("BEFORE_PUT_WINDOW", "尚未进入普通回售适用期")
    text = text.replace("CONSOLIDATED", "合并口径")
    text = text.replace("普通回售机制可用=True", "普通回售机制：当前可用")
    text = text.replace("普通回售机制可用=false", "普通回售机制：当前不可用")
    text = re.sub(r"普通回售窗口起点\s*=\s*", "普通回售窗口起点：", text)
    text = re.sub(r"合同到期日\s*=\s*", "合同到期日：", text)
    text = re.sub(r"最近一期经审计每股净资产\s*=\s*", "最近一期经审计每股净资产为 ", text)
    text = re.sub(r"股票面值\s*=\s*", "股票面值为 ", text)
    text = re.sub(r"当前\s*K\s*=\s*", "当前转股价 K 为 ", text)
    text = re.sub(r"触发线\s*=\s*", "触发线为 ", text)
    text = re.sub(r"回售边界\s*=\s*", "回售边界为 ", text)
    text = re.sub(
        r"cash_pressure_yi\s*[=：]\s*([+-]?\d+(?:\.\d+)?)",
        r"集中现金压力：\1 亿元",
        text,
    )
    text = re.sub(
        r"remaining_contract_cash_C\s*[=：]\s*([+-]?\d+(?:\.\d+)?)",
        r"剩余合同现金：\1 元",
        text,
    )
    text = text.replace(
        "证据包 未预装当前触发计数（missing_or_deferred 中列明 current_put_trigger_count_if_not_structurally_available）",
        "当前回售触发计数尚未由结构化数据预装；进入回售适用期后需要继续更新计数",
    )

    def _round_long_decimal(match: re.Match[str]) -> str:
        value = float(match.group(0))
        if abs(value) >= 10:
            return f"{value:.2f}"
        return f"{value:.3f}".rstrip("0").rstrip(".")

    text = re.sub(r"(?<!\d)-?\d+\.\d{5,}(?!\d)", _round_long_decimal, text)
    return text


def _humanize_machine_fact(fact: Any) -> str:
    text = _humanize_prose(fact)
    if not text:
        return ""
    text = text.replace("Engineering Anchor：", "当前状态锚：")
    text = text.replace("Engineering Anchor:", "当前状态锚：")

    prefix = ""
    body = text
    financial_match = re.match(
        r"^financial_first_layer（合并口径，(\d{4}-\d{2}-\d{2})）：(.+)$",
        body,
    )
    if financial_match:
        prefix = f"最新合并口径财务数据（{financial_match.group(1)}）："
        body = financial_match.group(2)
    for marker, label in (
        ("market_state：", "当前市场状态："),
        ("financial_first_layer（合并口径，", "最新合并口径财务数据（"),
        ("economic_judgment：", "当前经济判断："),
        ("existing_path_facts.contract_fact：", "当前合同事实："),
        ("cross_path_revision.contract_fact：", "同券下修状态："),
        ("cross_path_maturity.contract_fact：", "同券到期现金事实："),
        ("put_exercise_pressure_scenarios：", "回售压力场景："),
        ("PRELOADED:PUT_PRESSURE_SCENARIOS：", "回售压力场景："),
    ):
        if body.startswith(marker):
            prefix = label
            body = body[len(marker):]
            break

    machineish = (
        "=" in body
        and (
            re.match(r"^[A-Za-z_][A-Za-z0-9_.]*\s*=", body)
            or "，" in body
            or "," in body
        )
    )
    if machineish:
        parts = re.split(r"[，,](?=\s*[A-Za-z_][A-Za-z0-9_.]*\s*=)", body.rstrip("。"))
        human = "；".join(_humanize_key_value_clause(part) for part in parts)
        return prefix + human + ("。" if text.endswith("。") else "")

    if "redeem_status 为空" in body or re.search(r"[A-Za-z_][A-Za-z0-9_.]*=", body):
        parts = re.split(r"[，,](?=\s*(?:[A-Za-z_][A-Za-z0-9_.]*\s*=|redeem_status\s+为空))", body.rstrip("。"))
        human = "；".join(_humanize_key_value_clause(part) for part in parts)
        return prefix + human + ("。" if text.endswith("。") else "")

    return prefix + body


def translate_object(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            humanize_key(str(key)): translate_object(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [translate_object(item) for item in value]
    return value


def _pct(spread: Any, price: Any) -> float | None:
    try:
        return round(float(spread) / float(price) * 100.0, 2)
    except Exception:
        return None
def _metrics(path_id: str, economic: dict[str, Any]) -> list[dict[str, Any]]:
    if path_id == "MATURITY_CASH":
        spread = economic.get("spread_C_minus_P")
        price = economic.get("current_price_P")
        return [
            {"label": "当前价格", "value": price, "unit": "元"},
            {"label": "合同现金", "value": economic.get("remaining_contract_cash_C"), "unit": "元"},
            {"label": "绝对价差", "value": spread, "unit": "元"},
            {"label": "相对当前价空间", "value": _pct(spread, price), "unit": "%"},
        ]
    if path_id == "PUT":
        spread = economic.get("spread")
        price = economic.get("current_price_P")
        return [
            {"label": "当前价格", "value": price, "unit": "元"},
            {"label": "回售参考现金", "value": economic.get("put_reference"), "unit": "元"},
            {"label": "绝对价差", "value": spread, "unit": "元"},
            {"label": "相对当前价空间", "value": _pct(spread, price), "unit": "%"},
        ]
    if path_id == "DOWNWARD_REVISION":
        return [
            {"label": "当前价格", "value": economic.get("current_price"), "unit": "元"},
            {"label": "当前转股价值", "value": economic.get("current_cv"), "unit": "元"},
            {"label": "模型参考价值", "value": economic.get("discovery_reference"), "unit": "元"},
            {"label": "模型价差", "value": economic.get("discovery_spread"), "unit": "元"},
        ]
    return []


def _event_state_text(path_id: str, event_state: Any) -> str:
    raw = str(event_state or "").strip()
    if not raw:
        return "—"
    if raw in EVENT_STATE_LABELS:
        return EVENT_STATE_LABELS[raw]
    return _humanize_prose(raw)


def _payment_stability_text(path: dict[str, Any]) -> str:
    result = path.get("path_result") or {}
    judgments = result.get("judgments") or {}
    candidates = [
        judgments.get("payment_stability"),
        judgments.get("cash_payment_stability"),
    ]
    for value in candidates:
        if isinstance(value, dict):
            text = value.get("level") or value.get("value") or value.get("reasoning")
        else:
            text = value
        if isinstance(text, str) and text.strip():
            return text.strip()
    return ""


def _path_is_floor(path: dict[str, Any]) -> bool:
    if path.get("research_state") != "COMPLETED":
        return False
    path_id = str(path.get("path_id") or "")
    stability = _payment_stability_text(path)
    if "大概率稳定" not in stability:
        return False
    if path_id == "MATURITY_CASH":
        return True
    if path_id == "PUT":
        state = str(path.get("current_event_state") or "")
        return state not in {"", "BEFORE_PUT_WINDOW", "未进入"}
    return False


def _bond_floor_class(record: dict[str, Any]) -> tuple[str, str | None]:
    for path in record.get("paths", []):
        if _path_is_floor(path):
            return (
                "保底型",
                f"{PATH_LABELS.get(str(path.get('path_id') or ''), path.get('path_id'))}"
                "路径已完成研究，支付稳定性为大概率稳定。",
            )
    return "非保底型", None


def _parse_date(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value)[:10]
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _solve_ytm(
    *,
    current_price: Any,
    market_cutoff: Any,
    contract_fact: dict[str, Any] | None,
) -> float | None:
    if not isinstance(contract_fact, dict):
        return None
    try:
        price = float(current_price)
    except Exception:
        return None
    cutoff = _parse_date(market_cutoff)
    maturity = _parse_date(contract_fact.get("contract_maturity_date"))
    if not cutoff or not maturity or maturity <= cutoff or price <= 0:
        return None

    cashflows: list[tuple[float, float]] = []
    for item in contract_fact.get("remaining_intermediate_coupons") or []:
        payment = _parse_date(item.get("payment_date"))
        try:
            cash = float(item.get("cash"))
        except Exception:
            continue
        if payment and payment > cutoff and cash > 0:
            years = (payment - cutoff).days / 365.2425
            cashflows.append((years, cash))

    try:
        final_cash = float(contract_fact.get("maturity_redemption_cash"))
    except Exception:
        final_cash = 0.0
    if final_cash > 0:
        years = (maturity - cutoff).days / 365.2425
        cashflows.append((years, final_cash))
    if not cashflows:
        return None

    def npv(rate: float) -> float:
        return sum(cash / ((1.0 + rate) ** years) for years, cash in cashflows)

    low, high = -0.99, 10.0
    if npv(low) < price or npv(high) > price:
        return None
    for _ in range(120):
        mid = (low + high) / 2.0
        if npv(mid) > price:
            low = mid
        else:
            high = mid
    return round(((low + high) / 2.0) * 100.0, 2)


def _path_time_and_ytm(
    path: dict[str, Any],
    *,
    market_cutoff: Any,
    maturity_contract_fact: dict[str, Any] | None,
) -> tuple[str | None, float | None]:
    path_id = str(path.get("path_id") or "")
    economic = path.get("economic_judgment") or {}
    if path_id == "MATURITY_CASH":
        date_text = (
            (maturity_contract_fact or {}).get("contract_maturity_date")
            if isinstance(maturity_contract_fact, dict)
            else None
        )
        ytm = _solve_ytm(
            current_price=economic.get("current_price_P"),
            market_cutoff=market_cutoff,
            contract_fact=maturity_contract_fact,
        )
        return (str(date_text)[:10] if date_text else None), ytm

    result = path.get("path_result") or {}
    fact_spine = result.get("fact_spine") or {}
    if path_id == "PUT":
        trigger = fact_spine.get("trigger_state") or {}
        legal = fact_spine.get("legal_time") or {}
        earliest = trigger.get("earliest_right_formation")
        if isinstance(earliest, str) and earliest.strip():
            return earliest.strip(), None
        window = legal.get("put_window_start")
        if window:
            return f"最早自 {str(window)[:10]} 起形成", None
        maturity = _parse_date(
            (maturity_contract_fact or {}).get("contract_maturity_date")
            if isinstance(maturity_contract_fact, dict)
            else None
        )
        if maturity:
            try:
                window = maturity.replace(year=maturity.year - 2)
                return f"最早自 {window.date().isoformat()} 起形成", None
            except Exception:
                pass
    return None, None


def _status_explanation(path_id: str, state: str, event_state: Any) -> str:
    if state == "COMPLETED":
        return "该路径已完成当前阶段深研，下面展示完整研究结论。"
    if state == "HOLD_WAITING_EVIDENCE":
        return "经济机会继续保留，但当前仍有关键证据未闭合；不会因为缺证据自动删除机会。"
    if state == "PENDING":
        return "已进入工程研究触发节点，正在等待本轮路径研究。"
    if state == "IN_PROGRESS":
        return "路径研究正在进行中。"
    if state == "NOT_TRIGGERED" and path_id == "DOWNWARD_REVISION":
        return (
            "下修的正向经济空间已经被识别，但当前事件状态为"
            f"“{event_state or '未进入'}”；尚未进入需要投入完整深研的事件节点。"
        )
    return "经济机会已经识别，当前尚未进入深研触发节点。"


def _evidence_view(items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    output = []
    for item in items or []:
        output.append({
            "标题": item.get("title") or item.get("source_title") or item.get("claim") or item.get("evidence_id"),
            "日期": item.get("source_date"),
            "来源类型": SOURCE_TYPE_LABELS.get(str(item.get("source_type") or ""), item.get("source_type")),
            "支持内容": item.get("supports") or item.get("claim"),
            "可信度": CONFIDENCE_LABELS.get(str(item.get("confidence") or ""), item.get("confidence")),
            "定位": item.get("locator") or item.get("source_url"),
        })
    return output
def _summary_view(summary: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(summary, dict):
        return None
    risks = summary.get("main_risks") or []
    return {
        "核心结论": _humanize_prose(summary.get("core_conclusion")),
        "为什么": [
            _humanize_prose(item) for item in (summary.get("why") or [])
        ],
        "经济结果": _humanize_prose(summary.get("economic_result")),
        "首要风险提醒": _humanize_prose(risks[0]) if risks else None,
        "下一步关注": _humanize_prose(summary.get("next_focus")),
    }


def _structured_fact_table(facts: list[Any]) -> dict[str, Any] | None:
    timeline_rows = []
    non_timeline = []
    for fact in facts or []:
        text = str(fact or "").strip()
        match = re.match(r"^(\d{4}-\d{2}-\d{2})\s+(.+)$", text)
        if match:
            timeline_rows.append([match.group(1), _humanize_prose(match.group(2))])
        else:
            non_timeline.append(text)
    if len(timeline_rows) >= 3:
        return {
            "type": "timeline",
            "columns": ["日期", "事件"],
            "rows": timeline_rows,
            "remaining_facts": [
                _humanize_machine_fact(x) for x in non_timeline if x
            ],
        }

    scenario_rows = []
    non_scenario = []
    pattern = re.compile(
        r"^CV_(\d+(?:\.\d+)?)：(?:neutral_reference|discovery_reference)=([+-]?\d+(?:\.\d+)?)"
        r"，reference_minus_current_price=([+-]?\d+(?:\.\d+)?)$"
    )
    for fact in facts or []:
        text = str(fact or "").strip()
        match = pattern.match(text)
        if match:
            scenario_rows.append([
                f"{match.group(1)} 元",
                f"{float(match.group(2)):.2f} 元",
                f"{float(match.group(3)):+.2f} 元",
            ])
        else:
            non_scenario.append(text)
    if len(scenario_rows) >= 3:
        return {
            "type": "scenario",
            "columns": ["转股价值情景", "藏宝图参考债价", "相对当前价空间"],
            "rows": scenario_rows,
            "remaining_facts": [
                _humanize_machine_fact(x) for x in non_scenario if x
            ],
        }
    return None


def _logic_chain_view(items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    output = []
    for idx, item in enumerate(items or [], start=1):
        if not isinstance(item, dict):
            continue
        raw_facts = item.get("facts") or []
        fact_table = _structured_fact_table(raw_facts)
        display_facts = (
            fact_table.get("remaining_facts", [])
            if fact_table
            else [
                _humanize_machine_fact(fact)
                for fact in raw_facts
                if str(fact or "").strip()
            ]
        )
        output.append({
            "序号": idx,
            "节点编号": item.get("step_id"),
            "标题": _humanize_prose(item.get("title")),
            "问题": _humanize_prose(item.get("question")),
            "状态": LOGIC_STATE_LABELS.get(
                str(item.get("state") or ""), item.get("state")
            ),
            "先给答案": _humanize_prose(item.get("answer")),
            "关键事实": display_facts,
            "事实表格": fact_table,
            "为什么": _humanize_prose(item.get("reasoning")),
            "本步结论": _humanize_prose(item.get("conclusion")),
            "证据编号": item.get("evidence_ids") or [],
        })
    return output


def build_path_view(
    path: dict[str, Any],
    *,
    market_cutoff: Any = None,
    maturity_contract_fact: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path_id = str(path.get("path_id") or "")
    state = str(path.get("research_state") or "")
    result = path.get("path_result") or {}
    judgments = result.get("judgments") or {}
    opportunity_time, ytm_pct = _path_time_and_ytm(
        path,
        market_cutoff=market_cutoff,
        maturity_contract_fact=maturity_contract_fact,
    )

    return {
        "path_id": path_id,
        "path_name": PATH_LABELS.get(path_id, path_id),
        "opportunity_status": "存在正向经济空间",
        "research_state": state,
        "research_state_text": STATE_LABELS.get(state, state),
        "current_event_state": path.get("current_event_state"),
        "current_event_state_text": _event_state_text(
            path_id, path.get("current_event_state")
        ),
        "opportunity_time": opportunity_time,
        "ytm_pct": ytm_pct,
        "status_explanation": _status_explanation(
            path_id, state, path.get("current_event_state")
        ),
        "metrics": _metrics(path_id, path.get("economic_judgment") or {}),
        "research": {
            "总判断": _summary_view(result.get("summary")),
            "研究逻辑链": _logic_chain_view(result.get("logic_chain")),
            "研究判断": translate_object(judgments),
            "关键事实链": translate_object(result.get("fact_spine") or {}),
            "风险与未决事项": {
                "当前风险": result.get("key_risks") or [],
                "失效条件": result.get("failure_conditions") or [],
                "未来天然不确定": result.get("unknown_a") or [],
                "当前仍待查证": result.get("unknown_b") or [],
            },
            "未来天然不确定事项": result.get("unknown_a") or [],
            "当前仍待查证事项": result.get("unknown_b") or [],
            "关键风险": result.get("key_risks") or [],
            "失效条件": result.get("failure_conditions") or [],
            "下一更新节点": translate_object(result.get("next_update_nodes") or []),
            "关键证据": _evidence_view(result.get("key_evidence")),
        } if result else None,
        "audit": {
            "path_result_id": path.get("latest_path_result_id"),
            "trigger_reason": path.get("trigger_reason"),
            "review_ready": path.get("review_ready"),
            "research_history_count": path.get("research_history_count", 0),
            "economic_judgment": path.get("economic_judgment"),
        },
    }


def build_opportunity_view(
    record: dict[str, Any],
    *,
    maturity_contract_fact: dict[str, Any] | None = None,
) -> dict[str, Any]:
    paths = [
        build_path_view(
            path,
            market_cutoff=record.get("market_cutoff"),
            maturity_contract_fact=maturity_contract_fact,
        )
        for path in record.get("paths", [])
    ]
    completed = sum(p["research_state"] == "COMPLETED" for p in paths)
    hold = sum(p["research_state"] == "HOLD_WAITING_EVIDENCE" for p in paths)
    waiting = sum(p["research_state"] == "NOT_TRIGGERED" for p in paths)
    floor_class, floor_reason = _bond_floor_class(record)
    return {
        "view_contract_version": VIEW_CONTRACT_VERSION,
        "bond_code": record.get("bond_code"),
        "bond_name": record.get("bond_name"),
        "market_cutoff": record.get("market_cutoff"),
        "opportunity_path_count": len(paths),
        "floor_class": floor_class,
        "floor_reason": floor_reason,
        "research_summary": {
            "已完成深研": completed,
            "等待补充证据": hold,
            "等待事件节点": waiting,
        },
        "paths": paths,
        "audit": {
            "record_state": record.get("record_state"),
            "market_snapshot_id": record.get("market_snapshot_id"),
        },
    }


def _quick_judgment(path: dict[str, Any]) -> str | None:
    result = path.get("path_result") or {}
    summary = result.get("summary") or {}
    core = summary.get("core_conclusion") if isinstance(summary, dict) else None
    if isinstance(core, str) and core.strip():
        return core.strip()
    judgments = result.get("judgments") or {}
    for key in (
        "core_judgment",
        "payment_stability_rationale",
        "current_reality_event_state",
        "economic_translation",
        "final_success_assessment",
    ):
        value = judgments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if path.get("research_state") == "NOT_TRIGGERED":
        return _status_explanation(
            str(path.get("path_id") or ""),
            "NOT_TRIGGERED",
            path.get("current_event_state"),
        )
    return None


def build_opportunity_card(
    record: dict[str, Any],
    *,
    maturity_contract_fact: dict[str, Any] | None = None,
) -> dict[str, Any]:
    paths = []
    for path in record.get("paths", []):
        path_id = str(path.get("path_id") or "")
        state = str(path.get("research_state") or "")
        opportunity_time, ytm_pct = _path_time_and_ytm(
            path,
            market_cutoff=record.get("market_cutoff"),
            maturity_contract_fact=maturity_contract_fact,
        )
        paths.append({
            "path_id": path_id,
            "path_name": PATH_LABELS.get(path_id, path_id),
            "research_state": state,
            "research_state_text": STATE_LABELS.get(state, state),
            "current_event_state": path.get("current_event_state"),
            "current_event_state_text": _event_state_text(
                path_id, path.get("current_event_state")
            ),
            "opportunity_time": opportunity_time,
            "ytm_pct": ytm_pct,
            "metrics": _metrics(path_id, path.get("economic_judgment") or {}),
            "quick_judgment": _quick_judgment(path),
        })
    floor_class, floor_reason = _bond_floor_class(record)
    return {
        "bond_code": record.get("bond_code"),
        "bond_name": record.get("bond_name"),
        "market_cutoff": record.get("market_cutoff"),
        "opportunity_path_count": len(paths),
        "floor_class": floor_class,
        "floor_reason": floor_reason,
        "paths": paths,
    }


def build_opportunity_list(
    payload: dict[str, Any],
    *,
    maturity_contracts: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    records = payload.get("records") or []
    maturity_contracts = maturity_contracts or {}
    return {
        "view_contract_version": VIEW_CONTRACT_VERSION,
        "market_snapshot_id": payload.get("market_snapshot_id"),
        "market_cutoff": payload.get("market_cutoff"),
        "bond_count": payload.get("bond_count", len(records)),
        "keep_path_count": payload.get("keep_path_count"),
        "record_state_summary": payload.get("record_state_summary", {}),
        "opportunities": [
            build_opportunity_card(
                record,
                maturity_contract_fact=maturity_contracts.get(
                    str(record.get("bond_code") or "").zfill(6)
                ),
            )
            for record in records
        ],
    }
