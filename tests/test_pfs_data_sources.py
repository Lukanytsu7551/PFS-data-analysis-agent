import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from agent.agent import BusinessAgent
from data.sources.csv import CSVDataSource
from data.sources.excel import ExcelDataSource
from data.sources.http import HTTPAPIDataSource


FIXTURE = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "pfs_sales.csv"


class _FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - stdlib handler API
        if self.path == "/sales.json":
            payload = json.dumps(
                {"items": [{"region": "华东", "sales": 42000}, {"region": "华南", "sales": 28000}]},
                ensure_ascii=False,
            ).encode("utf-8")
            content_type = "application/json; charset=utf-8"
            status = 200
        elif self.path == "/sales.csv":
            payload = "region,sales\n华东,42000\n华南,28000\n".encode("utf-8")
            # Exercise the common API mistake: UTF-8 bytes with no declared charset.
            content_type = "text/csv"
            status = 200
        elif self.path == "/empty.json":
            payload = b"[]"
            content_type = "application/json"
            status = 200
        elif self.path == "/error":
            payload = b"server error"
            content_type = "text/plain"
            status = 500
        else:
            payload = b"not found"
            content_type = "text/plain"
            status = 404

        self.server.seen_headers.append(dict(self.headers))
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        return


class PfsDataSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
        cls.httpd.seen_headers = []
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.httpd.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=2)

    def test_csv_loads_schema_query_and_preview(self):
        source = CSVDataSource(str(FIXTURE), "pfs_sales.csv")

        self.assertEqual(["pfs_sales"], source.list_tables())
        self.assertIn("sales_amount", source.get_schema())
        frame, error = source.execute_query(
            "SELECT COUNT(*) AS row_count, SUM(sales_amount) AS total_sales FROM pfs_sales"
        )
        self.assertEqual("", error)
        self.assertEqual(9, int(frame.iloc[0]["row_count"]))
        self.assertEqual(100000, int(frame.iloc[0]["total_sales"]))
        preview = source.get_preview_table("pfs_sales", max_rows=2)
        self.assertEqual(9, preview["total_rows"])
        self.assertEqual(2, len(preview["rows"]))

    def test_excel_loads_multiple_sheets_and_keeps_workbook_order(self):
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sales.xlsx"
            with pd.ExcelWriter(path, engine="openpyxl") as writer:
                pd.DataFrame({"region": ["华东", "华南"], "sales": [42, 28]}).to_excel(
                    writer, sheet_name="Sales", index=False
                )
                pd.DataFrame({"channel": ["线上", "线下"], "orders": [7, 3]}).to_excel(
                    writer, sheet_name="Orders", index=False
                )

            source = ExcelDataSource(str(path), "sales.xlsx")
            try:
                self.assertEqual({"Sales", "Orders"}, set(source.list_tables()))
                self.assertEqual(["Sales", "Orders"], [item["name"] for item in source.get_preview()])
                frame, error = source.execute_query(
                    "SELECT SUM(sales) AS total_sales FROM Sales"
                )
                self.assertEqual("", error)
                self.assertEqual(70, int(frame.iloc[0]["total_sales"]))
                self.assertEqual(2, source.get_preview()[0]["total_rows"])
                self.assertIn("Orders", source.get_schema())

                agent = BusinessAgent(
                    client=None,
                    model="pfs-excel-table-lifecycle",
                    data_source=source,
                    session_id="pfs-excel-table-lifecycle",
                )
                blocked = agent._tool_create_analysis_table(
                    "SELECT SUM(sales) AS total_sales FROM Sales",
                    "Sales",
                )
                self.assertIn("不能覆盖原始数据表", blocked)
                created = agent._tool_create_analysis_table(
                    "SELECT SUM(sales) AS total_sales FROM Sales",
                    "excel_summary",
                )
                self.assertIn("excel_summary", created)
                deleted = agent._tool_delete_analysis_tables(["excel_summary"], confirm=True)
                self.assertIn("excel_summary", deleted)
                self.assertNotIn("excel_summary", source.list_tables())
                raw_delete = agent._tool_delete_analysis_tables(["Sales"], confirm=True)
                self.assertIn("原始源表和无法判定的表会被保护", raw_delete)
                self.assertIn("Sales", source.list_tables())
            finally:
                source.close()

    def test_http_json_and_csv_are_loaded_from_local_fixture_server(self):
        json_source = HTTPAPIDataSource(f"{self.base_url}/sales.json", display_name="远程销售 JSON")
        self.assertEqual(["api_data"], json_source.list_tables())
        frame, error = json_source.execute_query("SELECT SUM(sales) AS total_sales FROM api_data")
        self.assertEqual("", error)
        self.assertEqual(70000, int(frame.iloc[0]["total_sales"]))
        self.assertEqual(2, json_source.get_preview_table("api_data")["total_rows"])

        csv_source = HTTPAPIDataSource(f"{self.base_url}/sales.csv")
        frame, error = csv_source.execute_query("SELECT COUNT(*) AS row_count FROM api_data")
        self.assertEqual("", error)
        self.assertEqual(2, int(frame.iloc[0]["row_count"]))
        frame, error = csv_source.execute_query("SELECT region FROM api_data ORDER BY sales")
        self.assertEqual("", error)
        self.assertEqual(["华南", "华东"], frame["region"].tolist())

    def test_http_auth_header_is_sent_and_invalid_responses_fail_closed(self):
        HTTPAPIDataSource(
            f"{self.base_url}/sales.json",
            auth_type="bearer",
            auth_value="fixture-token",
        )
        self.assertEqual("Bearer fixture-token", self.httpd.seen_headers[-1]["Authorization"])

        with self.assertRaises(ValueError):
            HTTPAPIDataSource(f"{self.base_url}/empty.json")
        with self.assertRaises(Exception):
            HTTPAPIDataSource(f"{self.base_url}/error")


if __name__ == "__main__":
    unittest.main()
