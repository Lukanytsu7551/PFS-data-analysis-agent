"""Deterministic numeric distribution-drift diagnostics.

The evaluator is intentionally small and dependency-free. It compares two
already validated numeric samples with the two-sample Kolmogorov–Smirnov
distance and a few quantiles for explanation. It does not calculate a
p-value, infer a cause, or turn a small holdout into a production stability
claim.
"""

from __future__ import annotations

from bisect import bisect_right
from math import isfinite
from typing import Any, Iterable, Mapping


class DistributionDriftError(ValueError):
    """Raised when a numeric distribution comparison is malformed."""


_QUANTILES = (0.1, 0.5, 0.9)


def _finite_sample(values: Iterable[Any], *, label: str) -> tuple[float, ...]:
    if isinstance(values, (str, bytes, bytearray)):
        raise DistributionDriftError(f"{label} must be a numeric collection")
    try:
        prepared = tuple(values)
    except TypeError as exc:
        raise DistributionDriftError(f"{label} must be a numeric collection") from exc
    normalized: list[float] = []
    for index, value in enumerate(prepared):
        if isinstance(value, bool):
            raise DistributionDriftError(f"{label}[{index}] must be finite")
        try:
            numeric = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise DistributionDriftError(f"{label}[{index}] must be finite") from exc
        if not isfinite(numeric):
            raise DistributionDriftError(f"{label}[{index}] must be finite")
        normalized.append(numeric)
    if not normalized:
        raise DistributionDriftError(f"{label} must not be empty")
    return tuple(normalized)


def _rounded(value: float) -> float:
    rounded = round(float(value), 4)
    return 0.0 if rounded == 0 else rounded


