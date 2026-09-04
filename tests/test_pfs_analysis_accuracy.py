import importlib.util
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from pfs_agent.model_evaluation import evaluate_prediction_rows
from agent.tools.business.data import (
    _build_temporal_holdout_evaluation,
    _model_evaluation_for_result,
)


ROOT = Path(__file__).resolve().parents[1]


def _load(relative_path: str):
    path = ROOT / "Function" / "Analyze" / relative_path / "analyze.py"
    spec = importlib.util.spec_from_file_location(f"pfs_accuracy_{path.parent.name}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载分析模块：{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PfsAnalysisAccuracyTests(unittest.TestCase):
    def test_classification_and_clustering_preserve_result_invariants(self):
        frame = pd.DataFrame(
            {
                "signal": np.r_[np.zeros(15), np.ones(15)],
                "label": np.r_[np.zeros(15), np.ones(15)],
                "secondary": np.arange(30, dtype=float),
            }
        )

        tree = _load("Decision_Tree")
        tree_result, tree_breakdown, tree_roc, _ = tree.run(
            frame, target_column="label", groupby_column="CART", n_deciles=3
        )
        self.assertTrue((tree_result["importance"] >= 0).all())
        self.assertAlmostEqual(100.0, float(tree_result["importance_pct"].sum()), places=1)
        self.assertTrue(tree_breakdown["count"].ge(0).all())
        self.assertTrue(tree_roc["fpr"].between(0, 1).all())
        self.assertTrue(tree_roc["tpr"].between(0, 1).all())

        logistic = _load("Logistic_Regression")
        log_result, log_breakdown, log_roc, _ = logistic.run(
            frame, target_column="label", n_deciles=300
        )
        self.assertGreaterEqual(len(log_result), 1)
        self.assertTrue(np.isfinite(log_result["coefficient"]).all())
        self.assertEqual(9, int(log_breakdown["count"].sum()))
        self.assertTrue(log_roc["auc"].between(0, 1).all())

        kmeans = _load("K-Means")
        cluster_result, cluster_breakdown, elbow, _ = kmeans.run(
            frame, target_column="secondary", n_deciles=2
        )
        self.assertEqual(2, len(cluster_result))
        self.assertEqual(30, int(cluster_result["count"].sum()))
        self.assertAlmostEqual(100.0, float(cluster_result["pct"].sum()), places=6)
        self.assertEqual(30, len(cluster_breakdown))
        self.assertTrue((elbow["inertia"] >= 0).all())
        self.assertTrue(elbow["silhouette"].dropna().between(-1, 1).all())

    def test_time_series_outputs_have_future_finite_forecasts(self):
        modules = [
            ("Time_Series_ARIMA", "date"),
            ("Time_Series_SARIMA", "date"),
            ("Time_Series_VAR", "date,y,x2"),
            ("Time_Series_Prophet", "date"),
            ("Time_Series_GRU", "date"),
        ]
        n = 60
        t = np.arange(n, dtype=float)
        frame = pd.DataFrame(
            {
                "date": pd.date_range("2024-01-01", periods=n, freq="D"),
                "y": 20 + t * 0.5 + np.sin(t / 3),
                "x2": 5 + np.cos(t / 4),
            }
        )
        for name, hint in modules:
            with self.subTest(analyzer=name):
                module = _load(name)
                kwargs = {"target_column": "y", "groupby_column": hint, "n_deciles": 4}
                if name == "Time_Series_ARIMA":
                    kwargs["groupby_column"] = "1,1,0"
                try:
                    output = module.run(frame, **kwargs)
                except ImportError as exc:
                    self.skipTest(f"{name} optional dependency unavailable: {exc}")
                result, _breakdown, metrics, _markdown = output
                self.assertGreaterEqual(len(result), 4)
                self.assertGreaterEqual(len(metrics), 1)
                future = result[result["segment"] == "forecast"]
                self.assertEqual(4, len(future))
                self.assertTrue(pd.to_datetime(future["ds"]).gt(frame["date"].max()).all())
                self.assertTrue(np.isfinite(future["y_pred"].to_numpy(dtype=float)).all())
                quality = _model_evaluation_for_result(
                    name,
                    result,
                    _breakdown,
                    metrics,
                    target_column="y",
                )
                self.assertIsNotNone(quality)
                self.assertEqual("time_series", quality["task_type"])
                self.assertGreater(quality["scope"]["paired_rows"], 0)
                self.assertEqual(4, quality["scope"]["excluded_forecast_rows"])
                self.assertIn("mae", quality["metrics"])
                self.assertIn("rmse", quality["metrics"])

    def test_time_series_models_pass_real_temporal_holdout_path(self):
        modules = [
            ("Time_Series_ARIMA", "1,1,0"),
            ("Time_Series_SARIMA", "date"),
            ("Time_Series_VAR", "date,y,x2"),
            ("Time_Series_Prophet", "date"),
            ("Time_Series_GRU", "date"),
        ]
        n = 32
        t = np.arange(n, dtype=float)
        frame = pd.DataFrame(
            {
                "date": pd.date_range("2025-01-01", periods=n, freq="D"),
                "y": 100 + t * 1.8 + (t % 5) * 2,
                "x2": 50 + t * 0.9 + (t % 3),
            }
        )
        for name, hint in modules:
            with self.subTest(analyzer=name):
                try:
                    quality = _build_temporal_holdout_evaluation(
                        name,
                        frame,
                        "y",
                        hint,
                        3,
                        {"evaluation_mode": "temporal_holdout", "holdout_size": 3},
                    )
                except ImportError as exc:
                    self.skipTest(f"{name} optional dependency unavailable: {exc}")
                self.assertEqual("temporal_holdout", quality["time_series"]["evaluation_scope"])
                self.assertEqual(3, quality["scope"]["paired_rows"])
                self.assertEqual(3, quality["scope"]["total_rows"])
                self.assertEqual(1.0, quality["scope"]["paired_ratio"])
                self.assertTrue(
                    all(
                        quality["metrics"][metric]["value"] is not None
                        for metric in ("mae", "rmse", "bias", "mape", "wape", "smape")
                    )
                )

    def test_arima_auto_order_recovers_from_numeric_selection_failure(self):
        module = _load("Time_Series_ARIMA")
        n = 60
        t = np.arange(n, dtype=float)
        frame = pd.DataFrame(
            {
                "date": pd.date_range("2024-01-01", periods=n, freq="D"),
                "y": 20 + t * 0.5 + np.sin(t / 3),
            }
        )

        result, _breakdown, metrics, _markdown = module.run(
            frame, target_column="y", groupby_column="date", n_deciles=4
        )
        future = result[result["segment"] == "forecast"]
        self.assertEqual(4, len(future))
        self.assertTrue(pd.to_datetime(future["ds"]).gt(frame["date"].max()).all())
        self.assertTrue(np.isfinite(future["y_pred"].to_numpy(dtype=float)).all())
        order = metrics.loc[metrics["metric"] == "模型阶数 (p,d,q)", "value"].iloc[0]
        self.assertRegex(str(order), r"^\([0-9]+, [0-9]+, [0-9]+\)$")

    def test_regression_recovers_exact_linear_relationship(self):
        module = _load("Regression")
        x = np.arange(20, dtype=float)
        result, breakdown, metrics, _ = module.run(
            pd.DataFrame({"x": x, "sales": 2 * x + 1}),
            target_column="sales",
        )

        metric = metrics.set_index("metric")
        self.assertEqual(1.0, metric.loc["R²", "train_value"])
        self.assertEqual(1.0, metric.loc["R²", "test_value"])
        self.assertEqual(0.0, metric.loc["RMSE", "test_value"])
        coefficient = result.loc[result["feature"] == "x", "coefficient"].iloc[0]
        self.assertAlmostEqual(10.881984, coefficient, places=5)
        self.assertEqual(6, len(breakdown))
        quality = evaluate_prediction_rows(
            breakdown,
            case_id="linear-regression-test",
            task_type="regression",
            expected_key="y_actual",
            predicted_key="y_pred",
        )
        self.assertEqual(1.0, quality["metrics"]["r2"]["value"])
        self.assertEqual(0.0, quality["metrics"]["rmse"]["value"])

    def test_sklearn_model_infers_regression_for_continuous_random_forest(self):
        module = _load("Sklearn_Model")
        x = np.arange(40, dtype=float)
        frame = pd.DataFrame({"x": x, "sales": 3 * x + 2})

        result, importance, residuals, _markdown = module.run(
            frame, target_column="sales", groupby_column="rf", n_deciles=42
        )
        metrics = result.set_index("metric")
        self.assertIn("r2", metrics.index)
        self.assertIn("rmse", metrics.index)
        self.assertTrue(np.isfinite(metrics.loc[["r2", "mae", "rmse"], "value"].to_numpy(dtype=float)).all())
        self.assertEqual(1, len(importance))
        self.assertAlmostEqual(100.0, float(importance["importance_pct"].sum()), places=2)
        self.assertEqual(12, len(residuals))
        self.assertTrue(np.isfinite(residuals["predicted"].to_numpy(dtype=float)).all())
        quality = evaluate_prediction_rows(
            residuals,
            case_id="sklearn-regression-test",
            task_type="regression",
        )
        for metric_name in ("r2", "mae", "rmse"):
            self.assertEqual(
                float(metrics.loc[metric_name, "value"]),
                quality["metrics"][metric_name]["value"],
            )

    def test_sklearn_model_rejects_invalid_input_boundaries(self):
        module = _load("Sklearn_Model")
        with self.assertRaisesRegex(ValueError, "输入数据为空"):
            module.run(pd.DataFrame(), target_column="y")

        with self.assertRaisesRegex(ValueError, "含缺失值"):
            module.run(
                pd.DataFrame({"x": [1.0, 2.0, 3.0], "label": [0.0, np.nan, 1.0]}),
                target_column="label", groupby_column="lr",
            )

        with self.assertRaisesRegex(ValueError, "至少需要两个不同类别"):
            module.run(
                pd.DataFrame({"x": [1.0, 2.0, 3.0], "label": [1, 1, 1]}),
                target_column="label", groupby_column="lr",
            )

        with self.assertRaisesRegex(ValueError, "必须是数值列"):
            module.run(
                pd.DataFrame({"x": [1.0, 2.0, 3.0], "sales": ["a", "b", "c"]}),
                target_column="sales", groupby_column="lm",
            )

        with self.assertRaisesRegex(ValueError, "聚类样本数不足"):
            module.run(
                pd.DataFrame({"x": [1.0, 2.0], "y": [2.0, 3.0]}),
                target_column="", groupby_column="kmeans", n_deciles=3,
            )

    def test_sklearn_model_handles_multiclass_imbalance_and_missing_feature(self):
        module = _load("Sklearn_Model")
        labels = ["A"] * 12 + ["B"] * 5 + ["C"] * 3
        frame = pd.DataFrame({
            "signal": np.r_[np.zeros(12), np.ones(5), np.full(3, 2.0)],
            "optional": [np.nan, 1.0] * 10,
            "segment": labels,
        })
        result, importance, confusion, _markdown = module.run(
            frame, target_column="segment", groupby_column="rf"
        )
        metrics = result.set_index("metric")
        self.assertIn("f1_macro", metrics.index)
        self.assertTrue(np.isfinite(metrics.loc[
            ["accuracy", "precision_macro", "recall_macro", "f1_macro"], "value"
        ].to_numpy(dtype=float)).all())
        self.assertEqual({"A", "B", "C"}, set(confusion["actual"]))
        self.assertEqual({"A", "B", "C"}, set(confusion["predicted"]))
        self.assertEqual(9, len(confusion))
        self.assertAlmostEqual(100.0, float(importance["importance_pct"].sum()), places=1)

    def test_ab_analysis_reports_group_means_and_lift(self):
        module = _load("AB_Test_Analysis")
        result, quality, metrics, markdown = module.run(
            pd.DataFrame(
                {
                    "variant": ["control"] * 4 + ["treatment"] * 4,
                    "sales": [1, 2, 3, 4, 2, 3, 4, 5],
                }
            ),
            target_column="sales",
            groupby_column="variant",
        )

        groups = result.set_index("role")
        self.assertEqual(2.5, groups.loc["control", "mean"])
        self.assertEqual(3.5, groups.loc["treatment", "mean"])
        metric = metrics.set_index("metric")["value"]
        self.assertEqual(1.0, metric["absolute_lift"])
        self.assertAlmostEqual(0.4, float(metric["relative_lift"]), places=6)
        self.assertIn("尚无足够证据", markdown)
        self.assertTrue((quality["status"] == "pass").any())

    def test_decile_analysis_preserves_total_and_cumulative_share(self):
        module = _load("Data_Decile_Analysis")
        result, breakdown, _ = module.run(
            pd.DataFrame({"sales": np.arange(1, 9, dtype=float)}),
            target_column="sales",
            n_deciles=4,
        )

        self.assertEqual(4, len(result))
        self.assertEqual(36.0, result["sum"].sum())
        self.assertEqual(8, int(result["count"].sum()))
        self.assertEqual(100.0, float(result.iloc[-1]["cumulative_pct"]))
        self.assertTrue(breakdown.empty)

    def test_univariate_screening_finds_signal_and_handles_constant_candidate(self):
        module = _load("Univariate_Screening")
        x = np.arange(20, dtype=float)
        output = module.run(
            pd.DataFrame({"signal": x, "constant": np.ones(20), "target": 2 * x + 1}),
            target_column="target",
        )

        result = output["analysis_result"]
        signal = result.loc[result["变量"] == "signal"].iloc[0]
        constant = result.loc[result["变量"] == "constant"].iloc[0]
        self.assertEqual("***", signal["显著性"])
        self.assertAlmostEqual(1.0, float(signal["R²"]), places=6)
        self.assertEqual("+", signal["方向"])
        self.assertTrue(pd.isna(constant["p值"]))
        metrics = output["analysis_metrics"].set_index("指标")["值"]
        self.assertEqual("1", metrics["显著变量数 (p<0.05)"])


if __name__ == "__main__":
    unittest.main()
