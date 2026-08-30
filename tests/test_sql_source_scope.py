import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from data.sources.sql import SQLDataSource


class SqlSourceScopeTests(unittest.TestCase):
    """The SQL connector must never execute tables outside the selected scope."""

    def test_selected_table_queries_and_ctes_work(self):
        with TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "report.sqlite3"
            with sqlite3.connect(database) as connection:
                connection.executescript(
                    """
                    CREATE TABLE sales_report (
                        month TEXT NOT NULL,
                        region TEXT NOT NULL,
                        sales_amount REAL NOT NULL
                    );
                    INSERT INTO sales_report VALUES
                        ('2026-01', '华东', 42000),
                        ('2026-01', '华南', 33000);
                    CREATE TABLE private_notes (value TEXT);
                    INSERT INTO private_notes VALUES ('must not be exposed');
                    """
                )

            source = SQLDataSource(f"sqlite:///{database}", "PFS SQLite E2E")
            self.assertEqual(["sales_report"], source.set_analysis_tables(["sales_report"]))

            frame, error = source.execute_query(
                "SELECT region, SUM(sales_amount) AS total_sales "
                "FROM sales_report GROUP BY region ORDER BY total_sales DESC"
            )
            self.assertFalse(error)
            self.assertEqual(
                [{"region": "华东", "total_sales": 42000.0},
                 {"region": "华南", "total_sales": 33000.0}],
                frame.to_dict(orient="records"),
            )

            frame, error = source.execute_query(
                "WITH monthly AS ("
                "SELECT region, SUM(sales_amount) AS total_sales "
                "FROM sales_report GROUP BY region"
                ") SELECT * FROM monthly ORDER BY total_sales DESC"
            )
            self.assertFalse(error)
            self.assertEqual(["华东", "华南"], frame["region"].tolist())

    def test_unknown_and_system_tables_fail_closed(self):
        with TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "report.sqlite3"
            with sqlite3.connect(database) as connection:
                connection.execute("CREATE TABLE sales_report (value INTEGER)")
                connection.execute("INSERT INTO sales_report VALUES (1)")

            source = SQLDataSource(f"sqlite:///{database}", "PFS SQLite Scope")
            source.set_analysis_tables(["sales_report"])

            _frame, error = source.execute_query("SELECT * FROM private_notes")
            self.assertIn("未加入当前分析范围", error)

            _frame, error = source.execute_query("SELECT * FROM sqlite_master")
            self.assertIn("未加入当前分析范围", error)

            # A scalar query has no source table and remains useful for a
            # connection smoke test; it does not weaken table authorization.
            frame, error = source.execute_query("SELECT 1 AS connection_ok")
            self.assertFalse(error)
            self.assertEqual([1], frame["connection_ok"].tolist())


if __name__ == "__main__":
    unittest.main()
