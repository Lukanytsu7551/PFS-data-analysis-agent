import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agent.agent import BusinessAgent
from data.sources.csv import CSVDataSource


FIXTURE = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "pfs_sales.csv"


class PfsAgentVerticalSliceTests(unittest.TestCase):
    """Verify the local PFS path from source data to analysis artifacts."""

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

        query = self.agent._tool_query_data(
            "SELECT region, SUM(sales_amount) AS total_sales "
            "FROM pfs_sales GROUP BY region ORDER BY total_sales DESC"
        )
        self.assertIn("华东", query)
        self.assertIn("42000", query)

        table = self.agent._tool_create_analysis_table(
            "SELECT region, SUM(sales_amount) AS total_sales "
            "FROM pfs_sales GROUP BY region",
            "pfs_region_summary",
        )
        self.assertIn("pfs_region_summary", table)

        selection = self.agent._tool_select_chart(
            "按地区比较销售额", ["region", "total_sales"]
        )
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

    def test_clean_data_operations_write_derived_table_and_preserve_source(self):
        csv = (
            "month,region,sales_amount\n"
            "2026-01,华东,10\n"
            "2026-02,华东,\n"
            "2026-03,华东,30\n"
        )
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

            winsorized = agent._clean_dataframe(cleaned, "winsorize", lower_pct=10, upper_pct=90, columns=["sales_amount"])[0]
            self.assertLessEqual(float(winsorized["sales_amount"].max()), 28.0)
            trimmed, _summary = agent._clean_dataframe(cleaned, "trimming", trim_column="sales_amount", min_val=15, max_val=25)
            self.assertEqual([20.0], trimmed["sales_amount"].tolist())

            before_tables = set(source.list_tables())
            failed = agent._tool_clean_data("unsupported", table_name="quality")
            self.assertIn("未知操作", failed)
            self.assertEqual(before_tables, set(source.list_tables()))


if __name__ == "__main__":
    unittest.main()
