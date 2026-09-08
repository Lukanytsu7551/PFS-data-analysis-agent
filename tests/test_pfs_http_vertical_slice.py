import io
import uuid
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from api import create_app
from api.state import session_manager


class PfsHttpVerticalSliceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config.update(TESTING=True)

    def setUp(self):
        self.client = self.app.test_client()
        self.sid = f"pfs-http-{uuid.uuid4().hex[:12]}"
        session_manager.get_or_create(self.sid)

    def tearDown(self):
        session_manager.remove(self.sid)

    def _upload(self):
        csv = (
            "month,region,sales_amount\n"
            "2026-01,华东,1200\n"
            "2026-01,华南,800\n"
            "2026-02,华东,1500\n"
            "2026-02,华南,900\n"
        ).encode("utf-8")
        return self.client.post(
            f"/api/session/{self.sid}/upload",
            data={"file": (io.BytesIO(csv), "quarterly_sales.csv")},
            content_type="multipart/form-data",
        )

    def _upload_profit_csv(self):
        csv = (
            "month,region,sales_amount,profit_amount\n"
            "2026-01,华东,1200,300\n"
            "2026-01,华南,800,160\n"
            "2026-02,华东,1500,450\n"
            "2026-02,华南,900,180\n"
        ).encode("utf-8")
        return self.client.post(
            f"/api/session/{self.sid}/upload",
            data={"file": (io.BytesIO(csv), "profit_sales.csv")},
            content_type="multipart/form-data",
        )

    def test_upload_list_and_analyze_uploaded_csv(self):
        uploaded = self._upload()
        self.assertEqual(200, uploaded.status_code)
        upload_payload = uploaded.get_json()
        self.assertTrue(upload_payload["ok"])
        source_id = upload_payload["added"][0]["source_id"]

        listed = self.client.get(f"/api/session/{self.sid}/pfs/sources")
        self.assertEqual(200, listed.status_code)
        listed_payload = listed.get_json()
        self.assertTrue(listed_payload["ok"])
        self.assertEqual(source_id, listed_payload["sources"][0]["source_id"])
        self.assertEqual(4, listed_payload["sources"][0]["row_count"])

        analyzed = self.client.post(
            f"/api/session/{self.sid}/pfs/analyze",
            json={
                "source_id": source_id,
                "run_id": "http-upload-run",
                "value_column": "sales_amount",
                "date_column": "month",
                "dimension": "region",
                "date_from": "2026-01",
                "date_to": "2026-02",
            },
        )
        self.assertEqual(200, analyzed.status_code)
        result = analyzed.get_json()["result"]
        self.assertEqual(4400, result["total"])
        self.assertEqual(["华东", "华南"], [item["dimension"] for item in result["groups"]])
        self.assertEqual(2700, result["groups"][0]["value"])
        self.assertEqual(1, len(result["evidence"]))
        self.assertEqual("http-upload-run", result["request"]["run_id"])

    def test_fixture_report_export_returns_server_recomputed_json_and_csv(self):
        json_response = self.client.post(
            "/api/pfs/export",
            json={"format": "json", "run_id": "fixture-export-json"},
        )
        self.assertEqual(200, json_response.status_code)
        self.assertIn(
            'attachment; filename="pfs-report-fixture-export-json.json"',
            json_response.headers["Content-Disposition"],
        )
        json_payload = json_response.get_json()
        self.assertTrue(json_payload["ok"])
        self.assertEqual(100000, json_payload["result"]["total"])
        self.assertEqual("fixture-export-json", json_payload["result"]["run_id"])
        self.assertTrue(json_response.headers["X-PFS-Source-SHA256"])

        csv_response = self.client.post(
            "/api/pfs/export",
            json={"format": "csv", "run_id": "fixture-export-csv"},
        )
        self.assertEqual(200, csv_response.status_code)
        self.assertIn(
            'attachment; filename="pfs-report-fixture-export-csv.csv"',
            csv_response.headers["Content-Disposition"],
        )
        csv_body = csv_response.get_data(as_text=True)
        self.assertIn("summary,total,100000", csv_body)
        self.assertIn("group,region,华东,42000", csv_body)
        self.assertIn("evidence,", csv_body)

    def test_uploaded_report_export_recomputes_natural_language_contract(self):
        uploaded = self._upload()
        source_id = uploaded.get_json()["added"][0]["source_id"]
        response = self.client.post(
            f"/api/session/{self.sid}/pfs/export",
            json={
                "format": "csv",
                "source_id": source_id,
                "question": "按地区统计 2026年1月到2026年2月的销售额",
                "run_id": "uploaded-export",
            },
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual("uploaded-export", response.headers["X-PFS-Report-Run"])
        body = response.get_data(as_text=True)
        self.assertIn("summary,total,4400", body)
        self.assertIn("group,region,华东,2700", body)

    def test_report_export_rejects_unknown_format(self):
        response = self.client.post("/api/pfs/export", json={"format": "xlsx"})
        self.assertEqual(400, response.status_code)
        self.assertFalse(response.get_json()["ok"])
        self.assertIn("format must be json or csv", response.get_json()["error"])

    def test_capabilities_report_implemented_and_environment_boundaries(self):
        response = self.client.get("/api/pfs/capabilities")
        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertEqual("implemented_server_recomputed", payload["reporting"]["report_export_json"])
        self.assertEqual("implemented", payload["models"]["openai_compatible_catalog"])
        self.assertEqual(
            "environment_dependent",
            payload["models"]["configured_provider_live_run"],
        )
        self.assertIn("other_connectors_pending", payload["runtime"]["external_sources"])
        self.assertNotIn("evidence_ledger", payload)
        self.assertEqual(404, self.client.get("/api/pfs/ledger").status_code)

    def test_natural_language_query_uses_deterministic_analysis(self):
        uploaded = self._upload()
        source_id = uploaded.get_json()["added"][0]["source_id"]
        response = self.client.post(
            f"/api/session/{self.sid}/pfs/query",
            json={
                "source_id": source_id,
                "question": "按地区统计 2026年1月到2026年2月的销售额",
                "run_id": "nl-run",
            },
        )
        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(4400, payload["result"]["total"])
        self.assertEqual("region", payload["interpretation"]["request"]["dimension"])
        self.assertEqual("2026-01", payload["interpretation"]["request"]["date_from"])
        self.assertEqual("2026-02", payload["interpretation"]["request"]["date_to"])
        self.assertEqual(1, len(payload["result"]["evidence"]))

    def test_natural_language_query_selects_profit_metric(self):
        uploaded = self._upload_profit_csv()
        source_id = uploaded.get_json()["added"][0]["source_id"]
        response = self.client.post(
            f"/api/session/{self.sid}/pfs/query",
            json={
                "source_id": source_id,
                "question": "按地区统计 2026年1月到2026年2月的利润",
                "run_id": "profit-run",
            },
        )
        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual("利润", payload["interpretation"]["metric"]["label"])
        self.assertEqual("profit_amount", payload["interpretation"]["metric"]["value_column"])
        self.assertEqual(1090, payload["result"]["total"])
        self.assertEqual(750, payload["result"]["groups"][0]["value"])

    def test_natural_language_query_rejects_unsupported_margin(self):
        uploaded = self._upload_profit_csv()
        source_id = uploaded.get_json()["added"][0]["source_id"]
        response = self.client.post(
            f"/api/session/{self.sid}/pfs/query",
            json={
                "source_id": source_id,
                "question": "按地区统计 2026年1月到2026年2月的利润率",
            },
        )
        self.assertEqual(400, response.status_code)
        self.assertEqual("pfs_query_failed", response.get_json()["code"])

    def test_natural_language_query_rejects_ambiguous_question(self):
        uploaded = self._upload()
        source_id = uploaded.get_json()["added"][0]["source_id"]
        response = self.client.post(
            f"/api/session/{self.sid}/pfs/query", json={"source_id": source_id, "question": "帮我看看数据"}
        )
        self.assertEqual(400, response.status_code)
        self.assertFalse(response.get_json()["ok"])
        self.assertIn("明确问题", response.get_json()["error"])

    def test_chat_deterministic_mode_streams_pfs_result(self):
        uploaded = self._upload()
        source_id = uploaded.get_json()["added"][0]["source_id"]
        response = self.client.post(
            f"/api/session/{self.sid}/chat",
            json={
                "message": "按地区统计 2026年1月到2026年2月的销售额",
                "pfs_mode": "deterministic",
                "source_id": source_id,
                "run_id": "chat-pfs-run",
            },
        )
        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn("pfs_result", body)
        self.assertIn("4400", body)
        self.assertIn("Claim", body)

    def test_chat_deterministic_mode_requires_source(self):
        response = self.client.post(
            f"/api/session/{self.sid}/chat",
            json={
                "message": "按地区统计销售额",
                "pfs_mode": "deterministic",
            },
        )
        self.assertEqual(400, response.status_code)
        self.assertEqual("pfs_source_required", response.get_json()["code"])

    def test_chat_deterministic_mode_rejects_ambiguous_question(self):
        uploaded = self._upload()
        source_id = uploaded.get_json()["added"][0]["source_id"]
        response = self.client.post(
            f"/api/session/{self.sid}/chat",
            json={
                "message": "帮我看看数据",
                "pfs_mode": "deterministic",
                "source_id": source_id,
            },
        )
        self.assertEqual(400, response.status_code)
        self.assertEqual("pfs_query_failed", response.get_json()["code"])

    def test_uploaded_analysis_rejects_unknown_metric_column(self):
        uploaded = self._upload()
        source_id = uploaded.get_json()["added"][0]["source_id"]
        response = self.client.post(
            f"/api/session/{self.sid}/pfs/analyze",
            json={"source_id": source_id, "value_column": "not_a_column"},
        )
        self.assertEqual(400, response.status_code)
        self.assertFalse(response.get_json()["ok"])
        self.assertIn("metric columns missing", response.get_json()["error"])

    def test_upload_list_and_analyze_uploaded_xlsx(self):
        with TemporaryDirectory() as temp_dir:
            workbook = Path(temp_dir) / "monthly_sales.xlsx"
            with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
                pd.DataFrame(
                    {
                        "month": ["2026-01", "2026-02", "2026-03"],
                        "region": ["华东", "华东", "华南"],
                        "sales_amount": [1200, 1500, 900],
                    }
                ).to_excel(writer, sheet_name="Monthly Sales", index=False)
                pd.DataFrame({"channel": ["线上", "线下"], "orders": [8, 4]}).to_excel(
                    writer, sheet_name="Orders", index=False
                )

            with workbook.open("rb") as handle:
                uploaded = self.client.post(
                    f"/api/session/{self.sid}/upload",
                    data={"file": (handle, workbook.name)},
                    content_type="multipart/form-data",
                )

        self.assertEqual(200, uploaded.status_code)
        upload_payload = uploaded.get_json()
        self.assertTrue(upload_payload["ok"])
        # One workbook is one data source; its worksheets remain tables inside
        # that source and are exposed by the source preview/schema.
        self.assertEqual(1, len(upload_payload["added"]))
        source_id = upload_payload["added"][0]["source_id"]

        listed = self.client.get(f"/api/session/{self.sid}/pfs/sources")
        self.assertEqual(200, listed.status_code)
        listed_payload = listed.get_json()
        self.assertTrue(listed_payload["ok"])
        self.assertEqual(1, len(listed_payload["sources"]))
        self.assertEqual("monthly_sales.xlsx", listed_payload["sources"][0]["name"])
        self.assertIn("month", listed_payload["sources"][0]["columns"])
        self.assertEqual(3, listed_payload["sources"][0]["row_count"])

        analyzed = self.client.post(
            f"/api/session/{self.sid}/pfs/analyze",
            json={
                "source_id": source_id,
                "run_id": "http-xlsx-run",
                "value_column": "sales_amount",
                "date_column": "month",
                "dimension": "region",
                "date_from": "2026-01",
                "date_to": "2026-03",
            },
        )
        self.assertEqual(200, analyzed.status_code)
        result = analyzed.get_json()["result"]
        self.assertEqual(3600, result["total"])
        self.assertEqual(["华东", "华南"], [item["dimension"] for item in result["groups"]])
        self.assertEqual("http-xlsx-run", result["request"]["run_id"])
        self.assertEqual(1, len(result["evidence"]))

        exported = self.client.post(
            f"/api/session/{self.sid}/pfs/export",
            json={
                "format": "json",
                "source_id": source_id,
                "run_id": "http-xlsx-export",
                "value_column": "sales_amount",
                "date_column": "month",
                "dimension": "region",
                "date_from": "2026-01",
                "date_to": "2026-03",
            },
        )
        self.assertEqual(200, exported.status_code)
        self.assertEqual("http-xlsx-export", exported.headers["X-PFS-Report-Run"])
        self.assertIn("pfs-report-http-xlsx-export.json", exported.headers["Content-Disposition"])
        self.assertEqual(3600, exported.get_json()["result"]["total"])


if __name__ == "__main__":
    unittest.main()
