import unittest

from pfs_agent import (
    ApprovalMode,
    PolicyContext,
    PolicyGate,
    ToolCall,
    ToolRegistry,
    ToolRisk,
    ToolSpec,
)
from pfs_agent.contracts import ContractError


QUERY = ToolSpec(
    tool_id="query_data",
    purpose="Read a bounded analytical result",
    risk=ToolRisk.COMPUTE,
    input_schema={
        "type": "object",
        "properties": {"sql": {"type": "string"}},
        "required": ["sql"],
        "additionalProperties": False,
    },
    output_type="table_artifact",
    required_scopes=("data:read",),
    max_calls=3,
    timeout_seconds=10,
)

EXPORT = ToolSpec(
    tool_id="export_report",
    purpose="Write a report artifact for a confirmed run",
    risk=ToolRisk.SIDE_EFFECT,
    input_schema={"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
    output_type="report_artifact",
    required_scopes=("artifact:write",),
    approval=ApprovalMode.USER,
    idempotency_required=True,
    timeout_seconds=15,
)


def context(**overrides):
    values = {
        "allowed_tools": frozenset({"query_data", "export_report"}),
        "granted_scopes": frozenset({"data:read", "artifact:write"}),
        "workspace_id": "ws-1",
        "remaining_calls": 3,
        "remaining_seconds": 60,
        "remaining_cost_cents": 100,
    }
    values.update(overrides)
    return PolicyContext(**values)


class ToolPolicyTests(unittest.TestCase):
    def setUp(self):
        registry = ToolRegistry((QUERY, EXPORT))
        self.gate = PolicyGate(registry)

    def test_read_only_query_can_pass(self):
        decision = self.gate.evaluate(
            ToolCall("run-1", "query_data", {"sql": "SELECT 1"}, workspace_id="ws-1"),
            context(),
        )
        self.assertEqual((decision.allowed, decision.code), (True, "allowed"))

    def test_unknown_or_hidden_tool_is_denied(self):
        unknown = self.gate.evaluate(ToolCall("run-1", "missing_tool", {}, workspace_id="ws-1"), context())
        hidden = self.gate.evaluate(
            ToolCall("run-1", "query_data", {"sql": "SELECT 1"}, workspace_id="ws-1"),
            context(allowed_tools=frozenset()),
        )
        self.assertEqual(unknown.code, "unknown_tool")
        self.assertEqual(hidden.code, "tool_not_allowed")

    def test_invalid_arguments_are_denied_before_execution(self):
        decision = self.gate.evaluate(
            ToolCall("run-1", "query_data", {"sql": 123}, workspace_id="ws-1"),
            context(),
        )
        self.assertEqual(decision.code, "invalid_arguments")

    def test_scope_workspace_and_budget_are_enforced(self):
        wrong_workspace = self.gate.evaluate(
            ToolCall("run-1", "query_data", {"sql": "SELECT 1"}, workspace_id="ws-2"),
            context(),
        )
        no_scope = self.gate.evaluate(
            ToolCall("run-1", "query_data", {"sql": "SELECT 1"}, workspace_id="ws-1"),
            context(granted_scopes=frozenset()),
        )
        no_budget = self.gate.evaluate(
            ToolCall("run-1", "query_data", {"sql": "SELECT 1"}, workspace_id="ws-1"),
            context(remaining_calls=0),
        )
        self.assertEqual(wrong_workspace.code, "workspace_forbidden")
        self.assertEqual(no_scope.code, "scope_forbidden")
        self.assertEqual(no_budget.code, "call_budget_exceeded")

        per_tool_limit = self.gate.evaluate(
            ToolCall("run-1", "query_data", {"sql": "SELECT 1"}, workspace_id="ws-1"),
            context(tool_call_counts={"query_data": 3}),
        )
        self.assertEqual(per_tool_limit.code, "tool_call_limit_exceeded")

    def test_side_effect_requires_approval_and_idempotency(self):
        call = ToolCall("run-1", "export_report", {"title": "月报"}, workspace_id="ws-1")
        pending = self.gate.evaluate(call, context())
        self.assertEqual((pending.code, pending.requires_approval), ("approval_required", True))

        approved_without_key = self.gate.evaluate(call, context(approval_granted=True))
        self.assertEqual(approved_without_key.code, "idempotency_required")

        approved = self.gate.evaluate(
            ToolCall(
                "run-1",
                "export_report",
                {"title": "月报"},
                workspace_id="ws-1",
                idempotency_key="run-1:export:1",
            ),
            context(approval_granted=True, remaining_calls=1),
        )
        self.assertTrue(approved.allowed)

    def test_side_effect_contract_cannot_omit_safety_fields(self):
        with self.assertRaises(ContractError):
            ToolSpec(
                tool_id="delete_data",
                purpose="Delete data",
                risk=ToolRisk.SIDE_EFFECT,
                input_schema={"type": "object"},
                output_type="none",
            )


if __name__ == "__main__":
    unittest.main()
