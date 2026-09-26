from __future__ import annotations

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
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


def _path_preloaded_notice_candidates(
    ctx: ToolContext,
) -> list[dict[str, Any]]:
    pack = ctx.input_payload.get("evidence_pack") or {}
    facts = pack.get("facts") or {}
    candidates: list[dict[str, Any]] = []
    for key in (
        "official_notice_candidate_index",
        "official_notice_behavior_index",
        "official_contract_document_index",
        "official_payment_notice_candidate_index",
    ):
        value = facts.get(key)
        if isinstance(value, list):
            candidates.extend(
                item for item in value if isinstance(item, dict)
            )

    cross_revision = facts.get("cross_path_revision") or {}
    if isinstance(cross_revision, dict):
        value = cross_revision.get("official_notice_behavior_index")
        if isinstance(value, list):
            candidates.extend(
                item for item in value if isinstance(item, dict)
            )
    return candidates


def _eastmoney_announcement_id(url: str) -> str | None:
    match = re.search(r"(AN\d+)", str(url or ""))
    return match.group(1) if match else None


def path_evidence_search(
    ctx: ToolContext,
    args: dict[str, Any],
) -> dict[str, Any]:
    task = ctx.input_payload.get("path_research_task") or {}
    pack = ctx.input_payload.get("evidence_pack") or {}

    bond_code = str(task.get("bond_code") or "").zfill(6)
    bond_name = str(task.get("bond_name") or "")
    stock_code = str(pack.get("stock_code") or "").zfill(6)
    cutoff = str(task.get("market_cutoff") or "")

    if len(bond_code) != 6 or not bond_code.isdigit():
        raise RuntimeError("PATH_RESEARCH input has invalid bond_code")
    if len(stock_code) != 6 or not stock_code.isdigit():
        raise RuntimeError("PATH_RESEARCH input has invalid stock_code")
    if not cutoff:
        raise RuntimeError("PATH_RESEARCH input has no market_cutoff")

    keyword = str(args.get("keyword") or "").strip()
    start_date, end_date = _bounded_dates(
        cutoff,
        args.get("start_date"),
        args.get("end_date"),
    )
    start_ts = pd.Timestamp(start_date)
    end_ts = pd.Timestamp(end_date)

    results: list[dict[str, Any]] = []
    for candidate in _path_preloaded_notice_candidates(ctx):
        title = str(candidate.get("title") or "")
        published_value = candidate.get("notice_date")
        published_ts = pd.to_datetime(published_value, errors="coerce")
        if pd.isna(published_ts):
            continue
        published_ts = published_ts.normalize()
        if published_ts < start_ts or published_ts > end_ts:
            continue

        searchable = " ".join([
            title,
            str(candidate.get("event_kind") or ""),
            str(candidate.get("notice_type") or ""),
        ])
        if keyword and keyword not in searchable:
            continue

        detail_url = str(candidate.get("url") or "")
        announcement_id = _eastmoney_announcement_id(detail_url)
        if not announcement_id:
            continue

        results.append({
            "evidence_id": announcement_id,
            "source_type": "eastmoney_announcement",
            "stock_code": stock_code,
            "bond_code": bond_code,
            "bond_name": bond_name,
            "title": title,
            "published_at": published_ts.date().isoformat(),
            "detail_url": detail_url,
            "content_api_url": (
                "https://np-cnotice-stock.eastmoney.com/"
                "api/content/ann"
            ),
            "event_kind": candidate.get("event_kind"),
            "keyword": keyword,
            "preloaded_candidate": True,
        })
        if len(results) >= MAX_SEARCH_RESULTS:
            break

    search_source = "PRELOADED_NOTICE_INDEX"

    # Fall back to CNINFO only when the frozen Evidence Pack has no matching
    # notice candidate. This keeps Path Research bounded to the current task
    # while still allowing a narrow primary-source search for missing evidence.
    if not results:
        cninfo_keyword = keyword or "转债"
        df = ak.stock_zh_a_disclosure_report_cninfo(
            symbol=stock_code,
            market="沪深京",
            keyword=cninfo_keyword,
            category="",
            start_date=start_date,
            end_date=end_date,
        )

        for _, item in df.head(MAX_SEARCH_RESULTS).iterrows():
            detail_url = str(item.get("公告链接") or "")
            parsed = parse_qs(urlparse(detail_url).query)
            announcement_id = (parsed.get("announcementId") or [None])[0]
            published_value = item.get("公告时间")
            if not announcement_id or pd.isna(published_value):
                continue
            published = pd.Timestamp(published_value).date().isoformat()
            pdf_url = (
                f"http://static.cninfo.com.cn/finalpage/"
                f"{published}/{announcement_id}.PDF"
            )
            results.append({
                "evidence_id": str(announcement_id),
                "source_type": "cninfo_announcement",
                "stock_code": stock_code,
                "bond_code": bond_code,
                "bond_name": bond_name,
                "title": _strip_html(item.get("公告标题")),
                "published_at": published,
                "detail_url": detail_url,
                "pdf_url": pdf_url,
                "keyword": cninfo_keyword,
                "preloaded_candidate": False,
            })
        search_source = "CNINFO_FALLBACK"

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
        "task_id": str(task.get("task_id") or ""),
        "query": {
            "stock_code": stock_code,
            "keyword": keyword,
            "start_date": start_date,
            "end_date": end_date,
        },
        "search_source": search_source,
        "count": len(results),
        "results": results,
    }


