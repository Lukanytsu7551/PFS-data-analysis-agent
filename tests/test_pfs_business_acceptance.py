from __future__ import annotations

from dataclasses import replace
import io
from pathlib import Path
import unittest
import uuid

from api import create_app
from api.state import session_manager
from pfs_agent.business_acceptance import (
    BusinessAcceptanceError,
    evaluate_city_portfolio,
    evaluate_city_monthly_pnl,
    evaluate_city_user_supply,
)
from pfs_agent.reporting import load_tabular_snapshot


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "deploy" / "samples" / "Sample-data.xlsx"
WORKSHEET = "10城数据包"
MONTHLY_SAMPLE = ROOT / "data" / "fixtures" / "pfs_city_monthly_pnl.csv"


class CityPortfolioBusinessAcceptanceTests(unittest.TestCase):
    def _snapshot(self):
        return load_tabular_snapshot(
            SAMPLE,
            source_id="city-portfolio-sample",
            date_column="",
            worksheet=WORKSHEET,
            file_name=SAMPLE.name,
        )

    def test_bundled_business_sample_reconciles_to_independent_truth(self):
        result = evaluate_city_portfolio(self._snapshot())

        self.assertEqual("share_with_caveats", result["assessment"])
        self.assertEqual(0, result["quality"]["blocker_count"])
        self.assertEqual(6, result["quality"]["passed_count"])
        self.assertEqual(10, result["metrics"]["city_count"])
        self.assertEqual(760, result["metrics"]["daily_orders_thousand"])
        self.assertEqual(277_400_000, result["metrics"]["annual_orders"])
        self.assertEqual(15_629_300_000, result["metrics"]["annual_gmv_yuan"])
        self.assertEqual(156.293, result["metrics"]["annual_gmv_100m"])
        self.assertEqual(56.34, result["metrics"]["weighted_aov_yuan"])
        self.assertEqual(10.16, result["metrics"]["weighted_fulfillment_cost_yuan"])
        self.assertEqual(4.1, result["metrics"]["weighted_subsidy_cost_yuan"])
        self.assertEqual(42.08, result["metrics"]["modeled_unit_contribution_yuan"])
        self.assertEqual(11_674_160_000, result["metrics"]["modeled_annual_contribution_yuan"])
        self.assertEqual(9, result["metrics"]["declared_loss_city_count"])
        self.assertEqual(
            ["泉州", "惠州", "徐州"],
            [item["city"] for item in result["rankings"]["scale_leaders"]],
        )
        self.assertEqual(
            ["赣州", "桂林", "宜昌"],
            [item["city"] for item in result["rankings"]["growth_leaders"]],
        )
        self.assertIn("不等于收入、毛利或净利润", result["caveats"][2])
        self.assertEqual("supported_with_caveat", result["claims"][2]["status"])
        self.assertEqual(1, len(result["evidence"]))
        self.assertEqual(WORKSHEET, result["evidence"][0]["worksheet"])
        self.assertEqual(10, result["evidence"][0]["included_rows"])
        self.assertTrue(result["evidence"][0]["content_sha256"])
        self.assertTrue(
            all(
                claim["evidence_ids"] == [result["evidence"][0]["evidence_id"]]
                for claim in result["claims"]
            )
        )

    def test_quality_blockers_prevent_business_metrics_from_being_shared(self):
        snapshot = self._snapshot()
        rows = [dict(row) for row in snapshot.rows]
        rows[1]["城市编号"] = rows[0]["城市编号"]
        rows[2]["订单结构_其他占比（%）"] = "9"

        result = evaluate_city_portfolio(replace(snapshot, rows=tuple(rows)))

        self.assertEqual("needs_revision", result["assessment"])
        self.assertGreaterEqual(result["quality"]["blocker_count"], 2)
        self.assertIsNone(result["metrics"])
        self.assertEqual({}, result["rankings"])
        self.assertEqual([], result["claims"])
        failures = {
            item["check_id"]: item for item in result["quality"]["checks"]
            if item["status"] == "fail"
        }
        self.assertIn("city_grain_unique", failures)
        self.assertIn("order_mix_reconciles", failures)

    def test_wrong_schema_returns_stable_repair_code(self):
        snapshot = self._snapshot()
        with self.assertRaises(BusinessAcceptanceError) as context:
            evaluate_city_portfolio(replace(snapshot, columns=("month", "sales")))
        self.assertEqual("business_scenario_columns_missing", context.exception.code)


