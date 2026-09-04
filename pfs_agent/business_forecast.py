"""Business-shaped demand forecasting acceptance for PFS.

The scenario deliberately stays narrow: it validates a monthly demand series,
fits a selected local time-series analyzer on the training prefix, and scores
the next real observations.  It is not a production forecast, capacity plan,
or ROI promise.  A missing business threshold is reported as ``needs_review``
instead of being treated as an acceptance.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import Any, Mapping

from .business_acceptance import GMV, MONTH, MONTHLY_ORDERS
from .distribution_drift import evaluate_numeric_distribution_drift_columns
from .reporting import DataSnapshot


DEMAND_FORECAST_SCENARIO_ID = "demand_forecast_v1"
DEMAND_FORECAST_REQUIRED_COLUMNS = (MONTH, MONTHLY_ORDERS, GMV)
SUPPORTED_FORECAST_MODELS = (
    "Time_Series_ARIMA",
    "Time_Series_SARIMA",
    "Time_Series_VAR",
    "Time_Series_Prophet",
    "Time_Series_GRU",
)
SUPPORTED_BUSINESS_THRESHOLDS = (
    "max_total_delta_pct",
    "max_holdout_orders_mean_shift_pct",
    "max_holdout_orders_ks_distance",
    "max_holdout_gmv_ks_distance",
    "min_model_wape_lift_pct",
)
FORECAST_MODEL_LABELS = {
    "Time_Series_ARIMA": "ARIMA",
    "Time_Series_SARIMA": "SARIMA",
    "Time_Series_VAR": "VAR",
    "Time_Series_Prophet": "Prophet",
    "Time_Series_GRU": "GRU",
}


class BusinessForecastError(ValueError):
    """Stable, user-actionable failure for a forecast acceptance request."""

    def __init__(self, message: str, *, code: str = "business_forecast_failed") -> None:
        super().__init__(message)
        self.code = code


def _check(
    check_id: str,
    label: str,
    status: str,
    *,
    severity: str,
    evidence: str,
    impact: str = "",
) -> dict[str, str]:
    return {
        "check_id": check_id,
        "label": label,
        "status": status,
        "severity": severity,
        "evidence": evidence,
        "impact": impact,
    }


def _decimal(value: object, *, label: str) -> Decimal:
    try:
        parsed = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError) as exc:
        raise BusinessForecastError(f"invalid numeric value in {label}") from exc
    if not parsed.is_finite():
        raise BusinessForecastError(f"invalid numeric value in {label}")
    return parsed


def _parse_month(value: object) -> tuple[int, int]:
    text = str(value or "").strip()
    try:
        parsed = datetime.strptime(text, "%Y-%m")
    except ValueError as exc:
        raise BusinessForecastError(
            f"forecast month must use YYYY-MM: {text!r}",
            code="business_forecast_date_invalid",
        ) from exc
    return parsed.year, parsed.month


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _base_payload(
    snapshot: DataSnapshot,
    checks: list[dict[str, str]],
    *,
    model_name: str,
    holdout_size: int,
    rolling_origins: int = 2,
    business_thresholds: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    blockers = [item for item in checks if item["status"] == "fail"]
    reviews = [item for item in checks if item["status"] == "needs_review"]
    evidence_id = f"business-snapshot-{snapshot.content_sha256[:16]}"
    return {
        "scenario": {
            "id": DEMAND_FORECAST_SCENARIO_ID,
            "name": "经营需求预测质量验收",
            "question": "用历史订单数据预测下一段需求，模型误差是否达到业务可接受范围？",
            "audience": "经营与供给计划负责人",
            "grain": "每行一个月份",
        },
        "source": {
            "source_id": snapshot.source_id,
            "file_name": snapshot.file_name,
            "worksheet": snapshot.worksheet,
            "content_sha256": snapshot.content_sha256,
            "row_count": snapshot.row_count,
        },
        "assessment": "needs_revision" if blockers else "share_with_caveats",
        "quality": {
            "checks": checks,
            "blocker_count": len(blockers),
            "review_count": len(reviews),
            "passed_count": sum(item["status"] == "pass" for item in checks),
        },
        "model": {
            "id": model_name,
            "label": FORECAST_MODEL_LABELS.get(model_name, model_name),
            "holdout_size": holdout_size,
            "rolling_origins": rolling_origins,
        },
        "business_thresholds": dict(business_thresholds or {}),
        "metrics": None,
        "model_evaluation": None,
        "distribution_drift": None,
        "multivariate_distribution_drift": None,
        "claims": [],
        "evidence": [
            {
                "evidence_id": evidence_id,
                "kind": "tabular_snapshot",
                "source_id": snapshot.source_id,
                "file_name": snapshot.file_name,
                "worksheet": snapshot.worksheet,
                "included_rows": snapshot.row_count,
                "content_sha256": snapshot.content_sha256,
                "locator": (
                    f"{snapshot.file_name}#{snapshot.worksheet}#sha256="
                    f"{snapshot.content_sha256}"
                ),
                "date_from": snapshot.min_date,
                "date_to": snapshot.max_date,
                "columns": list(snapshot.columns),
            }
        ],
        "caveats": [
            "这是上传快照上的本地时间切分验收，不是公司生产预测或供应商模型服务。",
            "末尾 holdout 和有限滚动窗口只代表本次快照的历史区间，不能代表长期稳定性。",
            "模型误差只能提示预测偏差，不能单独推出库存、运力、预算或收入收益。",
            "订单和 GMV 分布漂移使用列边际两样本 KS 距离；holdout 较小时只能作为风险信号，不能代替联合分布、统计显著性或生产监控。",
        ],
    }


def _error_payload(
    snapshot: DataSnapshot,
    checks: list[dict[str, str]],
    *,
    model_name: str,
    holdout_size: int,
    rolling_origins: int = 2,
    business_thresholds: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return _base_payload(
        snapshot,
        checks,
        model_name=model_name,
        holdout_size=holdout_size,
        rolling_origins=rolling_origins,
        business_thresholds=business_thresholds,
    )


def _normalize_business_thresholds(
    thresholds: Mapping[str, Any] | None,
) -> dict[str, float]:
    if thresholds is None:
        return {}
    if not isinstance(thresholds, Mapping):
        raise BusinessForecastError(
            "business_thresholds must be an object",
            code="business_forecast_thresholds_invalid",
        )
    unknown = sorted(set(thresholds) - set(SUPPORTED_BUSINESS_THRESHOLDS))
    if unknown:
        raise BusinessForecastError(
            "unsupported business threshold: " + ", ".join(str(item) for item in unknown),
            code="business_forecast_thresholds_invalid",
        )
    normalized: dict[str, float] = {}
    for key in SUPPORTED_BUSINESS_THRESHOLDS:
        if key not in thresholds:
            continue
        value = thresholds[key]
        try:
            parsed = _decimal(value, label=key)
        except BusinessForecastError as exc:
            raise BusinessForecastError(
                f"{key} must be a finite non-negative number",
                code="business_forecast_thresholds_invalid",
            ) from exc
        if parsed < 0:
            raise BusinessForecastError(
                f"{key} must be a finite non-negative number",
                code="business_forecast_thresholds_invalid",
            )
        if key in {"max_holdout_orders_ks_distance", "max_holdout_gmv_ks_distance"} and parsed > 1:
            raise BusinessForecastError(
                f"{key} must be between 0 and 1",
                code="business_forecast_thresholds_invalid",
            )
        if key == "min_model_wape_lift_pct" and parsed > 100:
            raise BusinessForecastError(
                f"{key} must be between 0 and 100",
                code="business_forecast_thresholds_invalid",
            )
        normalized[key] = float(parsed)
    return normalized


def _threshold_status(
    evaluation: Mapping[str, Any],
    thresholds: Mapping[str, Any] | None,
    *,
    label: str = "holdout",
):
    if thresholds:
        quality = evaluation.get("quality") or {}
        passed = quality.get("passed")
        return (
            "pass" if passed is True else "fail",
            "；".join(
                f"{item.get('metric')} {item.get('operator')} {item.get('threshold')}，实际 {item.get('actual')}"
                for item in quality.get("checks") or []
            )
            or "未生成完整质量检查",
        )
    return "needs_review", f"未配置业务通过阈值，仅报告 {label} 指标"


def _naive_baseline_rows(
    training_values: list[float],
    prediction_rows: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Build a last-observation-carried-forward baseline for a holdout."""
    if not training_values:
        raise BusinessForecastError(
            "naive baseline requires at least one training value",
            code="business_forecast_baseline_failed",
        )
    last_value = float(training_values[-1])
    return [
        {
            "ds": str(row.get("ds") or ""),
            "segment": "holdout",
            "y_actual": float(row["y_actual"]),
            "y_pred": last_value,
        }
        for row in prediction_rows
    ]


