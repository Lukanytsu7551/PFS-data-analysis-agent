from __future__ import annotations

import unittest

from pfs_agent.model_evaluation import (
    PredictionEvaluationCase,
    PredictionEvaluationError,
    build_prediction_evaluation_case,
    build_time_series_evaluation_input,
    evaluate_classification_confusion_rows,
    evaluate_prediction_quality,
    evaluate_prediction_rows,
    evaluate_time_series_holdout_rows,
    evaluate_time_series_rows,
)


class PfsPredictionEvaluationTests(unittest.TestCase):
    def test_classification_reports_aggregate_and_per_label_quality(self):
        result = evaluate_prediction_quality(
            (
                PredictionEvaluationCase(
                    "holdout-main",
                    ("a", "a", "b", "b"),
                    ("a", "a", "b", "a"),
                ),
            ),
            task_type="classification",
            thresholds={"min_accuracy": 0.75, "min_macro_f1": 0.73},
        )

        self.assertEqual("pfs_prediction_evaluation", result["engine"])
        self.assertEqual("classification", result["task_type"])
        self.assertEqual(4, result["scope"]["sample_count"])
        self.assertEqual(0.75, result["metrics"]["accuracy"]["value"])
        self.assertEqual(0.7333, result["metrics"]["macro_f1"]["value"])
        self.assertTrue(result["quality"]["passed"])
        self.assertEqual(2, len(result["class_diagnostics"]["per_label"]))
        self.assertNotIn("expected", result["cases"][0])

    def test_regression_reports_error_metrics_and_threshold_failures(self):
        result = evaluate_prediction_quality(
            (
                PredictionEvaluationCase("forecast", (10, 20, 30), (11, 18, 33)),
            ),
            task_type="regression",
            thresholds={"max_mae": 1.0, "max_rmse": 2.0},
        )

        self.assertEqual(2.0, result["metrics"]["mae"]["value"])
        self.assertEqual(2.1602, result["metrics"]["rmse"]["value"])
        self.assertEqual(0.6667, result["metrics"]["bias"]["value"])
        self.assertFalse(result["quality"]["passed"])
        self.assertEqual(["mae", "rmse"], result["quality"]["failures"])

    def test_no_threshold_is_a_metric_report_not_an_acceptance(self):
        result = evaluate_prediction_quality(
            (PredictionEvaluationCase("exact", (1, 2, 3), (1, 2, 3)),),
            task_type="regression",
        )

        self.assertIsNone(result["quality"]["passed"])
        self.assertEqual([], result["quality"]["checks"])
        self.assertEqual(1.0, result["metrics"]["r2"]["value"])

    def test_analyzer_rows_can_feed_the_same_evaluation_contract(self):
        rows = [
            {"actual": "yes", "predicted": "yes"},
            {"actual": "no", "predicted": "yes"},
            {"actual": "no", "predicted": "no"},
        ]

        case = build_prediction_evaluation_case(rows, case_id="confusion")
        result = evaluate_prediction_rows(
            rows,
            case_id="confusion",
            task_type="classification",
            thresholds={"min_accuracy": 0.66},
        )

        self.assertEqual(3, case.sample_count)
        self.assertEqual(0.6667, result["metrics"]["accuracy"]["value"])
        self.assertTrue(result["quality"]["passed"])

    def test_time_series_evaluation_excludes_future_rows_and_reports_coverage(self):
        rows = [
            {"ds": "2026-01-01", "segment": "history", "y_actual": 100, "y_pred": 90},
            {"ds": "2026-02-01", "segment": "history", "y_actual": 200, "y_pred": 220},
            {"ds": "2026-03-01", "segment": "history", "y_actual": None, "y_pred": 235},
            {"ds": "2026-04-01", "segment": "forecast", "y_actual": None, "y_pred": 250},
        ]

        extracted = build_time_series_evaluation_input(rows, case_id="monthly-forecast")
        result = evaluate_time_series_rows(rows, case_id="monthly-forecast")

        self.assertEqual(2, extracted.paired_rows)
        self.assertEqual(1, extracted.excluded_unpaired_rows)
        self.assertEqual(1, extracted.excluded_forecast_rows)
        self.assertEqual(4, result["scope"]["total_rows"])
        self.assertEqual(2, result["scope"]["paired_rows"])
        self.assertEqual(0.6667, result["scope"]["paired_ratio"])
        self.assertEqual("paired_history", result["time_series"]["evaluation_scope"])
        self.assertEqual(15.0, result["metrics"]["mae"]["value"])
        self.assertEqual(15.8114, result["metrics"]["rmse"]["value"])
        self.assertEqual(10.0, result["metrics"]["mape"]["value"])
        self.assertIn("paired_coverage", result["quality"]["failures"])
        self.assertTrue(
            any("未来 forecast 行没有真实值" in item for item in result["limitations"])
        )

    def test_time_series_holdout_requires_explicit_temporal_boundary(self):
        rows = [
            {"ds": "2026-01-01", "segment": "train", "y_actual": 100, "y_pred": 99},
            {"ds": "2026-02-01", "segment": "train", "y_actual": 110, "y_pred": 111},
            {"ds": "2026-03-01", "segment": "holdout", "y_actual": 120, "y_pred": 118},
            {"ds": "2026-04-01", "segment": "holdout", "y_actual": 130, "y_pred": 134},
            {"ds": "2026-05-01", "segment": "forecast", "y_actual": None, "y_pred": 140},
        ]

        result = evaluate_time_series_holdout_rows(
            rows,
            case_id="monthly-holdout",
            training_end="2026-02-28",
            thresholds={"max_mae": 5},
        )

        self.assertEqual("temporal_holdout", result["time_series"]["evaluation_scope"])
        self.assertEqual(2, result["scope"]["paired_rows"])
        self.assertEqual(2, result["scope"]["excluded_scope_rows"])
        self.assertEqual(1, result["scope"]["excluded_forecast_rows"])
        self.assertEqual(1.0, result["scope"]["paired_ratio"])
        self.assertTrue(result["quality"]["passed"])
        self.assertTrue(any("时间切分" in item for item in result["limitations"]))

        with self.assertRaisesRegex(PredictionEvaluationError, "after training_end"):
            evaluate_time_series_holdout_rows(
                [
                    {"ds": "2026-02-01", "segment": "holdout", "y_actual": 1, "y_pred": 1},
                ],
                case_id="bad-holdout",
                training_end="2026-02-28",
            )

    def test_time_series_thresholds_support_percentage_metrics(self):
        result = evaluate_prediction_quality(
            (PredictionEvaluationCase("history", (100, 200), (100, 200)),),
            task_type="time_series",
            thresholds={"max_mape": 0.0, "max_wape": 0.0, "max_smape": 0.0},
        )

        self.assertTrue(result["quality"]["passed"])
        self.assertEqual(0.0, result["metrics"]["wape"]["value"])

    def test_aggregated_confusion_rows_use_exact_counts_without_expanding_labels(self):
        result = evaluate_classification_confusion_rows(
            [
                {"actual": "a", "predicted": "a", "count": 3},
                {"actual": "a", "predicted": "b", "count": 1},
                {"actual": "b", "predicted": "b", "count": 2},
            ],
            case_id="classifier-test",
            thresholds={"min_accuracy": 0.8},
        )

        self.assertEqual(6, result["scope"]["sample_count"])
        self.assertEqual(0.8333, result["metrics"]["accuracy"]["value"])
        self.assertEqual(0.875, result["metrics"]["balanced_accuracy"]["value"])
        self.assertTrue(result["quality"]["passed"])
        self.assertEqual(3, result["class_diagnostics"]["confusion_matrix"]["str:'a'"]["str:'a'"])

    def test_aggregated_confusion_rows_reject_negative_or_fractional_counts(self):
        with self.assertRaisesRegex(PredictionEvaluationError, "non-negative integer"):
            evaluate_classification_confusion_rows(
                [{"actual": "a", "predicted": "a", "count": -1}],
                case_id="invalid-count",
            )

    def test_row_adapter_rejects_missing_columns_and_empty_frames(self):
        with self.assertRaisesRegex(PredictionEvaluationError, "must contain"):
            build_prediction_evaluation_case(
                [{"actual": 1}], case_id="missing", predicted_key="prediction"
            )
        with self.assertRaisesRegex(PredictionEvaluationError, "must not be empty"):
            build_prediction_evaluation_case([], case_id="empty")

    def test_invalid_cases_and_thresholds_are_rejected(self):
        with self.assertRaisesRegex(PredictionEvaluationError, "lengths must match"):
            PredictionEvaluationCase("bad", (1, 2), (1,))
        with self.assertRaisesRegex(PredictionEvaluationError, "task_type"):
            evaluate_prediction_quality(
                (PredictionEvaluationCase("case", ("a",), ("a",)),),
                task_type="ranking",
            )
        with self.assertRaisesRegex(PredictionEvaluationError, "unsupported"):
            evaluate_prediction_quality(
                (PredictionEvaluationCase("case", ("a",), ("a",)),),
                task_type="classification",
                thresholds={"max_rmse": 1},
            )
        with self.assertRaisesRegex(PredictionEvaluationError, "finite number"):
            evaluate_prediction_quality(
                (PredictionEvaluationCase("case", (1, float("nan")), (1, 2)),),
                task_type="regression",
            )


if __name__ == "__main__":
    unittest.main()
