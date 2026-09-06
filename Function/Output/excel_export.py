import logging
log = logging.getLogger(__name__)
import os
import re
import pandas as pd
from typing import List
from agent.errors import AgentRunTimeout
from agent.jobs import JobCanceled


def export_to_excel(
    datasource,
    tables: List[str],
    filepath: str,
    *,
    query_runner=None,
    abort_check=None,
) -> str:
    """
    Query *tables* from *datasource* and write each as a separate sheet
    in an Excel file at *filepath*.  Returns filepath on success.
    Raises ValueError if no table could be exported.
    """
    os.makedirs(os.path.dirname(filepath), exist_ok=True)

    execute_query = query_runner or datasource.execute_query

    writer = pd.ExcelWriter(filepath, engine="openpyxl")
    written = 0

    def _close_writer() -> None:
        try:
            writer.close()
        finally:
            # openpyxl can raise while finalizing an empty workbook before
            # pandas closes its file handle. Close the handle explicitly so an
            # aborted export never leaks a descriptor.
            handles = getattr(writer, "_handles", None)
            if handles is not None:
                handles.close()

    try:
        for table in tables:
            if abort_check is not None:
                abort_check()
            try:
                df, err = execute_query(f'SELECT * FROM "{table}"')
                if abort_check is not None:
                    abort_check()
                if err or df is None or df.empty:
                    continue
                # Excel sheet name: max 31 chars, no special chars
                sheet_name = re.sub(r'[\\/*?:\[\]]', '_', table)[:31]
                df.to_excel(writer, sheet_name=sheet_name, index=False)
                if abort_check is not None:
                    abort_check()
                written += 1
            except (JobCanceled, AgentRunTimeout):
                raise
            except Exception as e:
                log.warning("[excel_export] 导出表 '%s' 失败: %s", table, e)
                continue
    except (JobCanceled, AgentRunTimeout):
        # Do not save an incomplete workbook on cancellation. Calling
        # ``writer.close()`` here can raise "At least one sheet must be
        # visible" and leave openpyxl's temporary ZipFile with a closed file;
        # the caller removes the temp path, so closing the raw handle is the
        # only cleanup needed.
        try:
            handles = getattr(writer, "_handles", None)
            if handles is not None:
                handles.close()
        except Exception:
            log.debug("[excel_export] ignored writer handle error after abort", exc_info=True)
        raise
    else:
        _close_writer()

    if written == 0:
        raise ValueError("没有可导出的表格数据，请确认表名正确且数据不为空。")

    return filepath