def _baseline_comparison(
    model_evaluation: Mapping[str, Any],
    baseline_evaluation: Mapping[str, Any],
    *,
    minimum_lift_pct: float | None,
    label: str,
) -> dict[str, Any]:
    """Compare model WAPE with a transparent last-value baseline."""
    model_wape = (model_evaluation.get("metrics") or {}).get("wape", {}).get("value")
    baseline_wape = (baseline_evaluation.get("metrics") or {}).get("wape", {}).get("value")
    try:
        model_value = float(model_wape) if model_wape is not None else None
        baseline_value = float(baseline_wape) if baseline_wape is not None else None
    except (TypeError, ValueError, OverflowError):
        model_value = None
        baseline_value = None
    if model_value is not None and not isfinite(model_value):
        model_value = None
    if baseline_value is not None and not isfinite(baseline_value):
        baseline_value = None
    if model_value is None or baseline_value is None:
        lift = None
        status = "fail" if minimum_lift_pct is not None else "needs_review"
        reason = "模型或朴素基线未生成 WAPE，无法比较"
    elif baseline_value == 0:
        lift = 0.0 if model_value == 0 else -100.0
        status = (
            "needs_review"
            if minimum_lift_pct is None
            else "pass"
            if lift >= minimum_lift_pct
            else "fail"
        )
        reason = "朴素基线 WAPE 为 0，只能确认模型是否同样无误差"
    else:
        lift = (baseline_value - model_value) / baseline_value * 100
        status = (
            "needs_review"
            if minimum_lift_pct is None
            else "pass"
            if lift >= minimum_lift_pct
            else "fail"
        )
        reason = "模型 WAPE 相对 last-value 朴素基线的改善比例"
    return {
        "method": "last_value_naive",
        "label": label,
        "model_wape_pct": round(model_value, 4) if model_value is not None else None,
        "baseline_wape_pct": round(baseline_value, 4) if baseline_value is not None else None,
        "wape_lift_pct": round(lift, 4) if lift is not None else None,
        "minimum_wape_lift_pct": minimum_lift_pct,
        "quality": {
            "status": status,
            "passed": None if minimum_lift_pct is None else status == "pass",
            "threshold": minimum_lift_pct,
            "checks": (
                [
                    {
                        "metric": "wape_lift_pct",
                        "operator": ">=",
                        "threshold": minimum_lift_pct,
                        "actual": round(lift, 4) if lift is not None else None,
                        "passed": status == "pass",
                    }
                ]
                if minimum_lift_pct is not None
                else []
            ),
        },
        "reason": reason,
        "limitations": [
            "last-value 朴素基线只表示最近一期延续法，不代表完整预算、季节性或供给约束基线。",
            "WAPE 改善是同一 holdout/滚动窗口内的相对指标，不代表生产收益或因果效果。",
        ],
    }


def _baseline_evidence(comparison: Mapping[str, Any]) -> str:
    model_wape = comparison.get("model_wape_pct")
    baseline_wape = comparison.get("baseline_wape_pct")
    lift = comparison.get("wape_lift_pct")
    label = comparison.get("label") or "该评估"
    if model_wape is None or baseline_wape is None or lift is None:
        return f"{label}无法同时生成模型和 last-value 朴素基线 WAPE"
    threshold = comparison.get("minimum_wape_lift_pct")
    threshold_text = (
        f"；业务改善下限 {float(threshold):.2f}%"
        if threshold is not None
        else "；未配置改善下限"
    )
    return (
        f"{label}模型 WAPE {float(model_wape):.2f}%，last-value 朴素基线 "
        f"WAPE {float(baseline_wape):.2f}%，相对改善 {float(lift):+.2f}%"
        f"{threshold_text}"
    )


