import unittest
import threading
import tempfile
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from agent.workflows.models import (
    NodeRunStatus,
    WorkflowContractError,
    WorkflowErrorCode,
    validate_workflow_graph,
)
from agent.workflows.runtime import WorkflowRuntime
from agent.workflows.scheduler import WorkflowScheduler
from data.workflow_run_store import WorkflowRunStore


class PfsWorkflowBudgetTests(unittest.TestCase):
    def test_node_total_token_limit_is_validated(self):
        graph = {
            "entry_node_ids": ["step"],
            "nodes": [
                {
                    "node_id": "step",
                    "type": "agent",
                    "agent_profile_id": "profile",
                    "limits": {"max_total_tokens": 0},
                }
            ],
            "edges": [],
        }

        with self.assertRaisesRegex(WorkflowContractError, "max_total_tokens"):
            validate_workflow_graph(graph)

    def test_total_node_run_limit_cannot_be_smaller_than_initial_graph(self):
        graph = {
            "entry_node_ids": ["first", "second"],
            "nodes": [
                {
                    "node_id": "first",
                    "type": "agent",
                    "agent_profile_id": "profile-a",
                },
                {
                    "node_id": "second",
                    "type": "agent",
                    "agent_profile_id": "profile-b",
                },
            ],
            "edges": [],
            "limits": {"max_total_node_runs": 1},
        }

        with self.assertRaisesRegex(WorkflowContractError, "at least"):
            validate_workflow_graph(graph)

    def test_graph_remaining_budget_clamps_model_node(self):
        scheduler = WorkflowScheduler.__new__(WorkflowScheduler)
        scheduler.run_store = SimpleNamespace(
            list_node_runs=lambda _run_id: [
                {
                    "input_tokens": 80,
                    "output_tokens": 20,
                    "cost_usd": 0.4,
                }
            ]
        )
        graph = {
            "limits": {
                "max_total_tokens": 150,
                "max_total_cost_usd": 1.0,
            }
        }
        node = {
            "node_id": "model-step",
            "type": "agent",
            "limits": {
                "max_total_tokens": 100,
                "max_cost_usd": 0.1,
            },
        }

        bounded = scheduler._apply_graph_model_budget("run-1", graph, node)

        self.assertEqual(50, bounded["limits"]["max_total_tokens"])
        self.assertEqual(0.1, bounded["limits"]["max_cost_usd"])
        self.assertEqual(100, node["limits"]["max_total_tokens"])

    def test_workflow_cost_preflight_fails_closed_for_unknown_model_price(self):
        runtime = WorkflowRuntime.__new__(WorkflowRuntime)
        runtime.session = SimpleNamespace(model_provider="deepseek")
        runtime.workflow_store = SimpleNamespace(
            get_agent_profile=lambda _profile_id: {"model_policy": "inherit"}
        )
        manager = SimpleNamespace(
            get_default_provider=lambda: "deepseek",
            get_config=lambda _provider: SimpleNamespace(model="deepseek-chat"),
        )
        graph = {
            "limits": {"max_total_cost_usd": 1.0},
            "nodes": [
                {
                    "node_id": "model-step",
                    "type": "agent",
                    "agent_profile_id": "profile",
                    "limits": {},
                }
            ],
        }

        with (
            patch("LLM.llm_config_manager.get_config_manager", return_value=manager),
            patch("agent.workflows.runtime.lookup_model_price", return_value=None),
        ):
            with self.assertRaises(WorkflowContractError) as context:
                runtime._preflight_graph(graph)

        self.assertIs(WorkflowErrorCode.UNKNOWN_PRICE, context.exception.code)
        self.assertIn("完整输入/输出单价", str(context.exception))

    def test_workflow_cost_preflight_allows_complete_model_price(self):
        runtime = WorkflowRuntime.__new__(WorkflowRuntime)
        runtime.session = SimpleNamespace(model_provider="deepseek")
        runtime.workflow_store = SimpleNamespace(
            get_agent_profile=lambda _profile_id: {"model_policy": "inherit"}
        )
        manager = SimpleNamespace(
            get_default_provider=lambda: "deepseek",
            get_config=lambda _provider: SimpleNamespace(model="deepseek-chat"),
        )
        graph = {
            "limits": {"max_total_cost_usd": 1.0},
            "nodes": [
                {
                    "node_id": "model-step",
                    "type": "agent",
                    "agent_profile_id": "profile",
                    "limits": {},
                }
            ],
        }

        with (
            patch("LLM.llm_config_manager.get_config_manager", return_value=manager),
            patch(
                "agent.workflows.runtime.lookup_model_price",
                return_value={"input": 1.0, "output": 2.0},
            ),
        ):
            runtime._preflight_graph(graph)

    def test_token_budget_blocks_when_usage_reaches_limit(self):
        class FakeRunStore:
            def __init__(self):
                self.transitions = []
                self.run_transition = None

            def list_node_runs(self, _run_id):
                return [
                    {
                        "id": "ready",
                        "status": "ready",
                        "input_tokens": 0,
                        "output_tokens": 0,
                    },
                    {
                        "id": "done",
                        "status": "succeeded",
                        "input_tokens": 80,
                        "output_tokens": 20,
                    },
                ]

            def transition_node(self, node_id, status, error=""):
                self.transitions.append((node_id, status, error))

            def transition_run(self, run_id, status, failure_code="", failure_message=""):
                self.run_transition = (
                    run_id,
                    status,
                    failure_code,
                    failure_message,
                )

        scheduler = WorkflowScheduler.__new__(WorkflowScheduler)
        scheduler.run_store = FakeRunStore()

        self.assertTrue(scheduler._expire_token_budget("run-1", {"limits": {"max_total_tokens": 100}}))
        self.assertEqual(
            [("ready", "canceled", "workflow token budget exceeded")],
            scheduler.run_store.transitions,
        )
        self.assertEqual("workflow_token_budget_exceeded", scheduler.run_store.run_transition[2])

    def test_sqlite_claim_reserves_graph_budget_atomically_and_releases_on_output(self):
        with tempfile.TemporaryDirectory(prefix="pfs-workflow-budget-") as temp_dir:
            path = Path(temp_dir) / "workflow.sqlite3"
            workspace_id = "budget-workspace"
            first_store = WorkflowRunStore(path, workspace_id)
            second_store = WorkflowRunStore(path, workspace_id)
            graph = {
                "nodes": [
                    {"node_id": "first", "agent_profile_id": "profile-a"},
                    {"node_id": "second", "agent_profile_id": "profile-b"},
                ]
            }
            try:
                run = first_store.create_run(
                    workflow_version_id="version-1",
                    session_id="session-1",
                    graph=graph,
                    inputs={},
                )
                node_ids = {item["node_id"]: item["id"] for item in first_store.list_node_runs(run["id"])}
                for node_id in node_ids.values():
                    first_store.transition_node(node_id, NodeRunStatus.READY)

                barrier = threading.Barrier(2)
                results = []

                def claim(store, node_id):
                    barrier.wait(timeout=3)
                    results.append(
                        store.claim_node_with_budget(
                            node_id,
                            f"dispatch:{node_id}",
                            max_total_tokens=100,
                            max_total_cost_usd=1.0,
                            reserved_tokens=80,
                            reserved_cost_usd=0.8,
                        )
                    )

                threads = [
                    threading.Thread(target=claim, args=(first_store, node_ids["first"])),
                    threading.Thread(target=claim, args=(second_store, node_ids["second"])),
                ]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=5)

                self.assertEqual([False, True], sorted(results))
                claimed = [
                    item
                    for item in first_store.list_node_runs(run["id"])
                    if item["status"] == NodeRunStatus.QUEUED.value
                ]
                self.assertEqual(1, len(claimed))
                self.assertEqual(80, claimed[0]["reserved_tokens"])
                self.assertEqual(0.8, claimed[0]["reserved_cost_usd"])
                first_store.transition_node(claimed[0]["id"], NodeRunStatus.RUNNING)
                first_store.transition_node(claimed[0]["id"], NodeRunStatus.OUTPUT_READY)
                released = first_store.get_node_run(claimed[0]["id"])
                self.assertEqual(0, released["reserved_tokens"])
                self.assertIsNone(released["reserved_cost_usd"])

                other_id = next(node_id for node_id in node_ids.values() if node_id != claimed[0]["id"])
                self.assertTrue(
                    second_store.claim_node_with_budget(
                        other_id,
                        "dispatch:released",
                        max_total_tokens=100,
                        max_total_cost_usd=1.0,
                        reserved_tokens=80,
                        reserved_cost_usd=0.8,
                    )
                )
            finally:
                first_store._conn.close()
                second_store._conn.close()


if __name__ == "__main__":
    unittest.main()
