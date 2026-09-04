from __future__ import annotations

import unittest

from pfs_agent.distribution_drift import (
    DistributionDriftError,
    evaluate_numeric_distribution_drift,
    evaluate_numeric_distribution_drift_columns,
)


class DistributionDriftTests(unittest.TestCase):
    def test_ks_distance_is_deterministic_and_explained_by_quantiles(self):
        result = evaluate_numeric_distribution_drift(
            [1, 2, 3, 4],
            [3, 4],
            metric_name="orders",
        )

        self.assertEqual("orders", result["metric"])
        self.assertEqual("two_sample_ks_distance", result["method"])
        self.assertEqual(0.5, result["ks_distance"])
        self.assertEqual("needs_review", result["quality"]["status"])
        self.assertIsNone(result["quality"]["passed"])
        self.assertIn("0.5", result["reference_quantiles"])
        self.assertIn("0.9", result["comparison_quantiles"])

    def test_threshold_passes_or_fails_without_statistical_overclaim(self):
        passed = evaluate_numeric_distribution_drift([1, 2, 3], [1, 2], max_ks_distance=1)
        failed = evaluate_numeric_distribution_drift([1, 2, 3], [3, 4], max_ks_distance=0)

        self.assertEqual("pass", passed["quality"]["status"])
        self.assertTrue(passed["quality"]["passed"])
        self.assertEqual("fail", failed["quality"]["status"])
        self.assertFalse(failed["quality"]["passed"])
        self.assertTrue(failed["limitations"])

    def test_rejects_empty_non_numeric_and_out_of_range_threshold(self):
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            evaluate_numeric_distribution_drift([], [1])
        with self.assertRaisesRegex(ValueError, "must be finite"):
            evaluate_numeric_distribution_drift([1, float("nan")], [1])
        with self.assertRaisesRegex(DistributionDriftError, "between 0 and 1"):
            evaluate_numeric_distribution_drift([1], [1], max_ks_distance=1.1)

    def test_columnwise_drift_keeps_metric_results_and_aggregate_status(self):
        result = evaluate_numeric_distribution_drift_columns(
            {"orders": [1, 2, 3, 4], "gmv": [10, 20, 30, 40]},
            {"orders": [1, 2], "gmv": [40, 50]},
            max_ks_distances={"orders": 1, "gmv": 0},
        )

        self.assertEqual("columnwise_two_sample_ks_distance", result["method"])
        self.assertEqual(["orders", "gmv"], result["metric_names"])
        self.assertEqual("pass", result["metrics"]["orders"]["quality"]["status"])
        self.assertEqual("fail", result["metrics"]["gmv"]["quality"]["status"])
        self.assertEqual("fail", result["quality"]["status"])
        self.assertFalse(result["quality"]["passed"])
        self.assertTrue(any("列边际" in item for item in result["limitations"]))

    def test_columnwise_drift_requires_matching_columns(self):
        with self.assertRaisesRegex(DistributionDriftError, "columns must match"):
            evaluate_numeric_distribution_drift_columns(
                {"orders": [1]},
                {"gmv": [1]},
            )


if __name__ == "__main__":
    unittest.main()
