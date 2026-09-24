from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import akshare as ak
import pandas as pd


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
    df.to_csv(out / name, index=False)


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
    name = str(row.get("bond_name", ""))
    if "定转" in name:
        return "定向可转债 / 非公开品种"
    if invalid_number(row.get("P")):
        listing = str(row.get("上市日期", "")).strip()
        if listing in {"", "-", "nan", "NaT", "None"}:
            return "尚未形成有效上市交易价格"
        return "已上市但当前无有效交易价格"
    if invalid_number(row.get("S")):
        return "正股价格缺失或异常"
    if invalid_number(row.get("K")):
        return "转股价缺失或异常"
    return "其他数据完整性异常"


def run_acquisition(output_dir: Path, snapshot_mode: str, market_cutoff: str) -> AcquisitionResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    result = AcquisitionResult(
        run_id=run_id,
        snapshot_mode=snapshot_mode,
        market_cutoff=market_cutoff,
        status="RUNNING",
        started_at=now_utc(),
    )

    s0 = step(result, "S0", "冻结本次运行")
    s0.status = "PASS"
    s0.metrics = {"run_id": run_id, "snapshot_mode": snapshot_mode, "market_cutoff": market_cutoff}
    s0.conclusion = "运行身份和市场时点已冻结，可以开始取数。"
    emit(s0)

    s1 = step(result, "S1", "获取主行情")
    try:
        comp = call_with_retry(lambda: ak.bond_cov_comparison().copy(), s1)
    except Exception as exc:
        return fail(
            result,
            s1,
            output_dir,
            f"主行情获取失败，自动重试后仍无法取得数据；本次运行停在 S1。错误：{type(exc).__name__}",
        )
    comp["转债代码"] = comp["转债代码"].astype(str).str.zfill(6)
    save_frame(comp, output_dir, "main_market_raw.csv")
    s1.metrics.update(
        {
            "candidate_count": len(comp),
            "unique_codes": comp["转债代码"].nunique(),
            "price_covered": int(pd.to_numeric(comp["转债最新价"], errors="coerce").notna().sum()),
        }
    )
    s1.details = {
        "notes": [
            "主数据源：东方财富可转债比价数据（通过 AKShare 获取）。",
            "P / S / K / 源CV 保持在同一主行情快照中，不由其他来源逐字段覆盖。",
        ]
    }
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

    base_mask = (
        main["P"].notna()
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
        info = call_with_retry(lambda: ak.bond_zh_cov_info_ths().copy(), s3)
    except Exception as exc:
        return fail(
            result,
            s3,
            output_dir,
            f"到期日数据获取失败，自动重试后仍不可用；基础样本已生成，但期限/规模模型无法继续。错误：{type(exc).__name__}",
        )
    info["债券代码"] = info["债券代码"].astype(str).str.zfill(6)
    maturity = info.rename(columns={"债券代码": "bond_code", "到期时间": "maturity_date"})[
        ["bond_code", "maturity_date"]
    ].drop_duplicates("bond_code")
    enriched = base.merge(maturity, on="bond_code", how="left", validate="one_to_one")
    enriched["maturity_date"] = pd.to_datetime(enriched["maturity_date"], errors="coerce")
    duration_sample = enriched[enriched["maturity_date"].notna()].copy()
    maturity_missing = enriched[enriched["maturity_date"].isna()].copy()
    s3.metrics.update(
        {
            "base_sample": len(base),
            "maturity_covered": len(duration_sample),
            "maturity_missing": len(maturity_missing),
        }
    )
    s3.details = {
        "notes": ["到期日主源：同花顺可转债基础信息。T 由程序根据到期日与市场截面日期自行计算。"],
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
        size_raw = call_with_retry(lambda: ak.bond_cb_redeem_jsl().copy(), s4)
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
    k_conflicts = enriched[enriched["K_aux_diff"] > 0.001].sort_values("K_aux_diff", ascending=False)
    maturity_mismatch = enriched[
        enriched["maturity_day_diff"].notna() & (enriched["maturity_day_diff"] != 0)
    ].copy()

    s5.metrics = {
        "cv_diff_ratio_median": float(enriched["cv_diff_ratio"].median()),
        "cv_diff_ratio_max": float(enriched["cv_diff_ratio"].max()),
        "k_conflicts": len(k_conflicts),
        "maturity_mismatch": len(maturity_mismatch),
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
    s5.status = "WARNING" if snapshot_mode == "LIVE_TEST" else "PASS"
    if snapshot_mode == "LIVE_TEST":
        s5.warnings.append("当前为盘中测试，字段存在异步刷新可能，本次结果不可冻结为正式市场快照。")
    s5.conclusion = "多来源交叉审计完成；未发现程序必须立即停止的结构性冲突。"
    emit(s5)

    s6 = step(result, "S6", "数据准备完成")
    support = enriched["source_CV"].between(50, 130, inclusive="both").sum()
    core = enriched["source_CV"].between(70, 100, inclusive="both").sum()
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
        ]
    }
    s6.status = "PASS"
    s6.conclusion = "数据获取单元已生成结构化、可审计样本，可以交给市场价值映射计算单元。"
    emit(s6)

    result.status = "WARNING" if any(s.status == "WARNING" for s in result.steps) else "PASS"
    result.completed_at = now_utc()
    persist(result, output_dir)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="./runtime_data/latest")
    parser.add_argument("--snapshot-mode", choices=["CLOSE", "LIVE_TEST"], default="LIVE_TEST")
    parser.add_argument("--market-cutoff", default=datetime.now().date().isoformat())
    args = parser.parse_args()
    result = run_acquisition(Path(args.output), args.snapshot_mode, args.market_cutoff)
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
