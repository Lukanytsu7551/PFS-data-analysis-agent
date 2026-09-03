import io
import threading
import uuid
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

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

    def _upload_missing_metric_csv(self):
        csv = (
            "month,region,orders\n"
            "2026-01,华东,12\n"
            "2026-02,华南,8\n"
        ).encode("utf-8")
        return self.client.post(
            f"/api/session/{self.sid}/upload",
            data={"file": (io.BytesIO(csv), "missing-sales.csv")},
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
        evidence = result["evidence"][0]
        self.assertEqual("quarterly_sales.csv", evidence["file_name"])
        self.assertEqual("", evidence["worksheet"])
        self.assertEqual(4, evidence["included_rows"])
        self.assertEqual(result["snapshot"]["content_sha256"], evidence["content_sha256"])
        self.assertIn("quarterly_sales.csv#sha256=", evidence["locator"])
        self.assertEqual([evidence["evidence_id"]], result["claims"][0]["evidence_ids"])
        self.assertEqual([evidence["evidence_id"]], [link["evidence_id"] for link in result["claims"][0]["evidence_links"]])
        self.assertEqual("http-upload-run", result["request"]["run_id"])

    def test_active_uploaded_analysis_can_be_canceled_before_governance_persistence(self):
        uploaded = self._upload()
        source_id = uploaded.get_json()["added"][0]["source_id"]
        run_id = "http-cancel-run"
        analysis_started = threading.Event()
        release_analysis = threading.Event()
        response_holder = {}

        def slow_analysis(*_args, **_kwargs):
            analysis_started.set()
            release_analysis.wait(timeout=3)
            return object()

        def post_analysis():
            with self.app.test_client() as client:
                response_holder["response"] = client.post(
                    f"/api/session/{self.sid}/pfs/analyze",
                    json={
                        "source_id": source_id,
                        "run_id": run_id,
                        "value_column": "sales_amount",
                        "date_column": "month",
                        "dimension": "region",
                    },
                )

        with patch("api.pfs.analyze_file", side_effect=slow_analysis), patch(
            "api.pfs._governance_result"
        ) as govern:
            worker = threading.Thread(target=post_analysis, daemon=True)
            worker.start()
            try:
                self.assertTrue(analysis_started.wait(timeout=2))
                canceled = self.client.post(
                    f"/api/session/{self.sid}/pfs/runs/{run_id}/cancel"
                )
                self.assertEqual(202, canceled.status_code)
                self.assertEqual("cancel_requested", canceled.get_json()["status"])
            finally:
                release_analysis.set()
                worker.join(timeout=3)

            self.assertFalse(worker.is_alive())
            govern.assert_not_called()

        response = response_holder["response"]
        self.assertEqual(409, response.status_code)
        self.assertEqual("pfs_analysis_canceled", response.get_json()["code"])

    def test_cancel_rejects_run_that_is_not_active_in_session(self):
        response = self.client.post(
            f"/api/session/{self.sid}/pfs/runs/not-running/cancel"
        )
        self.assertEqual(404, response.status_code)
        self.assertEqual("pfs_analysis_run_not_active", response.get_json()["code"])

    def test_upload_rejects_oversized_file_with_explicit_code(self):
        from api.datasource import MAX_UPLOAD_BYTES

        response = self.client.post(
            f"/api/session/{self.sid}/upload",
            data={"file": (io.BytesIO(b"x" * (MAX_UPLOAD_BYTES + 1)), "too-large.csv")},
            content_type="multipart/form-data",
        )
        self.assertEqual(413, response.status_code)
        payload = response.get_json()
        self.assertFalse(payload["ok"])
        self.assertEqual("upload_file_too_large", payload["code"])

    def test_invalid_date_source_remains_selectable_and_analysis_explains_repair(self):
        csv = (
            "month,region,sales_amount\n"
            "2026-02-30,华东,1200\n"
        ).encode("utf-8")
        uploaded = self.client.post(
            f"/api/session/{self.sid}/upload",
            data={"file": (io.BytesIO(csv), "invalid-date.csv")},
            content_type="multipart/form-data",
        )
        self.assertEqual(200, uploaded.status_code)
        source_id = uploaded.get_json()["added"][0]["source_id"]

        listed = self.client.get(f"/api/session/{self.sid}/pfs/sources")
        self.assertEqual(200, listed.status_code)
        source = listed.get_json()["sources"][0]
        self.assertEqual(source_id, source["source_id"])
        self.assertEqual(["month", "region", "sales_amount"], source["columns"])
        self.assertEqual(1, source["row_count"])
        self.assertEqual("source_date_invalid", source["validation_error"]["code"])

        analyzed = self.client.post(
            f"/api/session/{self.sid}/pfs/analyze",
            json={
                "source_id": source_id,
                "run_id": "invalid-date-run",
                "value_column": "sales_amount",
                "date_column": "month",
                "dimension": "region",
            },
        )
        self.assertEqual(400, analyzed.status_code)
        self.assertEqual("source_date_invalid", analyzed.get_json()["code"])

    def test_question_missing_metric_column_keeps_actionable_error_code(self):
        uploaded = self._upload_missing_metric_csv()
        self.assertEqual(200, uploaded.status_code)
        source_id = uploaded.get_json()["added"][0]["source_id"]

        response = self.client.post(
            f"/api/session/{self.sid}/pfs/query",
            json={
                "source_id": source_id,
                "run_id": "missing-metric-query",
                "question": "按地区统计销售额",
            },
        )
        self.assertEqual(400, response.status_code)
        payload = response.get_json()
        self.assertFalse(payload["ok"])
        self.assertEqual("source_columns_missing", payload["code"])
        self.assertIn("指标字段", payload["error"])

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

    def test_capabilities_report_real_verified_and_pending_boundaries(self):
        response = self.client.get("/api/pfs/capabilities")
        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertEqual("implemented_server_recomputed", payload["reporting"]["report_export_json"])
        self.assertEqual("verified_local_http", payload["models"]["deepseek_chat"])
        self.assertIn("other_connectors_pending", payload["runtime"]["external_sources"])

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

        ledger = self.client.get(
            f"/api/pfs/ledger?task_id={self.sid}:chat-pfs-run"
        ).get_json()
        self.assertTrue(ledger["ok"])
        self.assertEqual(2, len(ledger["claims"]))
        self.assertEqual(1, len(ledger["evidence"]))
        self.assertEqual("quarterly_sales.csv", ledger["evidence"][0]["file_name"])
        self.assertEqual(
            [ledger["evidence"][0]["evidence_id"]],
            ledger["claims"][0]["evidence_ids"],
        )

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
        self.assertEqual("monthly_sales.xlsx", listed_payload["sources"][0]["file_name"])
        self.assertEqual(
            ["Monthly Sales", "Orders"],
            [item["name"] for item in listed_payload["sources"][0]["worksheets"]],
        )
        self.assertIn("month", listed_payload["sources"][0]["worksheets"][0]["columns"])

        analyzed = self.client.post(
            f"/api/session/{self.sid}/pfs/analyze",
            json={
                "source_id": source_id,
                "worksheet": "Monthly Sales",
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
        self.assertEqual("Monthly Sales", result["evidence"][0]["worksheet"])
        self.assertEqual(3, result["evidence"][0]["included_rows"])
        self.assertEqual("monthly_sales.xlsx", result["evidence"][0]["file_name"])
        self.assertEqual(result["snapshot"]["content_sha256"], result["evidence"][0]["content_sha256"])

        chat = self.client.post(
            f"/api/session/{self.sid}/chat",
            json={
                "message": "按地区统计 2026年1月到2026年3月的销售额",
                "pfs_mode": "deterministic",
                "source_id": source_id,
                "worksheet": "Monthly Sales",
                "run_id": "chat-xlsx-run",
            },
        )
        self.assertEqual(200, chat.status_code)
        self.assertIn("pfs_result", chat.get_data(as_text=True))
        chat_ledger = self.client.get(
            f"/api/pfs/ledger?task_id={self.sid}:chat-xlsx-run"
        ).get_json()
        self.assertTrue(chat_ledger["ok"])
        self.assertEqual(2, len(chat_ledger["claims"]))
        self.assertEqual(1, len(chat_ledger["evidence"]))
        self.assertEqual("monthly_sales.xlsx", chat_ledger["evidence"][0]["file_name"])
        self.assertEqual("Monthly Sales", chat_ledger["evidence"][0]["worksheet"])
        self.assertEqual(
            [chat_ledger["evidence"][0]["evidence_id"]],
            chat_ledger["claims"][0]["evidence_ids"],
        )

        exported = self.client.post(
            f"/api/session/{self.sid}/pfs/export",
            json={
                "format": "json",
                "source_id": source_id,
                "worksheet": "Monthly Sales",
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

    def test_multi_sheet_xlsx_requires_valid_worksheet_and_returns_stable_codes(self):
        with TemporaryDirectory() as temp_dir:
            workbook = Path(temp_dir) / "multi.xlsx"
            with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
                pd.DataFrame({"note": ["说明"]}).to_excel(writer, sheet_name="Readme", index=False)
                pd.DataFrame(
                    {
                        "month": ["2026-01"],
                        "region": ["华东"],
                        "sales_amount": [1200],
                    }
                ).to_excel(writer, sheet_name="Sales", index=False)
            with workbook.open("rb") as handle:
                uploaded = self.client.post(
                    f"/api/session/{self.sid}/upload",
                    data={"file": (handle, workbook.name)},
                    content_type="multipart/form-data",
                )
        source_id = uploaded.get_json()["added"][0]["source_id"]

        missing = self.client.post(
            f"/api/session/{self.sid}/pfs/analyze",
            json={"source_id": source_id},
        )
        unknown = self.client.post(
            f"/api/session/{self.sid}/pfs/analyze",
            json={"source_id": source_id, "worksheet": "Missing"},
        )

        self.assertEqual(400, missing.status_code)
        self.assertEqual("worksheet_required", missing.get_json()["code"])
        self.assertEqual(400, unknown.status_code)
        self.assertEqual("worksheet_not_found", unknown.get_json()["code"])

    def test_report_governance_is_idempotent_and_session_scoped(self):
        uploaded = self._upload()
        source_id = uploaded.get_json()["added"][0]["source_id"]
        request = {
            "source_id": source_id,
            "run_id": "governance-idempotent",
            "value_column": "sales_amount",
            "date_column": "month",
            "dimension": "region",
        }

        first = self.client.post(f"/api/session/{self.sid}/pfs/analyze", json=request)
        second = self.client.post(f"/api/session/{self.sid}/pfs/analyze", json=request)
        self.assertEqual(200, first.status_code)
        self.assertEqual(200, second.status_code)
        first_result = first.get_json()["result"]
        second_result = second.get_json()["result"]
        self.assertEqual(
            [claim["claim_id"] for claim in first_result["claims"]],
            [claim["claim_id"] for claim in second_result["claims"]],
        )
        task_id = f"{self.sid}:governance-idempotent"
        ledger = self.client.get(f"/api/pfs/ledger?task_id={task_id}").get_json()
        self.assertEqual(2, len(ledger["claims"]))
        self.assertEqual(1, len(ledger["evidence"]))

        claim_id = first_result["claims"][0]["claim_id"]
        decided = self.client.post(
            f"/api/session/{self.sid}/pfs/ledger/claims/{claim_id}/decision",
            json={
                "task_id": task_id,
                "decision": "支持",
                "reason": "端到端核验",
            },
        )
        self.assertEqual(200, decided.status_code, decided.get_data(as_text=True))
        self.assertEqual("支持", decided.get_json()["claim"]["human_decision"])
        detail = self.client.get(
            f"/api/session/{self.sid}/pfs/ledger/claims/{claim_id}?task_id={task_id}"
        )
        self.assertEqual(200, detail.status_code, detail.get_data(as_text=True))
        self.assertEqual(claim_id, detail.get_json()["claim"]["claim_id"])
        self.assertEqual(1, len(detail.get_json()["evidence"]))
        wrong_task = self.client.get(
            f"/api/session/{self.sid}/pfs/ledger/claims/{claim_id}?task_id={self.sid}:other"
        )
        self.assertEqual(404, wrong_task.status_code)

        other_sid = f"pfs-http-other-{uuid.uuid4().hex[:12]}"
        session_manager.get_or_create(other_sid)
        try:
            forbidden = self.client.post(
                f"/api/session/{other_sid}/pfs/ledger/claims/{claim_id}/decision",
                json={
                    "task_id": task_id,
                    "decision": "反驳",
                    "reason": "不应跨会话裁决",
                },
            )
            self.assertEqual(404, forbidden.status_code)
        finally:
            session_manager.remove(other_sid)


if __name__ == "__main__":
    unittest.main()
