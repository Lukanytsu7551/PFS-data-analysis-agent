from __future__ import annotations

from dataclasses import replace
import io
from pathlib import Path
import unittest
import uuid

from api import create_app
from api.state import session_manager
from pfs_agent.business_forecast import (
    SUPPORTED_FORECAST_MODELS,
    evaluate_demand_forecast,
)
from pfs_agent.reporting import load_tabular_snapshot


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data" / "fixtures" / "pfs_monthly_demand.csv"


class DemandForecastBusinessAcceptanceTests(unittest.TestCase):
    def _snapshot(self):
        return load_tabular_snapshot(
            FIXTURE,
            source_id="monthly-demand-sample",
            date_column="月份",
            file_name=FIXTURE.name,
        )

    def test_demand_forecast_retrains_and_reports_business_metrics(self):
        result = evaluate_demand_forecast(self._snapshot(), holdout_size=3)

        self.assertEqual("demand_forecast_v1", result["scenario"]["id"])
        self.assertEqual("share_with_caveats", result["assessment"])
        self.assertEqual(0, result["quality"]["blocker_count"])
        self.assertEqual(7, result["quality"]["review_count"])
        self.assertEqual("Prophet", result["metrics"]["model_label"])
        self.assertEqual(27, result["metrics"]["training_rows"])
        self.assertEqual(3, result["metrics"]["holdout_rows"])
        self.assertEqual(4560.0, result["metrics"]["holdout_actual_orders_10k"])
        self.assertEqual("temporal_holdout", result["model_evaluation"]["time_series"]["evaluation_scope"])
        self.assertEqual(3, result["model_evaluation"]["scope"]["paired_rows"])
        self.assertEqual(2, result["metrics"]["rolling_origins"])
        self.assertEqual(6, result["metrics"]["rolling_holdout_rows"])
        self.assertEqual(
            "rolling_temporal_holdout",
            result["model_evaluation"]["rolling"]["time_series"]["evaluation_scope"],
        )
        self.assertNotIn("_prediction_rows", result["model_evaluation"])
        self.assertNotIn("_prediction_rows", result["model_evaluation"]["rolling"])
        self.assertIsNotNone(result["metrics"]["wape_pct"])
        self.assertIsNotNone(result["metrics"]["rolling_wape_range_pct"])
        self.assertIsNotNone(result["metrics"]["holdout_orders_ks_distance"])
        self.assertIsNotNone(result["metrics"]["holdout_gmv_ks_distance"])
        self.assertEqual("needs_review", result["business_guardrails"]["distribution_status"])
        self.assertEqual("needs_review", result["business_guardrails"]["gmv_distribution_status"])
        self.assertEqual("needs_review", result["business_guardrails"]["multivariate_distribution_status"])
        self.assertEqual("needs_review", result["business_guardrails"]["baseline_status"])
        self.assertGreater(result["metrics"]["naive_baseline_wape_pct"], result["metrics"]["wape_pct"])
        self.assertGreater(result["metrics"]["model_wape_lift_vs_naive_pct"], 0)
        self.assertEqual("last_value_naive", result["baseline_comparison"]["method"])
        self.assertEqual("last_value_naive", result["rolling_baseline_comparison"]["method"])
        self.assertEqual(8, len(result["claims"]))
        self.assertTrue(result["evidence"][0]["content_sha256"])

    def test_each_supported_local_model_can_run_the_same_business_contract(self):
        for model_name in SUPPORTED_FORECAST_MODELS:
            with self.subTest(model=model_name):
                result = evaluate_demand_forecast(
                    self._snapshot(), model_name=model_name, holdout_size=3
                )
                self.assertEqual("share_with_caveats", result["assessment"])
                self.assertEqual(3, result["metrics"]["holdout_rows"])
                self.assertTrue(result["metrics"]["wape_pct"] >= 0)

    def test_business_threshold_can_be_explicitly_checked(self):
        result = evaluate_demand_forecast(
            self._snapshot(),
            holdout_size=3,
            quality_thresholds={"max_wape": 100},
            business_thresholds={
                "max_total_delta_pct": 100,
                "max_holdout_orders_mean_shift_pct": 100,
                "max_holdout_orders_ks_distance": 1,
                "max_holdout_gmv_ks_distance": 1,
                "min_model_wape_lift_pct": 0,
            },
        )

        self.assertEqual("pass", next(
            item["status"]
            for item in result["quality"]["checks"]
            if item["check_id"] == "demand_forecast_quality_threshold"
        ))
        self.assertTrue(result["metrics"]["quality_passed"])
        self.assertEqual("pass", result["metrics"]["business_total_guard_status"])
        self.assertEqual("pass", result["metrics"]["business_distribution_guard_status"])
        self.assertEqual("pass", result["metrics"]["business_gmv_distribution_guard_status"])
        self.assertEqual("pass", result["business_guardrails"]["multivariate_distribution_status"])
        self.assertEqual("pass", result["business_guardrails"]["baseline_status"])

    def test_business_mean_shift_guard_blocks_when_threshold_is_missed(self):
        result = evaluate_demand_forecast(
            self._snapshot(),
            holdout_size=3,
            business_thresholds={"max_holdout_orders_mean_shift_pct": 0},
        )

        self.assertEqual("needs_revision", result["assessment"])
        self.assertEqual("fail", result["business_guardrails"]["mean_shift_status"])
        self.assertIn(
            "demand_forecast_target_mean_shift",
            {item["check_id"] for item in result["quality"]["checks"] if item["status"] == "fail"},
        )

    def test_business_total_volume_guard_blocks_when_threshold_is_missed(self):
        result = evaluate_demand_forecast(
            self._snapshot(),
            holdout_size=3,
            business_thresholds={"max_total_delta_pct": 0},
        )

        self.assertEqual("needs_revision", result["assessment"])
        self.assertEqual("fail", result["business_guardrails"]["status"])
        self.assertIn(
            "demand_forecast_total_volume_guard",
            {item["check_id"] for item in result["quality"]["checks"] if item["status"] == "fail"},
        )

    def test_model_baseline_guard_blocks_when_improvement_is_below_threshold(self):
        result = evaluate_demand_forecast(
            self._snapshot(),
            holdout_size=3,
            business_thresholds={"min_model_wape_lift_pct": 100},
        )

        self.assertEqual("needs_revision", result["assessment"])
        self.assertEqual("fail", result["business_guardrails"]["baseline_status"])
        self.assertIn(
            "demand_forecast_model_vs_naive_baseline",
            {item["check_id"] for item in result["quality"]["checks"] if item["status"] == "fail"},
        )

    def test_business_threshold_contract_rejects_unknown_or_negative_values(self):
        with self.assertRaisesRegex(ValueError, "unsupported business threshold"):
            evaluate_demand_forecast(self._snapshot(), business_thresholds={"max_bias": 1})
        with self.assertRaisesRegex(ValueError, "finite non-negative"):
            evaluate_demand_forecast(self._snapshot(), business_thresholds={"max_total_delta_pct": -1})
        with self.assertRaisesRegex(ValueError, "between 0 and 1"):
            evaluate_demand_forecast(
                self._snapshot(),
                business_thresholds={"max_holdout_orders_ks_distance": 1.1},
            )
        with self.assertRaisesRegex(ValueError, "between 0 and 1"):
            evaluate_demand_forecast(
                self._snapshot(),
                business_thresholds={"max_holdout_gmv_ks_distance": 1.1},
            )
        with self.assertRaisesRegex(ValueError, "between 0 and 100"):
            evaluate_demand_forecast(
                self._snapshot(),
                business_thresholds={"min_model_wape_lift_pct": 100.1},
            )

    def test_duplicate_or_gap_months_block_forecast_before_model_execution(self):
        snapshot = self._snapshot()
        duplicate_rows = [dict(row) for row in snapshot.rows]
        duplicate_rows[-1]["月份"] = duplicate_rows[-2]["月份"]
        duplicate = evaluate_demand_forecast(replace(snapshot, rows=tuple(duplicate_rows)))
        self.assertEqual("needs_revision", duplicate["assessment"])
        self.assertIsNone(duplicate["metrics"])
        self.assertIn(
            "demand_month_grain_unique",
            {item["check_id"] for item in duplicate["quality"]["checks"] if item["status"] == "fail"},
        )

        gap_rows = [dict(row) for row in snapshot.rows]
        gap_rows[12]["月份"] = "2025-02"
        gap = evaluate_demand_forecast(replace(snapshot, rows=tuple(gap_rows)))
        self.assertEqual("needs_revision", gap["assessment"])
        self.assertIsNone(gap["metrics"])
        self.assertIn(
            "demand_month_sequence_contiguous",
            {item["check_id"] for item in gap["quality"]["checks"] if item["status"] == "fail"},
        )

        unordered_rows = list(reversed(snapshot.rows))
        unordered = evaluate_demand_forecast(replace(snapshot, rows=tuple(unordered_rows)))
        self.assertEqual("needs_revision", unordered["assessment"])
        self.assertIsNone(unordered["metrics"])
        self.assertIn(
            "demand_month_sequence_ordered",
            {item["check_id"] for item in unordered["quality"]["checks"] if item["status"] == "fail"},
        )


class DemandForecastBusinessAcceptanceApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config.update(TESTING=True)

    def setUp(self):
        self.client = self.app.test_client()
        self.sid = f"demand-forecast-{uuid.uuid4().hex[:12]}"
        session_manager.get_or_create(self.sid)

    def tearDown(self):
        session_manager.remove(self.sid)

    def _upload(self):
        response = self.client.post(
            f"/api/session/{self.sid}/upload",
            data={"file": (io.BytesIO(FIXTURE.read_bytes()), FIXTURE.name)},
            content_type="multipart/form-data",
        )
        self.assertEqual(200, response.status_code)
        return response.get_json()["added"][0]["source_id"]

    def test_auto_detection_runs_forecast_and_keeps_light_trace(self):
        source_id = self._upload()
        response = self.client.post(
            f"/api/session/{self.sid}/pfs/business-acceptance",
            json={
                "source_id": source_id,
                "scenario": "auto",
                "model": "Time_Series_ARIMA",
                "holdout_size": 3,
                "quality_thresholds": {"max_wape": 100},
                "business_thresholds": {
                    "max_total_delta_pct": 100,
                    "max_holdout_orders_mean_shift_pct": 100,
                    "max_holdout_orders_ks_distance": 1,
                    "max_holdout_gmv_ks_distance": 1,
                    "min_model_wape_lift_pct": 0,
                },
            },
        )

        self.assertEqual(200, response.status_code)
        result = response.get_json()["result"]
        self.assertEqual("demand_forecast_v1", result["scenario"]["id"])
        self.assertEqual("ARIMA", result["metrics"]["model_label"])
        self.assertNotIn("governance", result)
        self.assertEqual(8, len(result["claims"]))
        self.assertEqual("pass", result["business_guardrails"]["status"])
        self.assertEqual("Time_Series_ARIMA", result["request"]["model"])
        self.assertEqual(100, result["request"]["quality_thresholds"]["max_wape"])
        self.assertEqual(100.0, result["request"]["business_thresholds"]["max_total_delta_pct"])
        self.assertEqual(1.0, result["request"]["business_thresholds"]["max_holdout_orders_ks_distance"])
        self.assertEqual(1.0, result["request"]["business_thresholds"]["max_holdout_gmv_ks_distance"])
        self.assertEqual(0.0, result["request"]["business_thresholds"]["min_model_wape_lift_pct"])
        self.assertTrue(all(claim["claim"] for claim in result["claims"]))
        self.assertTrue(all(claim["evidence_ids"] for claim in result["claims"]))
        self.assertEqual(result["source"]["content_sha256"], result["evidence"][0]["content_sha256"])

        for output_format in ("json", "csv"):
            exported = self.client.post(
                f"/api/session/{self.sid}/pfs/export",
                json={
                    "format": output_format,
                    "source_id": source_id,
                    "scenario": "demand_forecast_v1",
                    "model": "Time_Series_ARIMA",
                    "holdout_size": 3,
                    "quality_thresholds": {"max_wape": 100},
                    "rolling_origins": 2,
                    "business_thresholds": {
                        "max_total_delta_pct": 100,
                        "max_holdout_orders_mean_shift_pct": 100,
                        "max_holdout_orders_ks_distance": 1,
                        "max_holdout_gmv_ks_distance": 1,
                        "min_model_wape_lift_pct": 0,
                    },
                },
            )
            self.assertEqual(200, exported.status_code)
            if output_format == "json":
                exported_result = exported.get_json()["result"]
                self.assertEqual("ARIMA", exported_result["metrics"]["model_label"])
                self.assertEqual("pass", exported_result["business_guardrails"]["status"])
            else:
                exported_csv = exported.get_data(as_text=True)
                self.assertIn("business_total_guard_status", exported_csv)
                self.assertIn("model_wape_lift_vs_naive_pct", exported_csv)
                self.assertIn("min_model_wape_lift_pct", exported_csv)


