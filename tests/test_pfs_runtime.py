import unittest

from pfs_agent.runtime import build_builtin_registry, evaluate_builtin_call


class PfsRuntimeAdapterTests(unittest.TestCase):
    def setUp(self):
        class RuntimeSpec:
            def __init__(self, name, category, requires_data_source=False):
                self.name = name
                self.category = category
                self.requires_data_source = requires_data_source
                self.requires_workspace = False
                self.discovery_summary = ""

        self.registry = build_builtin_registry(
            [RuntimeSpec("get_schema", "read", True), RuntimeSpec("query_data", "read", True)],
            [
                {"function": {"name": "get_schema", "parameters": {"type": "object", "properties": {}}}},
                {
                    "function": {
                        "name": "query_data",
                        "parameters": {
                            "type": "object",
                            "properties": {"sql": {"type": "string"}},
                            "required": ["sql"],
                            "additionalProperties": False,
                        },
                    }
                },
            ],
        )

    def test_read_call_passes_with_data_scope(self):
        decision = evaluate_builtin_call(
            self.registry,
            run_id="run-1",
            tool_id="query_data",
            arguments={"sql": "select 1"},
            workspace_id="",
            visible_tools={"query_data"},
            has_data_source=True,
            has_workspace=False,
            remaining_calls=10,
            remaining_seconds=60,
            tool_call_counts={},
        )
        self.assertIsNotNone(decision)
        self.assertTrue(decision.allowed)

    def test_data_tool_fails_closed_without_data_scope(self):
        decision = evaluate_builtin_call(
            self.registry,
            run_id="run-1",
            tool_id="query_data",
            arguments={"sql": "select 1"},
            workspace_id="",
            visible_tools={"query_data"},
            has_data_source=False,
            has_workspace=False,
            remaining_calls=10,
            remaining_seconds=60,
            tool_call_counts={},
        )
        self.assertIsNotNone(decision)
        self.assertEqual("scope_forbidden", decision.code)

    def test_deferred_side_effect_is_not_changed_by_first_slice(self):
        class RuntimeSpec:
            name = "write_file"
            category = "write"
            requires_data_source = False
            requires_workspace = False
            discovery_summary = ""

        registry = build_builtin_registry(
            [RuntimeSpec()],
            [{"function": {"name": "write_file", "parameters": {"type": "object"}}}],
        )
        decision = evaluate_builtin_call(
            registry,
            run_id="run-1",
            tool_id="write_file",
            arguments={},
            workspace_id="",
            visible_tools={"write_file"},
            has_data_source=False,
            has_workspace=True,
            remaining_calls=10,
            remaining_seconds=60,
            tool_call_counts={},
        )
        self.assertIsNone(decision)


if __name__ == "__main__":
    unittest.main()
