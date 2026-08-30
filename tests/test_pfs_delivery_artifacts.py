import json
import unittest
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from api import create_app
from api.state import session_manager
from agent.agent import BusinessAgent


class PfsDeliveryArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config.update(TESTING=True)

    def setUp(self):
        self.client = self.app.test_client()
        self.sid = f"pfs-delivery-{uuid.uuid4().hex[:12]}"
        session_manager.get_or_create(self.sid)

    def tearDown(self):
        session_manager.remove(self.sid)

    def test_fixture_delivery_generates_parseable_office_files(self):
        with TemporaryDirectory() as tmp, patch.object(
            BusinessAgent, "_get_export_dir", return_value=tmp
        ), patch("agent.tools.business.export.register_artifact"):
            for output_format, extension in (("xlsx", ".xlsx"), ("docx", ".docx"), ("pptx", ".pptx")):
                with self.subTest(output_format=output_format):
                    response = self.client.post(
                        "/api/pfs/deliver",
                        json={"format": output_format, "run_id": f"delivery-{output_format}"},
                    )
                    self.assertEqual(200, response.status_code, response.get_data(as_text=True))
                    payload = response.get_json()
                    self.assertTrue(payload["ok"])
                    artifact = payload["artifacts"][0]
                    self.assertEqual(output_format, artifact["type"])
                    self.assertTrue(artifact["name"].endswith(extension))
                    self.assertTrue((Path(tmp) / artifact["name"]).is_file())

            from openpyxl import load_workbook
            from docx import Document
            from pptx import Presentation

            workbook = load_workbook(next(Path(tmp).glob("*.xlsx")), read_only=True, data_only=True)
            self.assertIn("pfs_sales", workbook.sheetnames)
            workbook.close()
            document_text = "\n".join(
                paragraph.text for paragraph in Document(next(Path(tmp).glob("*.docx"))).paragraphs
            )
            self.assertIn("PFS 销售额分析报告", document_text)
            self.assertIn("SHA-256", document_text)
            presentation = Presentation(next(Path(tmp).glob("*.pptx")))
            self.assertEqual(4, len(presentation.slides))

    def test_fixture_delivery_generates_dashboard_links_and_pfs_content(self):
        import api.dashboard as dashboard_module

        with TemporaryDirectory() as tmp, patch.object(
            dashboard_module, "_DASHBOARD_DIR", tmp
        ):
            response = self.client.post(
                "/api/pfs/deliver",
                json={"format": "dashboard", "run_id": "delivery-dashboard"},
            )
            self.assertEqual(200, response.status_code, response.get_data(as_text=True))
            payload = response.get_json()
            self.assertTrue(payload["ok"])
            self.assertEqual(["dashboard", "dashboard"], [item["type"] for item in payload["artifacts"]])
            dashboard_files = list(Path(tmp).glob("*.json"))
            self.assertEqual(1, len(dashboard_files))
            dashboard = json.loads(dashboard_files[0].read_text(encoding="utf-8"))
            self.assertEqual("PFS 销售额分析看板", dashboard["name"])
            self.assertEqual(2, len(dashboard["widgets"]))

            export_response = self.client.get(payload["artifacts"][1]["url"])
            self.assertEqual(200, export_response.status_code)
            disposition = export_response.headers["Content-Disposition"]
            disposition.encode("latin-1")
            self.assertIn("filename*=UTF-8''", disposition)
            self.assertIn("PFS_", disposition)

    def test_delivery_rejects_unknown_format(self):
        response = self.client.post(
            "/api/pfs/deliver",
            json={"format": "pdf", "run_id": "delivery-pdf"},
        )
        self.assertEqual(400, response.status_code)
        self.assertFalse(response.get_json()["ok"])
        self.assertIn("xlsx, docx, pptx, or dashboard", response.get_json()["error"])

    def test_multi_sheet_delivery_uses_only_the_analyzed_worksheet(self):
        import api.dashboard as dashboard_module

        with TemporaryDirectory() as source_tmp, TemporaryDirectory() as export_tmp, TemporaryDirectory() as dashboard_tmp:
            workbook_path = Path(source_tmp) / "regional workbook.xlsx"
            with pd.ExcelWriter(workbook_path, engine="openpyxl") as writer:
                pd.DataFrame({"note": ["do not export"]}).to_excel(
                    writer, sheet_name="Read Me", index=False
                )
                pd.DataFrame(
                    {
                        "month": ["2026-01", "2026-02"],
                        "region": ["华东", "华南"],
                        "sales_amount": [1200, 900],
                    }
                ).to_excel(writer, sheet_name="Sales Data", index=False)
            with workbook_path.open("rb") as handle:
                uploaded = self.client.post(
                    f"/api/session/{self.sid}/upload",
                    data={"file": (handle, workbook_path.name)},
                    content_type="multipart/form-data",
                )
            source_id = uploaded.get_json()["added"][0]["source_id"]
            report = {
                "source_id": source_id,
                "worksheet": "Sales Data",
                "value_column": "sales_amount",
                "date_column": "month",
                "dimension": "region",
                "run_id": "selected-sheet-delivery",
            }

            with patch.object(BusinessAgent, "_get_export_dir", return_value=export_tmp), patch(
                "agent.tools.business.export.register_artifact"
            ):
                excel_response = self.client.post(
                    f"/api/session/{self.sid}/pfs/deliver", json={**report, "format": "xlsx"}
                )
            self.assertEqual(200, excel_response.status_code, excel_response.get_data(as_text=True))
            from openpyxl import load_workbook

            exported = load_workbook(next(Path(export_tmp).glob("*.xlsx")), read_only=True)
            self.assertEqual(["Sales_Data"], exported.sheetnames)
            exported.close()

            with patch.object(dashboard_module, "_DASHBOARD_DIR", dashboard_tmp):
                dashboard_response = self.client.post(
                    f"/api/session/{self.sid}/pfs/deliver",
                    json={**report, "format": "dashboard"},
                )
            self.assertEqual(
                200, dashboard_response.status_code, dashboard_response.get_data(as_text=True)
            )
            dashboard = json.loads(next(Path(dashboard_tmp).glob("*.json")).read_text(encoding="utf-8"))
            sql = "\n".join(widget["sql"] for widget in dashboard["widgets"])
            self.assertIn('FROM "Sales_Data"', sql)
            self.assertNotIn('FROM "Read_Me"', sql)


if __name__ == "__main__":
    unittest.main()