class CityMonthlyPnlBusinessAcceptanceTests(unittest.TestCase):
    def _snapshot(self):
        return load_tabular_snapshot(
            MONTHLY_SAMPLE,
            source_id="city-monthly-pnl-sample",
            date_column="月份",
            file_name=MONTHLY_SAMPLE.name,
        )

    def test_monthly_pnl_reconciles_and_compares_matched_periods(self):
        result = evaluate_city_monthly_pnl(self._snapshot())

        self.assertEqual("share_with_caveats", result["assessment"])
        self.assertEqual(0, result["quality"]["blocker_count"])
        self.assertEqual(8, result["quality"]["passed_count"])
        metrics = result["metrics"]
        self.assertEqual("2026-01 至 2026-03", metrics["current_period"])
        self.assertEqual("2025-01 至 2025-03", metrics["comparison_period"])
        self.assertEqual(3, metrics["city_count"])
        self.assertEqual(2465, metrics["current_orders_10k"])
        self.assertEqual(145865, metrics["current_gmv_10k"])
        self.assertEqual(18780.3, metrics["current_revenue_10k"])
        self.assertEqual(1636.225, metrics["current_operating_profit_10k"])
        self.assertEqual(15.42, metrics["revenue_yoy_pct"])
        self.assertEqual(8.71, metrics["current_operating_margin_pct"])
        self.assertEqual(935.725, metrics["operating_profit_delta_10k"])
        self.assertEqual(
            ["泉州", "徐州", "桂林"],
            [item["city"] for item in result["rankings"]["profit_leaders"]],
        )
        self.assertEqual(
            ["桂林"],
            [item["city"] for item in result["rankings"]["attention_candidates"]],
        )
        self.assertTrue(all(claim["evidence_ids"] for claim in result["claims"]))

    def test_period_gap_and_broken_profit_bridge_block_sharing(self):
        snapshot = self._snapshot()
        rows = [dict(row) for row in snapshot.rows]
        rows = [
            row for row in rows
            if not (row["城市编号"] == "C9" and row["月份"] == "2026-02")
        ]
        rows[0]["报表经营利润（万元）"] = "999"

        result = evaluate_city_monthly_pnl(replace(snapshot, rows=tuple(rows)))

        self.assertEqual("needs_revision", result["assessment"])
        self.assertIsNone(result["metrics"])
        failures = {
            item["check_id"] for item in result["quality"]["checks"]
            if item["status"] == "fail"
        }
        self.assertIn("like_for_like_period_coverage", failures)
        self.assertIn("pnl_bridge_reconciles", failures)


