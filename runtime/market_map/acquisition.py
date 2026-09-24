from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import akshare as ak
import pandas as pd


STANDARD_CB_PREFIXES = {"110", "111", "113", "118", "123", "127", "128"}
DAYS_PER_MONTH = 365.2425 / 12.0


@dataclass
class Step:
    id: str
    name: str
    status: str = "PENDING"
    metrics: dict[str, Any] = field(default_factory=dict)
    conclusion: str = ""
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class AcquisitionResult:
    run_id: str
    snapshot_mode: str
    market_cutoff: str
    status: str
    started_at: str
    completed_at: str | None = None
    steps: list[Step] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def emit(step: Step) -> None:
    print(json.dumps({"type": "step", **asdict(step)}, ensure_ascii=False), flush=True)


def step(run: AcquisitionResult, sid: str, name: str) -> Step:
    item = Step(id=sid, name=name, status="RUNNING")
    run.steps.append(item)
    emit(item)
    return item


def persist(run: AcquisitionResult, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "acquisition_result.json").write_text(
        json.dumps(asdict(run), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def fail(run: AcquisitionResult, current: Step, output_dir: Path, message: str) -> AcquisitionResult:
    current.status = "FAIL"
    current.conclusion = message
    run.errors.append(message)
    run.status = "FAIL"
    run.completed_at = now_utc()
    emit(current)
    persist(run, output_dir)
    return run


def call_with_retry(
    fn: Callable[[], Any],
    current: Step,
    attempts: int = 3,
    delay_seconds: float = 2.0,
) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            current.metrics["attempt"] = attempt
            return fn()
        except Exception as exc:
            last_error = exc
            short = f"{type(exc).__name__}: {str(exc)[:180]}"
            if attempt < attempts:
                current.status = "RUNNING"
                current.conclusion = f"第 {attempt} 次取数失败，准备自动重试。"
                current.warnings = [short]
                emit(current)
                time.sleep(delay_seconds)
            else:
                current.warnings = [short]
    assert last_error is not None
    raise last_error


def save_frame(df: pd.DataFrame, out: Path, name: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / name, index=False)


def persist_source_manifest(manifest: dict[str, Any], output_dir: Path) -> None:
    (output_dir / "source_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def register_source(
    manifest: dict[str, Any],
    output_dir: Path,
    *,
    source: str,
    adapter: str,
    role: str,
    status: str,
    fetched_at: str,
    row_count: int | None = None,
    raw_file: str | None = None,
    degraded: bool = False,
    error: str | None = None,
) -> None:
    record = {
        "source": source,
        "adapter": adapter,
        "role": role,
        "status": status,
        "fetched_at": fetched_at,
        "market_cutoff": manifest["market_cutoff"],
        "row_count": row_count,
        "raw_file": raw_file,
        "degraded": degraded,
    }
    if error:
        record["error"] = error
    manifest["sources"].append(record)
    persist_source_manifest(manifest, output_dir)


def fetch_and_freeze(
    fn: Callable[[], pd.DataFrame],
    current: Step,
    *,
    raw_dir: Path,
    manifest: dict[str, Any],
    output_dir: Path,
    source: str,
    adapter: str,
    role: str,
    filename: str,
    attempts: int = 3,
    delay_seconds: float = 2.0,
    degraded: bool = False,
) -> pd.DataFrame:
    try:
        df = call_with_retry(
            fn,
            current,
            attempts=attempts,
            delay_seconds=delay_seconds,
        )
    except Exception as exc:
        register_source(
            manifest,
            output_dir,
            source=source,
            adapter=adapter,
            role=role,
            status="FAIL",
            fetched_at=now_utc(),
            degraded=degraded,
            error=f"{type(exc).__name__}: {str(exc)[:300]}",
        )
        raise

    raw_path = raw_dir / filename
    df.to_csv(raw_path, index=False)
    register_source(
        manifest,
        output_dir,
        source=source,
        adapter=adapter,
        role=role,
        status="PASS",
        fetched_at=now_utc(),
        row_count=len(df),
        raw_file=str(raw_path.relative_to(output_dir)),
        degraded=degraded,
    )
    return df


def freeze_existing_frame(
    df: pd.DataFrame,
    *,
    raw_dir: Path,
    manifest: dict[str, Any],
    output_dir: Path,
    source: str,
    adapter: str,
    role: str,
    filename: str,
    degraded: bool = False,
) -> pd.DataFrame:
    raw_path = raw_dir / filename
    df.to_csv(raw_path, index=False)
    register_source(
        manifest,
        output_dir,
        source=source,
        adapter=adapter,
        role=role,
        status="PASS",
        fetched_at=now_utc(),
        row_count=len(df),
        raw_file=str(raw_path.relative_to(output_dir)),
        degraded=degraded,
    )
    return df


def records(df: pd.DataFrame, columns: list[str] | None = None, limit: int = 50) -> list[dict[str, Any]]:
    view = df if columns is None else df[[c for c in columns if c in df.columns]]
    return json.loads(view.head(limit).to_json(orient="records", date_format="iso", force_ascii=False))


def table(title: str, columns: list[tuple[str, str]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "title": title,
        "columns": [{"key": key, "label": label} for key, label in columns],
        "rows": rows,
    }


def invalid_number(value: Any) -> bool:
    return pd.isna(value) or float(value) <= 0


def classify_exclusion(row: pd.Series) -> str:
    if not bool(row.get("_standard_prefix", True)):
        return "非标准可转债品种（如可交换债）"
    if not bool(row.get("_public_name", True)):
        return "定向可转债 / 非公开品种"
    if not bool(row.get("_listed_by_cutoff", True)):
        return "尚未上市 / 未形成有效公开交易"
    if invalid_number(row.get("P")):
        return "已上市但当前无有效交易价格"
    if invalid_number(row.get("S")):
        return "正股价格缺失或异常"
    if invalid_number(row.get("K")):
        return "转股价缺失或异常"
    return "其他数据完整性异常"


def build_fallback_market_snapshot(jsl: pd.DataFrame, em: pd.DataFrame) -> pd.DataFrame:
    """
    Build a normalized fallback market snapshot from already-frozen raw sources.

    - Jisilu supplies the active candidate set and audit quote.
    - Eastmoney datacenter supplies P/S/K/CV and listing metadata.
    - P/S/K/CV remain from one economic quote source (Eastmoney datacenter).
    """
    jsl = jsl.copy()
    em = em.copy()
    jsl["代码"] = jsl["代码"].astype(str).str.zfill(6)
    em["债券代码"] = em["债券代码"].astype(str).str.zfill(6)

    meta = em[
        [
            "债券代码",
            "债券简称",
            "上市时间",
            "申购日期",
            "正股代码",
            "正股简称",
            "正股价",
            "转股价",
            "转股价值",
            "债现价",
        ]
    ].drop_duplicates("债券代码")

    merged = jsl.merge(
        meta,
        left_on="代码",
        right_on="债券代码",
        how="left",
        suffixes=("_jsl", "_em"),
    )

    out = pd.DataFrame(
        {
            "转债代码": merged["代码"],
            "转债名称": merged["名称"],
            "转债最新价": pd.to_numeric(merged["债现价"], errors="coerce").fillna(
                pd.to_numeric(merged["现价"], errors="coerce")
            ),
            "正股代码": merged["正股代码_em"].fillna(merged["正股代码_jsl"]),
            "正股名称": merged["正股简称"].fillna(merged["正股名称"]),
            "正股最新价": pd.to_numeric(merged["正股价_em"], errors="coerce"),
            "转股价": pd.to_numeric(merged["转股价_em"], errors="coerce"),
            "转股价值": pd.to_numeric(merged["转股价值"], errors="coerce"),
            "上市日期": merged["上市时间"],
            "申购日期": merged["申购日期"],
        }
    )

    out["备用审计_集思录转债价"] = pd.to_numeric(merged["现价"], errors="coerce")
    out["备用审计_集思录正股价"] = pd.to_numeric(merged["正股价_jsl"], errors="coerce")
    out["备用审计_集思录转股价"] = pd.to_numeric(merged["转股价_jsl"], errors="coerce")
    return out


def run_acquisition(
    output_dir: Path,
    snapshot_mode: str,
    market_cutoff: str,
    fixture_dir: Path | None = None,
) -> AcquisitionResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    if snapshot_mode in {"REPLAY_TEST", "HISTORICAL_REPLAY"}:
        if fixture_dir is None:
            raise ValueError(
                f"{snapshot_mode} requires --fixture-dir"
            )
        fixture_dir = fixture_dir.resolve()
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    result = AcquisitionResult(
        run_id=run_id,
        snapshot_mode=snapshot_mode,
        market_cutoff=market_cutoff,
        status="RUNNING",
        started_at=now_utc(),
    )

    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    source_manifest: dict[str, Any] = {
        "run_id": run_id,
        "snapshot_mode": snapshot_mode,
        "market_cutoff": market_cutoff,
        "created_at": now_utc(),
        "sources": [],
    }
    persist_source_manifest(source_manifest, output_dir)

    s0 = step(result, "S0", "冻结本次运行")
    s0.metrics = {
        "run_id": run_id,
        "snapshot_mode": snapshot_mode,
        "market_cutoff": market_cutoff,
    }

    if snapshot_mode == "CLOSE":
        china_now = datetime.now(ZoneInfo("Asia/Shanghai"))
        s0.metrics["china_time"] = china_now.isoformat(timespec="minutes")

        if market_cutoff != china_now.date().isoformat():
            return fail(
                result,
                s0,
                output_dir,
                "正式收盘模式只允许生成中国市场当天收盘截面；"
                "历史日期必须使用历史回放 / 历史数据模式，禁止用当前行情回填过去日期。",
            )

        if (china_now.hour, china_now.minute) < (15, 10):
            return fail(
                result,
                s0,
                output_dir,
                "当前尚未超过 15:10（北京时间），正式收盘截面尚未冻结；请使用盘中测试模式。",
            )

        try:
            trade_dates = fetch_and_freeze(
                lambda: ak.tool_trade_date_hist_sina().copy(),
                s0,
                raw_dir=raw_dir,
                manifest=source_manifest,
                output_dir=output_dir,
                source="新浪交易日历",
                adapter="ak.tool_trade_date_hist_sina",
                role="close_trade_day_gate",
                filename="trade_calendar_sina.csv",
                attempts=2,
                delay_seconds=1.0,
            )
            trade_dates["trade_date"] = pd.to_datetime(
                trade_dates["trade_date"], errors="coerce"
            ).dt.date
            if china_now.date() not in set(trade_dates["trade_date"].dropna()):
                return fail(
                    result,
                    s0,
                    output_dir,
                    "今天不是A股交易日，不能生成今天的正式收盘截面。",
                )
            s0.metrics["trade_day_check"] = "PASS"
        except Exception as exc:
            s0.status = "WARNING"
            s0.warnings = [
                f"交易日历校验暂时不可用：{type(exc).__name__}；继续运行但保留时间审计警告。"
            ]

    if s0.status != "WARNING":
        s0.status = "PASS"
    s0.conclusion = "运行身份和市场时点已冻结，可以开始取数。"
    emit(s0)

    s1 = step(result, "S1", "获取主行情")
    market_source = "REPLAY_FIXTURE"
    primary_error: Exception | None = None

    if snapshot_mode == "REPLAY_TEST":
        comp = pd.read_csv(
            fixture_dir / "comparison_raw.csv",
            dtype={"转债代码": str},
        )
        freeze_existing_frame(
            comp,
            raw_dir=raw_dir,
            manifest=source_manifest,
            output_dir=output_dir,
            source="固定历史样本",
            adapter="fixture:comparison_raw.csv",
            role="market_snapshot_replay",
            filename="market_replay_fixture.csv",
        )
        s1.metrics["data_mode"] = "fixture_replay"
    elif snapshot_mode == "HISTORICAL_REPLAY":
        historical_raw = fixture_dir / "raw"
        primary_path = historical_raw / "market_primary_eastmoney_push2.csv"
        fallback_jsl_path = historical_raw / "market_fallback_jisilu.csv"
        fallback_em_path = historical_raw / "market_fallback_eastmoney_datacenter.csv"

        if primary_path.exists():
            comp = pd.read_csv(
                primary_path,
                dtype={"转债代码": str},
            )
            market_source = "HISTORICAL_PRIMARY_RAW"
            freeze_existing_frame(
                comp,
                raw_dir=raw_dir,
                manifest=source_manifest,
                output_dir=output_dir,
                source="历史正式快照 Raw",
                adapter="historical:market_primary_eastmoney_push2.csv",
                role="market_historical_replay",
                filename="market_historical_primary.csv",
            )
        elif fallback_jsl_path.exists() and fallback_em_path.exists():
            jsl_market = pd.read_csv(
                fallback_jsl_path,
                dtype={"代码": str},
            )
            em_market = pd.read_csv(
                fallback_em_path,
                dtype={"债券代码": str},
            )
            freeze_existing_frame(
                jsl_market,
                raw_dir=raw_dir,
                manifest=source_manifest,
                output_dir=output_dir,
                source="历史正式快照 Raw",
                adapter="historical:market_fallback_jisilu.csv",
                role="market_historical_fallback_universe",
                filename="market_historical_jisilu.csv",
            )
            freeze_existing_frame(
                em_market,
                raw_dir=raw_dir,
                manifest=source_manifest,
                output_dir=output_dir,
                source="历史正式快照 Raw",
                adapter="historical:market_fallback_eastmoney_datacenter.csv",
                role="market_historical_fallback_quote",
                filename="market_historical_eastmoney_datacenter.csv",
            )
            comp = build_fallback_market_snapshot(
                jsl_market,
                em_market,
            )
            market_source = "HISTORICAL_FALLBACK_RAW"
        else:
            raise FileNotFoundError(
                "历史 Acquisition Run 缺少可重建主行情的冻结 Raw。"
            )

        s1.metrics["data_mode"] = "historical_formal_replay"
        s1.metrics["historical_source_dir"] = str(fixture_dir)
    else:
        market_source = "EASTMONEY_PUSH2"
        try:
            comp = fetch_and_freeze(
                lambda: ak.bond_cov_comparison().copy(),
                s1,
                raw_dir=raw_dir,
                manifest=source_manifest,
                output_dir=output_dir,
                source="东方财富 push2",
                adapter="ak.bond_cov_comparison",
                role="market_primary",
                filename="market_primary_eastmoney_push2.csv",
                attempts=2,
                delay_seconds=1.5,
            )
        except Exception as exc:
            primary_error = exc
            market_source = "FALLBACK_EM_DATACENTER_PLUS_JSL"
            s1.status = "RUNNING"
            s1.conclusion = "主行情接口连续失败，正在自动切换备用行情链路。"
            s1.warnings = [
                f"东方财富 push2 主源不可用：{type(exc).__name__}；开始切换备用源。"
            ]
            emit(s1)

            try:
                jsl_market = fetch_and_freeze(
                    lambda: ak.bond_cb_redeem_jsl().copy(),
                    s1,
                    raw_dir=raw_dir,
                    manifest=source_manifest,
                    output_dir=output_dir,
                    source="集思录强赎列表",
                    adapter="ak.bond_cb_redeem_jsl",
                    role="market_fallback_universe_and_audit",
                    filename="market_fallback_jisilu.csv",
                    attempts=2,
                    delay_seconds=1.5,
                    degraded=True,
                )
                em_market = fetch_and_freeze(
                    lambda: ak.bond_zh_cov().copy(),
                    s1,
                    raw_dir=raw_dir,
                    manifest=source_manifest,
                    output_dir=output_dir,
                    source="东方财富 datacenter",
                    adapter="ak.bond_zh_cov",
                    role="market_fallback_economic_quote",
                    filename="market_fallback_eastmoney_datacenter.csv",
                    attempts=2,
                    delay_seconds=1.5,
                    degraded=True,
                )
                comp = build_fallback_market_snapshot(jsl_market, em_market)
            except Exception as fallback_exc:
                return fail(
                    result,
                    s1,
                    output_dir,
                    "主行情源与备用行情链路均不可用；"
                    f"主源错误：{type(primary_error).__name__}；"
                    f"备用源错误：{type(fallback_exc).__name__}。",
                )

    comp["转债代码"] = comp["转债代码"].astype(str).str.zfill(6)
    save_frame(comp, output_dir, "main_market_raw.csv")
    s1.metrics.update(
        {
            "market_source": market_source,
            "candidate_count": len(comp),
            "unique_codes": comp["转债代码"].nunique(),
            "price_covered": int(pd.to_numeric(comp["转债最新价"], errors="coerce").notna().sum()),
        }
    )
    source_note = (
        (
            f"本次使用固定测试样本回放：{fixture_dir}"
            if snapshot_mode == "REPLAY_TEST"
            else f"本次使用历史正式快照 Raw 回放：{fixture_dir}"
        )
        if snapshot_mode in {"REPLAY_TEST", "HISTORICAL_REPLAY"}
        else (
            "主源：东方财富 push2 可转债比价接口。"
            if market_source == "EASTMONEY_PUSH2"
            else "备用链路：集思录活跃候选集合 + 东方财富数据中心 P/S/K/CV 与上市元数据。"
        )
    )
    s1.details = {
        "notes": [
            source_note,
            "P / S / K / 源CV 保持在同一经济行情源内；备用链路仍会在后续步骤做跨源审计。",
        ]
    }

    if primary_error is not None and snapshot_mode not in {"REPLAY_TEST", "HISTORICAL_REPLAY"}:
        s1.status = "WARNING"
        s1.warnings = [
            "主行情 push2 接口不可用，本次已自动切换备用链路；结果可继续运行，但保留数据源降级警告。"
        ]
        s1.conclusion = f"备用行情链路取得成功，共 {len(comp)} 个候选对象；进入可转债范围筛选。"
    else:
        s1.status = "PASS"
        s1.warnings = []
        s1.conclusion = f"主行情取得成功，共 {len(comp)} 个候选对象；进入可转债范围筛选。"
    emit(s1)

    s2 = step(result, "S2", "可转债范围筛选")
    main = comp.rename(
        columns={
            "转债代码": "bond_code",
            "转债名称": "bond_name",
            "转债最新价": "P",
            "正股代码": "stock_code",
            "正股最新价": "S",
            "转股价": "K",
            "转股价值": "source_CV",
        }
    ).copy()
    for c in ["P", "S", "K", "source_CV"]:
        main[c] = pd.to_numeric(main[c], errors="coerce")

    duplicate_codes = main[
        main["bond_code"].duplicated(keep=False)
    ].sort_values("bond_code")
    if len(duplicate_codes):
        s2.metrics = {
            "candidate": len(main),
            "duplicate_code_rows": len(duplicate_codes),
            "duplicate_codes": int(duplicate_codes["bond_code"].nunique()),
        }
        s2.details = {
            "tables": [
                table(
                    "重复转债代码",
                    [
                        ("bond_code", "代码"),
                        ("bond_name", "名称"),
                        ("P", "转债价格"),
                    ],
                    records(
                        duplicate_codes,
                        ["bond_code", "bond_name", "P"],
                        50,
                    ),
                )
            ]
        }
        return fail(
            result,
            s2,
            output_dir,
            "主行情存在重复转债代码，无法形成唯一 Universe；本次运行停止在 S2。",
        )

    cutoff_ts = pd.Timestamp(market_cutoff)
    main["_standard_prefix"] = main["bond_code"].astype(str).str[:3].isin(STANDARD_CB_PREFIXES)
    main["_public_name"] = ~main["bond_name"].astype(str).str.contains("定转", na=False)
    main["_listing_date"] = pd.to_datetime(main.get("上市日期"), errors="coerce")
    main["_listed_by_cutoff"] = (
        main["_listing_date"].notna()
        & (main["_listing_date"] <= cutoff_ts)
    )

    base_mask = (
        main["_standard_prefix"]
        & main["_public_name"]
        & main["_listed_by_cutoff"]
        & main["P"].notna()
        & (main["P"] > 0)
        & main["S"].notna()
        & (main["S"] > 0)
        & main["K"].notna()
        & (main["K"] > 0)
    )
    base = main[base_mask].copy()
    excluded = main.loc[~base_mask].copy()
    if len(excluded):
        excluded["exclude_reason"] = excluded.apply(classify_exclusion, axis=1)
    save_frame(
        excluded[
            [c for c in ["bond_code", "bond_name", "P", "S", "K", "上市日期", "申购日期", "exclude_reason"] if c in excluded.columns]
        ],
        output_dir,
        "universe_excluded.csv",
    )
    reason_counts = excluded["exclude_reason"].value_counts().to_dict() if len(excluded) else {}
    s2.metrics = {"candidate": len(main), "base_sample": len(base), "excluded": len(excluded)}
    s2.details = {
        "reason_counts": reason_counts,
        "tables": [
            table(
                "被排除对象",
                [
                    ("bond_code", "代码"),
                    ("bond_name", "名称"),
                    ("P", "转债价格"),
                    ("S", "正股价格"),
                    ("K", "转股价"),
                    ("上市日期", "上市日期"),
                    ("exclude_reason", "排除原因"),
                ],
                records(
                    excluded,
                    ["bond_code", "bond_name", "P", "S", "K", "上市日期", "exclude_reason"],
                ),
            )
        ],
    }
    if not len(base):
        return fail(result, s2, output_dir, "没有对象通过基础数据门槛，本次运行停止。")
    s2.status = "PASS"
    s2.conclusion = f"{len(base)} 只形成可计算基础样本；{len(excluded)} 只被数据门槛排除。"
    emit(s2)

    s3 = step(result, "S3", "补充到期日")
    try:
        if snapshot_mode == "REPLAY_TEST":
            info = pd.read_csv(
                fixture_dir / "info_raw.csv",
                dtype={"债券代码": str},
            )
            freeze_existing_frame(
                info,
                raw_dir=raw_dir,
                manifest=source_manifest,
                output_dir=output_dir,
                source="固定历史样本",
                adapter="fixture:info_raw.csv",
                role="maturity_replay",
                filename="maturity_replay_fixture.csv",
            )
        elif snapshot_mode == "HISTORICAL_REPLAY":
            info_path = fixture_dir / "raw" / "maturity_ths.csv"
            if not info_path.exists():
                raise FileNotFoundError(
                    "历史 Acquisition Run 缺少 raw/maturity_ths.csv"
                )
            info = pd.read_csv(
                info_path,
                dtype={"债券代码": str},
            )
            freeze_existing_frame(
                info,
                raw_dir=raw_dir,
                manifest=source_manifest,
                output_dir=output_dir,
                source="历史正式快照 Raw",
                adapter="historical:maturity_ths.csv",
                role="maturity_historical_replay",
                filename="maturity_historical_ths.csv",
            )
        else:
            info = fetch_and_freeze(
                lambda: ak.bond_zh_cov_info_ths().copy(),
                s3,
                raw_dir=raw_dir,
                manifest=source_manifest,
                output_dir=output_dir,
                source="同花顺可转债基础信息",
                adapter="ak.bond_zh_cov_info_ths",
                role="maturity_primary",
                filename="maturity_ths.csv",
                attempts=3,
                delay_seconds=2.0,
            )
    except Exception as exc:
        return fail(
            result,
            s3,
            output_dir,
            f"到期日主数据源获取失败，自动重试后仍不可用；本次尚未进入逐券 fallback，运行停止在 S3。错误：{type(exc).__name__}",
        )
    info["债券代码"] = info["债券代码"].astype(str).str.zfill(6)
    maturity = info.rename(columns={"债券代码": "bond_code", "到期时间": "maturity_date"})[
        ["bond_code", "maturity_date"]
    ].drop_duplicates("bond_code")
    enriched = base.merge(maturity, on="bond_code", how="left", validate="one_to_one")
    enriched["maturity_date"] = pd.to_datetime(enriched["maturity_date"], errors="coerce")
    maturity_missing = enriched[enriched["maturity_date"].isna()].copy()

    fallback_rows: list[pd.DataFrame] = []
    fallback_errors: list[str] = []

    if snapshot_mode == "HISTORICAL_REPLAY" and len(maturity_missing):
        historical_fb_path = (
            fixture_dir / "raw" / "maturity_eastmoney_fallback.csv"
        )
        if historical_fb_path.exists():
            maturity_fb_raw = pd.read_csv(
                historical_fb_path,
                dtype={"SECURITY_CODE": str},
            )
            freeze_existing_frame(
                maturity_fb_raw,
                raw_dir=raw_dir,
                manifest=source_manifest,
                output_dir=output_dir,
                source="历史正式快照 Raw",
                adapter="historical:maturity_eastmoney_fallback.csv",
                role="maturity_historical_missing_only_fallback",
                filename="maturity_historical_eastmoney_fallback.csv",
                degraded=True,
            )
            maturity_fb = maturity_fb_raw.rename(
                columns={
                    "SECURITY_CODE": "bond_code",
                    "EXPIRE_DATE": "maturity_date",
                }
            )[["bond_code", "maturity_date"]].copy()
            maturity_fb["bond_code"] = (
                maturity_fb["bond_code"].astype(str).str.zfill(6)
            )
            maturity_fb["maturity_date"] = pd.to_datetime(
                maturity_fb["maturity_date"],
                errors="coerce",
            )
            resolved_map = (
                maturity_fb.dropna(subset=["maturity_date"])
                .drop_duplicates("bond_code")
                .set_index("bond_code")["maturity_date"]
            )
            miss_mask = enriched["maturity_date"].isna()
            enriched.loc[miss_mask, "maturity_date"] = enriched.loc[
                miss_mask, "bond_code"
            ].map(resolved_map)

    if snapshot_mode not in {"REPLAY_TEST", "HISTORICAL_REPLAY"} and len(maturity_missing):
        for code in maturity_missing["bond_code"].astype(str):
            try:
                one = ak.bond_zh_cov_info(symbol=code, indicator="基本信息").copy()
                if len(one):
                    one["_requested_bond_code"] = code
                    fallback_rows.append(one)
            except Exception as exc:
                fallback_errors.append(f"{code}: {type(exc).__name__}")

        if fallback_rows:
            maturity_fb_raw = pd.concat(fallback_rows, ignore_index=True)
            freeze_existing_frame(
                maturity_fb_raw,
                raw_dir=raw_dir,
                manifest=source_manifest,
                output_dir=output_dir,
                source="东方财富单券可转债详情",
                adapter="ak.bond_zh_cov_info(indicator=基本信息)",
                role="maturity_missing_only_fallback",
                filename="maturity_eastmoney_fallback.csv",
                degraded=True,
            )
            maturity_fb = maturity_fb_raw.rename(
                columns={
                    "SECURITY_CODE": "bond_code",
                    "EXPIRE_DATE": "maturity_date",
                }
            )[["bond_code", "maturity_date"]].copy()
            maturity_fb["bond_code"] = maturity_fb["bond_code"].astype(str).str.zfill(6)
            maturity_fb["maturity_date"] = pd.to_datetime(
                maturity_fb["maturity_date"], errors="coerce"
            )
            resolved_map = maturity_fb.dropna(subset=["maturity_date"]).drop_duplicates(
                "bond_code"
            ).set_index("bond_code")["maturity_date"]
            miss_mask = enriched["maturity_date"].isna()
            enriched.loc[miss_mask, "maturity_date"] = enriched.loc[
                miss_mask, "bond_code"
            ].map(resolved_map)

    duration_sample = enriched[enriched["maturity_date"].notna()].copy()
    maturity_missing = enriched[enriched["maturity_date"].isna()].copy()
    s3.metrics.update(
        {
            "base_sample": len(base),
            "maturity_covered": len(duration_sample),
            "maturity_missing": len(maturity_missing),
            "maturity_fallback_resolved": int(len(fallback_rows)),
        }
    )
    s3.details = {
        "notes": [
            "到期日主源：同花顺可转债基础信息。T 由程序根据到期日与市场截面日期自行计算。",
            "只有主源缺失对象才按需调用东方财富单券详情，不对全市场逐券重复请求。",
            *([f"单券 fallback 失败：{', '.join(fallback_errors[:10])}"] if fallback_errors else []),
        ],
        "tables": [
            table(
                "缺少到期日的对象",
                [("bond_code", "代码"), ("bond_name", "名称")],
                records(maturity_missing, ["bond_code", "bond_name"]),
            )
        ] if len(maturity_missing) else [],
    }
    if not len(duration_sample):
        return fail(result, s3, output_dir, "没有样本取得有效到期日，期限样本无法建立。")
    s3.status = "PASS" if len(duration_sample) == len(base) else "WARNING"
    s3.warnings = [] if s3.status == "PASS" else ["部分基础样本缺少到期日，仅退出期限/规模样本，不退出基础样本。"]
    s3.conclusion = f"{len(duration_sample)} 只获得有效到期日，可进入期限样本。"
    emit(s3)

    s4 = step(result, "S4", "补充剩余规模")
    try:
        if snapshot_mode == "REPLAY_TEST":
            size_raw = pd.read_csv(
                fixture_dir / "redeem_raw.csv",
                dtype={"代码": str},
            )
            freeze_existing_frame(
                size_raw,
                raw_dir=raw_dir,
                manifest=source_manifest,
                output_dir=output_dir,
                source="固定历史样本",
                adapter="fixture:redeem_raw.csv",
                role="size_replay",
                filename="size_replay_fixture.csv",
            )
        elif snapshot_mode == "HISTORICAL_REPLAY":
            size_path = fixture_dir / "raw" / "size_jisilu.csv"
            if not size_path.exists():
                raise FileNotFoundError(
                    "历史 Acquisition Run 缺少 raw/size_jisilu.csv"
                )
            size_raw = pd.read_csv(
                size_path,
                dtype={"代码": str},
            )
            freeze_existing_frame(
                size_raw,
                raw_dir=raw_dir,
                manifest=source_manifest,
                output_dir=output_dir,
                source="历史正式快照 Raw",
                adapter="historical:size_jisilu.csv",
                role="size_historical_replay",
                filename="size_historical_jisilu.csv",
            )
        else:
            size_raw = fetch_and_freeze(
                lambda: ak.bond_cb_redeem_jsl().copy(),
                s4,
                raw_dir=raw_dir,
                manifest=source_manifest,
                output_dir=output_dir,
                source="集思录强赎列表",
                adapter="ak.bond_cb_redeem_jsl",
                role="remaining_size_primary",
                filename="size_jisilu.csv",
                attempts=3,
                delay_seconds=2.0,
            )
    except Exception as exc:
        return fail(
            result,
            s4,
            output_dir,
            f"剩余规模数据获取失败，自动重试后仍不可用；基础/期限样本已生成，但规模模型无法继续。错误：{type(exc).__name__}",
        )
    size_raw["代码"] = size_raw["代码"].astype(str).str.zfill(6)
    size = size_raw.rename(
        columns={
            "代码": "bond_code",
            "名称": "size_source_name",
            "剩余规模": "remaining_size",
            "规模": "issue_size",
            "现价": "P_aux",
            "正股价": "S_aux",
            "转股价": "K_aux",
            "到期日": "maturity_aux",
        }
    )[
        [
            "bond_code",
            "size_source_name",
            "remaining_size",
            "issue_size",
            "P_aux",
            "S_aux",
            "K_aux",
            "maturity_aux",
        ]
    ].drop_duplicates("bond_code")
    for c in ["remaining_size", "issue_size", "P_aux", "S_aux", "K_aux"]:
        size[c] = pd.to_numeric(size[c], errors="coerce")
    size["maturity_aux"] = pd.to_datetime(size["maturity_aux"], errors="coerce")

    main_codes = set(main["bond_code"])
    size_extra = size[~size["bond_code"].isin(main_codes)].copy()

    enriched = enriched.merge(size, on="bond_code", how="left", validate="one_to_one")
    size_ok = (
        (enriched["remaining_size"] > 0)
        & (enriched["issue_size"] > 0)
        & (enriched["remaining_size"] <= enriched["issue_size"] + 1e-9)
    )
    scale_sample = enriched[enriched["maturity_date"].notna() & size_ok].copy()
    size_problem = enriched[enriched["maturity_date"].notna() & ~size_ok].copy()
    s4.metrics.update(
        {
            "duration_sample": len(duration_sample),
            "scale_sample": len(scale_sample),
            "size_invalid_or_missing": len(size_problem),
            "aux_source_extra": len(size_extra),
        }
    )
    tables = []
    if len(size_problem):
        tables.append(
            table(
                "剩余规模缺失或异常",
                [
                    ("bond_code", "代码"),
                    ("bond_name", "名称"),
                    ("remaining_size", "剩余规模"),
                    ("issue_size", "发行规模"),
                ],
                records(size_problem, ["bond_code", "bond_name", "remaining_size", "issue_size"]),
            )
        )
    if len(size_extra):
        tables.append(
            table(
                "辅助规模源额外对象（不会反向扩展主Universe）",
                [
                    ("bond_code", "代码"),
                    ("size_source_name", "名称"),
                    ("remaining_size", "剩余规模"),
                    ("issue_size", "发行规模"),
                ],
                records(size_extra, ["bond_code", "size_source_name", "remaining_size", "issue_size"]),
            )
        )
    s4.details = {
        "notes": [
            "剩余规模候选源：集思录强赎列表（通过 AKShare 获取）。",
            "辅助源只按主Universe代码补充 Size，不允许自行扩大可转债范围。",
        ],
        "tables": tables,
    }
    if not len(scale_sample):
        return fail(result, s4, output_dir, "没有样本通过剩余规模审计，规模样本无法建立。")
    s4.status = "PASS" if len(scale_sample) == len(duration_sample) else "WARNING"
    s4.warnings = [] if s4.status == "PASS" else ["部分期限样本缺少或存在异常剩余规模，仅退出规模样本。"]
    s4.conclusion = f"{len(scale_sample)} 只通过剩余规模结构审计，可以进入规模样本。"
    emit(s4)

    s5 = step(result, "S5", "多来源交叉审计")
    enriched["calc_CV"] = 100 * enriched["S"] / enriched["K"]
    enriched["cv_diff_ratio"] = (
        (enriched["calc_CV"] - enriched["source_CV"]).abs() / enriched["source_CV"].abs()
    )
    enriched["cv_diff_pct"] = enriched["cv_diff_ratio"] * 100
    enriched["P_aux_diff"] = (enriched["P"] - enriched["P_aux"]).abs()
    enriched["S_aux_diff"] = (enriched["S"] - enriched["S_aux"]).abs()
    enriched["K_aux_diff"] = (enriched["K"] - enriched["K_aux"]).abs()
    enriched["maturity_day_diff"] = (
        enriched["maturity_date"] - enriched["maturity_aux"]
    ).dt.days
    save_frame(enriched, output_dir, "market_input_audit.csv")

    top_cv = enriched.sort_values("cv_diff_ratio", ascending=False).head(10)

    p_diff_nonzero = enriched[
        enriched["P_aux_diff"].notna() & (enriched["P_aux_diff"] > 1e-9)
    ].sort_values("P_aux_diff", ascending=False)
    s_diff_nonzero = enriched[
        enriched["S_aux_diff"].notna() & (enriched["S_aux_diff"] > 1e-9)
    ].sort_values("S_aux_diff", ascending=False)
    k_conflicts = enriched[
        enriched["K_aux_diff"].notna() & (enriched["K_aux_diff"] > 0.001)
    ].sort_values("K_aux_diff", ascending=False)

    maturity_mismatch = enriched[
        enriched["maturity_day_diff"].notna()
        & (enriched["maturity_day_diff"] != 0)
    ].copy()
    maturity_convention = maturity_mismatch[
        maturity_mismatch["maturity_day_diff"].abs() <= 1
    ].copy()
    maturity_material = maturity_mismatch[
        maturity_mismatch["maturity_day_diff"].abs() > 1
    ].copy()

    name_conflicts = enriched[
        enriched["size_source_name"].notna()
        & (
            enriched["bond_name"].astype(str).str.strip()
            != enriched["size_source_name"].astype(str).str.strip()
        )
    ].copy()

    unresolved: list[dict[str, Any]] = []
    for _, row in k_conflicts.iterrows():
        unresolved.append(
            {
                "conflict_id": f"{run_id}:{str(row['bond_code'])}:K",
                "type": "PROGRAM_CONFLICT",
                "field": "K",
                "bond_code": str(row["bond_code"]),
                "bond_name": str(row["bond_name"]),
                "primary_value": None if pd.isna(row["K"]) else float(row["K"]),
                "auxiliary_value": None if pd.isna(row["K_aux"]) else float(row["K_aux"]),
                "difference": None if pd.isna(row["K_aux_diff"]) else float(row["K_aux_diff"]),
                "resolution_required": "确认 market_cutoff 时已经生效的最后一个有效转股价",
            }
        )

    for _, row in maturity_material.iterrows():
        unresolved.append(
            {
                "conflict_id": f"{run_id}:{str(row['bond_code'])}:maturity_date",
                "type": "PROGRAM_CONFLICT",
                "field": "maturity_date",
                "bond_code": str(row["bond_code"]),
                "bond_name": str(row["bond_name"]),
                "primary_value": (
                    None
                    if pd.isna(row["maturity_date"])
                    else pd.Timestamp(row["maturity_date"]).date().isoformat()
                ),
                "auxiliary_value": (
                    None
                    if pd.isna(row["maturity_aux"])
                    else pd.Timestamp(row["maturity_aux"]).date().isoformat()
                ),
                "difference_days": int(row["maturity_day_diff"]),
                "resolution_required": "确认合同到期日及不同数据源日期口径",
            }
        )

    s5.metrics = {
        "cv_diff_ratio_median": float(enriched["cv_diff_ratio"].median()),
        "cv_diff_ratio_max": float(enriched["cv_diff_ratio"].max()),
        "p_cross_source_diff": len(p_diff_nonzero),
        "s_cross_source_diff": len(s_diff_nonzero),
        "k_conflicts": len(k_conflicts),
        "maturity_mismatch": len(maturity_mismatch),
        "maturity_convention_diff": len(maturity_convention),
        "maturity_material_conflict": len(maturity_material),
        "identity_name_conflicts": len(name_conflicts),
        "unresolved_conflicts": len(unresolved),
    }
    s5.details = {
        "notes": [
            "源CV与程序自算CV同时保留；程序自算公式：100 × 正股价 / 转股价。",
            "盘中模式下，不同字段可能存在非原子刷新，因此轻微差异记录为审计信息，不自动覆盖主值。",
        ],
        "tables": [
            table(
                "CV差异最大的10只",
                [
                    ("bond_code", "代码"),
                    ("bond_name", "名称"),
                    ("source_CV", "源CV"),
                    ("calc_CV", "程序自算CV"),
                    ("cv_diff_pct", "差异%"),
                ],
                records(top_cv, ["bond_code", "bond_name", "source_CV", "calc_CV", "cv_diff_pct"], 10),
            ),
            table(
                "转债价格跨源差异",
                [
                    ("bond_code", "代码"),
                    ("bond_name", "名称"),
                    ("P", "主源价格"),
                    ("P_aux", "辅助源价格"),
                    ("P_aux_diff", "绝对差"),
                ],
                records(
                    p_diff_nonzero,
                    ["bond_code", "bond_name", "P", "P_aux", "P_aux_diff"],
                    30,
                ),
            ),
            table(
                "正股价格跨源差异",
                [
                    ("bond_code", "代码"),
                    ("bond_name", "名称"),
                    ("S", "主源正股价"),
                    ("S_aux", "辅助源正股价"),
                    ("S_aux_diff", "绝对差"),
                ],
                records(
                    s_diff_nonzero,
                    ["bond_code", "bond_name", "S", "S_aux", "S_aux_diff"],
                    30,
                ),
            ),
            table(
                "转股价跨源冲突",
                [
                    ("bond_code", "代码"),
                    ("bond_name", "名称"),
                    ("K", "主源转股价"),
                    ("K_aux", "辅助源转股价"),
                    ("K_aux_diff", "绝对差"),
                ],
                records(k_conflicts, ["bond_code", "bond_name", "K", "K_aux", "K_aux_diff"], 30),
            ),
            table(
                "到期日跨源口径差异（前30只）",
                [
                    ("bond_code", "代码"),
                    ("bond_name", "名称"),
                    ("maturity_date", "主口径到期日"),
                    ("maturity_aux", "辅助口径到期日"),
                    ("maturity_day_diff", "相差天数"),
                ],
                records(
                    maturity_mismatch,
                    ["bond_code", "bond_name", "maturity_date", "maturity_aux", "maturity_day_diff"],
                    30,
                ),
            ),
        ],
    }
    acquisition_audit = {
        "run_id": run_id,
        "snapshot_mode": snapshot_mode,
        "market_cutoff": market_cutoff,
        "audit_status": "NEEDS_REVIEW" if unresolved else "PASS",
        "field_coverage": {
            "rows": len(enriched),
            "P_missing": int(enriched["P"].isna().sum()),
            "S_missing": int(enriched["S"].isna().sum()),
            "K_missing": int(enriched["K"].isna().sum()),
            "source_CV_missing": int(enriched["source_CV"].isna().sum()),
            "maturity_missing": int(enriched["maturity_date"].isna().sum()),
            "remaining_size_missing": int(enriched["remaining_size"].isna().sum()),
        },
        "cv_crosscheck": {
            "median_diff_ratio": float(enriched["cv_diff_ratio"].median()),
            "max_diff_ratio": float(enriched["cv_diff_ratio"].max()),
        },
        "cross_source": {
            "P_nonzero_diff_count": len(p_diff_nonzero),
            "S_nonzero_diff_count": len(s_diff_nonzero),
            "K_conflict_count": len(k_conflicts),
            "maturity_mismatch_count": len(maturity_mismatch),
            "maturity_convention_diff_count": len(maturity_convention),
            "maturity_material_conflict_count": len(maturity_material),
            "identity_name_conflict_count": len(name_conflicts),
        },
        "size_sanity": {
            "invalid_or_missing": int(len(size_problem)),
            "scale_sample": int(len(scale_sample)),
        },
        "warnings": [],
        "errors": [],
        "unresolved": unresolved,
    }

    if len(maturity_convention):
        acquisition_audit["warnings"].append(
            f"{len(maturity_convention)} 只到期日存在 ±1 天已知口径差，保留主口径并记录审计。"
        )
    if len(p_diff_nonzero):
        acquisition_audit["warnings"].append(
            f"{len(p_diff_nonzero)} 只转债价格存在跨源差异；当前只记录，不设置拍脑袋硬阈值。"
        )
    if len(s_diff_nonzero):
        acquisition_audit["warnings"].append(
            f"{len(s_diff_nonzero)} 只正股价格存在跨源差异；当前只记录，不设置拍脑袋硬阈值。"
        )
    if len(name_conflicts):
        acquisition_audit["warnings"].append(
            f"{len(name_conflicts)} 只债券名称跨源不完全一致，已记录供身份审计。"
        )

    (output_dir / "acquisition_audit.json").write_text(
        json.dumps(acquisition_audit, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    semantic_request_path = output_dir / "semantic_review_request.json"
    if semantic_request_path.exists():
        semantic_request_path.unlink()

    if unresolved:
        semantic_request = {
            "run_id": run_id,
            "market_cutoff": market_cutoff,
            "status": "NEEDS_REVIEW",
            "created_at": now_utc(),
            "requests": [
                {
                    "conflict_id": item["conflict_id"],
                    "bond_code": item["bond_code"],
                    "bond_name": item["bond_name"],
                    "field": item["field"],
                    "primary_value": item.get("primary_value"),
                    "auxiliary_value": item.get("auxiliary_value"),
                    "difference": item.get("difference"),
                    "difference_days": item.get("difference_days"),
                    "resolution_question": item["resolution_required"],
                    "evidence_context": {
                        "source_manifest_ref": "source_manifest.json",
                        "acquisition_audit_ref": "acquisition_audit.json",
                        "raw_dir_ref": "raw/",
                    },
                }
                for item in unresolved
            ],
        }
        semantic_request_path.write_text(
            json.dumps(semantic_request, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    trusted_path = output_dir / "trusted_market_input.csv"
    if trusted_path.exists():
        trusted_path.unlink()

    if not unresolved:
        trusted = enriched[
            [
                "bond_code",
                "bond_name",
                "stock_code",
                "P",
                "S",
                "K",
                "source_CV",
                "calc_CV",
                "maturity_date",
                "remaining_size",
                "issue_size",
                "maturity_day_diff",
                "P_aux_diff",
                "S_aux_diff",
                "size_source_name",
            ]
        ].copy()

        trusted["trusted_S"] = pd.to_numeric(trusted["S"], errors="coerce")
        trusted["trusted_K"] = pd.to_numeric(trusted["K"], errors="coerce")
        trusted["trusted_CV"] = (
            100.0 * trusted["trusted_S"] / trusted["trusted_K"]
        )

        trusted["remaining_months"] = (
            pd.to_datetime(trusted["maturity_date"], errors="coerce")
            - pd.Timestamp(market_cutoff)
        ).dt.days / DAYS_PER_MONTH

        def _warning_flags(row: pd.Series) -> str:
            flags: list[str] = []
            maturity_diff = row.get("maturity_day_diff")
            if pd.notna(maturity_diff) and 0 < abs(float(maturity_diff)) <= 1:
                flags.append("MATURITY_CONVENTION_DIFF")
            p_diff = row.get("P_aux_diff")
            if pd.notna(p_diff) and float(p_diff) > 1e-9:
                flags.append("P_CROSS_SOURCE_DIFF")
            s_diff = row.get("S_aux_diff")
            if pd.notna(s_diff) and float(s_diff) > 1e-9:
                flags.append("S_CROSS_SOURCE_DIFF")
            aux_name = row.get("size_source_name")
            if pd.notna(aux_name) and str(row.get("bond_name", "")).strip() != str(aux_name).strip():
                flags.append("IDENTITY_NAME_DIFF")
            return ";".join(flags)

        trusted["warning_flags"] = trusted.apply(_warning_flags, axis=1)
        trusted["data_status"] = "TRUSTED"
        trusted["source_manifest_ref"] = "source_manifest.json"

        trusted = trusted[
            [
                "bond_code",
                "bond_name",
                "stock_code",
                "P",
                "S",
                "K",
                "trusted_S",
                "trusted_K",
                "source_CV",
                "calc_CV",
                "trusted_CV",
                "maturity_date",
                "remaining_months",
                "remaining_size",
                "issue_size",
                "data_status",
                "warning_flags",
                "source_manifest_ref",
            ]
        ]
        trusted.to_csv(trusted_path, index=False)

    if unresolved:
        s5.status = "NEEDS_REVIEW"
        s5.warnings.append(
            f"程序发现 {len(unresolved)} 个实质数据冲突，已写入 acquisition_audit.json / unresolved，"
            "并生成 semantic_review_request.json；在语义审计解决前不得进入正式计算。"
        )
        s5.conclusion = "确定性审计发现实质冲突，本次数据停在 R2，等待语义审计。"
    else:
        s5.status = (
            "WARNING"
            if snapshot_mode in {"LIVE_TEST", "REPLAY_TEST", "HISTORICAL_REPLAY"}
            else "PASS"
        )
        if snapshot_mode == "LIVE_TEST":
            s5.warnings.append("当前为盘中测试，字段存在异步刷新可能，本次结果不可冻结为正式市场快照。")
        elif snapshot_mode == "REPLAY_TEST":
            s5.warnings.append(
                "当前为固定历史测试样本回放，仅用于单元测试，不生成正式市场快照。"
            )
        elif snapshot_mode == "HISTORICAL_REPLAY":
            s5.warnings.append(
                "当前为历史正式快照回放：只读取当时冻结的 Raw，不联网，不更新正式市场快照。"
            )
        s5.conclusion = "多来源交叉审计完成；未发现需要阻断 Runtime 的实质冲突。"
    emit(s5)

    s6 = step(result, "S6", "数据准备完成")
    if not unresolved and trusted_path.exists():
        support = trusted["trusted_CV"].between(50, 130, inclusive="both").sum()
        core = trusted["trusted_CV"].between(70, 100, inclusive="both").sum()
    else:
        support = enriched["calc_CV"].between(50, 130, inclusive="both").sum()
        core = enriched["calc_CV"].between(70, 100, inclusive="both").sum()
    result.counts = {
        "source_candidate": len(main),
        "base_sample": len(base),
        "duration_sample": len(duration_sample),
        "scale_sample": len(scale_sample),
        "support_zone": int(support),
        "core_zone": int(core),
    }
    s6.metrics = result.counts
    s6.details = {
        "notes": [
            f"基础样本 {len(base)} 只；期限样本 {len(duration_sample)} 只；规模样本 {len(scale_sample)} 只。",
            f"当前支持区间（CV 50–130）{int(support)} 只；核心区间（CV 70–100）{int(core)} 只。",
            (
                "已生成 trusted_market_input.csv，作为后续计算的正式可信输入。"
                if not unresolved
                else "存在 unresolved，未生成 trusted_market_input.csv。"
            ),
        ]
    }
    if unresolved:
        s6.status = "NEEDS_REVIEW"
        s6.warnings = [
            "存在未解决的 PROGRAM_CONFLICT，本次不能形成 Trusted Market Input，也不能进入市场价值映射计算。"
        ]
        s6.conclusion = "数据获取与确定性审计已完成，但存在未解决冲突；等待语义审计后重新生成可信输入。"
    else:
        s6.status = "PASS"
        s6.conclusion = "数据获取与确定性审计完成，可以进入 Trusted Market Input / 市场价值映射计算。"
    emit(s6)

    if any(s.status == "FAIL" for s in result.steps):
        result.status = "FAIL"
    elif any(s.status == "NEEDS_REVIEW" for s in result.steps):
        result.status = "NEEDS_REVIEW"
    elif any(s.status == "WARNING" for s in result.steps):
        result.status = "WARNING"
    else:
        result.status = "PASS"
    result.completed_at = now_utc()
    persist(result, output_dir)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="./runtime_data/latest")
    parser.add_argument(
        "--snapshot-mode",
        choices=["CLOSE", "LIVE_TEST", "REPLAY_TEST", "HISTORICAL_REPLAY"],
        default="LIVE_TEST",
    )
    parser.add_argument("--market-cutoff", default=datetime.now().date().isoformat())
    parser.add_argument("--fixture-dir", default=None)
    args = parser.parse_args()
    result = run_acquisition(
        Path(args.output),
        args.snapshot_mode,
        args.market_cutoff,
        Path(args.fixture_dir) if args.fixture_dir else None,
    )
    print(
        json.dumps(
            {"type": "run_complete", "status": result.status, "run_id": result.run_id},
            ensure_ascii=False,
        ),
        flush=True,
    )
    if result.status == "FAIL":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
