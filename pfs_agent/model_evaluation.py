"""Deterministic local evaluation for model prediction quality.

This module evaluates fixed prediction contracts and stable rows emitted by
local analyzers.  It is intentionally independent of provider calls: a
passing result means that the supplied predictions meet the declared metrics,
not that a production model is accurate or safe for a business decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite, isnan, sqrt
from typing import Any, Iterable, Mapping


class PredictionEvaluationError(ValueError):
    """Raised when a prediction evaluation case or threshold is malformed."""


_TASK_TYPES = frozenset({"classification", "regression", "time_series"})
_CLASSIFICATION_THRESHOLDS = frozenset({"min_accuracy", "min_macro_f1"})
_REGRESSION_THRESHOLDS = frozenset({"max_mae", "max_rmse", "min_r2"})
_TIME_SERIES_THRESHOLDS = _REGRESSION_THRESHOLDS | frozenset(
    {"max_mape", "max_wape", "max_smape"}
)


@dataclass(frozen=True)
class PredictionEvaluationCase:
    """Expected and observed predictions for one fixed evaluation slice."""

    case_id: str
    expected: tuple[Any, ...]
    predicted: tuple[Any, ...]

    def __post_init__(self) -> None:
        case_id = str(self.case_id or "").strip()
        if not case_id:
            raise PredictionEvaluationError("prediction case_id must not be empty")
        expected = _as_sequence(self.expected, label="expected")
        predicted = _as_sequence(self.predicted, label="predicted")
        if not expected:
            raise PredictionEvaluationError("prediction cases must not be empty")
        if len(expected) != len(predicted):
            raise PredictionEvaluationError(
                f"prediction case {case_id} expected and predicted lengths must match"
            )
        object.__setattr__(self, "case_id", case_id)
        object.__setattr__(self, "expected", expected)
        object.__setattr__(self, "predicted", predicted)

    @property
    def sample_count(self) -> int:
        return len(self.expected)

    def to_dict(self) -> dict[str, Any]:
        """Return metadata only; raw labels and values are not echoed."""
        return {"case_id": self.case_id, "sample_count": self.sample_count}


@dataclass(frozen=True)
class TimeSeriesEvaluationInput:
    """A paired historical slice extracted from a forecast result table."""

    case: PredictionEvaluationCase
    total_rows: int
    excluded_forecast_rows: int
    excluded_unpaired_rows: int
    timestamps: tuple[str, ...] = ()
    excluded_scope_rows: int = 0
    evaluation_segment: str = ""
    training_end: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.case, PredictionEvaluationCase):
            raise PredictionEvaluationError("time-series case must be a PredictionEvaluationCase")
        if self.total_rows < self.case.sample_count:
            raise PredictionEvaluationError("time-series total_rows cannot be smaller than paired rows")
        if self.excluded_forecast_rows < 0 or self.excluded_unpaired_rows < 0:
            raise PredictionEvaluationError("time-series excluded row counts must be non-negative")
        if self.excluded_scope_rows < 0:
            raise PredictionEvaluationError("time-series excluded scope rows must be non-negative")
        if self.total_rows != (
            self.case.sample_count
            + self.excluded_forecast_rows
            + self.excluded_unpaired_rows
            + self.excluded_scope_rows
        ):
            raise PredictionEvaluationError(
                "time-series row counts must account for every input row"
            )
        if self.evaluation_segment and not self.training_end:
            raise PredictionEvaluationError(
                "time-series holdout evaluation requires training_end"
            )

    @property
    def paired_rows(self) -> int:
        return self.case.sample_count

    @property
    def eligible_rows(self) -> int:
        """Rows that belong to the selected evaluation scope."""
        return self.paired_rows + self.excluded_unpaired_rows

    @property
    def paired_ratio(self) -> float:
        return round(self.paired_rows / self.eligible_rows, 4) if self.eligible_rows else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case.case_id,
            "total_rows": self.total_rows,
            "paired_rows": self.paired_rows,
            "eligible_rows": self.eligible_rows,
            "excluded_forecast_rows": self.excluded_forecast_rows,
            "excluded_unpaired_rows": self.excluded_unpaired_rows,
            "excluded_scope_rows": self.excluded_scope_rows,
            "evaluation_segment": self.evaluation_segment,
            "training_end": self.training_end,
            "paired_ratio": self.paired_ratio,
            "time_start": self.timestamps[0] if self.timestamps else "",
            "time_end": self.timestamps[-1] if self.timestamps else "",
        }


def _as_sequence(value: Any, *, label: str) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes, bytearray)):
        raise PredictionEvaluationError(f"prediction {label} must be a collection")
    try:
        return tuple(value)
    except TypeError as exc:
        raise PredictionEvaluationError(f"prediction {label} must be a collection") from exc


def _as_prediction_rows(rows: Any) -> tuple[Mapping[str, Any], ...]:
    """Normalize a list of row mappings or a pandas-like records object."""
    to_dict = getattr(rows, "to_dict", None)
    if callable(to_dict):
        try:
            rows = to_dict(orient="records")
        except TypeError as exc:
            raise PredictionEvaluationError(
                "prediction rows object must support to_dict(orient='records')"
            ) from exc
    if isinstance(rows, (str, bytes, bytearray, Mapping)):
        raise PredictionEvaluationError("prediction rows must be a collection of mappings")
    try:
        prepared = tuple(rows)
    except TypeError as exc:
        raise PredictionEvaluationError("prediction rows must be a collection of mappings") from exc
    normalized: list[Mapping[str, Any]] = []
    for index, row in enumerate(prepared):
        if not isinstance(row, Mapping):
            raise PredictionEvaluationError(f"prediction row {index} must be a mapping")
        normalized.append(row)
    return tuple(normalized)


def _rounded(value: float | None) -> float | None:
    if value is None:
        return None
    rounded = round(float(value), 4)
    return 0.0 if rounded == 0 else rounded


def _metric(value: float | None, *, sample_count: int) -> dict[str, Any]:
    return {"value": _rounded(value), "sample_count": sample_count}


def _label_key(value: Any) -> str:
    """Create a collision-resistant, deterministic key for metric output."""
    try:
        hash(value)
    except TypeError as exc:
        raise PredictionEvaluationError("classification labels must be hashable") from exc
    return f"{type(value).__name__}:{value!r}"


def _validate_task_type(task_type: str) -> str:
    normalized = str(task_type or "").strip().lower()
    if normalized not in _TASK_TYPES:
        raise PredictionEvaluationError(
            "task_type must be classification, regression, or time_series"
        )
    return normalized


def _validate_numeric(value: Any, *, label: str, case_id: str, index: int) -> float:
    if isinstance(value, bool):
        raise PredictionEvaluationError(
            f"{label} value at {case_id}[{index}] must be a finite number"
        )
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PredictionEvaluationError(
            f"{label} value at {case_id}[{index}] must be a finite number"
        ) from exc
    if not isfinite(numeric):
        raise PredictionEvaluationError(
            f"{label} value at {case_id}[{index}] must be a finite number"
        )
    return numeric


def _classification_metrics(
    expected: tuple[Any, ...], predicted: tuple[Any, ...]
) -> dict[str, Any]:
    expected_keys = tuple(_label_key(value) for value in expected)
    predicted_keys = tuple(_label_key(value) for value in predicted)
    labels = sorted(set(expected_keys) | set(predicted_keys))
    confusion_matrix: dict[str, dict[str, int]] = {
        actual: {observed: 0 for observed in labels} for actual in labels
    }
    for actual, observed in zip(expected_keys, predicted_keys):
        confusion_matrix[actual][observed] += 1
    return _classification_metrics_from_confusion(confusion_matrix)


def _classification_metrics_from_confusion(
    confusion_matrix: Mapping[str, Mapping[str, int]],
) -> dict[str, Any]:
    labels = sorted(set(confusion_matrix))
    if not labels:
        raise PredictionEvaluationError("classification confusion rows must not be empty")
    total = sum(sum(int(value) for value in confusion_matrix[actual].values()) for actual in labels)
    if total <= 0:
        raise PredictionEvaluationError("classification confusion rows must contain a positive count")
    correct = sum(int(confusion_matrix[label].get(label, 0)) for label in labels)
    per_label: dict[str, dict[str, Any]] = {}
    f1_values: list[float] = []
    recall_values: list[float] = []

    for label in labels:
        true_positive = int(confusion_matrix[label].get(label, 0))
        false_positive = sum(
            int(confusion_matrix[actual].get(label, 0))
            for actual in labels
            if actual != label
        )
        false_negative = sum(
            int(confusion_matrix[label].get(observed, 0))
            for observed in labels
            if observed != label
        )
        precision = (
            true_positive / (true_positive + false_positive)
            if true_positive + false_positive
            else 0.0
        )
        recall = (
            true_positive / (true_positive + false_negative)
            if true_positive + false_negative
            else 0.0
        )
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        f1_values.append(f1)
        recall_values.append(recall)
        per_label[label] = {
            "precision": _rounded(precision),
            "recall": _rounded(recall),
            "f1": _rounded(f1),
            "support": sum(int(value) for value in confusion_matrix[label].values()),
        }

    return {
        "accuracy": _metric(correct / total, sample_count=total),
        "macro_f1": _metric(sum(f1_values) / len(f1_values), sample_count=total),
        "balanced_accuracy": _metric(
            sum(recall_values) / len(recall_values), sample_count=total
        ),
        "labels": labels,
        "per_label": per_label,
        "confusion_matrix": {
            actual: {observed: int(confusion_matrix[actual].get(observed, 0)) for observed in labels}
            for actual in labels
        },
    }


def _regression_metrics(
    expected: tuple[Any, ...], predicted: tuple[Any, ...], *, case_id: str
) -> dict[str, Any]:
    actual_values = tuple(
        _validate_numeric(value, label="expected", case_id=case_id, index=index)
        for index, value in enumerate(expected)
    )
    predicted_values = tuple(
        _validate_numeric(value, label="predicted", case_id=case_id, index=index)
        for index, value in enumerate(predicted)
    )
    total = len(actual_values)
    errors = tuple(observed - actual for actual, observed in zip(actual_values, predicted_values))
    absolute_errors = tuple(abs(error) for error in errors)
    squared_errors = tuple(error * error for error in errors)
    mean_actual = sum(actual_values) / total
    total_sum_squares = sum((actual - mean_actual) ** 2 for actual in actual_values)
    r2 = (
        1 - sum(squared_errors) / total_sum_squares
        if total_sum_squares
        else None
    )
    return {
        "mae": _metric(sum(absolute_errors) / total, sample_count=total),
        "rmse": _metric(sqrt(sum(squared_errors) / total), sample_count=total),
        "bias": _metric(sum(errors) / total, sample_count=total),
        "r2": _metric(r2, sample_count=total),
    }


def _time_series_metrics(
    expected: tuple[Any, ...], predicted: tuple[Any, ...], *, case_id: str
) -> dict[str, Any]:
    """Return regression errors plus percentage metrics for paired forecasts."""
    base = _regression_metrics(expected, predicted, case_id=case_id)
    actual_values = tuple(
        _validate_numeric(value, label="expected", case_id=case_id, index=index)
        for index, value in enumerate(expected)
    )
    predicted_values = tuple(
        _validate_numeric(value, label="predicted", case_id=case_id, index=index)
        for index, value in enumerate(predicted)
    )
    absolute_errors = tuple(
        abs(observed - actual)
        for actual, observed in zip(actual_values, predicted_values)
    )
    nonzero_actual = tuple(abs(actual) > 0 for actual in actual_values)
    mape = (
        sum(
            abs(observed - actual) / abs(actual)
            for actual, observed, include in zip(
                actual_values, predicted_values, nonzero_actual
            )
            if include
        )
        / sum(nonzero_actual)
        * 100
        if any(nonzero_actual)
        else None
    )
    smape_denominators = tuple(
        abs(actual) + abs(observed)
        for actual, observed in zip(actual_values, predicted_values)
    )
    smape = (
        sum(
            2 * error / denominator
            for error, denominator in zip(absolute_errors, smape_denominators)
            if denominator > 0
        )
        / sum(denominator > 0 for denominator in smape_denominators)
        * 100
        if any(denominator > 0 for denominator in smape_denominators)
        else None
    )
    actual_total = sum(abs(actual) for actual in actual_values)
    wape = sum(absolute_errors) / actual_total * 100 if actual_total > 0 else None
    return {
        **base,
        "mape": _metric(mape, sample_count=sum(nonzero_actual)),
        "wape": _metric(wape, sample_count=len(actual_values)),
        "smape": _metric(smape, sample_count=sum(denominator > 0 for denominator in smape_denominators)),
    }


def _validate_thresholds(
    task_type: str, thresholds: Mapping[str, Any] | None
) -> dict[str, float]:
    if thresholds is None:
        return {}
    if not isinstance(thresholds, Mapping):
        raise PredictionEvaluationError("thresholds must be a mapping")
    allowed = {
        "classification": _CLASSIFICATION_THRESHOLDS,
        "regression": _REGRESSION_THRESHOLDS,
        "time_series": _TIME_SERIES_THRESHOLDS,
    }[task_type]
    unknown_names = [name for name in thresholds if not isinstance(name, str)]
    if unknown_names:
        raise PredictionEvaluationError("threshold names must be strings")
    unknown = sorted(set(thresholds) - allowed)
    if unknown:
        raise PredictionEvaluationError(f"unsupported {task_type} threshold: {unknown[0]}")
    normalized: dict[str, float] = {}
    for name, value in thresholds.items():
        numeric = _validate_numeric(value, label="threshold", case_id=name, index=0)
        if numeric < 0 or (task_type == "classification" and numeric > 1):
            bound = "between 0 and 1" if task_type == "classification" else "non-negative"
            raise PredictionEvaluationError(f"threshold {name} must be {bound}")
        normalized[name] = numeric
    return normalized


def build_prediction_evaluation_case(
    rows: Any,
    *,
    case_id: str,
    expected_key: str = "actual",
    predicted_key: str = "predicted",
) -> PredictionEvaluationCase:
    """Build a case directly from analyzer residual or confusion rows."""
    expected_key = str(expected_key or "").strip()
    predicted_key = str(predicted_key or "").strip()
    if not expected_key or not predicted_key:
        raise PredictionEvaluationError("expected_key and predicted_key must not be empty")
    prepared_rows = _as_prediction_rows(rows)
    if not prepared_rows:
        raise PredictionEvaluationError("prediction rows must not be empty")
    expected: list[Any] = []
    predicted: list[Any] = []
    for index, row in enumerate(prepared_rows):
        if expected_key not in row or predicted_key not in row:
            raise PredictionEvaluationError(
                f"prediction row {index} must contain {expected_key} and {predicted_key}"
            )
        expected.append(row[expected_key])
        predicted.append(row[predicted_key])
    return PredictionEvaluationCase(case_id, tuple(expected), tuple(predicted))


def evaluate_prediction_rows(
    rows: Any,
    *,
    case_id: str,
    task_type: str,
    expected_key: str = "actual",
    predicted_key: str = "predicted",
    thresholds: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate analyzer output rows without coupling PFS to pandas."""
    case = build_prediction_evaluation_case(
        rows,
        case_id=case_id,
        expected_key=expected_key,
        predicted_key=predicted_key,
    )
    return evaluate_prediction_quality((case,), task_type=task_type, thresholds=thresholds)