class DemandForecastUiContractTests(unittest.TestCase):
    def test_workbench_exposes_demand_forecast_scenario(self):
        template = (ROOT / "templates" / "agent_chat.html").read_text(encoding="utf-8")
        script = (ROOT / "frontend" / "legacy" / "pfs-report-preview.js").read_text(
            encoding="utf-8"
        )

        self.assertIn('value="demand_forecast_v1"', template)
        self.assertIn("经营需求预测", template)
        self.assertIn("demand_forecast_v1", script)
        self.assertIn("Holdout WAPE", script)
        self.assertIn("pfs-business-forecast-model", template)
        self.assertIn("business_thresholds", script)
        self.assertIn("quality_thresholds", script)
        self.assertIn("总量偏差上限", template)
        self.assertIn("训练→Holdout 均值漂移上限", template)
        self.assertIn("pfs-business-forecast-ks-limit", template)
        self.assertIn("pfs-business-forecast-gmv-ks-limit", template)
        self.assertIn("pfs-business-forecast-rolling", template)
        self.assertIn("pfs-business-forecast-wape-limit", template)
        self.assertIn("pfs-business-forecast-baseline-limit", template)
        self.assertIn("min_model_wape_lift_pct", script)
        self.assertIn("last-value", script)
        self.assertIn("待确认", script)


if __name__ == "__main__":
    unittest.main()
