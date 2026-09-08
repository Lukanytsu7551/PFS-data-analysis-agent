import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from data.sources.csv import CSVDataSource
from data.sources.excel import ExcelDataSource
from data.sources.http import HTTPAPIDataSource
from data.sources.workspace_persistent import WorkspacePersistentSource


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
            content_type = "text/csv; charset=utf-8"
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
            self.assertEqual({"Sales", "Orders"}, set(source.list_tables()))
            self.assertEqual(["Sales", "Orders"], [item["name"] for item in source.get_preview()])
            frame, error = source.execute_query(
                "SELECT SUM(sales) AS total_sales FROM Sales"
            )
            self.assertEqual("", error)
            self.assertEqual(70, int(frame.iloc[0]["total_sales"]))
            self.assertEqual(2, source.get_preview()[0]["total_rows"])
            self.assertIn("Orders", source.get_schema())

    def test_excel_numeric_filename_alias_and_text_measures_are_queryable(self):
        """File-derived names and numeric-looking Excel text must not break SQL."""
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "25年单边流_24年单边流入.xlsx"
            with pd.ExcelWriter(path, engine="openpyxl") as writer:
                pd.DataFrame(
                    {
                        "设置自定义人数": ["10", "20"],
                        "设置固定人数": ["20", "20"],
                    }
                ).to_excel(writer, sheet_name="Sheet1", index=False)

            source = ExcelDataSource(str(path), path.name)
            physical_table = "Sheet1"
            self.assertEqual([physical_table], source.list_tables())
            frame, error = source.execute_query(
                'SELECT ROUND(设置自定义人数 * 100.0 / '
                'NULLIF(设置固定人数 + 设置自定义人数, 0), 2) AS ratio '
                'FROM "Sheet1" ORDER BY ratio'
            )
            self.assertEqual("", error)
            self.assertEqual([33.33, 50.0], frame["ratio"].tolist())

    def test_existing_workspace_text_measures_are_recovered_without_mutation(self):
        """Older persisted workspaces with VARCHAR measures get a safe retry."""
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "workspace.duckdb"
            import duckdb

            connection = duckdb.connect(str(db_path))
            connection.execute(
                'CREATE TABLE "_25年单边流_24年单边流入" '
                '("设置自定义人数" VARCHAR, "设置固定人数" VARCHAR)'
            )
            connection.execute(
                'INSERT INTO "_25年单边流_24年单边流入" VALUES (\'10\', \'20\'), (\'20\', \'20\')'
            )
            connection.close()

            source = WorkspacePersistentSource(str(db_path))
            try:
                frame, error = source.execute_query(
                    'SELECT ROUND(设置自定义人数 * 100.0 / '
                    'NULLIF(设置固定人数 + 设置自定义人数, 0), 2) AS ratio '
                    'FROM "25年单边流_24年单边流入" ORDER BY ratio'
                )
                self.assertEqual("", error)
                self.assertEqual([33.33, 50.0], frame["ratio"].tolist())
                schema = source.get_schema()
                self.assertIn("VARCHAR", schema)
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
