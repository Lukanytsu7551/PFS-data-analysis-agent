import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agent.tools.business.data import DataToolsMixin
from agent.workflows.knowledge import decide_knowledge_candidate
from api import knowledge as knowledge_api
from Function.Knowledge.knowledge_base import KnowledgeBase
from api import create_app


class KnowledgeScopeTests(unittest.TestCase):
    def test_knowledge_api_keeps_workspace_scope_for_files_and_database(self):
        with patch.object(
            knowledge_api,
            "_scope_context",
            return_value=("workspace-a", "user-a"),
        ), patch(
            "Function.Knowledge.knowledge_base.knowledge_scope_dir",
            return_value=Path("/tmp/workspace-a-knowledge"),
        ) as scope_dir:
            self.assertEqual(
                Path("/tmp/workspace-a-knowledge"), knowledge_api._kb_dir()
            )
        scope_dir.assert_called_once_with(
            workspace_id="workspace-a", user_id="user-a",
        )

        with patch.object(
            knowledge_api,
            "_scope_context",
            return_value=("workspace-a", "user-a"),
        ), patch(
            "Function.Knowledge.knowledge_base.KnowledgeBase",
        ) as knowledge_base:
            self.assertIs(knowledge_base.return_value, knowledge_api._kb())
        knowledge_base.assert_called_once_with(
            workspace_id="workspace-a", user_id="user-a",
        )

    def test_knowledge_api_reuses_and_closes_connection_per_request(self):
        app = create_app()
        app.config.update(TESTING=True)
        with patch.object(
            knowledge_api,
            "_scope_context",
            return_value=("workspace-request", "user-request"),
        ), patch(
            "Function.Knowledge.knowledge_base.KnowledgeBase",
        ) as knowledge_base:
            knowledge_base.return_value.list_categories.return_value = []
            with app.test_client() as client:
                response = client.get("/api/knowledge/categories")

        self.assertEqual(200, response.status_code)
        self.assertEqual(1, knowledge_base.call_count)
        knowledge_base.return_value.close.assert_called_once_with()

    def test_agent_knowledge_lookup_uses_the_frozen_workspace_scope(self):
        agent = DataToolsMixin()
        agent._knowledge_allowed_this_turn = True
        agent._workspace_id = "workspace-agent"
        agent._user_id = "user-agent"

        with patch("Function.Knowledge.knowledge_base.KnowledgeBase") as knowledge_base:
            knowledge_base.return_value.search.return_value = {
                "metrics": [], "rules": [], "notes": [], "documents": [],
            }
            result = agent._tool_query_knowledge_results("订单量")

        self.assertEqual([], result["metrics"])
        knowledge_base.assert_called_once_with(
            workspace_id="workspace-agent", user_id="user-agent",
        )
        knowledge_base.return_value.close.assert_called_once_with()

    def test_workflow_knowledge_publish_uses_workspace_and_closes_store(self):
        candidate = {
            "id": "candidate-1",
            "status": "pending",
            "candidate_type": "metric_sql",
            "title": "城市订单量",
            "payload": {
                "name": "城市订单量",
                "definition": "城市订单总量",
                "sql_template": "SELECT COUNT(*) FROM orders",
            },
        }
        run_store = MagicMock()
        run_store.get_knowledge_candidate.return_value = candidate
        run_store.decide_knowledge_candidate.return_value = {
            **candidate,
            "status": "accepted",
        }
        runtime = SimpleNamespace(workspace_id="workspace-workflow", run_store=run_store)

        with patch("Function.Knowledge.knowledge_base.KnowledgeBase") as knowledge_base:
            knowledge_base.return_value.add_metric.return_value = {"id": 7}
            result = decide_knowledge_candidate(
                runtime,
                "candidate-1",
                decision="accept",
                user_id="user-workflow",
            )

        self.assertEqual("accepted", result["status"])
        knowledge_base.assert_called_once_with(
            workspace_id="workspace-workflow", user_id="user-workflow",
        )
        knowledge_base.return_value.close.assert_called_once_with()
        run_store.decide_knowledge_candidate.assert_called_once_with(
            "candidate-1",
            decision="accept",
            decided_by="",
            comment="",
            published_ref={"kind": "metric", "id": 7},
        )

    def test_knowledge_records_are_not_cross_read_between_workspace_databases(self):
        with TemporaryDirectory(prefix="pfs-knowledge-scope-") as temp_dir:
            root_a = Path(temp_dir) / "workspace-a"
            root_b = Path(temp_dir) / "workspace-b"
            kb_a = KnowledgeBase(workspace_id="workspace-a", workspace_root=root_a)
            kb_b = KnowledgeBase(workspace_id="workspace-b", workspace_root=root_b)
            try:
                kb_a.add_metric(
                    name="A 专属订单口径",
                    alias="a_workspace_marker",
                    definition="只属于工作区 A 的订单总量定义",
                )
                kb_b.add_metric(
                    name="B 专属订单口径",
                    alias="b_workspace_marker",
                    definition="只属于工作区 B 的订单总量定义",
                )

                results_a = kb_a.search("a_workspace_marker")
                results_b = kb_b.search("b_workspace_marker")
            finally:
                kb_a.close()
                kb_b.close()

        self.assertTrue(any(item["name"] == "A 专属订单口径" for item in results_a["metrics"]))
        self.assertFalse(any(item["name"] == "B 专属订单口径" for item in results_a["metrics"]))
        self.assertTrue(any(item["name"] == "B 专属订单口径" for item in results_b["metrics"]))
        self.assertFalse(any(item["name"] == "A 专属订单口径" for item in results_b["metrics"]))


if __name__ == "__main__":
    unittest.main()
