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
    """Machine event stream. Web updates the same Step Card by step id."""
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
        except Exception as exc:  # source/network boundary
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

    s0 = step(result, "S0", "Freeze Run")
    s0.status = "PASS"
    s0.metrics = {"run_id": run_id, "snapshot_mode": snapshot_mode, "market_cutoff": market_cutoff}
    s0.conclusion = "运行身份和市场时点已冻结，可以开始取数。"
    emit(s0)

    s1 = step(result, "S1", "Main Market Snapshot")
    try:
        comp = call_with_retry(lambda: ak.bond_cov_comparison().copy(), s1)
    except Exception as exc:
        return fail(
            result, s1, output_dir,
            f"主行情获取失败，自动重试后仍无法取得数据；本次运行停在 S1。错误：{type(exc).__name__}"
        )
    comp["转债代码"] = comp["转债代码"].astype(str).str.zfill(6)
    save_frame(comp, output_dir, "main_market_raw.csv")
    s1.metrics.update({"candidate_count": len(comp), "unique_codes": comp["转债代码"].nunique()})
    s1.status = "PASS"
    s1.warnings = []
    s1.conclusion = f"主行情取得成功，共 {len(comp)} 个候选对象；进入 Universe 分类。"
    emit(s1)

    s2 = step(result, "S2", "Universe Classification")
    main = comp.rename(columns={
        "转债代码": "bond_code", "转债名称": "bond_name", "转债最新价": "P",
        "正股代码": "stock_code", "正股最新价": "S", "转股价": "K", "转股价值": "source_CV",
    }).copy()
    for c in ["P", "S", "K", "source_CV"]:
        main[c] = pd.to_numeric(main[c], errors="coerce")
    base_mask = (
        main["P"].notna() & (main["P"] > 0)
        & main["S"].notna() & (main["S"] > 0)
        & main["K"].notna() & (main["K"] > 0)
    )
    base = main[base_mask].copy()
    excluded = main.loc[~base_mask, ["bond_code", "bond_name", "P", "S", "K"]]
    save_frame(excluded, output_dir, "universe_excluded.csv")
    s2.metrics = {"candidate": len(main), "base_sample": len(base), "excluded": len(excluded)}
    if not len(base):
        return fail(result, s2, output_dir, "没有对象通过 Base Data Gate，本次运行停止。")
    s2.status = "PASS"
    s2.conclusion = f"{len(base)} 只形成可计算 Base 样本；{len(excluded)} 只被 Data Gate 排除。"
    emit(s2)

    s3 = step(result, "S3", "Maturity Enrichment")
    try:
        info = call_with_retry(lambda: ak.bond_zh_cov_info_ths().copy(), s3)
    except Exception as exc:
        return fail(
            result, s3, output_dir,
            f"到期日数据获取失败，自动重试后仍不可用；Base 样本已生成，但 Duration/Scale 无法继续。错误：{type(exc).__name__}"
        )
    info["债券代码"] = info["债券代码"].astype(str).str.zfill(6)
    maturity = info.rename(columns={"债券代码": "bond_code", "到期时间": "maturity_date"})[
        ["bond_code", "maturity_date"]
    ].drop_duplicates("bond_code")
    enriched = base.merge(maturity, on="bond_code", how="left", validate="one_to_one")
    enriched["maturity_date"] = pd.to_datetime(enriched["maturity_date"], errors="coerce")
    duration_sample = enriched[enriched["maturity_date"].notna()].copy()
    s3.metrics.update({
        "base_sample": len(base),
        "maturity_covered": len(duration_sample),
        "maturity_missing": len(base) - len(duration_sample),
    })
    if not len(duration_sample):
        return fail(result, s3, output_dir, "没有样本取得有效到期日，Duration Gate 无法建立。")
    s3.status = "PASS" if len(duration_sample) == len(base) else "WARNING"
    s3.warnings = [] if s3.status == "PASS" else ["部分 Base 样本缺少 maturity，仅退出 Duration/Scale，不退出 Base。"]
    s3.conclusion = f"{len(duration_sample)} 只获得有效到期日，可进入 Duration Gate。"
    emit(s3)

    s4 = step(result, "S4", "Remaining Size Enrichment")
    try:
        size_raw = call_with_retry(lambda: ak.bond_cb_redeem_jsl().copy(), s4)
    except Exception as exc:
        return fail(
            result, s4, output_dir,
            f"剩余规模数据获取失败，自动重试后仍不可用；Base/Duration 已生成，但 Scale 无法继续。错误：{type(exc).__name__}"
        )
    size_raw["代码"] = size_raw["代码"].astype(str).str.zfill(6)
    size = size_raw.rename(columns={"代码": "bond_code", "剩余规模": "remaining_size", "规模": "issue_size"})[
        ["bond_code", "remaining_size", "issue_size"]
    ].drop_duplicates("bond_code")
    size["remaining_size"] = pd.to_numeric(size["remaining_size"], errors="coerce")
    size["issue_size"] = pd.to_numeric(size["issue_size"], errors="coerce")
    enriched = enriched.merge(size, on="bond_code", how="left", validate="one_to_one")
    size_ok = (
        (enriched["remaining_size"] > 0)
        & (enriched["issue_size"] > 0)
        & (enriched["remaining_size"] <= enriched["issue_size"] + 1e-9)
    )
    scale_sample = enriched[enriched["maturity_date"].notna() & size_ok].copy()
    s4.metrics.update({
        "duration_sample": len(duration_sample),
        "scale_sample": len(scale_sample),
        "size_invalid_or_missing": len(duration_sample) - len(scale_sample),
    })
    if not len(scale_sample):
        return fail(result, s4, output_dir, "没有样本通过 Size Gate，Scale Sample 无法建立。")
    s4.status = "PASS" if len(scale_sample) == len(duration_sample) else "WARNING"
    s4.warnings = [] if s4.status == "PASS" else ["部分 Duration 样本缺少/异常 Size，仅退出 Scale。"]
    s4.conclusion = f"{len(scale_sample)} 只通过 Size 结构审计，可以进入 Scale Gate。"
    emit(s4)

    s5 = step(result, "S5", "Cross-source Audit")
    enriched["calc_CV"] = 100 * enriched["S"] / enriched["K"]
    enriched["cv_diff_ratio"] = (
        (enriched["calc_CV"] - enriched["source_CV"]).abs() / enriched["source_CV"].abs()
    )
    save_frame(enriched, output_dir, "market_input_audit.csv")
    s5.metrics = {
        "cv_diff_ratio_median": float(enriched["cv_diff_ratio"].median()),
        "cv_diff_ratio_max": float(enriched["cv_diff_ratio"].max()),
    }
    s5.status = "WARNING" if snapshot_mode == "LIVE_TEST" else "PASS"
    if snapshot_mode == "LIVE_TEST":
        s5.warnings.append("盘中字段存在异步刷新可能，本次结果不可冻结为正式 Market Snapshot。")
    s5.conclusion = "交叉审计完成；正式模型应优先使用 CLOSE Snapshot。"
    emit(s5)

    s6 = step(result, "S6", "Acquisition Ready")
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
    s6.status = "PASS"
    s6.conclusion = "Acquisition Unit 已生成结构化样本，可以交给 Market Map Calculation。"
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
        json.dumps({"type": "run_complete", "status": result.status, "run_id": result.run_id},
                   ensure_ascii=False),
        flush=True,
    )
    if result.status == "FAIL":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
