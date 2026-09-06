import json
import threading
import unittest
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from api import create_app
from api.state import session_manager
from agent.agent import BusinessAgent
from data.sources.csv import CSVDataSource
from data.session import ChatSession


class AnalysisTableDeleteApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config.update(TESTING=True)

    def setUp(self):
        self.client = self.app.test_client()
        self.sid = f"analysis-delete-{uuid.uuid4().hex[:12]}"
        self.temp_dir = TemporaryDirectory()
        path = Path(self.temp_dir.name) / "sales.csv"
        path.write_text(
            "region,sales\n华东,42\n华南,28\n",
            encoding="utf-8",
        )
        self.source = CSVDataSource(str(path), path.name)
        self.session = session_manager.get_or_create(self.sid)
        self.source_id = self.session.add_source(self.source)
        self.agent = BusinessAgent(
            client=None,
            model="analysis-delete-test",
            data_source=self.source,
            session_id=self.sid,
        )

    def tearDown(self):
        session_manager.remove(self.sid)
        self.temp_dir.cleanup()

    def _create_table(self, name):
        result = self.agent._tool_create_analysis_table(
            "SELECT region, SUM(sales) AS total_sales FROM sales GROUP BY region",
            name,
        )
        self.assertIn(name, result)
        self.assertIn(name, self.source.list_tables())

    def _delete(self, name, key):
        return self.client.post(
            f"/api/session/{self.sid}/analysis-tables/delete",
            json={
                "source_id": self.source_id,
                "table_names": [name],
                "confirm": True,
                "operation_key": key,
            },
        )

    def test_delete_replays_same_request_without_touching_again(self):
        self._create_table("sales_summary")

        first = self._delete("sales_summary", "delete:sales-summary:1")
        self.assertEqual(200, first.status_code)
        first_payload = first.get_json()
        self.assertTrue(first_payload["ok"])
        self.assertFalse(first_payload["idempotent_replay"])
        self.assertEqual("succeeded", first_payload["audit"]["status"])
        self.assertEqual(["sales_summary"], first_payload["audit"]["deleted"])
        self.assertNotIn("sales_summary", self.source.list_tables())

        replay = self._delete("sales_summary", "delete:sales-summary:1")
        self.assertEqual(200, replay.status_code)
        replay_payload = replay.get_json()
        self.assertTrue(replay_payload["ok"])
        self.assertTrue(replay_payload["idempotent_replay"])
        self.assertEqual("succeeded", replay_payload["audit"]["status"])
        self.assertEqual(["sales_summary"], replay_payload["audit"]["deleted"])

    def test_same_key_with_different_tables_is_rejected(self):
        self._create_table("sales_summary")
        self._create_table("sales_summary_other")

        first = self._delete("sales_summary", "delete:conflict:1")
        self.assertEqual(200, first.status_code)

        conflict = self._delete("sales_summary_other", "delete:conflict:1")
        self.assertEqual(409, conflict.status_code)
        payload = conflict.get_json()
        self.assertFalse(payload["ok"])
        self.assertEqual("conflict", payload["audit"]["status"])
        self.assertIn("sales_summary_other", self.source.list_tables())

    def test_audit_endpoint_exposes_safe_delete_timeline(self):
        self._create_table("sales_summary")
        response = self._delete("sales_summary", "delete:audit:1")
        self.assertEqual(200, response.status_code)

        with patch("api.audit.list_registered_artifacts", return_value=[]):
            audit_response = self.client.get(f"/api/session/{self.sid}/audit")
        self.assertEqual(200, audit_response.status_code)
        payload = audit_response.get_json()
        items = payload["analysis_delete_operations"]
        self.assertEqual(1, len(items))
        self.assertEqual("analysis_table_delete", payload["timeline"][0]["type"])
        self.assertEqual("succeeded", items[0]["status"])
        self.assertEqual(["sales_summary"], items[0]["deleted"])
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn(self.temp_dir.name, serialized)
        self.assertNotIn("SELECT region", serialized)

    def test_raw_table_is_rejected_and_remains_present(self):
        response = self._delete("sales", "delete:raw:1")
        self.assertEqual(400, response.status_code)
        payload = response.get_json()
        self.assertFalse(payload["ok"])
        self.assertEqual("rejected", payload["audit"]["status"])
        self.assertIn("sales", self.source.list_tables())

    def test_delete_audit_survives_session_state_round_trip(self):
        self._create_table("sales_summary")
        response = self._delete("sales_summary", "delete:restore:1")
        self.assertEqual(200, response.status_code)

        restored = ChatSession(session_id="restored-delete-audit")
        restored.restore_rewind_state(self.session.capture_rewind_state())
        self.assertEqual(1, len(restored.analysis_delete_operations))
        self.assertEqual(
            "succeeded", restored.analysis_delete_operations[0]["status"]
        )
        self.assertEqual(
            ["sales_summary"], restored.analysis_delete_operations[0]["deleted"]
        )

    def test_concurrent_reservation_has_one_owner(self):
        decisions = []
        barrier = threading.Barrier(2)

        def reserve():
            barrier.wait(timeout=2)
            decisions.append(
                self.session.begin_analysis_delete_operation(
                    operation_key="delete:concurrent:1",
                    request_sha256="a" * 64,
                    table_names=["sales_summary"],
                    source_name="sales.csv",
                )[0]
            )

        threads = [threading.Thread(target=reserve) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)
        self.assertEqual(["in_progress", "new"], sorted(decisions))
        self.session.finish_analysis_delete_operation(
            "delete:concurrent:1", status="succeeded", deleted=["sales_summary"]
        )


if __name__ == "__main__":
    unittest.main()