def _quantile(values: tuple[float, ...], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _relative_shift_pct(reference: float, comparison: float) -> float | None:
    denominator = abs(reference)
    if denominator == 0:
        return 0.0 if comparison == 0 else None
    return abs(comparison - reference) / denominator * 100


def _ks_distance(reference: tuple[float, ...], comparison: tuple[float, ...]) -> float:
    reference_ordered = sorted(reference)
    comparison_ordered = sorted(comparison)
    values = sorted(set(reference_ordered + comparison_ordered))
    distance = 0.0
    for value in values:
        reference_cdf = bisect_right(reference_ordered, value) / len(reference_ordered)
        comparison_cdf = bisect_right(comparison_ordered, value) / len(comparison_ordered)
        distance = max(distance, abs(reference_cdf - comparison_cdf))
    return distance


def evaluate_numeric_distribution_drift(
    reference: Iterable[Any],
    comparison: Iterable[Any],
    *,
    metric_name: str = "value",
    max_ks_distance: float | None = None,
) -> dict[str, Any]:
    """Compare a reference sample with a later comparison sample.

    ``max_ks_distance`` is an optional business threshold in the inclusive
    ``[0, 1]`` range. Without it the result deliberately remains
    ``needs_review``. The returned quantiles are diagnostic only; the
    acceptance check is the KS distance so the rule is explicit and stable.
    """

    reference_values = _finite_sample(reference, label="reference")
    comparison_values = _finite_sample(comparison, label="comparison")
    if max_ks_distance is not None:
        if isinstance(max_ks_distance, bool):
            raise DistributionDriftError("max_ks_distance must be between 0 and 1")
        try:
            max_ks_distance = float(max_ks_distance)
        except (TypeError, ValueError, OverflowError) as exc:
            raise DistributionDriftError("max_ks_distance must be between 0 and 1") from exc
        if not isfinite(max_ks_distance) or not 0 <= max_ks_distance <= 1:
            raise DistributionDriftError("max_ks_distance must be between 0 and 1")

    reference_quantiles = {
        str(probability): _rounded(_quantile(reference_values, probability))
        for probability in _QUANTILES
    }
    comparison_quantiles = {
        str(probability): _rounded(_quantile(comparison_values, probability))
        for probability in _QUANTILES
    }
    shifts = [
        _relative_shift_pct(
            reference_quantiles[str(probability)],
            comparison_quantiles[str(probability)],
        )
        for probability in _QUANTILES
    ]
    finite_shifts = [value for value in shifts if value is not None]
    ks_distance = _rounded(_ks_distance(reference_values, comparison_values))
    status = (
        "needs_review"
        if max_ks_distance is None
        else "pass"
        if ks_distance <= max_ks_distance
        else "fail"
    )
    return {
        "metric": metric_name,
        "method": "two_sample_ks_distance",
        "reference_rows": len(reference_values),
        "comparison_rows": len(comparison_values),
        "ks_distance": ks_distance,
        "reference_quantiles": reference_quantiles,
        "comparison_quantiles": comparison_quantiles,
        "max_abs_quantile_shift_pct": (
            _rounded(max(finite_shifts)) if finite_shifts else None
        ),
        "quality": {
            "status": status,
            "passed": None if max_ks_distance is None else status == "pass",
            "threshold": max_ks_distance,
            "checks": [
                {
                    "metric": "ks_distance",
                    "operator": "<=",
                    "threshold": max_ks_distance,
                    "actual": ks_distance,
                    "passed": status == "pass",
                }
            ]
            if max_ks_distance is not None
            else [],
        },
        "limitations": [
            "KS 距离只描述两个数值样本的分布差异，不判断漂移原因、统计显著性或因果关系。",
            "comparison 样本通常是少量 holdout 月份，小样本分布信号需要结合业务复核。",
        ],
    }


def evaluate_numeric_distribution_drift_columns(
    reference: Mapping[str, Iterable[Any]],
    comparison: Mapping[str, Iterable[Any]],
    *,
    max_ks_distances: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare several numeric columns using independent marginal KS checks.

    This helper deliberately evaluates each metric independently.  It is a
    multi-metric diagnostic, not a joint-distribution test: it does not model
    correlation, interactions, causal drivers, or statistical significance.
    Each column may have its own optional ``[0, 1]`` KS threshold.  If any
    configured column fails, the aggregate fails; an unconfigured column keeps
    the aggregate at ``needs_review`` until a reviewer supplies its limit.
    """

    if not isinstance(reference, Mapping) or not reference:
        raise DistributionDriftError("reference columns must be a non-empty object")
    if not isinstance(comparison, Mapping) or not comparison:
        raise DistributionDriftError("comparison columns must be a non-empty object")
    reference_names = list(reference)
    comparison_names = list(comparison)
    if set(reference_names) != set(comparison_names):
        missing = sorted(set(reference_names) - set(comparison_names), key=str)
        extra = sorted(set(comparison_names) - set(reference_names), key=str)
        details: list[str] = []
        if missing:
            details.append("missing=" + ",".join(str(item) for item in missing))
        if extra:
            details.append("extra=" + ",".join(str(item) for item in extra))
        raise DistributionDriftError("reference/comparison columns must match: " + "; ".join(details))
    if max_ks_distances is not None and not isinstance(max_ks_distances, Mapping):
        raise DistributionDriftError("max_ks_distances must be an object")
    unknown_thresholds = (
        set(max_ks_distances or {}) - set(reference_names)
        if max_ks_distances is not None
        else set()
    )
    if unknown_thresholds:
        raise DistributionDriftError(
            "unsupported distribution threshold: "
            + ", ".join(str(item) for item in sorted(unknown_thresholds, key=str))
        )

    metrics: dict[str, dict[str, Any]] = {}
    for name in reference_names:
        metric_name = str(name or "").strip()
        if not metric_name:
            raise DistributionDriftError("distribution metric names must not be empty")
        metrics[metric_name] = evaluate_numeric_distribution_drift(
            reference[name],
            comparison[name],
            metric_name=metric_name,
            max_ks_distance=(max_ks_distances or {}).get(name),
        )

    statuses = [item["quality"]["status"] for item in metrics.values()]
    aggregate_status = (
        "fail"
        if "fail" in statuses
        else "needs_review"
        if "needs_review" in statuses
        else "pass"
    )
    return {
        "method": "columnwise_two_sample_ks_distance",
        "metrics": metrics,
        "metric_names": list(metrics),
        "quality": {
            "status": aggregate_status,
            "passed": None if aggregate_status == "needs_review" else aggregate_status == "pass",
            "checks": [
                {
                    "metric": name,
                    "operator": "<=",
                    "threshold": item["quality"]["threshold"],
                    "actual": item["ks_distance"],
                    "passed": item["quality"]["passed"],
                }
                for name, item in metrics.items()
                if item["quality"]["threshold"] is not None
            ],
        },
        "limitations": [
            "这里是多个指标的列边际 KS 距离汇总，不是联合分布、相关性或交互项检验。",
            "KS 距离只描述两个数值样本的分布差异，不判断漂移原因、统计显著性或因果关系。",
            "comparison 样本通常是少量 holdout 月份，小样本分布信号需要结合业务复核。",
        ],
    }
