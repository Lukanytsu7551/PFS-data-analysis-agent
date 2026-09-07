import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from agent.tools.results import (
    classify_tool_error,
    make_tool_result,
    read_tool_result_artifact,
)
from agent.agent import _tool_detail_value, _user_tool_audit_content


class ToolResultArtifactTests(unittest.TestCase):
    def test_recovery_id_is_not_a_user_facing_tool_label(self):
        detail = _tool_detail_value({"artifact_id": "tr_6317da56aa71f7fa46ce5bd34ea4df80"})
        self.assertEqual({"artifact_id": "[内部结果标识已隐藏]"}, detail)

        content = _user_tool_audit_content(
            "read_tool_result",
            {
                "artifact_id": "tr_6317da56aa71f7fa46ce5bd34ea4df80",
                "query": "企业主征信信息",
                "match_count": 1,
                "returned_chars": 320,
                "total_chars": 1280,
            },
        )
        self.assertIn("已补充读取完整查询结果", content)
        self.assertIn("企业主征信信息", content)
        self.assertNotIn("tr_6317da56aa71f7fa46ce5bd34ea4df80", content)

    def test_nested_sql_errors_are_not_reported_as_success(self):
        nested = make_tool_result(
            "run_analysis",
            "SQL Error while fetching data: 'run_analysis' requires 'analysis_name'.",
        )
        self.assertFalse(nested.ok)
        self.assertEqual("sql_execution_error", nested.error)

        missing_table = make_tool_result(
            "query_data",
            'SQL Error: Catalog Error: Table with name analysis_breakdown does not exist!',
        )
        self.assertFalse(missing_table.ok)
        self.assertEqual("table_not_found", missing_table.error)

        self.assertEqual(
            "analysis_error",
            classify_tool_error("Analysis error: target column is invalid", tool="run_analysis"),
        )
        self.assertEqual(
            "table_not_found",
            classify_tool_error(
                "Table 'missing' not found in any connected data source.",
                tool="get_table_detail",
            ),
        )
        self.assertEqual(
            "tool_error",
            classify_tool_error(
                "Error building analysis table: source rejected the query",
                tool="create_analysis_table",
            ),
        )

    def test_deduplicated_result_can_be_read_after_session_restore(self):
        with TemporaryDirectory(prefix="pfs-tool-result-") as raw:
            runtime = SimpleNamespace(
                cache_dir=Path(raw) / "cache",
                workspace_id="",
            )
            payload = "Table: 企业主基本信息\n" + ("column value\n" * 260)

            first = make_tool_result(
                "get_schema",
                payload,
                session_id="saved-session",
                runtime=runtime,
                result_char_budget=1,
            )
            second = make_tool_result(
                "get_schema",
                payload,
                session_id="restored-session",
                runtime=runtime,
                result_char_budget=1,
            )

            first_artifact = first.artifacts[0]
            second_artifact = second.artifacts[0]
            self.assertEqual(first_artifact["artifact_id"], second_artifact["artifact_id"])
            self.assertEqual("saved-session", second_artifact["session_id"])
            self.assertTrue(second.debug["result_budget"]["deduplicated"])

            result = read_tool_result_artifact(
                second_artifact["artifact_id"],
                allowed_artifacts=[
                    {
                        **second_artifact,
                        # This represents a pre-fix saved session that already
                        # recorded the current session id on a reused file.
                        "session_id": "restored-session",
                    }
                ],
                session_id="restored-session",
                workspace_id="",
                runtime=runtime,
                query="企业主基本信息",
            )
            self.assertGreater(result["match_count"], 0)
            self.assertIn("企业主基本信息", result["content"])

            record_path = runtime.cache_dir / "tool_results" / f"{first_artifact['artifact_id']}.json"
            record = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual("saved-session", record["session_id"])

    def test_same_schema_is_not_reused_across_workspaces(self):
        with TemporaryDirectory(prefix="pfs-tool-result-workspaces-") as raw:
            runtime_a = SimpleNamespace(
                cache_dir=Path(raw) / "shared",
                workspace_id="workspace-a",
            )
            runtime_b = SimpleNamespace(
                cache_dir=Path(raw) / "shared",
                workspace_id="workspace-b",
            )
            payload = "Table: sales\n" + ("value\n" * 260)

            first = make_tool_result(
                "get_schema",
                payload,
                session_id="session-a",
                runtime=runtime_a,
                result_char_budget=1,
            )
            second = make_tool_result(
                "get_schema",
                payload,
                session_id="session-b",
                runtime=runtime_b,
                result_char_budget=1,
            )

            self.assertNotEqual(
                first.artifacts[0]["artifact_id"],
                second.artifacts[0]["artifact_id"],
            )
            self.assertEqual("workspace-b", second.artifacts[0]["workspace_id"])


if __name__ == "__main__":
    unittest.main()
