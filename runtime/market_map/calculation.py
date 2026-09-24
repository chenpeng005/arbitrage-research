from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import statsmodels.api as sm


MODEL_VERSION = "market-map-calculation-v0.2"
HUBER_T = 1.345
RLM_MAXITER = 200
SUPPORT_MIN = 50.0
SUPPORT_MAX = 130.0
CORE_MIN = 70.0
CORE_MAX = 100.0
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
class CalculationResult:
    run_id: str
    market_cutoff: str
    model_version: str
    status: str
    started_at: str
    input_dir: str
    completed_at: str | None = None
    steps: list[Step] = field(default_factory=list)
    snapshot_path: str | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def emit(step: Step) -> None:
    print(json.dumps({"type": "step", **asdict(step)}, ensure_ascii=False), flush=True)


def step(run: CalculationResult, sid: str, name: str) -> Step:
    item = Step(id=sid, name=name, status="RUNNING")
    run.steps.append(item)
    emit(item)
    return item


def persist(run: CalculationResult, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "calculation_result.json").write_text(
        json.dumps(asdict(run), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def fail(
    run: CalculationResult,
    current: Step,
    output_dir: Path,
    message: str,
) -> CalculationResult:
    current.status = "FAIL"
    current.conclusion = message
    run.errors.append(message)
    run.status = "FAIL"
    run.completed_at = now_utc()
    emit(current)
    persist(run, output_dir)
    return run


def finite_dict(values: dict[str, float]) -> bool:
    return all(math.isfinite(float(v)) for v in values.values())


def mae(actual: pd.Series, predicted: pd.Series) -> float:
    return float(np.mean(np.abs(actual.to_numpy() - predicted.to_numpy())))


def huber_fit(y: pd.Series, x: pd.DataFrame):
    model = sm.RLM(
        y.astype(float).to_numpy(),
        x.astype(float).to_numpy(),
        M=sm.robust.norms.HuberT(t=HUBER_T),
    )
    return model.fit(maxiter=RLM_MAXITER, tol=1e-10)


def fit_iteration_count(fit: Any) -> int | None:
    try:
        return int(fit.fit_history.get("iteration"))
    except Exception:
        return None


def fit_converged(fit: Any) -> tuple[bool, int | None]:
    iteration = fit_iteration_count(fit)
    if iteration is None:
        return False, None
    return iteration < RLM_MAXITER, iteration


def coef_table(names: list[str], params: np.ndarray) -> list[dict[str, Any]]:
    return [{"parameter": n, "value": float(v)} for n, v in zip(names, params)]


def percentile_dict(series: pd.Series) -> dict[str, float]:
    return {
        "q25": float(series.quantile(0.25)),
        "q50": float(series.quantile(0.50)),
        "q75": float(series.quantile(0.75)),
    }


def fit_base(support: pd.DataFrame):
    z = (support["source_CV"] - 90.0) / 20.0
    x = pd.DataFrame(
        {
            "const": 1.0,
            "z": z,
            "z2": z * z,
        },
        index=support.index,
    )
    fit = huber_fit(support["P"], x)
    return fit


def base_predict(cv: pd.Series | np.ndarray, params: np.ndarray) -> np.ndarray:
    cv_arr = np.asarray(cv, dtype=float)
    z = (cv_arr - 90.0) / 20.0
    return params[0] + params[1] * z + params[2] * z * z


def fit_duration(core: pd.DataFrame):
    x_norm = (core["remaining_months"] - 36.0) / 24.0
    x = pd.DataFrame({"const": 1.0, "t_norm": x_norm}, index=core.index)
    return huber_fit(core["residual_base"], x)


def duration_predict(months: pd.Series | np.ndarray, params: np.ndarray) -> np.ndarray:
    m = np.asarray(months, dtype=float)
    return params[0] + params[1] * ((m - 36.0) / 24.0)


def fit_scale_neutral(core: pd.DataFrame):
    ln_size = np.log(core["remaining_size"].astype(float))
    x = pd.DataFrame({"const": 1.0, "ln_size": ln_size}, index=core.index)
    return huber_fit(core["residual_after_duration"], x)


def scale_neutral_predict(size: pd.Series | np.ndarray, params: np.ndarray) -> np.ndarray:
    s = np.asarray(size, dtype=float)
    return params[0] + params[1] * np.log(s)


def fit_scale_conservative(core: pd.DataFrame) -> float:
    x = np.maximum(0.0, np.log(core["remaining_size"].astype(float).to_numpy() / 20.0))
    y = core["residual_after_duration"].astype(float).to_numpy()
    active = x > 0
    if int(active.sum()) < 2:
        return float("nan")
    xa = x[active]
    ya = y[active]
    denom = float(np.dot(xa, xa))
    if denom <= 0:
        return float("nan")
    # Candidate diagnostic only: constrained one-sided slope, never positive.
    k = float(np.dot(xa, ya) / denom)
    return min(0.0, k)


def shape_gate(params: np.ndarray) -> tuple[bool, float]:
    grid = np.linspace(CORE_MIN, CORE_MAX, 301)
    pred = base_predict(grid, params)
    diffs = np.diff(pred)
    return bool(np.all(diffs >= -1e-9)), float(diffs.min())


def table(
    title: str,
    columns: list[tuple[str, str]],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "title": title,
        "columns": [{"key": k, "label": label} for k, label in columns],
        "rows": rows,
    }


def records(df: pd.DataFrame, columns: list[str], limit: int = 30) -> list[dict[str, Any]]:
    view = df[[c for c in columns if c in df.columns]].head(limit)
    return json.loads(view.to_json(orient="records", date_format="iso", force_ascii=False))


def run_calculation(
    input_dir: Path,
    output_dir: Path,
    market_cutoff: str,
) -> CalculationResult:
    input_dir = input_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    result = CalculationResult(
        run_id=run_id,
        market_cutoff=market_cutoff,
        model_version=MODEL_VERSION,
        status="RUNNING",
        started_at=now_utc(),
        input_dir=str(input_dir),
    )

    c0 = step(result, "C0", "读取已审计输入")
    trusted_path = input_dir / "trusted_market_input.csv"
    legacy_path = input_dir / "market_input_audit.csv"
    acquisition_path = input_dir / "acquisition_result.json"

    acquisition = {}
    if acquisition_path.exists():
        acquisition = json.loads(acquisition_path.read_text(encoding="utf-8"))
        if acquisition.get("status") not in {"PASS", "WARNING"}:
            return fail(
                result,
                c0,
                output_dir,
                f"上游数据获取单元状态为 {acquisition.get('status')}，禁止进入计算。",
            )

    if trusted_path.exists():
        input_path = trusted_path
        input_contract = "TRUSTED_MARKET_INPUT"
    elif legacy_path.exists():
        input_path = legacy_path
        input_contract = "LEGACY_AUDIT_INPUT"
        c0.warnings.append(
            "未找到 trusted_market_input.csv；当前使用兼容模式读取旧 market_input_audit.csv。"
        )
    else:
        return fail(
            result,
            c0,
            output_dir,
            f"找不到 Trusted Market Input 或兼容审计输入：{input_dir}",
        )

    df = pd.read_csv(input_path, dtype={"bond_code": str, "stock_code": str})
    required = ["bond_code", "bond_name", "P", "source_CV", "maturity_date", "remaining_size"]
    missing_cols = [c for c in required if c not in df.columns]
    if missing_cols:
        return fail(result, c0, output_dir, f"输入缺少必要字段：{missing_cols}")

    for col in ["P", "source_CV", "remaining_size"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["maturity_date"] = pd.to_datetime(df["maturity_date"], errors="coerce")
    cutoff = pd.Timestamp(market_cutoff)
    df["remaining_months"] = (df["maturity_date"] - cutoff).dt.days / DAYS_PER_MONTH

    valid = (
        df["P"].notna()
        & df["source_CV"].notna()
        & df["maturity_date"].notna()
        & df["remaining_size"].notna()
        & (df["remaining_months"] > 0)
        & (df["remaining_size"] > 0)
    )
    model_df = df[valid].copy()
    invalid_df = df[~valid].copy()
    c0.metrics = {
        "input_contract": input_contract,
        "input_rows": len(df),
        "model_ready_rows": len(model_df),
        "invalid_rows": len(invalid_df),
    }
    c0.details = {
        "notes": [
            (
                "计算单元读取 trusted_market_input.csv，不重新读取 Raw，也不重新执行 R2 审计。"
                if input_contract == "TRUSTED_MARKET_INPUT"
                else "当前为历史兼容模式：读取旧 market_input_audit.csv。"
            ),
            "剩余期限由程序根据 market_cutoff 与 maturity_date 再次复算，作为模型输入侧校验。",
        ],
        "tables": [
            table(
                "无法进入完整计算的对象",
                [
                    ("bond_code", "代码"),
                    ("bond_name", "名称"),
                    ("P", "转债价格"),
                    ("source_CV", "转股价值"),
                    ("remaining_months", "剩余月数"),
                    ("remaining_size", "剩余规模"),
                ],
                records(
                    invalid_df,
                    ["bond_code", "bond_name", "P", "source_CV", "remaining_months", "remaining_size"],
                ),
            )
        ] if len(invalid_df) else [],
    }
    if not len(model_df):
        return fail(result, c0, output_dir, "没有可用于模型计算的有效样本。")
    c0.status = "WARNING" if input_contract == "LEGACY_AUDIT_INPUT" else "PASS"
    c0.conclusion = (
        f"已读取 {len(df)} 只可信输入，其中 {len(model_df)} 只可进入完整模型计算。"
        if input_contract == "TRUSTED_MARKET_INPUT"
        else f"已读取 {len(df)} 只历史兼容输入，其中 {len(model_df)} 只可进入完整模型计算。"
    )
    emit(c0)

    c1 = step(result, "C1", "划分支持区间与核心区间")
    support = model_df[model_df["source_CV"].between(SUPPORT_MIN, SUPPORT_MAX, inclusive="both")].copy()
    core = model_df[model_df["source_CV"].between(CORE_MIN, CORE_MAX, inclusive="both")].copy()
    c1.metrics = {
        "support_sample": len(support),
        "core_sample": len(core),
        "support_min_cv": SUPPORT_MIN,
        "support_max_cv": SUPPORT_MAX,
        "core_min_cv": CORE_MIN,
        "core_max_cv": CORE_MAX,
    }
    c1.details = {
        "notes": [
            "Support Zone（CV 50–130）用于拟合基础曲线。",
            "Core Zone（CV 70–100）用于评价下修经济翻译，并拟合期限/规模残差函数。",
            "暂不设置拍脑袋的最小样本数硬阈值，只记录样本分布。",
        ]
    }
    if len(support) < 3 or len(core) < 3:
        return fail(result, c1, output_dir, "支持区间或核心区间样本不足，无法继续拟合。")
    c1.status = "PASS"
    c1.conclusion = f"支持区间 {len(support)} 只，核心区间 {len(core)} 只，可以开始拟合基础价值曲线。"
    emit(c1)

    c2 = step(result, "C2", "拟合基础价值曲线")
    try:
        base_fit = fit_base(support)
        base_params = np.asarray(base_fit.params, dtype=float)
    except Exception as exc:
        return fail(result, c2, output_dir, f"基础 Huber 二次回归失败：{type(exc).__name__}")
    base_param_dict = {
        "beta0": float(base_params[0]),
        "beta1": float(base_params[1]),
        "beta2": float(base_params[2]),
    }
    if not finite_dict(base_param_dict):
        return fail(result, c2, output_dir, "基础曲线参数出现 NaN / Inf。")
    model_df["base_anchor"] = base_predict(model_df["source_CV"], base_params)
    model_df["residual_base"] = model_df["P"] - model_df["base_anchor"]
    core = model_df[model_df["source_CV"].between(CORE_MIN, CORE_MAX, inclusive="both")].copy()
    base_core_mae = mae(core["P"], core["base_anchor"])
    c2.metrics = {
        "beta0": base_param_dict["beta0"],
        "beta1": base_param_dict["beta1"],
        "beta2": base_param_dict["beta2"],
        "core_mae": base_core_mae,
        "huber_t": HUBER_T,
    }
    c2.details = {
        "notes": [
            "模型：P = β0 + β1·z + β2·z²，z=(CV-90)/20。",
            "使用 Huber 稳健回归，保留真实样本但降低极端个券的过度影响。",
        ],
        "tables": [
            table(
                "基础曲线参数",
                [("parameter", "参数"), ("value", "数值")],
                coef_table(["β0", "β1", "β2"], base_params),
            )
        ],
    }
    c2.status = "PASS"
    c2.conclusion = f"基础价值曲线拟合完成；核心区间 MAE 为 {base_core_mae:.2f} 元。"
    emit(c2)

    c3 = step(result, "C3", "基础模型硬审计")
    shape_ok, min_step = shape_gate(base_params)
    cv_grid = np.array([70, 80, 90, 100], dtype=float)
    anchor_grid = base_predict(cv_grid, base_params)
    base_converged, fit_iter = fit_converged(base_fit)
    c3.metrics = {
        "fit_parameters_finite": True,
        "fit_converged": base_converged,
        "shape_monotonic": shape_ok,
        "min_grid_increment": min_step,
        "fit_iterations": fit_iter,
    }
    c3.details = {
        "tables": [
            table(
                "核心区间基础价格锚",
                [("cv", "转股价值"), ("anchor", "基础价格锚")],
                [
                    {"cv": float(cv), "anchor": float(anchor)}
                    for cv, anchor in zip(cv_grid, anchor_grid)
                ],
            )
        ]
    }
    if not base_converged:
        return fail(
            result,
            c3,
            output_dir,
            f"Fit Gate 失败：基础 Huber 拟合未在 {RLM_MAXITER} 次迭代内确认收敛。",
        )
    if not shape_ok:
        return fail(result, c3, output_dir, "Shape Gate 失败：CV 70–100 内基础价格锚不是单调不下降。")
    c3.status = "PASS"
    c3.conclusion = "Fit Gate 与 Shape Gate 均通过，基础曲线可以进入下一层期限残差拟合。"
    emit(c3)

    c4 = step(result, "C4", "拟合剩余期限调整")
    core = model_df[model_df["source_CV"].between(CORE_MIN, CORE_MAX, inclusive="both")].copy()
    try:
        dur_fit = fit_duration(core)
        dur_params = np.asarray(dur_fit.params, dtype=float)
    except Exception as exc:
        return fail(result, c4, output_dir, f"期限 Huber 回归失败：{type(exc).__name__}")
    dur_param_dict = {
        "intercept": float(dur_params[0]),
        "slope": float(dur_params[1]),
    }
    if not finite_dict(dur_param_dict):
        return fail(result, c4, output_dir, "期限函数参数出现 NaN / Inf。")
    duration_converged, duration_iter = fit_converged(dur_fit)
    if not duration_converged:
        return fail(
            result,
            c4,
            output_dir,
            f"期限 Fit Gate 失败：Huber 拟合未在 {RLM_MAXITER} 次迭代内确认收敛。",
        )
    model_df["duration_adjustment"] = duration_predict(model_df["remaining_months"], dur_params)
    model_df["anchor_base_duration"] = model_df["base_anchor"] + model_df["duration_adjustment"]
    model_df["residual_after_duration"] = model_df["P"] - model_df["anchor_base_duration"]
    core = model_df[model_df["source_CV"].between(CORE_MIN, CORE_MAX, inclusive="both")].copy()
    duration_core_mae = mae(core["P"], core["anchor_base_duration"])
    duration_improve = base_core_mae - duration_core_mae
    c4.metrics = {
        "fit_converged": duration_converged,
        "fit_iterations": duration_iter,
        "intercept": dur_param_dict["intercept"],
        "slope": dur_param_dict["slope"],
        "base_core_mae": base_core_mae,
        "base_duration_core_mae": duration_core_mae,
        "mae_improvement": duration_improve,
    }
    month_grid = np.array([6, 12, 24, 36, 54, 72], dtype=float)
    c4.details = {
        "notes": [
            "期限函数在 Core Zone 的基础残差上拟合。",
            "形式：DurationAdjustment = a + b × ((T-36)/24)。",
        ],
        "tables": [
            table(
                "期限调整函数示意",
                [("months", "剩余月数"), ("adjustment", "价格调整")],
                [
                    {"months": int(m), "adjustment": float(v)}
                    for m, v in zip(month_grid, duration_predict(month_grid, dur_params))
                ],
            )
        ],
    }
    c4.status = "PASS"
    c4.conclusion = (
        f"期限调整拟合完成；核心区间 MAE 从 {base_core_mae:.2f} 降至 "
        f"{duration_core_mae:.2f} 元，改善 {duration_improve:.2f} 元。"
    )
    emit(c4)

    c5 = step(result, "C5", "拟合剩余规模调整")
    core = model_df[
        model_df["source_CV"].between(CORE_MIN, CORE_MAX, inclusive="both")
        & (model_df["remaining_size"] > 0)
    ].copy()
    try:
        scale_fit = fit_scale_neutral(core)
        scale_params = np.asarray(scale_fit.params, dtype=float)
    except Exception as exc:
        return fail(result, c5, output_dir, f"规模 Huber 回归失败：{type(exc).__name__}")
    scale_param_dict = {
        "intercept": float(scale_params[0]),
        "ln_size_slope": float(scale_params[1]),
    }
    if not finite_dict(scale_param_dict):
        return fail(result, c5, output_dir, "规模函数参数出现 NaN / Inf。")
    scale_converged, scale_iter = fit_converged(scale_fit)
    if not scale_converged:
        return fail(
            result,
            c5,
            output_dir,
            f"规模 Fit Gate 失败：Huber 拟合未在 {RLM_MAXITER} 次迭代内确认收敛。",
        )

    model_df["scale_neutral"] = scale_neutral_predict(model_df["remaining_size"], scale_params)
    model_df["anchor_neutral"] = (
        model_df["base_anchor"] + model_df["duration_adjustment"] + model_df["scale_neutral"]
    )
    model_df["residual_final"] = model_df["P"] - model_df["anchor_neutral"]
    core = model_df[model_df["source_CV"].between(CORE_MIN, CORE_MAX, inclusive="both")].copy()
    scale_core_mae = mae(core["P"], core["anchor_neutral"])
    scale_improve = duration_core_mae - scale_core_mae
    conservative_k = fit_scale_conservative(core)

    c5.metrics = {
        "component_status": "REQUIRED",
        "fit_converged": scale_converged,
        "fit_iterations": scale_iter,
        "intercept": scale_param_dict["intercept"],
        "ln_size_slope": scale_param_dict["ln_size_slope"],
        "base_duration_core_mae": duration_core_mae,
        "with_scale_core_mae": scale_core_mae,
        "mae_improvement": scale_improve,
        "conservative_k": conservative_k,
    }
    size_grid = np.array([2, 5, 10, 20, 50, 100], dtype=float)
    c5.details = {
        "notes": [
            "ScaleNeutral 已按当前 04 规则正式进入中性参考主模型，定位为二阶修正项。",
            "Neutral 使用完整 After-duration Residual ~ ln(Size) 稳健拟合。",
            "Scale 不作为独立筛选门槛；Conservative 仅保留为诊断 / Resolver 研究视角。",
        ],
        "tables": [
            table(
                "Neutral 规模调整示意",
                [("size", "剩余规模（亿）"), ("adjustment", "价格调整")],
                [
                    {"size": float(s), "adjustment": float(v)}
                    for s, v in zip(size_grid, scale_neutral_predict(size_grid, scale_params))
                ],
            )
        ],
    }
    c5.status = "PASS"
    c5.conclusion = (
        f"规模调整拟合完成；核心区间 MAE 从 {duration_core_mae:.2f} 降至 "
        f"{scale_core_mae:.2f} 元，增量改善 {scale_improve:.2f} 元。"
    )
    emit(c5)

    c6 = step(result, "C6", "计算残差分布并冻结快照")
    residual_stats = percentile_dict(core["residual_final"])
    if not finite_dict(residual_stats):
        return fail(result, c6, output_dir, "残差分位数出现 NaN / Inf，禁止冻结 Snapshot.")

    discovery_reference = model_df["anchor_neutral"] + residual_stats["q50"]
    model_df["discovery_reference"] = discovery_reference
    # Compatibility alias for older visualization / fixture readers.
    model_df["discovery_reference_candidate"] = discovery_reference

    top_abs = core.assign(abs_residual=core["residual_final"].abs()).sort_values(
        "abs_residual", ascending=False
    ).head(15)
    model_audit = {
        "run_id": run_id,
        "market_cutoff": market_cutoff,
        "model_version": MODEL_VERSION,
        "status": "PASS",
        "hard_gates": {
            "input_contract": input_contract,
            "base_params_finite": finite_dict(base_param_dict),
            "base_fit_converged": base_converged,
            "base_fit_iterations": fit_iter,
            "shape_gate": shape_ok,
            "duration_params_finite": finite_dict(dur_param_dict),
            "duration_fit_converged": duration_converged,
            "duration_fit_iterations": duration_iter,
            "scale_params_finite": finite_dict(scale_param_dict),
            "scale_fit_converged": scale_converged,
            "scale_fit_iterations": scale_iter,
            "residual_stats_finite": finite_dict(residual_stats),
        },
        "sample_support": {
            "support_sample": int(len(support)),
            "core_sample": int(len(core)),
            "core_remaining_months_min": float(core["remaining_months"].min()),
            "core_remaining_months_max": float(core["remaining_months"].max()),
            "core_remaining_size_min": float(core["remaining_size"].min()),
            "core_remaining_size_max": float(core["remaining_size"].max()),
        },
        "diagnostics": {
            "base_core_mae": base_core_mae,
            "base_duration_core_mae": duration_core_mae,
            "base_duration_scale_core_mae": scale_core_mae,
            "duration_mae_improvement": duration_improve,
            "scale_mae_improvement": scale_improve,
            "q25": residual_stats["q25"],
            "q50": residual_stats["q50"],
            "q75": residual_stats["q75"],
        },
        "drift_audit": {
            "status": "NOT_IMPLEMENTED",
            "note": "跨截面 Drift 只做未来 WARNING 诊断，不属于当前 Hard Gate。",
        },
        "warnings": [],
    }
    model_audit_path = output_dir / "model_audit.json"
    model_audit_path.write_text(
        json.dumps(model_audit, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    acquisition_mode = acquisition.get("snapshot_mode")
    formal_snapshot = (
        acquisition_mode == "CLOSE"
        and input_contract == "TRUSTED_MARKET_INPUT"
        and model_audit["status"] == "PASS"
    )
    snapshot_class = "FORMAL_CLOSE" if formal_snapshot else "TEST_ONLY"

    acquisition_step_warnings: list[str] = []
    for acq_step in acquisition.get("steps", []):
        for warning in acq_step.get("warnings", []) or []:
            acquisition_step_warnings.append(str(warning))

    snapshot = {
        "snapshot_id": f"market-map-{market_cutoff}-{run_id}",
        "market_cutoff": market_cutoff,
        "model_version": MODEL_VERSION,
        "created_at": now_utc(),
        "snapshot_class": snapshot_class,
        "freeze": {
            "formal": formal_snapshot,
            "status": "FROZEN" if formal_snapshot else "TEST_ONLY",
            "rule": "CLOSE + TRUSTED_MARKET_INPUT + MODEL_AUDIT_PASS",
        },
        "model_audit_ref": "model_audit.json",
        "calculated_table_ref": "market_map_calculated.csv",
        "input": {
            "input_dir": str(input_dir),
            "input_contract": input_contract,
            "acquisition_run_id": acquisition.get("run_id"),
            "acquisition_status": acquisition.get("status"),
            "acquisition_snapshot_mode": acquisition_mode,
            "source_manifest_ref": (
                str(input_dir / "source_manifest.json")
                if (input_dir / "source_manifest.json").exists()
                else None
            ),
            "acquisition_audit_ref": (
                str(input_dir / "acquisition_audit.json")
                if (input_dir / "acquisition_audit.json").exists()
                else None
            ),
            "trusted_market_input_ref": (
                str(input_dir / "trusted_market_input.csv")
                if (input_dir / "trusted_market_input.csv").exists()
                else None
            ),
            "acquisition_warnings": acquisition_step_warnings,
        },
        "universe": acquisition.get("counts", {}),
        "zones": {
            "support": [SUPPORT_MIN, SUPPORT_MAX],
            "core": [CORE_MIN, CORE_MAX],
            "support_sample": len(support),
            "core_sample": len(core),
        },
        "components": {
            "base": {
                "status": "REQUIRED",
                "formula": "P = beta0 + beta1*z + beta2*z^2; z=(CV-90)/20",
                "huber_t": HUBER_T,
                "params": base_param_dict,
                "core_mae": base_core_mae,
                "shape_gate": shape_ok,
            },
            "duration": {
                "status": "REQUIRED",
                "formula": "a + b*((T_months-36)/24)",
                "huber_t": HUBER_T,
                "params": dur_param_dict,
                "core_mae_after": duration_core_mae,
            },
            "scale_neutral": {
                "status": "REQUIRED",
                "role": "SECONDARY_CORRECTION",
                "formula": "a + b*ln(Size)",
                "huber_t": HUBER_T,
                "params": scale_param_dict,
                "core_mae_after": scale_core_mae,
            },
            "scale_conservative": {
                "status": "CANDIDATE_DIAGNOSTIC",
                "formula": "k*max(0, ln(Size/20))",
                "k": conservative_k,
            },
        },
        "residual_core_after_scale": residual_stats,
        "discovery_reference": {
            "status": "ACTIVE",
            "formula": "Base + Duration + ScaleNeutral + CoreResidualQ50",
            "q50_role": "CALIBRATION",
            "q50_calibration": residual_stats["q50"],
        },
        "discovery_reference_candidate": {
            "status": "DEPRECATED_ALIAS",
            "formula": "Base + Duration + ScaleNeutral + CoreResidualQ50",
            "q50_calibration": residual_stats["q50"],
        },
        "diagnostics": {
            "base_core_mae": base_core_mae,
            "base_duration_core_mae": duration_core_mae,
            "base_duration_scale_core_mae": scale_core_mae,
            "duration_mae_improvement": duration_improve,
            "scale_mae_improvement": scale_improve,
        },
    }

    snapshot_path = output_dir / "market_map_snapshot.json"
    snapshot_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    model_df.to_csv(output_dir / "market_map_calculated.csv", index=False)

    c6.metrics = {
        "snapshot_class": snapshot_class,
        "formal_snapshot": formal_snapshot,
        "q25": residual_stats["q25"],
        "q50": residual_stats["q50"],
        "q75": residual_stats["q75"],
        "base_mae": base_core_mae,
        "base_duration_mae": duration_core_mae,
        "base_duration_scale_mae": scale_core_mae,
    }
    c6.details = {
        "notes": [
            "Q50 只用于把稳健回归中心校准到当前 Core Zone 的实际市场中位位置。",
            "Q25 / Q75 作为诊断保留，不用于当前 Discovery 门控。",
            "ScaleNeutral 为正式二阶修正；DiscoveryReference 为 Base + Duration + ScaleNeutral + Q50 校准。",
        ],
        "tables": [
            table(
                "Core Zone 最终残差绝对值最大的15只",
                [
                    ("bond_code", "代码"),
                    ("bond_name", "名称"),
                    ("P", "实际价格"),
                    ("source_CV", "转股价值"),
                    ("anchor_neutral", "中性锚"),
                    ("residual_final", "最终残差"),
                ],
                records(
                    top_abs,
                    ["bond_code", "bond_name", "P", "source_CV", "anchor_neutral", "residual_final"],
                    15,
                ),
            )
        ],
    }
    c6.status = "PASS"
    c6.conclusion = (
        (
            "正式 CLOSE 市场价值映射快照已冻结；"
            if formal_snapshot
            else "测试用市场价值映射快照已生成（不会标记为正式 CLOSE）；"
        )
        + f"Core residual Q50={residual_stats['q50']:.2f} 元。"
    )
    emit(c6)

    result.status = "WARNING" if any(s.status == "WARNING" for s in result.steps) else "PASS"
    result.snapshot_path = str(snapshot_path)
    result.completed_at = now_utc()
    persist(result, output_dir)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--market-cutoff", required=True)
    args = parser.parse_args()

    result = run_calculation(
        input_dir=Path(args.input_dir),
        output_dir=Path(args.output),
        market_cutoff=args.market_cutoff,
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
