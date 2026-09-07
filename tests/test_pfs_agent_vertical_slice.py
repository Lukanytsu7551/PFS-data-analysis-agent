import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from agent.agent import BusinessAgent
from agent.tools.business.data import _build_temporal_holdout_evaluation
from data.sources.csv import CSVDataSource


FIXTURE = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "pfs_sales.csv"


class PfsAgentVerticalSliceTests(unittest.TestCase):
    """Verify the local PFS path from source data to analysis artifacts."""

    @staticmethod
    def _finish_generator(generator):
        while True:
            try:
                next(generator)
            except StopIteration as stopped:
                return stopped.value

    def setUp(self):
        source = CSVDataSource(str(FIXTURE), "pfs_sales.csv")
        self.agent = BusinessAgent(
            client=None,
            model="pfs-local-test",
            data_source=source,
            session_id="pfs-vertical-slice",
        )

    def test_read_analyze_table_chart_and_profile(self):
        schema = self.agent._tool_get_schema()
        self.assertIn("pfs_sales", schema)
        self.assertIn("sales_amount", schema)

        detail = self.agent._tool_get_table_detail("pfs_sales")
        self.assertIn("Table: pfs_sales", detail)
        self.assertIn("sales_amount", detail)

        query = self.agent._tool_query_data(
            "SELECT region, SUM(sales_amount) AS total_sales "
            "FROM pfs_sales GROUP BY region ORDER BY total_sales DESC"
        )
        self.assertIn("华东", query)
        self.assertIn("42000", query)

        table = self.agent._tool_create_analysis_table(
            "SELECT region, SUM(sales_amount) AS total_sales FROM pfs_sales GROUP BY region",
            "pfs_region_summary",
        )
        self.assertIn("pfs_region_summary", table)

        selection = self.agent._tool_select_chart("按地区比较销售额", ["region", "total_sales"])
        self.assertIn("Bar_Chart", selection)

        chart = self.agent._tool_generate_chart(
            "Bar_Chart",
            "SELECT region, SUM(sales_amount) AS total_sales "
            "FROM pfs_sales GROUP BY region ORDER BY total_sales DESC",
            {"x": "region", "y": "total_sales"},
            "地区销售额",
        )
        self.assertNotIn("error", chart)
        self.assertIn("/static/vendor/plotly.min.js", chart["html"])
        self.assertGreater(len(chart["html"]), 500)

        profile = self.agent._tool_profile_data("pfs_sales")
        self.assertGreaterEqual(len(profile["charts"]), 1)
        self.assertIn("Plotly", profile["charts"][0])
        self.assertIn("总行数：**9**", profile["text"])
        self.assertIn("sales_amount", profile["text"])

    def test_run_analysis_persists_the_declared_result_tables(self):
        result = self.agent._tool_run_analysis(
            "Regression",
            "SELECT month, region, product, sales_amount FROM pfs_sales",
            "sales_amount",
            n_deciles=1,
        )
        self.assertNotIn("requires 'analysis_name'", result)
        self.assertIn("本次分析已生成的可查询结果表", result)
        self.assertIn("analysis_metrics", result)
        tables = set(self.agent.data_source.list_tables())
        self.assertTrue(
            {"analysis_result", "analysis_breakdown", "analysis_metrics"}.issubset(tables)
        )

    def test_named_analysis_result_mapping_is_normalized(self):
        with TemporaryDirectory(prefix="pfs-screening-") as raw:
            path = Path(raw) / "screening.csv"
            pd.DataFrame(
                {
                    "target": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
                    "feature_a": [2, 4, 5, 8, 10, 12, 14, 16, 18, 20],
                    "feature_b": [10, 9, 8, 7, 6, 5, 4, 3, 2, 1],
                }
            ).to_csv(path, index=False)
            source = CSVDataSource(str(path), "screening.csv")
            agent = BusinessAgent(
                client=None,
                model="pfs-screening-test",
                data_source=source,
                session_id="pfs-screening-test",
            )
            result = agent._tool_run_analysis(
                "Univariate_Screening",
                "SELECT target, feature_a, feature_b FROM screening",
                "target",
                groupby_column="0.05",
            )
            self.assertIn("analysis_metrics", result)
            self.assertTrue(
                {"analysis_result", "analysis_breakdown", "analysis_metrics"}.issubset(
                    set(source.list_tables())
                )
            )

    def test_invalid_inputs_fail_closed_without_mutating_source(self):
        before = set(self.agent.data_source.list_tables())
        self.assertIn("SQL Error", self.agent._tool_query_data("DELETE FROM pfs_sales"))
        self.assertIn("SQL Error", self.agent._tool_query_data("SELECT missing FROM pfs_sales"))
        self.assertIn("not found", self.agent._tool_get_table_detail("missing_table"))
        failed_chart = self.agent._tool_generate_chart(
            "Bar_Chart",
            "SELECT region, SUM(sales_amount) AS total_sales FROM pfs_sales GROUP BY region",
            {"x": "missing", "y": "total_sales"},
        )
        self.assertIn("error", failed_chart)
        self.assertEqual(before, set(self.agent.data_source.list_tables()))

    def test_background_query_and_table_paths_repeat_sql_safety_check(self):
        before = set(self.agent.data_source.list_tables())
        query_result = self._finish_generator(self.agent._tool_query_data_with_jobs("DELETE FROM pfs_sales"))
        self.assertIn("SQL Error", query_result[0])

        create_result = self._finish_generator(
            self.agent._tool_create_analysis_table_with_jobs(
                "CREATE TABLE leaked AS SELECT * FROM pfs_sales",
                "leaked",
            )
        )
        self.assertIn("Error building analysis table", create_result[0])
        self.assertEqual(before, set(self.agent.data_source.list_tables()))

    def test_analysis_tables_are_replaceable_and_deletable_but_raw_table_is_protected(self):
        blocked = self.agent._tool_create_analysis_table(
            "SELECT region, SUM(sales_amount) AS total_sales FROM pfs_sales GROUP BY region",
            "pfs_sales",
        )
        self.assertIn("不能覆盖原始数据表", blocked)
        self.assertIn("pfs_sales", self.agent.data_source.list_tables())

        created = self.agent._tool_create_analysis_table(
            "SELECT region, SUM(sales_amount) AS total_sales FROM pfs_sales GROUP BY region",
            "pfs_region_summary",
        )
        self.assertIn("pfs_region_summary", created)
        replaced = self.agent._tool_create_analysis_table(
            "SELECT region, COUNT(*) AS row_count FROM pfs_sales GROUP BY region",
            "pfs_region_summary",
        )
        self.assertIn("pfs_region_summary", replaced)

        deleted = self.agent._tool_delete_analysis_tables(["pfs_region_summary"], confirm=True)
        self.assertIn("pfs_region_summary", deleted)
        self.assertNotIn("pfs_region_summary", self.agent.data_source.list_tables())

        raw_delete = self.agent._tool_delete_analysis_tables(["pfs_sales"], confirm=True)
        self.assertIn("原始源表和无法判定的表会被保护", raw_delete)
        self.assertIn("pfs_sales", self.agent.data_source.list_tables())

    def test_clean_data_operations_write_derived_table_and_preserve_source(self):
        csv = "month,region,sales_amount\n2026-01,华东,10\n2026-02,华东,\n2026-03,华东,30\n"
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "quality.csv"
            path.write_text(csv, encoding="utf-8")
            source = CSVDataSource(str(path), path.name)
            agent = BusinessAgent(
                client=None, model="pfs-clean-test", data_source=source, session_id="pfs-clean"
            )

            filled = agent._tool_clean_data("fill_na", table_name="quality", fill_method="mean")
            self.assertIn("清洗结果已保存为表 `cleaned_data`", filled)
            cleaned, error = source.execute_query('SELECT sales_amount FROM "cleaned_data" ORDER BY month')
            self.assertFalse(error)
            self.assertEqual([10.0, 20.0, 30.0], cleaned["sales_amount"].tolist())
            original, error = source.execute_query('SELECT sales_amount FROM "quality" ORDER BY month')
            self.assertFalse(error)
            self.assertTrue(original["sales_amount"].isna().iloc[1])

            winsorized = agent._clean_dataframe(
                cleaned, "winsorize", lower_pct=10, upper_pct=90, columns=["sales_amount"]
            )[0]
            self.assertLessEqual(float(winsorized["sales_amount"].max()), 28.0)
            trimmed, _summary = agent._clean_dataframe(
                cleaned, "trimming", trim_column="sales_amount", min_val=15, max_val=25
            )
            self.assertEqual([20.0], trimmed["sales_amount"].tolist())

            before_tables = set(source.list_tables())
            failed = agent._tool_clean_data("unsupported", table_name="quality")
            self.assertIn("未知操作", failed)
            self.assertEqual(before_tables, set(source.list_tables()))

    def test_time_series_analysis_persists_real_model_quality_table(self):
        result_df = pd.DataFrame(
            [
                {"ds": "2026-01-01", "segment": "historical", "y_actual": 100.0, "y_pred": 95.0},
                {"ds": "2026-02-01", "segment": "historical", "y_actual": 120.0, "y_pred": 125.0},
                {"ds": "2026-03-01", "segment": "forecast", "y_actual": float("nan"), "y_pred": 130.0},
            ]
        )
        entry = {"output_tables": ["analysis_result", "analysis_breakdown", "analysis_metrics"]}
        writes = []

        def capture(frame, table_name):
            writes.append((table_name, frame.copy()))

        with patch.object(self.agent, "_write_analysis_df", side_effect=capture):
            markdown = self.agent._finalize_analysis_result(
                entry,
                (result_df, pd.DataFrame(), pd.DataFrame(), "基础分析结果"),
                "Time_Series_Prophet",
                "SELECT * FROM pfs_sales",
                "sales_amount",
                4,
            )

        evaluation_tables = [frame for name, frame in writes if name == "analysis_evaluation"]
        self.assertEqual(1, len(evaluation_tables))
        self.assertEqual({"mae", "rmse", "bias", "r2", "mape", "wape", "smape"}, set(evaluation_tables[0]["metric"]))
        self.assertIn("模型质量评估（Time_Series_Prophet）", markdown)
        self.assertIn("历史配对行 2/3", markdown)
        self.assertIn("未来 forecast 行排除 1 行", markdown)

    def test_classification_and_regression_outputs_share_model_quality_table(self):
        cases = [
            (
                "Regression",
                pd.DataFrame(
                    {
                        "y_actual": [10.0, 20.0, 30.0],
                        "y_pred": [11.0, 18.0, 33.0],
                    }
                ),
                pd.DataFrame(columns=["metric", "value"]),
                {"mae", "rmse", "bias", "r2"},
            ),
            (
                "Decision_Tree",
                pd.DataFrame(
                    {
                        "actual": ["low", "low", "high"],
                        "predicted": ["low", "high", "high"],
                        "count": [2, 1, 3],
                    }
                ),
                pd.DataFrame(columns=["metric", "value"]),
                {"accuracy", "macro_f1", "balanced_accuracy"},
            ),
        ]
        for analysis_name, breakdown, extra, expected_metrics in cases:
            with self.subTest(analyzer=analysis_name):
                writes = []

                def capture(frame, table_name):
                    writes.append((table_name, frame.copy()))

                result_df = (
                    pd.DataFrame([{"metric": "accuracy", "value": 0.83}])
                    if analysis_name == "Decision_Tree"
                    else pd.DataFrame(columns=["feature"])
                )
                with patch.object(self.agent, "_write_analysis_df", side_effect=capture):
                    markdown = self.agent._finalize_analysis_result(
                        {"output_tables": ["analysis_result", "analysis_breakdown", "analysis_metrics"]},
                        (result_df, breakdown, extra, "基础分析结果"),
                        analysis_name,
                        "SELECT * FROM pfs_sales",
                        "sales_amount",
                        4,
                    )
                evaluation = [frame for name, frame in writes if name == "analysis_evaluation"]
                self.assertEqual(1, len(evaluation))
                self.assertEqual(expected_metrics, set(evaluation[0]["metric"]))
                self.assertIn("模型质量评估", markdown)

    def test_temporal_holdout_option_refits_prefix_and_aligns_future_source_rows(self):
        dates = pd.date_range("2026-01-01", periods=12, freq="D")
        frame = pd.DataFrame({"date": dates, "sales": [100 + index for index in range(12)]})
        forecast = pd.DataFrame(
            {
                "ds": dates[-2:],
                "y_actual": [float("nan"), float("nan")],
                "y_pred": [110.0, 111.0],
                "segment": ["forecast", "forecast"],
            }
        )
        fake_entry = {"output_tables": ["analysis_result", "analysis_breakdown", "analysis_metrics"]}
        fake_return = (forecast, pd.DataFrame(), pd.DataFrame(), "训练段结果")
        with patch(
            "agent.tools.business.data._execute_analysis",
            return_value=(fake_entry, fake_return),
        ) as execute:
            evaluation = _build_temporal_holdout_evaluation(
                "Time_Series_Prophet",
                frame,
                "sales",
                "date",
                2,
                {"evaluation_mode": "temporal_holdout", "holdout_size": 2},
            )

        execute.assert_called_once()
        self.assertEqual(10, len(execute.call_args.args[1]))
        self.assertEqual("temporal_holdout", evaluation["time_series"]["evaluation_scope"])
        self.assertEqual(2, evaluation["scope"]["paired_rows"])
        self.assertEqual(1.0, evaluation["scope"]["paired_ratio"])


if __name__ == "__main__":
    unittest.main()
