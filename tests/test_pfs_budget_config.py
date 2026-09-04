import unittest
from types import SimpleNamespace

from agent.agent import BusinessAgent
from agent.budget import BudgetConfigurationError, load_runtime_budget


class PfsBudgetConfigTests(unittest.TestCase):
    def test_defaults_preserve_current_agent_limits(self):
        budget = load_runtime_budget({})

        self.assertEqual(
            {
                "max_iterations": 120,
                "max_tool_calls": 480,
                "max_total_tokens": None,
                "max_run_seconds": 1800,
                "max_job_seconds": 1800,
            },
            budget.to_dict(),
        )

    def test_environment_overrides_are_normalized(self):
        budget = load_runtime_budget(
            {
                "PFS_MAX_ITERATIONS": "12",
                "PFS_MAX_TOOL_CALLS": "17",
                "PFS_MAX_TOTAL_TOKENS": "50000",
                "PFS_MAX_RUN_SECONDS": "90",
                "PFS_MAX_JOB_SECONDS": "120",
            }
        )

        self.assertEqual(
            {
                "max_iterations": 12,
                "max_tool_calls": 17,
                "max_total_tokens": 50000,
                "max_run_seconds": 90,
                "max_job_seconds": 120,
            },
            budget.to_dict(),
        )

    def test_tool_call_default_scales_with_iteration_budget(self):
        budget = load_runtime_budget({"PFS_MAX_ITERATIONS": "7"})

        self.assertEqual(7, budget.max_iterations)
        self.assertEqual(28, budget.max_tool_calls)

    def test_invalid_environment_values_fail_closed(self):
        invalid = {
            "PFS_MAX_ITERATIONS": "0",
            "PFS_MAX_TOOL_CALLS": "4001",
            "PFS_MAX_TOTAL_TOKENS": "2000001",
            "PFS_MAX_RUN_SECONDS": "9",
            "PFS_MAX_JOB_SECONDS": "7201",
        }

        for name, value in invalid.items():
            with self.subTest(name=name):
                with self.assertRaisesRegex(BudgetConfigurationError, name):
                    load_runtime_budget({name: value})

    def test_agent_stores_explicit_runtime_limits(self):
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace()))

        agent = BusinessAgent(
            client=client,
            model="pfs-runtime-budget-test",
            session_id="pfs-runtime-budget-test",
            max_iterations=12,
            max_tool_calls=17,
            max_total_tokens=50000,
            max_run_seconds=90,
            max_job_seconds=120,
        )

        self.assertEqual(12, agent._max_iterations)
        self.assertEqual(17, agent._max_tool_calls)
        self.assertEqual(50000, agent._max_total_tokens)
        self.assertEqual(90, agent._max_run_seconds)
        self.assertEqual(120, agent._max_job_seconds)