class CityUserSupplyBusinessAcceptanceTests(unittest.TestCase):
    def _snapshot(self):
        return load_tabular_snapshot(
            SAMPLE,
            source_id="city-user-supply-sample",
            date_column="",
            worksheet=WORKSHEET,
            file_name=SAMPLE.name,
        )

    def test_user_supply_snapshot_reconciles_efficiency_metrics(self):
        result = evaluate_city_user_supply(self._snapshot())

        self.assertEqual("city_user_supply_v1", result["scenario"]["id"])
        self.assertEqual("share_with_caveats", result["assessment"])
        self.assertEqual(0, result["quality"]["blocker_count"])
        self.assertEqual(7, result["quality"]["passed_count"])
        self.assertEqual(
            {
                "monthly_active_users_10k": 718.0,
                "high_value_users_10k": 150.2,
                "weighted_high_value_user_share_pct": 20.92,
                "daily_orders_thousand": 760.0,
                "active_merchants": 12470,
                "user_order_frequency_per_day": 0.106,
                "merchant_productivity_orders_per_day": 60.95,
                "user_order_frequency_median": 0.103,
                "merchant_productivity_median": 59.52,
                "dual_efficiency_attention_count": 5,
                "city_count": 10,
            },
            result["metrics"],
        )
        self.assertEqual(
            ["泉州", "惠州", "徐州"],
            [item["city"] for item in result["rankings"]["high_value_user_leaders"]],
        )
        self.assertEqual(
            ["惠州", "泉州", "徐州"],
            [item["city"] for item in result["rankings"]["user_engagement_leaders"]],
        )
        self.assertEqual(
            ["惠州", "泉州", "南宁"],
            [item["city"] for item in result["rankings"]["merchant_productivity_leaders"]],
        )
        self.assertEqual(
            ["赣州", "宜昌", "桂林", "威海", "洛阳"],
            [item["city"] for item in result["rankings"]["dual_efficiency_attention"]],
        )
        self.assertEqual(3, len(result["claims"]))
        self.assertTrue(all(claim["evidence_ids"] for claim in result["claims"]))
        self.assertIn("不构成因果判断", result["caveats"][-1])

    def test_non_positive_efficiency_denominator_blocks_metrics(self):
        snapshot = self._snapshot()
        rows = [dict(row) for row in snapshot.rows]
        rows[0]["月度活跃用户数（万）"] = "0"

        result = evaluate_city_user_supply(replace(snapshot, rows=tuple(rows)))

        self.assertEqual("needs_revision", result["assessment"])
        self.assertIsNone(result["metrics"])
        failures = {
            item["check_id"]
            for item in result["quality"]["checks"]
            if item["status"] == "fail"
        }
        self.assertIn("user_supply_denominators_positive", failures)


class CityPortfolioBusinessAcceptanceHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config.update(TESTING=True)

    def setUp(self):
        self.client = self.app.test_client()
        self.sid = f"business-acceptance-{uuid.uuid4().hex[:12]}"
        session_manager.get_or_create(self.sid)

    def tearDown(self):
        session_manager.remove(self.sid)

    def _upload_sample(self) -> str:
        response = self.client.post(
            f"/api/session/{self.sid}/upload",
            data={"file": (io.BytesIO(SAMPLE.read_bytes()), SAMPLE.name)},
            content_type="multipart/form-data",
        )
        self.assertEqual(200, response.status_code)
        return response.get_json()["added"][0]["source_id"]

    def _upload_monthly_sample(self) -> str:
        response = self.client.post(
            f"/api/session/{self.sid}/upload",
            data={"file": (io.BytesIO(MONTHLY_SAMPLE.read_bytes()), MONTHLY_SAMPLE.name)},
            content_type="multipart/form-data",
        )
        self.assertEqual(200, response.status_code)
        return response.get_json()["added"][0]["source_id"]

    def test_uploaded_business_workbook_runs_through_session_api(self):
        source_id = self._upload_sample()
        response = self.client.post(
            f"/api/session/{self.sid}/pfs/business-acceptance",
            json={
                "source_id": source_id,
                "worksheet": WORKSHEET,
                "scenario": "city_portfolio_v1",
            },
        )

        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual("Sample-data.xlsx", payload["result"]["source"]["file_name"])
        self.assertEqual(WORKSHEET, payload["result"]["source"]["worksheet"])
        self.assertEqual(156.293, payload["result"]["metrics"]["annual_gmv_100m"])

    def test_unknown_scenario_is_rejected_explicitly(self):
        source_id = self._upload_sample()
        response = self.client.post(
            f"/api/session/{self.sid}/pfs/business-acceptance",
            json={"source_id": source_id, "scenario": "magic_forecast"},
        )

        self.assertEqual(400, response.status_code)
        self.assertEqual("business_scenario_not_supported", response.get_json()["code"])

    def test_auto_detects_uploaded_monthly_pnl_scenario(self):
        source_id = self._upload_monthly_sample()
        response = self.client.post(
            f"/api/session/{self.sid}/pfs/business-acceptance",
            json={"source_id": source_id, "scenario": "auto"},
        )

        self.assertEqual(200, response.status_code)
        result = response.get_json()["result"]
        self.assertEqual("city_monthly_pnl_v1", result["scenario"]["id"])
        self.assertEqual(15.42, result["metrics"]["revenue_yoy_pct"])
        self.assertEqual("桂林", result["rankings"]["attention_candidates"][0]["city"])
        self.assertTrue(result["run_id"].startswith("business-city_monthly_pnl_v1-"))
        self.assertEqual(3, len(result["claims"]))
        self.assertTrue(all(claim["claim"] for claim in result["claims"]))
        self.assertTrue(all(claim["evidence_ids"] for claim in result["claims"]))
        self.assertEqual("supported_with_caveat", result["claims"][2]["status"])

    def test_uploaded_city_snapshot_runs_explicit_user_supply_scenario(self):
        source_id = self._upload_sample()
        response = self.client.post(
            f"/api/session/{self.sid}/pfs/business-acceptance",
            json={
                "source_id": source_id,
                "worksheet": WORKSHEET,
                "scenario": "city_user_supply_v1",
            },
        )

        self.assertEqual(200, response.status_code)
        result = response.get_json()["result"]
        self.assertEqual("city_user_supply_v1", result["scenario"]["id"])
        self.assertEqual(20.92, result["metrics"]["weighted_high_value_user_share_pct"])
        self.assertEqual("赣州", result["rankings"]["dual_efficiency_attention"][0]["city"])
        self.assertTrue(result["run_id"].startswith("business-city_user_supply_v1-"))

    def test_monthly_business_result_exports_server_recomputed_json_and_csv(self):
        source_id = self._upload_monthly_sample()
        request_payload = {
            "source_id": source_id,
            "scenario": "city_monthly_pnl_v1",
            "run_id": "monthly-business-export",
        }

        json_response = self.client.post(
            f"/api/session/{self.sid}/pfs/export",
            json={**request_payload, "format": "json"},
        )
        self.assertEqual(200, json_response.status_code)
        exported = json_response.get_json()["result"]
        self.assertEqual(15.42, exported["metrics"]["revenue_yoy_pct"])
        self.assertEqual(3, len(exported["claims"]))
        self.assertTrue(json_response.headers["X-PFS-Source-SHA256"])

        csv_response = self.client.post(
            f"/api/session/{self.sid}/pfs/export",
            json={**request_payload, "format": "csv"},
        )
        self.assertEqual(200, csv_response.status_code)
        csv_body = csv_response.get_data(as_text=True)
        self.assertIn("scenario,name,城市月度损益诊断", csv_body)
        self.assertIn("metric,revenue_yoy_pct,15.42", csv_body)
        self.assertIn("evidence,", csv_body)
        self.assertNotIn("approval=", csv_body)


class CityPortfolioBusinessAcceptanceUiContractTests(unittest.TestCase):
    def test_workbench_exposes_business_acceptance_action_and_renderer(self):
        template = (ROOT / "templates" / "agent_chat.html").read_text(encoding="utf-8")
        script = (ROOT / "frontend" / "legacy" / "pfs-report-preview.js").read_text(
            encoding="utf-8"
        )

        self.assertIn('id="pfs-business-run"', template)
        self.assertIn("真实业务验收", template)
        self.assertIn("/pfs/business-acceptance", script)
        self.assertIn("function renderBusinessAcceptance", script)
        self.assertIn("可汇报，但必须带限制条件", script)
        self.assertIn("scenario,", script)
        self.assertIn("city_monthly_pnl_v1", script)
        self.assertIn("city_user_supply_v1", script)
        self.assertIn('id="pfs-business-scenario-select"', template)
        self.assertIn("用户—供给效率", template)
        self.assertIn("月度损益", template)
        self.assertIn("不把贡献余量冒充利润", template)


if __name__ == "__main__":
    unittest.main()