def evaluate_demand_forecast(
    snapshot: DataSnapshot,
    *,
    model_name: str = "Time_Series_Prophet",
    holdout_size: int = 3,
    rolling_origins: int = 2,
    quality_thresholds: Mapping[str, Any] | None = None,
    business_thresholds: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a bounded, server-side demand forecast acceptance.

    The source is expected to contain one monthly row.  The function fits the
    chosen local analyzer through the Agent's strict temporal-holdout helper,
    so forecast values are generated from the training prefix rather than
    copied from caller-provided predictions.
    """
    model_name = str(model_name or "").strip()
    if model_name not in SUPPORTED_FORECAST_MODELS:
        raise BusinessForecastError(
            f"unsupported forecast model: {model_name}",
            code="business_forecast_model_not_supported",
        )
    if isinstance(holdout_size, bool):
        raise BusinessForecastError(
            "holdout_size must be a positive integer",
            code="business_forecast_holdout_invalid",
        )
    try:
        holdout_size = int(holdout_size)
    except (TypeError, ValueError, OverflowError) as exc:
        raise BusinessForecastError(
            "holdout_size must be a positive integer",
            code="business_forecast_holdout_invalid",
        ) from exc
    if not 1 <= holdout_size <= 12:
        raise BusinessForecastError(
            "holdout_size must be between 1 and 12",
            code="business_forecast_holdout_invalid",
        )
    if isinstance(rolling_origins, bool):
        raise BusinessForecastError(
            "rolling_origins must be a non-negative integer",
            code="business_forecast_rolling_invalid",
        )
    try:
        rolling_origins = int(rolling_origins)
    except (TypeError, ValueError, OverflowError) as exc:
        raise BusinessForecastError(
            "rolling_origins must be a non-negative integer",
            code="business_forecast_rolling_invalid",
        ) from exc
    if not 0 <= rolling_origins <= 4:
        raise BusinessForecastError(
            "rolling_origins must be between 0 and 4",
            code="business_forecast_rolling_invalid",
        )
    if quality_thresholds is not None and not isinstance(quality_thresholds, Mapping):
        raise BusinessForecastError(
            "quality_thresholds must be an object",
            code="business_forecast_thresholds_invalid",
        )
    business_thresholds = _normalize_business_thresholds(business_thresholds)

    missing = [column for column in DEMAND_FORECAST_REQUIRED_COLUMNS if column not in snapshot.columns]
    if missing:
        raise BusinessForecastError(
            "demand forecast columns missing: " + ", ".join(missing),
            code="business_scenario_columns_missing",
        )

    rows = list(snapshot.rows)
    checks: list[dict[str, str]] = []
    checks.append(
        _check(
            "demand_forecast_rows_present",
            "月度需求存在记录",
            "pass" if rows else "fail",
            severity="critical",
            evidence=f"读取 {len(rows)} 行月度需求" if rows else "未读取到月度需求",
            impact="没有真实历史行就无法验收预测" if not rows else "",
        )
    )
    if not rows:
        return _error_payload(
            snapshot,
            checks,
            model_name=model_name,
            holdout_size=holdout_size,
            rolling_origins=rolling_origins,
            business_thresholds=business_thresholds,
        )

    month_keys: list[tuple[int, int]] = []
    invalid_months: list[str] = []
    parsed_rows: list[dict[str, Decimal]] = []
    invalid_values: list[str] = []
    for index, row in enumerate(rows):
        try:
            month_key = _parse_month(row.get(MONTH))
        except BusinessForecastError:
            invalid_months.append(f"{index + 1}:{row.get(MONTH)}")
            month_key = (0, 0)
        month_keys.append(month_key)
        parsed: dict[str, Decimal] = {}
        for column in (MONTHLY_ORDERS, GMV):
            try:
                parsed[column] = _decimal(row.get(column, ""), label=column)
            except BusinessForecastError:
                invalid_values.append(f"{index + 1}:{column}")
        parsed_rows.append(parsed)

    duplicate_months = sorted(
        {f"{year:04d}-{month:02d}" for year, month in month_keys if month_keys.count((year, month)) > 1}
    )
    checks.append(
        _check(
            "demand_month_grain_unique",
            "月份粒度唯一",
            "fail" if duplicate_months else "pass",
            severity="critical",
            evidence=("重复月份：" + "、".join(duplicate_months[:8]))
            if duplicate_months
            else f"{len(rows)} 个月份均唯一",
            impact="重复月份会让训练和 holdout 样本重复" if duplicate_months else "",
        )
    )
    checks.append(
        _check(
            "demand_month_values_valid",
            "月份和需求金额可解析",
            "fail" if invalid_months or invalid_values else "pass",
            severity="critical",
            evidence=("月份异常：" + "、".join(invalid_months[:4]))
            if invalid_months
            else ("数值异常：" + "、".join(invalid_values[:4]))
            if invalid_values
            else "月份为 YYYY-MM，订单量和 GMV 均为有限数值",
            impact="无法建立时间顺序或计算预测误差" if invalid_months or invalid_values else "",
        )
    )

    range_errors: list[str] = []
    if not invalid_values:
        for index, parsed in enumerate(parsed_rows):
            if parsed.get(MONTHLY_ORDERS, Decimal("0")) <= 0:
                range_errors.append(f"{index + 1}:{MONTHLY_ORDERS}<=0")
            if parsed.get(GMV, Decimal("0")) < 0:
                range_errors.append(f"{index + 1}:{GMV}<0")
    checks.append(
        _check(
            "demand_values_in_business_range",
            "订单量和 GMV 范围有效",
            "fail" if range_errors else "pass",
            severity="high",
            evidence=("异常值：" + "、".join(range_errors[:8])) if range_errors else "订单量为正，GMV 非负",
            impact="异常目标会使预测误差失真" if range_errors else "",
        )
    )

    ordered_months = sorted(set(month_keys)) if not invalid_months else []
    gaps: list[str] = []
    if ordered_months:
        for left, right in zip(ordered_months, ordered_months[1:]):
            if _next_month(*left) != right:
                gaps.append(f"{left[0]:04d}-{left[1]:02d}→{right[0]:04d}-{right[1]:02d}")
    checks.append(
        _check(
            "demand_month_sequence_contiguous",
            "月份连续",
            "fail" if gaps else "pass",
            severity="high",
            evidence=("缺口：" + "、".join(gaps[:8])) if gaps else "月份按自然月连续",
            impact="时间缺口会改变预测步长含义" if gaps else "",
        )
    )
    source_months_ordered = not invalid_months and month_keys == ordered_months
    checks.append(
        _check(
            "demand_month_sequence_ordered",
            "源表月份按时间升序排列",
            "pass" if source_months_ordered else "fail",
            severity="high",
            evidence=(
                "源表月份未按升序排列，不能直接建立训练前缀"
                if invalid_months or not source_months_ordered
                else "源表月份按自然月升序排列"
            ),
            impact="乱序月份会让模型把错误顺序当成时间顺序" if not source_months_ordered else "",
        )
    )

    enough_training = len(rows) - holdout_size >= 14
    checks.append(
        _check(
            "demand_forecast_training_window",
            "训练窗口足够",
            "pass" if enough_training else "fail",
            severity="critical",
            evidence=f"总行数 {len(rows)}，holdout {holdout_size}，训练 {max(0, len(rows) - holdout_size)} 行",
            impact="训练段不足以支撑五类模型的统一验收" if not enough_training else "",
        )
    )
    rolling_training_rows = len(rows) - holdout_size * rolling_origins
    rolling_window_ready = rolling_origins == 0 or rolling_training_rows >= 14
    checks.append(
        _check(
            "demand_forecast_rolling_window",
            "滚动回测训练窗口足够",
            "pass" if rolling_window_ready else "fail",
            severity="high",
            evidence=(
                f"{rolling_origins} 个窗口，最早训练段 {max(0, rolling_training_rows)} 行"
                if rolling_origins
                else "未启用滚动回测窗口"
            ),
            impact="最早滚动窗口训练段不足，不能比较窗口误差" if not rolling_window_ready else "",
        )
    )

    distribution_drift = None
    gmv_distribution_drift = None
    multivariate_distribution_drift = None
    baseline_evaluation = None
    rolling_baseline_evaluation = None
    data_ready_for_drift = (
        enough_training
        and not invalid_months
        and not invalid_values
        and not range_errors
        and not duplicate_months
        and not gaps
        and source_months_ordered
    )
    if data_ready_for_drift:
        multivariate_distribution_drift = evaluate_numeric_distribution_drift_columns(
            {
                MONTHLY_ORDERS: [item[MONTHLY_ORDERS] for item in parsed_rows[:-holdout_size]],
                GMV: [item[GMV] for item in parsed_rows[:-holdout_size]],
            },
            {
                MONTHLY_ORDERS: [item[MONTHLY_ORDERS] for item in parsed_rows[-holdout_size:]],
                GMV: [item[GMV] for item in parsed_rows[-holdout_size:]],
            },
            max_ks_distances={
                MONTHLY_ORDERS: business_thresholds.get("max_holdout_orders_ks_distance"),
                GMV: business_thresholds.get("max_holdout_gmv_ks_distance"),
            },
        )
        distribution_drift = multivariate_distribution_drift["metrics"][MONTHLY_ORDERS]
        gmv_distribution_drift = multivariate_distribution_drift["metrics"][GMV]
        for drift, check_id, label in (
            (distribution_drift, "demand_forecast_distribution_drift", "订单"),
            (gmv_distribution_drift, "demand_forecast_gmv_distribution_drift", "GMV"),
        ):
            distribution_status = drift["quality"]["status"]
            distribution_threshold = drift["quality"]["threshold"]
            checks.append(
                _check(
                    check_id,
                    f"训练段与 holdout {label}分布差异可接受",
                    distribution_status,
                    severity="high",
                    evidence=(
                        f"{label}分布两样本 KS 距离 {drift['ks_distance']:.4f}"
                        + (
                            f"，业务上限 {distribution_threshold:.4f}"
                            if distribution_threshold is not None
                            else "；未配置分布漂移护栏"
                        )
                    ),
                    impact=(
                        f"{label}分布差异超过护栏，暂不能作为计划输入"
                        if distribution_status == "fail"
                        else f"请结合样本量和业务事件确认{label}分布变化；未配置 KS 护栏时仅作风险提示"
                        if distribution_status == "needs_review"
                        else ""
                    ),
                )
            )

    payload = _base_payload(
        snapshot,
        checks,
        model_name=model_name,
        holdout_size=holdout_size,
        rolling_origins=rolling_origins,
        business_thresholds=business_thresholds,
    )
    payload["request"] = {
        "scenario": DEMAND_FORECAST_SCENARIO_ID,
        "model": model_name,
        "holdout_size": holdout_size,
        "rolling_origins": rolling_origins,
        "quality_thresholds": dict(quality_thresholds or {}),
        "business_thresholds": dict(business_thresholds),
    }
    if any(item["status"] == "fail" for item in checks):
        return payload

    import pandas as pd

    frame = pd.DataFrame(
        {
            "month": [f"{year:04d}-{month:02d}" for year, month in month_keys],
            "orders_10k": [float(item[MONTHLY_ORDERS]) for item in parsed_rows],
            "gmv_10k": [float(item[GMV]) for item in parsed_rows],
        }
    )
    model_hint = "month,orders_10k,gmv_10k" if model_name == "Time_Series_VAR" else "month"
    options: dict[str, Any] = {
        "evaluation_mode": "temporal_holdout",
        "holdout_size": holdout_size,
        "temporal_alignment": "month",
    }
    if quality_thresholds:
        options["quality_thresholds"] = dict(quality_thresholds)
    try:
        from agent.tools.business.data import _build_temporal_holdout_evaluation

        evaluation = _build_temporal_holdout_evaluation(
            model_name,
            frame,
            "orders_10k",
            model_hint,
            holdout_size,
            options,
            include_prediction_rows=True,
        )
        final_prediction_rows = evaluation.pop("_prediction_rows", [])
        rolling_evaluation = None
        rolling_folds: list[dict[str, Any]] = []
        rolling_baseline_rows: list[dict[str, Any]] = []
        if rolling_origins:
            from pfs_agent.model_evaluation import evaluate_time_series_rows

            rolling_rows: list[dict[str, Any]] = []
            first_training_end = ""
            for fold_index in range(rolling_origins):
                is_final_fold = fold_index == rolling_origins - 1
                slice_end = len(frame) - holdout_size * (rolling_origins - fold_index - 1)
                if is_final_fold:
                    fold_evaluation = evaluation
                    fold_prediction_rows = final_prediction_rows
                else:
                    fold_evaluation = _build_temporal_holdout_evaluation(
                        model_name,
                        frame.iloc[:slice_end].copy(),
                        "orders_10k",
                        model_hint,
                        holdout_size,
                        options,
                        include_prediction_rows=True,
                    )
                    fold_prediction_rows = fold_evaluation.pop("_prediction_rows", [])
                if len(fold_prediction_rows) != holdout_size:
                    raise ValueError(
                        f"rolling holdout expected {holdout_size} rows, got {len(fold_prediction_rows)}"
                    )
                fold_training_end = slice_end - holdout_size
                fold_training_values = [
                    float(value)
                    for value in frame.iloc[:fold_training_end]["orders_10k"].tolist()
                ]
                rolling_baseline_rows.extend(
                    _naive_baseline_rows(fold_training_values, fold_prediction_rows)
                )
                fold_scope = fold_evaluation.get("time_series") or {}
                first_training_end = first_training_end or str(fold_scope.get("training_end") or "")
                rolling_rows.extend(fold_prediction_rows)
                fold_metrics = fold_evaluation.get("metrics") or {}
                rolling_folds.append(
                    {
                        "fold": fold_index + 1,
                        "training_end": fold_scope.get("training_end", ""),
                        "holdout_period": (
                            f"{fold_scope.get('time_start', '')} 至 "
                            f"{fold_scope.get('time_end', '')}"
                        ),
                        "holdout_rows": fold_evaluation.get("scope", {}).get("paired_rows", 0),
                        "mae_10k": (fold_metrics.get("mae") or {}).get("value"),
                        "wape_pct": (fold_metrics.get("wape") or {}).get("value"),
                        "bias_10k": (fold_metrics.get("bias") or {}).get("value"),
                    }
                )
            rolling_evaluation = evaluate_time_series_rows(
                rolling_rows,
                case_id=f"{model_name}:rolling-temporal-holdout",
                evaluation_segment="holdout",
                training_end=first_training_end,
                thresholds=quality_thresholds,
            )
            rolling_evaluation["time_series"].update(
                {
                    "evaluation_scope": "rolling_temporal_holdout",
                    "rolling_origins": rolling_origins,
                    "folds": rolling_folds,
                }
            )
            rolling_evaluation["limitations"] = [
                *rolling_evaluation.get("limitations", []),
                "每个滚动窗口都在对应历史前缀上重拟合；窗口数量有限，不能替代生产持续监控。",
            ]
            rolling_baseline_evaluation = evaluate_time_series_rows(
                rolling_baseline_rows,
                case_id=f"{model_name}:rolling-last-value-baseline",
                evaluation_segment="holdout",
                training_end=first_training_end,
            )
        training_values = [
            float(value) for value in frame.iloc[:-holdout_size]["orders_10k"].tolist()
        ]
        baseline_rows = _naive_baseline_rows(training_values, final_prediction_rows)
        baseline_evaluation = evaluate_time_series_rows(
            baseline_rows,
            case_id=f"{model_name}:last-value-baseline",
            evaluation_segment="holdout",
            training_end=str((evaluation.get("time_series") or {}).get("training_end") or ""),
        )
    except ImportError as exc:
        checks.append(
            _check(
                "demand_forecast_model_executed",
                "预测模型可执行",
                "fail",
                severity="critical",
                evidence="模型依赖不可用",
                impact=str(exc)[:240],
            )
        )
        return _error_payload(
            snapshot,
            checks,
            model_name=model_name,
            holdout_size=holdout_size,
            rolling_origins=rolling_origins,
            business_thresholds=business_thresholds,
        )
    except Exception as exc:
        checks.append(
            _check(
                "demand_forecast_model_executed",
                "预测模型可执行",
                "fail",
                severity="critical",
                evidence="训练前缀或真实 holdout 评估失败",
                impact=str(exc)[:240],
            )
        )
        return _error_payload(
            snapshot,
            checks,
            model_name=model_name,
            holdout_size=holdout_size,
            rolling_origins=rolling_origins,
            business_thresholds=business_thresholds,
        )

    status, quality_evidence = _threshold_status(evaluation, quality_thresholds)
    if rolling_evaluation is not None:
        rolling_status, rolling_quality_evidence = _threshold_status(
            rolling_evaluation,
            quality_thresholds,
            label="滚动窗口",
        )
    else:
        rolling_status = "needs_review"
        rolling_quality_evidence = "未启用滚动回测窗口，仅报告末尾 holdout 指标"
    checks.append(
        _check(
            "demand_forecast_quality_threshold",
            "预测误差达到业务阈值",
            status,
            severity="high",
            evidence=quality_evidence,
            impact=(
                "当前模型未达到请求的业务误差门槛"
                if status == "fail"
                else "需要业务负责人确认 WAPE/MAE 等阈值后才能作为验收结论"
                if status == "needs_review"
                else ""
            ),
        )
    )
    checks.append(
        _check(
            "demand_forecast_rolling_quality",
            "滚动窗口误差质量",
            rolling_status,
            severity="high",
            evidence=rolling_quality_evidence,
            impact=(
                "滚动窗口未达到请求的业务误差门槛"
                if rolling_status == "fail"
                else "需要业务负责人确认滚动窗口数量和误差阈值"
                if rolling_status == "needs_review"
                else ""
            ),
        )
    )
    summary = (evaluation.get("time_series") or {})
    metrics = evaluation.get("metrics") or {}
    rolling_metrics = (rolling_evaluation or {}).get("metrics") or {}
    rolling_wapes = [
        float(fold["wape_pct"])
        for fold in rolling_folds
        if fold.get("wape_pct") is not None
    ]
    rolling_wape_min = min(rolling_wapes) if rolling_wapes else None
    rolling_wape_max = max(rolling_wapes) if rolling_wapes else None
    actual_total = float(summary.get("holdout_actual_total") or 0.0)
    predicted_total = float(summary.get("holdout_predicted_total") or 0.0)
    delta = predicted_total - actual_total
    total_delta_pct = abs(delta) / actual_total * 100 if actual_total else None
    training_orders = [item[MONTHLY_ORDERS] for item in parsed_rows[:-holdout_size]]
    holdout_orders = [item[MONTHLY_ORDERS] for item in parsed_rows[-holdout_size:]]
    training_orders_mean = sum(training_orders, Decimal("0")) / Decimal(len(training_orders))
    holdout_orders_mean = sum(holdout_orders, Decimal("0")) / Decimal(len(holdout_orders))
    orders_mean_shift_pct = (
        abs(holdout_orders_mean - training_orders_mean) / abs(training_orders_mean) * 100
        if training_orders_mean
        else None
    )
    max_orders_mean_shift_pct = business_thresholds.get("max_holdout_orders_mean_shift_pct")
    if max_orders_mean_shift_pct is None:
        mean_shift_status = "needs_review"
        mean_shift_evidence = (
            f"训练段订单均值 {float(training_orders_mean):.2f} 万单，"
            f"holdout 均值 {float(holdout_orders_mean):.2f} 万单，"
            f"漂移 {float(orders_mean_shift_pct):.2f}%；未配置漂移护栏"
            if orders_mean_shift_pct is not None
            else "训练段订单均值为 0，无法计算漂移"
        )
        mean_shift_impact = "请确认训练段到 holdout 的需求结构变化是否仍适合外推"
    else:
        mean_shift_status = (
            "pass"
            if orders_mean_shift_pct is not None
            and float(orders_mean_shift_pct) <= max_orders_mean_shift_pct
            else "fail"
        )
        mean_shift_evidence = (
            f"订单均值漂移 {float(orders_mean_shift_pct):.2f}% "
            f"{'≤' if mean_shift_status == 'pass' else '>'} 业务上限 {max_orders_mean_shift_pct:.2f}%"
            if orders_mean_shift_pct is not None
            else "训练段订单均值为 0，无法计算漂移"
        )
        mean_shift_impact = (
            "训练段到 holdout 的订单均值漂移超过护栏，暂不能作为计划输入"
            if mean_shift_status == "fail"
            else ""
        )
    checks.append(
        _check(
            "demand_forecast_target_mean_shift",
            "训练段到 holdout 的订单均值漂移可接受",
            mean_shift_status,
            severity="high",
            evidence=mean_shift_evidence,
            impact=mean_shift_impact,
        )
    )
    max_total_delta_pct = business_thresholds.get("max_total_delta_pct")
    if max_total_delta_pct is None:
        total_guard_status = "needs_review"
        total_guard_evidence = (
            "未配置业务总量偏差护栏；"
            f"当前 holdout 预测总量绝对偏差为 {total_delta_pct:.2f}%"
            if total_delta_pct is not None
            else "未能计算 holdout 预测总量绝对偏差"
        )
        total_guard_impact = "请由经营负责人确认预测总量偏差上限后再作为计划输入"
    else:
        total_guard_status = "pass" if total_delta_pct is not None and total_delta_pct <= max_total_delta_pct else "fail"
        total_guard_evidence = (
            f"holdout 预测总量绝对偏差 {total_delta_pct:.2f}% "
            f"{'≤' if total_guard_status == 'pass' else '>'} 业务上限 {max_total_delta_pct:.2f}%"
            if total_delta_pct is not None
            else "holdout 实际总量为 0，无法计算相对偏差"
        )
        total_guard_impact = (
            "预测总量偏差超过业务护栏，暂不能作为计划输入"
            if total_guard_status == "fail"
            else ""
        )
    checks.append(
        _check(
            "demand_forecast_total_volume_guard",
            "预测总量偏差在业务护栏内",
            total_guard_status,
            severity="high",
            evidence=total_guard_evidence,
            impact=total_guard_impact,
        )
    )
    minimum_model_wape_lift_pct = business_thresholds.get("min_model_wape_lift_pct")
    baseline_comparison = _baseline_comparison(
        evaluation,
        baseline_evaluation or {},
        minimum_lift_pct=minimum_model_wape_lift_pct,
        label="末尾 holdout",
    )
    rolling_baseline_comparison = (
        _baseline_comparison(
            rolling_evaluation,
            rolling_baseline_evaluation or {},
            minimum_lift_pct=minimum_model_wape_lift_pct,
            label="滚动窗口",
        )
        if rolling_evaluation is not None
        else None
    )
    baseline_statuses = [baseline_comparison["quality"]["status"]]
    if rolling_baseline_comparison is not None:
        baseline_statuses.append(rolling_baseline_comparison["quality"]["status"])
    baseline_status = (
        "fail"
        if "fail" in baseline_statuses
        else "needs_review"
        if "needs_review" in baseline_statuses
        else "pass"
    )
    baseline_evidence = _baseline_evidence(baseline_comparison)
    if rolling_baseline_comparison is not None:
        baseline_evidence += f"；{_baseline_evidence(rolling_baseline_comparison)}"
    checks.append(
        _check(
            "demand_forecast_model_vs_naive_baseline",
            "模型相对 last-value 朴素基线的 WAPE 改善达到下限",
            baseline_status,
            severity="high",
            evidence=baseline_evidence,
            impact=(
                "模型没有相对于简单延续法达到业务改善下限，暂不能作为计划输入"
                if baseline_status == "fail"
                else "需要业务负责人确认模型相对 last-value 基线的最低改善幅度"
                if baseline_status == "needs_review"
                else ""
            ),
        )
    )
    payload["quality"] = {
        "checks": checks,
        "blocker_count": sum(item["status"] == "fail" for item in checks),
        "review_count": sum(item["status"] == "needs_review" for item in checks),
        "passed_count": sum(item["status"] == "pass" for item in checks),
    }
    payload["assessment"] = (
        "needs_revision"
        if any(item["status"] == "fail" for item in checks)
        else "share_with_caveats"
    )
    payload["multivariate_distribution_drift"] = multivariate_distribution_drift
    payload["baseline_comparison"] = baseline_comparison
    payload["rolling_baseline_comparison"] = rolling_baseline_comparison
    payload["metrics"] = {
        "model": model_name,
        "model_label": FORECAST_MODEL_LABELS[model_name],
        "training_rows": len(rows) - holdout_size,
        "holdout_rows": holdout_size,
        "holdout_period": f"{summary.get('time_start', '')} 至 {summary.get('time_end', '')}",
        "training_end": summary.get("training_end", ""),
        "holdout_actual_orders_10k": round(actual_total, 2),
        "holdout_predicted_orders_10k": round(predicted_total, 2),
        "holdout_delta_10k": round(delta, 2),
        "holdout_abs_delta_pct": round(abs(delta) / actual_total * 100, 2) if actual_total else None,
        "business_threshold_max_total_delta_pct": max_total_delta_pct,
        "business_total_guard_status": total_guard_status,
        "training_orders_mean_10k": round(float(training_orders_mean), 4),
        "holdout_orders_mean_10k": round(float(holdout_orders_mean), 4),
        "holdout_orders_mean_shift_pct": round(float(orders_mean_shift_pct), 4)
        if orders_mean_shift_pct is not None
        else None,
        "business_threshold_max_holdout_orders_mean_shift_pct": max_orders_mean_shift_pct,
        "business_mean_shift_guard_status": mean_shift_status,
        "holdout_orders_ks_distance": (
            distribution_drift["ks_distance"] if distribution_drift is not None else None
        ),
        "business_threshold_max_holdout_orders_ks_distance": (
            distribution_drift["quality"]["threshold"] if distribution_drift is not None else None
        ),
        "business_distribution_guard_status": (
            distribution_drift["quality"]["status"] if distribution_drift is not None else None
        ),
        "holdout_gmv_ks_distance": (
            gmv_distribution_drift["ks_distance"] if gmv_distribution_drift is not None else None
        ),
        "business_threshold_max_holdout_gmv_ks_distance": (
            gmv_distribution_drift["quality"]["threshold"]
            if gmv_distribution_drift is not None
            else None
        ),
        "business_gmv_distribution_guard_status": (
            gmv_distribution_drift["quality"]["status"]
            if gmv_distribution_drift is not None
            else None
        ),
        "multivariate_distribution_guard_status": (
            multivariate_distribution_drift["quality"]["status"]
            if multivariate_distribution_drift is not None
            else None
        ),
        "mae_10k": (metrics.get("mae") or {}).get("value"),
        "rmse_10k": (metrics.get("rmse") or {}).get("value"),
        "bias_10k": (metrics.get("bias") or {}).get("value"),
        "wape_pct": (metrics.get("wape") or {}).get("value"),
        "smape_pct": (metrics.get("smape") or {}).get("value"),
        "quality_passed": (evaluation.get("quality") or {}).get("passed"),
        "rolling_origins": rolling_origins,
        "rolling_holdout_rows": (rolling_evaluation or {}).get("scope", {}).get("paired_rows", 0),
        "rolling_mae_10k": (rolling_metrics.get("mae") or {}).get("value"),
        "rolling_wape_pct": (rolling_metrics.get("wape") or {}).get("value"),
        "rolling_wape_min_pct": round(rolling_wape_min, 4) if rolling_wape_min is not None else None,
        "rolling_wape_max_pct": round(rolling_wape_max, 4) if rolling_wape_max is not None else None,
        "rolling_wape_range_pct": (
            round(rolling_wape_max - rolling_wape_min, 4)
            if rolling_wape_min is not None and rolling_wape_max is not None
            else None
        ),
        "rolling_quality_passed": (rolling_evaluation or {}).get("quality", {}).get("passed"),
        "naive_baseline_wape_pct": baseline_comparison["baseline_wape_pct"],
        "model_wape_lift_vs_naive_pct": baseline_comparison["wape_lift_pct"],
        "business_threshold_min_model_wape_lift_pct": minimum_model_wape_lift_pct,
        "baseline_guard_status": baseline_comparison["quality"]["status"],
        "rolling_naive_baseline_wape_pct": (
            rolling_baseline_comparison["baseline_wape_pct"]
            if rolling_baseline_comparison is not None
            else None
        ),
        "rolling_model_wape_lift_vs_naive_pct": (
            rolling_baseline_comparison["wape_lift_pct"]
            if rolling_baseline_comparison is not None
            else None
        ),
        "rolling_baseline_guard_status": (
            rolling_baseline_comparison["quality"]["status"]
            if rolling_baseline_comparison is not None
            else None
        ),
    }
    payload["business_guardrails"] = {
        "max_total_delta_pct": max_total_delta_pct,
        "actual_abs_total_delta_pct": round(total_delta_pct, 4) if total_delta_pct is not None else None,
        "total_volume_status": total_guard_status,
        "max_holdout_orders_mean_shift_pct": max_orders_mean_shift_pct,
        "actual_holdout_orders_mean_shift_pct": (
            round(float(orders_mean_shift_pct), 4) if orders_mean_shift_pct is not None else None
        ),
        "mean_shift_status": mean_shift_status,
        "max_holdout_orders_ks_distance": (
            distribution_drift["quality"]["threshold"] if distribution_drift is not None else None
        ),
        "actual_holdout_orders_ks_distance": (
            distribution_drift["ks_distance"] if distribution_drift is not None else None
        ),
        "distribution_status": (
            distribution_drift["quality"]["status"] if distribution_drift is not None else None
        ),
        "max_holdout_gmv_ks_distance": (
            gmv_distribution_drift["quality"]["threshold"]
            if gmv_distribution_drift is not None
            else None
        ),
        "actual_holdout_gmv_ks_distance": (
            gmv_distribution_drift["ks_distance"]
            if gmv_distribution_drift is not None
            else None
        ),
        "gmv_distribution_status": (
            gmv_distribution_drift["quality"]["status"]
            if gmv_distribution_drift is not None
            else None
        ),
        "multivariate_distribution_status": (
            multivariate_distribution_drift["quality"]["status"]
            if multivariate_distribution_drift is not None
            else None
        ),
        "min_model_wape_lift_pct": minimum_model_wape_lift_pct,
        "actual_model_wape_lift_vs_naive_pct": baseline_comparison["wape_lift_pct"],
        "baseline_status": baseline_status,
        "status": (
            "fail"
            if "fail"
            in {
                total_guard_status,
                mean_shift_status,
                distribution_drift["quality"]["status"] if distribution_drift is not None else None,
                gmv_distribution_drift["quality"]["status"]
                if gmv_distribution_drift is not None
                else None,
                baseline_status,
            }
            else "needs_review"
            if "needs_review"
            in {
                total_guard_status,
                mean_shift_status,
                distribution_drift["quality"]["status"] if distribution_drift is not None else None,
                gmv_distribution_drift["quality"]["status"]
                if gmv_distribution_drift is not None
                else None,
                baseline_status,
            }
            else "pass"
        ),
    }
    payload["distribution_drift"] = distribution_drift
    payload["model_evaluation"] = {
        "engine": evaluation.get("engine", ""),
        "engine_version": evaluation.get("engine_version", ""),
        "task_type": evaluation.get("task_type", "time_series"),
        "scope": evaluation.get("scope", {}),
        "metrics": metrics,
        "quality": evaluation.get("quality", {}),
        "time_series": summary,
        "rolling": rolling_evaluation,
        "baseline": baseline_evaluation,
        "rolling_baseline": rolling_baseline_evaluation,
    }
    evidence_id = payload["evidence"][0]["evidence_id"]
    payload["claims"] = [
        {
            "claim": (
                f"{FORECAST_MODEL_LABELS[model_name]} 在末尾 {holdout_size} 个月 holdout 的订单量预测为 "
                f"{predicted_total:.2f} 万单，实际为 {actual_total:.2f} 万单，差异 {delta:+.2f} 万单。"
            ),
            "status": "supported_with_caveat",
            "basis": "训练前缀重拟合后，将预测时间戳与源快照末尾真实月份逐一对齐",
            "evidence_ids": [evidence_id],
        },
        {
            "claim": (
                f"该时间切分的 MAE 为 {payload['metrics']['mae_10k']} 万单、"
                f"WAPE 为 {payload['metrics']['wape_pct']}%；"
                + ("已达到请求阈值。" if status == "pass" else "尚未形成业务通过判定。")
            ),
            "status": "supported_with_caveat",
            "basis": "对 holdout 真实订单行计算误差，不把未来 forecast 或训练段混入分母",
            "evidence_ids": [evidence_id],
        },
    ]
    if rolling_evaluation is not None:
        payload["claims"].append(
            {
                "claim": (
                    f"{rolling_origins} 个滚动窗口合计 {payload['metrics']['rolling_holdout_rows']} 行，"
                    f"WAPE 为 {payload['metrics']['rolling_wape_pct']}%，"
                    f"窗口 WAPE 区间为 {payload['metrics']['rolling_wape_min_pct']}% 至 "
                    f"{payload['metrics']['rolling_wape_max_pct']}%。"
                ),
                "status": "supported_with_caveat",
                "basis": "各窗口独立使用历史训练前缀重拟合，并只与其后续真实月份配对",
                "evidence_ids": [evidence_id],
            }
        )
    baseline_claim = _baseline_evidence(baseline_comparison)
    if rolling_baseline_comparison is not None:
        baseline_claim += f"；{_baseline_evidence(rolling_baseline_comparison)}"
    baseline_claim += (
        f"。已按业务改善下限 {minimum_model_wape_lift_pct:.2f}% 判定为通过。"
        if baseline_status == "pass"
        else f"。已按业务改善下限 {minimum_model_wape_lift_pct:.2f}% 判定为未通过。"
        if baseline_status == "fail"
        else "。尚未配置模型改善下限，不能形成模型优于基线的业务准入结论。"
    )
    payload["claims"].append(
        {
            "claim": baseline_claim,
            "status": "supported_with_caveat",
            "basis": (
                "在同一末尾 holdout 和滚动窗口中，用训练段最后一期订单量生成 last-value 预测，"
                "再与模型 WAPE 对比；不把该相对改善解释为生产收益"
            ),
            "evidence_ids": [evidence_id],
        }
    )
    payload["claims"].append(
        {
            "claim": (
                f"末尾 holdout 的订单总量绝对偏差为 {total_delta_pct:.2f}%。"
                if total_delta_pct is not None
                else "末尾 holdout 无法计算订单总量绝对偏差。"
            )
            + (
                f"已按业务护栏 {max_total_delta_pct:.2f}% 判定为通过。"
                if total_guard_status == "pass"
                else f"已按业务护栏 {max_total_delta_pct:.2f}% 判定为未通过。"
                if total_guard_status == "fail"
                else "尚未配置业务总量偏差护栏，不能形成计划准入结论。"
            ),
            "status": "supported_with_caveat",
            "basis": "以 holdout 真实订单总量为分母，比较训练前缀重拟合后的预测总量",
            "evidence_ids": [evidence_id],
        }
    )
    payload["claims"].append(
        {
            "claim": (
                f"训练段到末尾 holdout 的订单均值漂移为 {float(orders_mean_shift_pct):.2f}%。"
                if orders_mean_shift_pct is not None
                else "训练段到末尾 holdout 无法计算订单均值漂移。"
            )
            + (
                f"已按业务护栏 {max_orders_mean_shift_pct:.2f}% 判定为通过。"
                if mean_shift_status == "pass"
                else f"已按业务护栏 {max_orders_mean_shift_pct:.2f}% 判定为未通过。"
                if mean_shift_status == "fail"
                else "尚未配置漂移护栏，该变化只作为风险提示。"
            ),
            "status": "supported_with_caveat",
            "basis": "比较训练前缀与 holdout 真实订单行的均值；这不是完整分布漂移或因果判断",
            "evidence_ids": [evidence_id],
        }
    )
    if distribution_drift is not None:
        distribution_status = distribution_drift["quality"]["status"]
        distribution_threshold = distribution_drift["quality"]["threshold"]
        payload["claims"].append(
            {
                "claim": (
                    "训练段与末尾 holdout 的订单分布两样本 KS 距离为 "
                    f"{distribution_drift['ks_distance']:.4f}。"
                )
                + (
                    f"已按业务护栏 {distribution_threshold:.4f} 判定为通过。"
                    if distribution_status == "pass"
                    else f"已按业务护栏 {distribution_threshold:.4f} 判定为未通过。"
                    if distribution_status == "fail"
                    else "尚未配置 KS 分布漂移护栏，该值只作为小样本风险信号。"
                ),
                "status": "supported_with_caveat",
                "basis": (
                    "对训练段和 holdout 真实订单样本计算两样本 KS 距离，"
                    "并展示 10/50/90 分位数辅助解释"
                ),
                "evidence_ids": [evidence_id],
            }
        )
    if gmv_distribution_drift is not None:
        gmv_distribution_status = gmv_distribution_drift["quality"]["status"]
        gmv_distribution_threshold = gmv_distribution_drift["quality"]["threshold"]
        payload["claims"].append(
            {
                "claim": (
                    "训练段与末尾 holdout 的 GMV 分布两样本 KS 距离为 "
                    f"{gmv_distribution_drift['ks_distance']:.4f}。"
                )
                + (
                    f"已按业务护栏 {gmv_distribution_threshold:.4f} 判定为通过。"
                    if gmv_distribution_status == "pass"
                    else f"已按业务护栏 {gmv_distribution_threshold:.4f} 判定为未通过。"
                    if gmv_distribution_status == "fail"
                    else "尚未配置 GMV KS 分布漂移护栏，该值只作为小样本风险信号。"
                ),
                "status": "supported_with_caveat",
                "basis": (
                    "对训练段和 holdout 真实 GMV 样本计算列边际两样本 KS 距离；"
                    "不代表订单与 GMV 的联合分布或因果关系"
                ),
                "evidence_ids": [evidence_id],
            }
        )
    payload["lineage"] = {
        "target": MONTHLY_ORDERS,
        "time_grain": MONTH,
        "training_prefix": f"按 {MONTH} 排序后排除末尾 {holdout_size} 行",
        "holdout_alignment": "模型预测时间戳必须与源快照末尾真实月份逐一相等；月度场景按自然月校验",
        "rolling_origins": rolling_origins,
            "metrics": "MAE/RMSE/bias/WAPE/sMAPE from pfs_prediction_evaluation",
            "business_guardrails": (
                "holdout 预测总量绝对偏差百分比，与可选 max_total_delta_pct 比较；"
                "训练段到 holdout 的订单均值漂移，与可选 max_holdout_orders_mean_shift_pct 比较；"
                "训练段与 holdout 的订单/GMV 列边际分布分别计算两样本 KS 距离，"
                "与可选 max_holdout_orders_ks_distance / max_holdout_gmv_ks_distance 比较；"
                "模型 WAPE 与 last-value 朴素基线比较，相对改善与可选 min_model_wape_lift_pct 比较；"
                "不做联合分布检验，也不把基线改善解释为生产收益"
            ),
    }
    return payload
