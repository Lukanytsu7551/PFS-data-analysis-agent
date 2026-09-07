import unittest
import tempfile
from pathlib import Path

from pfs_agent.reporting import AnalysisRequest, MetricContract, analyze_csv, analyze_file


FIXTURE = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "pfs_sales.csv"
METRIC = MetricContract(
    metric_id="sales_amount",
    label="销售额",
    formula="SUM(sales_amount)",
    value_column="sales_amount",
    date_column="month",
    dimension="region",
)


class PfsReportingTests(unittest.TestCase):
    def test_fixture_produces_stable_grouped_result_and_evidence(self):
        result = analyze_csv(
            FIXTURE,
            metric=METRIC,
            request=AnalysisRequest(
                run_id="run-fixture-001",
                metric_id="sales_amount",
                dimension="region",
                date_from="2026-01",
                date_to="2026-03",
            ),
            source_id="pfs-fixture-sales",
        )
        self.assertEqual("completed", result.status)
        self.assertEqual(100000, result.total)
        self.assertEqual(
            ["华东", "华南", "华北"],
            [item["dimension"] for item in result.groups],
        )
        self.assertEqual([42000, 33000, 25000], [item["value"] for item in result.groups])
        self.assertEqual(2, len(result.claims))
        self.assertEqual(1, len(result.evidence))
        self.assertTrue(result.claims[0]["evidence_ids"])
        self.assertEqual(
            result.evidence[0].content_sha256,
            result.snapshot.content_sha256,
        )

    def test_date_filter_changes_total_without_changing_contract(self):
        result = analyze_csv(
            FIXTURE,
            metric=METRIC,
            request=AnalysisRequest(
                run_id="run-fixture-002",
                metric_id="sales_amount",
                dimension="region",
                date_from="2026-02",
                date_to="2026-02",
            ),
            source_id="pfs-fixture-sales",
        )
        self.assertEqual(33000, result.total)
        self.assertEqual("华东", result.groups[0]["dimension"])
        self.assertEqual(14000, result.groups[0]["value"])

    @unittest.skipUnless(__import__("importlib.util").util.find_spec("openpyxl"), "openpyxl is not installed")
    def test_xlsx_uses_the_same_metric_and_evidence_contract(self):
        from openpyxl import Workbook

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sales.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.append(["month", "region", "sales_amount"])
            sheet.append(["2026-01", "华东", 42000])
            sheet.append(["2026-02", "华南", 33000])
            workbook.save(path)
            result = analyze_file(
                path,
                metric=METRIC,
                request=AnalysisRequest(
                    run_id="run-xlsx-001",
                    metric_id="sales_amount",
                    dimension="region",
                ),
                source_id="xlsx-sales",
            )

        self.assertEqual(75000, result.total)
        self.assertEqual(2, result.snapshot.row_count)
        self.assertEqual("xlsx-sales", result.snapshot.source_id)
        self.assertEqual("xlsx", result.snapshot.file_name.rsplit(".", 1)[-1])
        self.assertEqual(result.evidence[0].content_sha256, result.snapshot.content_sha256)


if __name__ == "__main__":
    unittest.main()
