"""Opportunity View Contract V1.

Pure presentation adapter from lossless Opportunity Record to a Chinese-first
user view. It must not change Economic KEEP or Path research conclusions.
"""

from __future__ import annotations

from typing import Any

VIEW_CONTRACT_VERSION = "opportunity-view-v1"

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


def humanize_key(key: str) -> str:
    if key in FIELD_OVERRIDES:
        return FIELD_OVERRIDES[key]
    if key in {"fact_spine", "judgments"}:
        return {"fact_spine": "关键事实链", "judgments": "研究判断"}[key]
    parts = str(key).replace("-", "_").split("_")
    translated = [TOKEN_LABELS.get(part, "") for part in parts]
    if translated and all(translated):
        return "".join(translated)
    return str(key)


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
def build_path_view(path: dict[str, Any]) -> dict[str, Any]:
    path_id = str(path.get("path_id") or "")
    state = str(path.get("research_state") or "")
    result = path.get("path_result") or {}
    judgments = result.get("judgments") or {}

    return {
        "path_id": path_id,
        "path_name": PATH_LABELS.get(path_id, path_id),
        "opportunity_status": "存在正向经济空间",
        "research_state": state,
        "research_state_text": STATE_LABELS.get(state, state),
        "current_event_state": path.get("current_event_state"),
        "status_explanation": _status_explanation(
            path_id, state, path.get("current_event_state")
        ),
        "metrics": _metrics(path_id, path.get("economic_judgment") or {}),
        "research": {
            "研究判断": translate_object(judgments),
            "关键事实链": translate_object(result.get("fact_spine") or {}),
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


def build_opportunity_view(record: dict[str, Any]) -> dict[str, Any]:
    paths = [build_path_view(path) for path in record.get("paths", [])]
    completed = sum(p["research_state"] == "COMPLETED" for p in paths)
    hold = sum(p["research_state"] == "HOLD_WAITING_EVIDENCE" for p in paths)
    waiting = sum(p["research_state"] == "NOT_TRIGGERED" for p in paths)
    return {
        "view_contract_version": VIEW_CONTRACT_VERSION,
        "bond_code": record.get("bond_code"),
        "bond_name": record.get("bond_name"),
        "market_cutoff": record.get("market_cutoff"),
        "opportunity_path_count": len(paths),
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


def build_opportunity_card(record: dict[str, Any]) -> dict[str, Any]:
    paths = []
    for path in record.get("paths", []):
        path_id = str(path.get("path_id") or "")
        state = str(path.get("research_state") or "")
        paths.append({
            "path_id": path_id,
            "path_name": PATH_LABELS.get(path_id, path_id),
            "research_state": state,
            "research_state_text": STATE_LABELS.get(state, state),
            "current_event_state": path.get("current_event_state"),
            "metrics": _metrics(path_id, path.get("economic_judgment") or {}),
            "quick_judgment": _quick_judgment(path),
        })
    return {
        "bond_code": record.get("bond_code"),
        "bond_name": record.get("bond_name"),
        "market_cutoff": record.get("market_cutoff"),
        "opportunity_path_count": len(paths),
        "paths": paths,
    }


def build_opportunity_list(payload: dict[str, Any]) -> dict[str, Any]:
    records = payload.get("records") or []
    return {
        "view_contract_version": VIEW_CONTRACT_VERSION,
        "market_snapshot_id": payload.get("market_snapshot_id"),
        "market_cutoff": payload.get("market_cutoff"),
        "bond_count": payload.get("bond_count", len(records)),
        "keep_path_count": payload.get("keep_path_count"),
        "record_state_summary": payload.get("record_state_summary", {}),
        "opportunities": [build_opportunity_card(record) for record in records],
    }