def _validate_count(value: Any, *, case_id: str, index: int) -> int:
    if isinstance(value, bool):
        raise PredictionEvaluationError(
            f"classification count at {case_id}[{index}] must be a non-negative integer"
        )
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PredictionEvaluationError(
            f"classification count at {case_id}[{index}] must be a non-negative integer"
        ) from exc
    if not isfinite(numeric) or numeric < 0 or not numeric.is_integer():
        raise PredictionEvaluationError(
            f"classification count at {case_id}[{index}] must be a non-negative integer"
        )
    return int(numeric)


def evaluate_classification_confusion_rows(
    rows: Any,
    *,
    case_id: str,
    actual_key: str = "actual",
    predicted_key: str = "predicted",
    count_key: str = "count",
    thresholds: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate an analyzer's aggregated actual/predicted confusion rows."""
    case_id = str(case_id or "").strip()
    if not case_id:
        raise PredictionEvaluationError("prediction case_id must not be empty")
    keys = {
        "actual": str(actual_key or "").strip(),
        "predicted": str(predicted_key or "").strip(),
        "count": str(count_key or "").strip(),
    }
    if not all(keys.values()):
        raise PredictionEvaluationError("actual_key, predicted_key, and count_key must not be empty")
    prepared_rows = _as_prediction_rows(rows)
    if not prepared_rows:
        raise PredictionEvaluationError("classification confusion rows must not be empty")

    labels: set[str] = set()
    confusion: dict[str, dict[str, int]] = {}
    for index, row in enumerate(prepared_rows):
        missing = [key for key in keys.values() if key not in row]
        if missing:
            raise PredictionEvaluationError(
                f"classification confusion row {index} must contain {', '.join(missing)}"
            )
        actual = _label_key(row[keys["actual"]])
        predicted = _label_key(row[keys["predicted"]])
        count = _validate_count(row[keys["count"]], case_id=case_id, index=index)
        labels.update((actual, predicted))
        actual_counts = confusion.setdefault(actual, {})
        actual_counts[predicted] = actual_counts.get(predicted, 0) + count
    for actual in labels:
        confusion.setdefault(actual, {})
        for predicted in labels:
            confusion[actual].setdefault(predicted, 0)

    aggregate_metrics = _classification_metrics_from_confusion(confusion)
    metric_names = ("accuracy", "macro_f1", "balanced_accuracy")
    metrics = {name: aggregate_metrics[name] for name in metric_names}
    normalized_thresholds = _validate_thresholds("classification", thresholds)
    checks = _quality_checks("classification", metrics, normalized_thresholds)
    return {
        "engine": "pfs_prediction_evaluation",
        "engine_version": "v1",
        "task_type": "classification",
        "scope": {
            "evaluation_cases": 1,
            "sample_count": sum(
                sum(int(value) for value in confusion[actual].values()) for actual in confusion
            ),
            "source_rows": len(prepared_rows),
        },
        "metrics": metrics,
        "quality": {
            "passed": all(check["passed"] for check in checks) if normalized_thresholds else None,
            "thresholds": {
                name: _rounded(value) for name, value in normalized_thresholds.items()
            },
            "checks": checks,
            "failures": [check["metric"] for check in checks if not check["passed"]],
        },
        "cases": [{"case_id": case_id, **aggregate_metrics}],
        "class_diagnostics": {
            "labels": aggregate_metrics["labels"],
            "per_label": aggregate_metrics["per_label"],
            "confusion_matrix": aggregate_metrics["confusion_matrix"],
        },
        "limitations": [
            "评估输入是分析器实际输出的聚合混淆矩阵，不回显原始标签行。",
            "总体指标可能掩盖分群、时间段或少数类别退化，应同时查看逐类别结果。",
            "本合同不覆盖数据漂移、校准、公平性、训练稳定性、因果归因或业务收益护栏。",
        ],
    }


def _is_missing_prediction(value: Any) -> bool:
    if value is None:
        return True
    try:
        return isnan(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def _timestamp_key(value: Any, *, label: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise PredictionEvaluationError(f"{label} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PredictionEvaluationError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_time_series_evaluation_input(
    rows: Any,
    *,
    case_id: str,
    actual_key: str = "y_actual",
    predicted_key: str = "y_pred",
    time_key: str = "ds",
    segment_key: str = "segment",
    forecast_segment: str = "forecast",
    evaluation_segment: str = "",
    training_end: str = "",
) -> TimeSeriesEvaluationInput:
    """Extract paired history from a forecast result without scoring future rows.

    Future forecast rows normally have no observed value. They are excluded
    only when marked with ``forecast_segment``. Missing values in other rows
    remain visible as structural coverage issues instead of disappearing from
    the quality result.
    """
    actual_key = str(actual_key or "").strip()
    predicted_key = str(predicted_key or "").strip()
    time_key = str(time_key or "").strip()
    segment_key = str(segment_key or "").strip()
    forecast_segment = str(forecast_segment or "forecast").strip().lower()
    evaluation_segment = str(evaluation_segment or "").strip().lower()
    training_end = str(training_end or "").strip()
    if not actual_key or not predicted_key:
        raise PredictionEvaluationError("actual_key and predicted_key must not be empty")
    if evaluation_segment:
        if not segment_key:
            raise PredictionEvaluationError(
                "time-series holdout evaluation requires segment_key"
            )
        if evaluation_segment == forecast_segment:
            raise PredictionEvaluationError(
                "time-series evaluation_segment cannot be the forecast segment"
            )
        if not training_end:
            raise PredictionEvaluationError(
                "time-series holdout evaluation requires training_end"
            )
        training_end_key = _timestamp_key(training_end, label="training_end")
    else:
        training_end_key = None
    prepared_rows = _as_prediction_rows(rows)
    if not prepared_rows:
        raise PredictionEvaluationError("time-series prediction rows must not be empty")

    expected: list[float] = []
    predicted: list[float] = []
    timestamps: list[str] = []
    excluded_forecast_rows = 0
    excluded_unpaired_rows = 0
    excluded_scope_rows = 0
    selected_timestamp_keys: list[datetime] = []
    for index, row in enumerate(prepared_rows):
        actual = row.get(actual_key)
        predicted_value = row.get(predicted_key)
        segment = str(row.get(segment_key) or "").strip().lower() if segment_key else ""
        if segment == forecast_segment:
            excluded_forecast_rows += 1
            continue
        if evaluation_segment and segment != evaluation_segment:
            excluded_scope_rows += 1
            continue
        timestamp = str(row.get(time_key) or "").strip() if time_key else ""
        if evaluation_segment:
            timestamp_key = _timestamp_key(
                timestamp,
                label=f"time value at {case_id}[{index}]",
            )
            if timestamp_key <= training_end_key:
                raise PredictionEvaluationError(
                    f"time-series holdout timestamp at {case_id}[{index}] must be after training_end"
                )
            selected_timestamp_keys.append(timestamp_key)
        if _is_missing_prediction(actual) or _is_missing_prediction(predicted_value):
            excluded_unpaired_rows += 1
            continue
        expected.append(
            _validate_numeric(actual, label="expected", case_id=case_id, index=index)
        )
        predicted.append(
            _validate_numeric(
                predicted_value,
                label="predicted",
                case_id=case_id,
                index=index,
            )
        )
        if time_key:
            timestamps.append(timestamp)

    if evaluation_segment and any(
        left > right for left, right in zip(selected_timestamp_keys, selected_timestamp_keys[1:])
    ):
        raise PredictionEvaluationError(
            "time-series holdout rows must be ordered by timestamp"
        )

    if not expected:
        raise PredictionEvaluationError(
            "time-series prediction rows contain no paired actual/predicted values"
        )
    return TimeSeriesEvaluationInput(
        case=PredictionEvaluationCase(case_id, tuple(expected), tuple(predicted)),
        total_rows=len(prepared_rows),
        excluded_forecast_rows=excluded_forecast_rows,
        excluded_unpaired_rows=excluded_unpaired_rows,
        timestamps=tuple(timestamps),
        excluded_scope_rows=excluded_scope_rows,
        evaluation_segment=evaluation_segment,
        training_end=training_end,
    )


def evaluate_time_series_rows(
    rows: Any,
    *,
    case_id: str,
    actual_key: str = "y_actual",
    predicted_key: str = "y_pred",
    time_key: str = "ds",
    segment_key: str = "segment",
    forecast_segment: str = "forecast",
    evaluation_segment: str = "",
    training_end: str = "",
    thresholds: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate paired historical rows emitted by a time-series analyzer."""
    extracted = build_time_series_evaluation_input(
        rows,
        case_id=case_id,
        actual_key=actual_key,
        predicted_key=predicted_key,
        time_key=time_key,
        segment_key=segment_key,
        forecast_segment=forecast_segment,
        evaluation_segment=evaluation_segment,
        training_end=training_end,
    )
    result = evaluate_prediction_quality(
        (extracted.case,),
        task_type="time_series",
        thresholds=thresholds,
    )
    paired_ratio = extracted.paired_ratio
    result["scope"].update(
        {
            "total_rows": extracted.total_rows,
            "paired_rows": extracted.paired_rows,
            "eligible_rows": extracted.eligible_rows,
            "excluded_forecast_rows": extracted.excluded_forecast_rows,
            "excluded_unpaired_rows": extracted.excluded_unpaired_rows,
            "excluded_scope_rows": extracted.excluded_scope_rows,
            "paired_ratio": paired_ratio,
        }
    )
    timestamps = extracted.timestamps
    result["time_series"] = {
        "evaluation_scope": "temporal_holdout" if evaluation_segment else "paired_history",
        "time_key": time_key,
        "evaluation_segment": extracted.evaluation_segment,
        "training_end": extracted.training_end,
        "time_start": timestamps[0] if timestamps else "",
        "time_end": timestamps[-1] if timestamps else "",
        "monotonic_input": all(
            left <= right for left, right in zip(timestamps, timestamps[1:])
        )
        if len(timestamps) > 1
        else True,
    }
    if extracted.excluded_unpaired_rows:
        coverage_check = {
            "metric": "paired_coverage",
            "operator": "=",
            "threshold": 1.0,
            "actual": paired_ratio,
            "passed": False,
            "reason": "historical_prediction_missing",
        }
        result["quality"]["checks"].append(coverage_check)
        result["quality"]["failures"].append("paired_coverage")
        if thresholds:
            result["quality"]["passed"] = False
    if evaluation_segment:
        result["limitations"] = [
            *result["limitations"],
            f"只评估 segment={evaluation_segment} 且晚于 training_end 的时间切分行；"
            "训练段和未来 forecast 行不进入误差分母。",
            "temporal_holdout 是输入行已标记的时间切分评估，不等于生产上线后的持续监控或业务收益。",
        ]
    else:
        result["limitations"] = [
            *result["limitations"],
            "未来 forecast 行没有真实值，只统计带 actual/predicted 的历史配对行。",
            "paired_history 是拟合或回测输出的配对误差，不等于时间外推 holdout、生产预测质量或业务收益。",
        ]
    return result


def evaluate_time_series_holdout_rows(
    rows: Any,
    *,
    case_id: str,
    training_end: str,
    holdout_segment: str = "holdout",
    actual_key: str = "y_actual",
    predicted_key: str = "y_pred",
    time_key: str = "ds",
    segment_key: str = "segment",
    forecast_segment: str = "forecast",
    thresholds: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate a strictly time-separated holdout emitted by a caller."""
    return evaluate_time_series_rows(
        rows,
        case_id=case_id,
        actual_key=actual_key,
        predicted_key=predicted_key,
        time_key=time_key,
        segment_key=segment_key,
        forecast_segment=forecast_segment,
        evaluation_segment=holdout_segment,
        training_end=training_end,
        thresholds=thresholds,
    )


def _quality_checks(task_type: str, metrics: Mapping[str, Any], thresholds: Mapping[str, float]):
    checks: list[dict[str, Any]] = []
    for threshold_name, threshold in thresholds.items():
        metric_name = threshold_name.removeprefix("min_").removeprefix("max_")
        metric = metrics[metric_name]
        actual = metric["value"]
        is_minimum = threshold_name.startswith("min_")
        passed = actual is not None and (
            actual >= threshold if is_minimum else actual <= threshold
        )
        checks.append(
            {
                "metric": metric_name,
                "operator": ">=" if is_minimum else "<=",
                "threshold": _rounded(threshold),
                "actual": actual,
                "passed": passed,
                "reason": None if actual is not None else "metric_unavailable",
            }
        )
    return checks


def evaluate_prediction_quality(
    cases: Iterable[PredictionEvaluationCase],
    *,
    task_type: str,
    thresholds: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate fixed predictions for classification, regression, or time series.

    ``thresholds`` is optional.  Without it the function reports metrics but
    leaves ``quality.passed`` as ``None``; this prevents a metric report from
    being mistaken for a business acceptance decision.
    """
    normalized_task = _validate_task_type(task_type)
    prepared_cases = tuple(cases)
    if not prepared_cases:
        raise PredictionEvaluationError("prediction evaluation cases must not be empty")
    if any(not isinstance(case, PredictionEvaluationCase) for case in prepared_cases):
        raise PredictionEvaluationError(
            "cases must contain PredictionEvaluationCase items"
        )
    case_ids = [case.case_id for case in prepared_cases]
    if len(set(case_ids)) != len(case_ids):
        raise PredictionEvaluationError("prediction case_id values must be unique")

    prepared_metrics: list[dict[str, Any]] = []
    expected_values: list[Any] = []
    predicted_values: list[Any] = []
    for case in prepared_cases:
        if normalized_task == "classification":
            metrics = _classification_metrics(case.expected, case.predicted)
        elif normalized_task == "regression":
            metrics = _regression_metrics(case.expected, case.predicted, case_id=case.case_id)
        else:
            metrics = _time_series_metrics(case.expected, case.predicted, case_id=case.case_id)
        prepared_metrics.append({"case_id": case.case_id, **metrics})
        expected_values.extend(case.expected)
        predicted_values.extend(case.predicted)

    aggregate_expected = tuple(expected_values)
    aggregate_predicted = tuple(predicted_values)
    if normalized_task == "classification":
        aggregate_metrics = _classification_metrics(aggregate_expected, aggregate_predicted)
        metric_names = ("accuracy", "macro_f1", "balanced_accuracy")
    elif normalized_task == "regression":
        aggregate_metrics = _regression_metrics(
            aggregate_expected, aggregate_predicted, case_id="aggregate"
        )
        metric_names = ("mae", "rmse", "bias", "r2")
    else:
        aggregate_metrics = _time_series_metrics(
            aggregate_expected, aggregate_predicted, case_id="aggregate"
        )
        metric_names = ("mae", "rmse", "bias", "mape", "wape", "smape", "r2")
    metrics = {name: aggregate_metrics[name] for name in metric_names}
    normalized_thresholds = _validate_thresholds(normalized_task, thresholds)
    checks = _quality_checks(normalized_task, metrics, normalized_thresholds)
    quality_passed: bool | None = None
    if normalized_thresholds:
        quality_passed = all(check["passed"] for check in checks)

    result: dict[str, Any] = {
        "engine": "pfs_prediction_evaluation",
        "engine_version": "v1",
        "task_type": normalized_task,
        "scope": {
            "evaluation_cases": len(prepared_cases),
            "sample_count": len(aggregate_expected),
        },
        "metrics": metrics,
        "quality": {
            "passed": quality_passed,
            "thresholds": {
                name: _rounded(value) for name, value in normalized_thresholds.items()
            },
            "checks": checks,
            "failures": [check["metric"] for check in checks if not check["passed"]],
        },
        "cases": prepared_metrics,
        "limitations": [
            "评估输入是人工维护的固定标签或本地分析器输出，不代表真实供应商服务已经接入。",
            "总体指标可能掩盖分群、时间段或少数类别退化，应同时查看逐 Case 和逐类别结果。",
            "本合同不覆盖数据漂移、校准、公平性、训练稳定性、因果归因或业务收益护栏。",
        ],
    }
    if normalized_task == "classification":
        result["class_diagnostics"] = {
            "labels": aggregate_metrics["labels"],
            "per_label": aggregate_metrics["per_label"],
            "confusion_matrix": aggregate_metrics["confusion_matrix"],
        }
    return result
