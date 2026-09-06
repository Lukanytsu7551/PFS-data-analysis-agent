import io
import unittest
import uuid

from api import create_app
from api.state import session_manager
from pfs_agent.metric_catalog import (
    DEFAULT_METRIC_CATALOG,
    MetricCatalog,
    MetricCatalogEntry,
    MetricCatalogError,
)
from pfs_agent.reporting import MetricContract


class PfsMetricCatalogTests(unittest.TestCase):
    def test_default_catalog_is_stable_and_versioned(self):
        entries = DEFAULT_METRIC_CATALOG.entries()
        self.assertEqual(
            ["profit_amount", "revenue", "sales_amount"],
            [entry.metric.metric_id for entry in entries],
        )
        self.assertEqual(["v1", "v1", "v1"], [entry.metric.version for entry in entries])
        self.assertEqual("PFS 数据分析", entries[-1].owner)
        self.assertEqual(("销售额", "销售金额", "销售"), entries[-1].aliases)
        self.assertEqual("sales_amount", DEFAULT_METRIC_CATALOG.resolve("sales_amount").metric_id)

    def test_catalog_rejects_duplicate_versions_and_unknown_versions(self):
        metric = MetricContract(
            metric_id="orders",
            label="订单量",
            formula="SUM(orders)",
            value_column="orders",
            date_column="month",
            dimension="region",
            version="v1",
        )
        catalog = MetricCatalog((MetricCatalogEntry(metric, "业务", "订单量"),))
        with self.assertRaisesRegex(MetricCatalogError, "already registered") as duplicate:
            catalog.register(MetricCatalogEntry(metric, "业务", "订单量"))
        self.assertEqual("metric_version_duplicate", duplicate.exception.code)
        with self.assertRaisesRegex(MetricCatalogError, "available: v1") as unknown:
            catalog.resolve("orders", "v2")
        self.assertEqual("metric_version_not_found", unknown.exception.code)

    def test_api_lists_and_filters_metric_catalog(self):
        app = create_app()
        app.config.update(TESTING=True)
        client = app.test_client()

        response = client.get("/api/pfs/metrics")
        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(3, payload["count"])
        self.assertEqual("sales_amount", payload["metrics"][-1]["metric_id"])
        self.assertEqual("v1", payload["metrics"][-1]["version"])
        self.assertEqual("PFS 数据分析", payload["metrics"][-1]["owner"])

        selected = client.get("/api/pfs/metrics?metric_id=revenue&version=v1")
        self.assertEqual(200, selected.status_code)
        self.assertEqual(["revenue"], [item["metric_id"] for item in selected.get_json()["metrics"]])

        invalid = client.get("/api/pfs/metrics?metric_id=revenue&version=v9")
        self.assertEqual(400, invalid.status_code)
        self.assertEqual("metric_version_not_found", invalid.get_json()["code"])

    def test_uploaded_analysis_can_explicitly_select_catalog_metric(self):
        app = create_app()
        app.config.update(TESTING=True)
        client = app.test_client()
        sid = f"metric-catalog-{uuid.uuid4().hex[:12]}"
        session_manager.get_or_create(sid)
        try:
            csv = ("month,region,revenue\n2026-01,华东,1200\n2026-01,华南,800\n").encode("utf-8")
            uploaded = client.post(
                f"/api/session/{sid}/upload",
                data={"file": (io.BytesIO(csv), "revenue.csv")},
                content_type="multipart/form-data",
            )
            self.assertEqual(200, uploaded.status_code)
            source_id = uploaded.get_json()["added"][0]["source_id"]
            analyzed = client.post(
                f"/api/session/{sid}/pfs/analyze",
                json={
                    "source_id": source_id,
                    "run_id": "catalog-analysis",
                    "catalog_metric_id": "revenue",
                    "catalog_version": "v1",
                },
            )
            self.assertEqual(200, analyzed.status_code)
            result = analyzed.get_json()["result"]
            self.assertEqual(2000, result["total"])
            self.assertEqual("revenue", result["metric"]["metric_id"])
            self.assertEqual("v1", result["metric"]["version"])

            missing = client.post(
                f"/api/session/{sid}/pfs/analyze",
                json={
                    "source_id": source_id,
                    "run_id": "catalog-missing-column",
                    "catalog_metric_id": "sales_amount",
                },
            )
            self.assertEqual(400, missing.status_code)
            self.assertEqual("metric_catalog_columns_missing", missing.get_json()["code"])
        finally:
            session_manager.remove(sid)


if __name__ == "__main__":
    unittest.main()