def path_evidence_fetch(
    ctx: ToolContext,
    args: dict[str, Any],
) -> dict[str, Any]:
    evidence_id = str(args.get("evidence_id") or "")
    catalog_path = ctx.ai_job_dir / "evidence_catalog.json"
    if not catalog_path.exists():
        raise ValueError(
            "path_evidence_fetch requires prior path_evidence_search"
        )
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    item = catalog.get(evidence_id)

    # Engineering-prefetched announcement candidates are already part of this
    # PATH_RESEARCH task's frozen Evidence Pack and therefore have valid
    # provenance even if a later narrow path_evidence_search query returns no
    # match. Allow fetch to hydrate such a candidate, but nothing outside the
    # current task's frozen candidate indexes.
    if item is None:
        task = ctx.input_payload.get("path_research_task") or {}
        pack = ctx.input_payload.get("evidence_pack") or {}
        stock_code = str(pack.get("stock_code") or "").zfill(6)
        bond_code = str(task.get("bond_code") or "").zfill(6)
        bond_name = str(task.get("bond_name") or "")
        for candidate in _path_preloaded_notice_candidates(ctx):
            detail_url = str(candidate.get("url") or "")
            announcement_id = _eastmoney_announcement_id(detail_url)
            if announcement_id != evidence_id:
                continue
            published_value = candidate.get("notice_date")
            published_ts = pd.to_datetime(published_value, errors="coerce")
            if pd.isna(published_ts):
                continue
            item = {
                "evidence_id": announcement_id,
                "source_type": "eastmoney_announcement",
                "stock_code": stock_code,
                "bond_code": bond_code,
                "bond_name": bond_name,
                "title": str(candidate.get("title") or ""),
                "published_at": published_ts.date().isoformat(),
                "detail_url": detail_url,
                "content_api_url": (
                    "https://np-cnotice-stock.eastmoney.com/"
                    "api/content/ann"
                ),
                "event_kind": candidate.get("event_kind"),
                "keyword": "PRELOADED_EVIDENCE_PACK",
                "preloaded_candidate": True,
            }
            catalog[evidence_id] = item
            catalog_path.write_text(
                json.dumps(catalog, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            break

    if item is None:
        raise ValueError(
            "evidence_id is neither a path_evidence_search result nor a "
            "prefetched candidate in this AI job"
        )

    if item.get("source_type") != "eastmoney_announcement":
        return evidence_fetch(ctx, args)

    evidence_dir = ctx.ai_job_dir / "evidence"
    evidence_dir.mkdir(exist_ok=True)
    txt_path = evidence_dir / f"{evidence_id}.txt"
    meta_path = evidence_dir / f"{evidence_id}.json"
    pdf_path = evidence_dir / f"{evidence_id}.pdf"

    api_url = str(item["content_api_url"])
    first = requests.get(
        api_url,
        params={
            "art_code": evidence_id,
            "client_source": "web",
            "page_index": 1,
        },
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://data.eastmoney.com/",
        },
        timeout=30,
    )
    first.raise_for_status()
    payload = first.json()
    data = payload.get("data") or {}
    if not data:
        raise RuntimeError("Eastmoney announcement content API returned no data")

    page_size = int(data.get("page_size") or 1)
    page_texts: dict[int, str] = {
        1: str(data.get("notice_content") or "")
    }

    def _fetch_content_page(page_index: int) -> tuple[int, str]:
        response = requests.get(
            api_url,
            params={
                "art_code": evidence_id,
                "client_source": "web",
                "page_index": page_index,
            },
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://data.eastmoney.com/",
            },
            timeout=30,
        )
        response.raise_for_status()
        page_data = response.json().get("data") or {}
        return page_index, str(page_data.get("notice_content") or "")

    if page_size > 1:
        with ThreadPoolExecutor(max_workers=min(8, page_size - 1)) as pool:
            futures = [
                pool.submit(_fetch_content_page, page_index)
                for page_index in range(2, page_size + 1)
            ]
            for future in as_completed(futures):
                page_index, page_text = future.result()
                page_texts[page_index] = page_text

    api_text = "\n".join(
        page_texts.get(page_index, "")
        for page_index in range(1, page_size + 1)
        if page_texts.get(page_index)
    )

    pdf_url = str(data.get("attach_url_web") or data.get("attach_url") or "")
    pdf_sha256 = None
    page_count = None
    pdf_artifact = None
    pdf_text = ""

    event_kind = str(item.get("event_kind") or "")
    should_parse_pdf = bool(
        pdf_url
        and (
            event_kind == "RATING_REPORT"
            or len(api_text.strip()) < 5000
        )
    )
    if should_parse_pdf:
        try:
            response = requests.get(
                pdf_url,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=30,
            )
            response.raise_for_status()
            if "pdf" in (response.headers.get("content-type") or "").lower():
                pdf_path.write_bytes(response.content)
                pdf_sha256 = hashlib.sha256(response.content).hexdigest()
                pdf_artifact = str(pdf_path.relative_to(ctx.ai_job_dir))
                try:
                    reader = PdfReader(str(pdf_path))
                    page_count = len(reader.pages)
                    pdf_text = "\n".join(
                        (page.extract_text() or "") for page in reader.pages
                    )
                except Exception:
                    page_count = None
                    pdf_text = ""
        except Exception:
            # The official announcement text from the content API remains
            # usable even when the mirrored PDF endpoint is temporarily down.
            pass

    text = pdf_text if len(pdf_text.strip()) > len(api_text.strip()) else api_text
    text_source = "PDF_EXTRACTED" if text is pdf_text and pdf_text else "CONTENT_API"
    txt_path.write_text(text, encoding="utf-8")
    text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()

    meta = {
        **item,
        "title": str(data.get("notice_title") or item.get("title") or ""),
        "published_at": str(data.get("notice_date") or item.get("published_at") or ""),
        "pdf_url": pdf_url or None,
        "pdf_artifact": pdf_artifact,
        "text_artifact": str(txt_path.relative_to(ctx.ai_job_dir)),
        "pdf_sha256": pdf_sha256,
        "text_sha256": text_sha256,
        "page_count": page_count,
        "content_page_count": page_size,
        "text_source": text_source,
        "api_text_chars": len(api_text),
        "pdf_text_chars": len(pdf_text),
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


def _prefetch_priority(path_id: str, item: dict[str, Any]) -> tuple[int, str]:
    kind = str(item.get("event_kind") or "")
    title = str(item.get("title") or "")
    if path_id == "MATURITY_CASH":
        if kind == "RATING_REPORT":
            rank = 0
        elif kind == "FINANCIAL_REPORT" and "摘要" not in title:
            rank = 1
        elif kind == "HARD_CREDIT_EVENT":
            rank = 2
        elif kind == "FINANCING_SUPPORT":
            rank = 3
        elif kind == "SUPPORT_OR_ASSET":
            rank = 4
        else:
            rank = 20
    elif path_id == "PUT":
        if kind == "RATING_REPORT":
            rank = 0
        elif kind == "FINANCIAL_REPORT" and "摘要" not in title:
            rank = 1
        elif kind == "HARD_CREDIT_EVENT":
            rank = 2
        elif kind in {"NO_REVISION", "REVISION_ACTION", "CONVERSION_PRICE_EVENT"}:
            rank = 3
        elif kind == "FINANCING_SUPPORT":
            rank = 4
        else:
            rank = 20
    elif path_id == "DOWNWARD_REVISION":
        rank = {
            "NO_REVISION": 0,
            "REVISION_ACTION": 1,
            "CONTRACT_DOCUMENT": 2,
            "CONVERSION_PRICE_EVENT": 3,
            "TRIGGER": 4,
            "EXPECTED_TRIGGER": 5,
        }.get(kind, 20)
    else:
        rank = 20
    # More recent notices first within the same evidence class.
    return rank, str(item.get("notice_date") or "")


def _snippet_keywords(path_id: str, event_kind: str) -> list[str]:
    if path_id == "MATURITY_CASH":
        if event_kind == "RATING_REPORT":
            return [
                "评级观点", "偿债", "流动性", "现金短期债务比",
                "授信", "债务", "支持", "风险",
            ]
        if event_kind == "FINANCIAL_REPORT":
            return [
                "母公司资产负债表", "母公司现金流量表", "货币资金",
                "受限", "短期借款", "一年内到期", "应付债券",
                "长期借款", "取得借款", "发行债券", "偿还债务",
            ]
        if event_kind == "HARD_CREDIT_EVENT":
            return ["逾期", "违约", "冻结", "重整", "持续经营"]
        if event_kind == "FINANCING_SUPPORT":
            return ["授信", "借款", "融资", "额度", "担保"]
        return ["现金", "债务", "偿债", "融资"]
    if path_id == "PUT":
        if event_kind == "RATING_REPORT":
            return [
                "评级观点", "偿债", "流动性", "现金短期债务比",
                "授信", "债务", "逾期", "支持", "风险",
            ]
        if event_kind == "FINANCIAL_REPORT":
            return [
                "母公司资产负债表", "母公司现金流量表", "货币资金",
                "受限", "短期借款", "一年内到期", "应付债券",
                "长期借款", "经营活动产生的现金流量净额",
            ]
        if event_kind == "HARD_CREDIT_EVENT":
            return [
                "逾期", "违约", "冻结", "担保逾期", "债务逾期",
                "重整", "持续经营",
            ]
        return [
            "回售", "下修", "转股价格", "债务", "现金", "偿债", "融资",
        ]
    if path_id == "DOWNWARD_REVISION":
        if event_kind == "CONTRACT_DOCUMENT":
            return [
                "向下修正条款", "转股价格向下修正", "修正后的转股价格",
                "最近一期经审计每股净资产", "股票面值", "二十个交易日",
                "前一交易日", "股东大会",
            ]
        return [
            "不向下修正", "向下修正", "转股价格", "董事会",
            "股东大会", "触发", "修正条款",
        ]
    return []


def _extract_relevant_snippets(
    text: str,
    keywords: list[str],
    *,
    radius: int = 500,
    max_snippets: int = 8,
    max_chars: int = 9000,
) -> list[str]:
    clean = str(text or "")
    snippets: list[str] = []
    occupied: list[tuple[int, int]] = []
    for keyword in keywords:
        start = 0
        while len(snippets) < max_snippets:
            idx = clean.find(keyword, start)
            if idx < 0:
                break
            left = max(0, idx - radius)
            right = min(len(clean), idx + len(keyword) + radius)
            start = idx + len(keyword)
            if any(not (right <= a or left >= b) for a, b in occupied):
                continue
            snippet = clean[left:right].strip()
            if snippet:
                snippets.append(snippet)
                occupied.append((left, right))
            if sum(len(x) for x in snippets) >= max_chars:
                break
        if len(snippets) >= max_snippets or sum(len(x) for x in snippets) >= max_chars:
            break
    if not snippets and clean:
        snippets = [clean[: min(len(clean), 4000)]]
    return snippets


def prefetch_path_research_evidence(
    ctx: ToolContext,
    *,
    max_docs: int = 3,
) -> list[dict[str, Any]]:
    """Program-side bounded evidence prefetch before the first AI response."""
    task = ctx.input_payload.get("path_research_task") or {}
    path_id = str(task.get("path_id") or "")
    candidates = _path_preloaded_notice_candidates(ctx)
    if not candidates:
        return []

    # Prefer distinct evidence classes so three slots do not get consumed by
    # duplicate annual-report summaries or repeated notices of the same type.
    ordered = sorted(
        candidates,
        key=lambda x: (
            _prefetch_priority(path_id, x)[0],
            -pd.Timestamp(x.get("notice_date") or "1900-01-01").value,
        ),
    )
    selected: list[dict[str, Any]] = []
    seen_classes: set[str] = set()
    for item in ordered:
        rank, _ = _prefetch_priority(path_id, item)
        if rank >= 20:
            continue
        kind = str(item.get("event_kind") or "OTHER")
        class_key = kind
        if kind == "FINANCIAL_REPORT":
            if "摘要" in str(item.get("title") or ""):
                continue
            class_key = "FINANCIAL_REPORT_FULL"
        if class_key in seen_classes:
            continue
        if not _eastmoney_announcement_id(str(item.get("url") or "")):
            continue
        selected.append(item)
        seen_classes.add(class_key)
        if len(selected) >= max_docs:
            break

    if not selected:
        return []

    catalog_path = ctx.ai_job_dir / "evidence_catalog.json"
    catalog = {}
    if catalog_path.exists():
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))

    for candidate in selected:
        evidence_id = _eastmoney_announcement_id(str(candidate.get("url") or ""))
        if not evidence_id:
            continue
        catalog[evidence_id] = {
            "evidence_id": evidence_id,
            "source_type": "eastmoney_announcement",
            "stock_code": str((ctx.input_payload.get("evidence_pack") or {}).get("stock_code") or "").zfill(6),
            "bond_code": str(task.get("bond_code") or "").zfill(6),
            "bond_name": str(task.get("bond_name") or ""),
            "title": str(candidate.get("title") or ""),
            "published_at": str(candidate.get("notice_date") or ""),
            "detail_url": str(candidate.get("url") or ""),
            "content_api_url": "https://np-cnotice-stock.eastmoney.com/api/content/ann",
            "event_kind": candidate.get("event_kind"),
            "keyword": "",
            "preloaded_candidate": True,
            "engineering_prefetch": True,
        }

    catalog_path.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    results: list[dict[str, Any]] = []
    for candidate in selected:
        evidence_id = _eastmoney_announcement_id(str(candidate.get("url") or ""))
        if not evidence_id:
            continue
        try:
            fetched = path_evidence_fetch(ctx, {"evidence_id": evidence_id})
            kind = str(candidate.get("event_kind") or "")
            full_text = str(fetched.get("text") or "")
            text_artifact = fetched.get("text_artifact")
            if text_artifact:
                artifact_path = ctx.ai_job_dir / str(text_artifact)
                if artifact_path.exists():
                    full_text = artifact_path.read_text(
                        encoding="utf-8",
                        errors="ignore",
                    )
            snippets = _extract_relevant_snippets(
                full_text,
                _snippet_keywords(path_id, kind),
            )
            results.append({
                "evidence_id": evidence_id,
                "source_type": fetched.get("source_type"),
                "event_kind": kind,
                "title": fetched.get("title"),
                "published_at": fetched.get("published_at"),
                "detail_url": fetched.get("detail_url"),
                "pdf_url": fetched.get("pdf_url"),
                "text_source": fetched.get("text_source"),
                "text_chars": fetched.get("text_chars"),
                "snippets": snippets,
            })
        except Exception as exc:
            results.append({
                "evidence_id": evidence_id,
                "event_kind": candidate.get("event_kind"),
                "title": candidate.get("title"),
                "published_at": candidate.get("notice_date"),
                "error": f"{type(exc).__name__}: {exc}",
                "snippets": [],
            })
    return results


TOOL_HANDLERS = {
    "evidence_search": evidence_search,
    "evidence_fetch": evidence_fetch,
    "path_evidence_search": path_evidence_search,
    "path_evidence_fetch": path_evidence_fetch,
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
