#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DataSource abstract base class.

All concrete sources implement this contract. Frontend code (api/, agent/)
never imports a concrete class directly — it always programs to this base.
"""
from typing import List, Tuple

import pandas as pd

MAX_DISPLAY_ROWS = 200   # max rows shown to the LLM in query results


class DataSource:
    name: str = ""

    def get_schema(self) -> str:
        raise NotImplementedError

    def execute_query(self, sql: str) -> Tuple[pd.DataFrame, str]:
        """Returns (dataframe, error_string). error_string is empty on success."""
        raise NotImplementedError

    def interrupt_query(self) -> bool:
        """Best-effort interrupt for a query running in another thread.

        Agent-level cancellation is cooperative, but a DuckDB query can hold
        the worker inside ``execute`` and never reach the next callback.  The
        built-in DuckDB-backed sources inherit this small capability; sources
        with no interruptible connection return ``False`` and retain the
        normal before/after cancellation checks.
        """
        for attr in ("_conn", "_duck"):
            connection = getattr(self, attr, None)
            interrupt = getattr(connection, "interrupt", None)
            if callable(interrupt):
                try:
                    interrupt()
                    return True
                except Exception:
                    return False
        return False

    def get_preview(self) -> List[dict]:
        """Return table metadata list (name / columns / total_rows). No row data."""
        return []

    def get_preview_table(self, table_name: str, max_rows: int = 100) -> dict:
        """Return row data for a single table. Called on demand by the frontend."""
        return {"name": table_name, "columns": [], "rows": [], "total_rows": 0}

    def create_analysis_table(
        self, sql: str, table_name: str = "analysis_data", _df=None
    ) -> str:
        raise NotImplementedError

    def list_tables(self) -> List[str]:
        """Return ALL table names currently in the data source, including
        analysis/derived tables created at runtime via create_analysis_table.
        Subclasses backed by DuckDB should query information_schema."""
        raise NotImplementedError

    @staticmethod
    def format_result(df: pd.DataFrame) -> str:
        if df.empty:
            return "Query returned no results."
        total = len(df)
        preview = df.head(MAX_DISPLAY_ROWS)
        text = preview.to_string(index=False, max_cols=30)
        if total > MAX_DISPLAY_ROWS:
            text += f"\n\n... showing {MAX_DISPLAY_ROWS} of {total} rows"
        return text
