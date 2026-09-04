import json
import unittest
import uuid
from unittest.mock import patch

from api import create_app
from api.state import session_manager
from pfs_agent.audit import build_session_audit
from pfs_agent.ledger import (
    ClaimRecord,
    EvidenceCandidate,
    EvidenceLedger,
)


class SessionAuditContractTests(unittest.TestCase):
    def _payload(self, **filters):
        session_id = "audit-session"
        ledger = EvidenceLedger()
        support, _ = ledger.register(EvidenceCandidate(
            source_url="https://pfs.local/source/sales",
            snippet="华东销售额同比增长 12%",
            task_id=f"{session_id}:run-1",
            title="sales.csv",
            file_name="sales.csv",
            worksheet="Sheet1",
            included_rows=24,
        ))
        refute, _ = ledger.register(EvidenceCandidate(
            source_url="https://pfs.local/source/budget",
            snippet="预算口径下华东销售额同比下降 2%",
            task_id=f"{session_id}:run-1",
            title="budget.xlsx",
        ))
        ledger.create_claim(ClaimRecord(
            claim_id="claim-conflict", task_id=f"{session_id}:run-1",
            text="华东销售额同比增长",
        ))
        ledger.link_claim(
            "claim-conflict", evidence_id=support.evidence_id,
            relation="supports", confidence=0.9,
        )
        ledger.link_claim(
            "claim-conflict", evidence_id=refute.evidence_id,
            relation="refutes", confidence=0.7,
        )
        ledger.create_claim(ClaimRecord(
            claim_id="claim-uncovered", task_id=f"{session_id}:run-1",
            text="利润率处于健康水平",
        ))
        return build_session_audit(
            session_id=session_id,
            jobs=[{
                "id": "run-1", "type": "conversation_analysis",
                "label": "销售趋势分析", "status": "succeeded",
                "created_at": "2026-08-31T09:00:00+00:00",
                "started_at": "2026-08-31T09:00:01+00:00",
                "finished_at": "2026-08-31T09:00:04+00:00",
                "result": {"step_count": 1, "answer": "done"},
            }],
            events=[{
                "type": "model_retry", "job_id": "run-1", "sequence": 2,
                "created_at": "2026-08-31T09:00:02+00:00", "provider": "deepseek",
                "model": "deepseek-chat", "attempt": 1, "max_retries": 3,
                "wait_seconds": 3, "reason": "provider_unavailable",
            }, {
                "type": "conversation_step_finished", "job_id": "run-1",
                "sequence": 3, "created_at": "2026-08-31T09:00:03+00:00",
                "tool": "query_data", "display": "执行数据查询",
                "status": "succeeded", "elapsed_seconds": 1.2,
                "path": "/Users/private/source.csv",
                "args": {"sql": "select secret from private"},
            }, {
                "type": "job_done", "job_id": "run-1", "sequence": 4,
                "created_at": "2026-08-31T09:00:04+00:00",
                "status": "succeeded",
            }],
            artifacts=[{
                "id": "artifact-1", "type": "xlsx", "run_id": "run-1",
                "created_at": "2026-08-31T09:00:04+00:00", "size_bytes": 1024,
                "claim_ids": ["claim-conflict"],
                "evidence_ids": [support.evidence_id, refute.evidence_id],
                "cost": {
                    "amount": None, "currency": "USD",
                    "source": "provider_usage_price_unknown",
                    "model_calls": 1,
                },
                "path": "/Users/private/output.xlsx",
            }],
            claims=[item.to_dict() for item in ledger.list_claims()],
            evidence=[item.to_dict() for item in ledger.list_evidence()],
            lifecycle_events=[{
                "event": "claim_decision", "session_id": session_id,
                "claim_id": "claim-conflict", "task_id": f"{session_id}:run-1",
                "decision": "needs_review", "reason": "业务口径不一致",
                "at": "2026-08-31T09:05:00+00:00",
            }],
            usage_breakdowns=[{
                "provider": "deepseek", "model": "deepseek-chat",
                "actual_prompt_tokens": 120, "actual_completion_tokens": 30,
                "recorded_at": 1788166802,
            }],
            command_metrics=[{
                "command": "analyze", "command_type": "prompt",
                "outcome": "success", "duration_ms": 2400,
                "recorded_at": 1788166801,
            }],
            filters=filters,
        )

    def test_aggregates_lineage_conflicts_and_unknown_cost(self):
        payload = self._payload()
        self.assertTrue(payload["ok"])
        self.assertEqual(2, payload["summary"]["claims"])
        self.assertEqual(1, payload["summary"]["conflicts"])
        self.assertEqual(1, payload["summary"]["uncovered_claims"])
        self.assertEqual(1, payload["summary"]["model_calls"])
        self.assertEqual(150, payload["summary"]["input_tokens"] + payload["summary"]["output_tokens"])
        self.assertIsNone(payload["cost"]["amount"])
        self.assertFalse(payload["cost"]["known"])
        self.assertEqual("supports", payload["claims"][0]["evidence_links"][0]["relation"])
        self.assertEqual(2, len(payload["claims"][0]["evidence"]))

    def test_timeline_is_stable_and_does_not_expose_paths_or_args(self):
        payload = self._payload()
        times = [item["created_at"] for item in payload["timeline"] if item["created_at"]]
        self.assertEqual(times, sorted(times, reverse=True))
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("/Users/private", serialized)
        self.assertNotIn("select secret", serialized)

    def test_type_status_query_and_time_filters(self):
        model = self._payload(type="model")
        self.assertTrue(model["timeline"])
        self.assertTrue(all(item["kind"] == "model" for item in model["timeline"]))

        retry = self._payload(type="retry")
        self.assertEqual(1, len(retry["timeline"]))
        self.assertEqual(1, retry["timeline"][0]["metadata"]["attempt"])

        failed = self._payload(status="failed")
        self.assertFalse(failed["timeline"])

        query = self._payload(query="query_data")
        self.assertEqual(1, len(query["timeline"]))
        self.assertEqual("tool", query["timeline"][0]["kind"])

        narrowed = self._payload(
            **{"from": "2026-08-31T09:04:00+00:00", "to": "2026-08-31T09:06:00+00:00"},
        )
        self.assertEqual(["approval"], [item["kind"] for item in narrowed["timeline"]])

    def test_empty_session_shape_is_stable(self):
        payload = build_session_audit(session_id="empty")
        self.assertEqual([], payload["timeline"])
        self.assertEqual(0, payload["summary"]["jobs"])
        self.assertEqual(0.0, payload["cost"]["amount"])


class SessionAuditHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config.update(TESTING=True)

    def setUp(self):
        self.client = self.app.test_client()
        self.sid = f"audit-http-{uuid.uuid4().hex[:12]}"
        self.other_sid = f"audit-other-{uuid.uuid4().hex[:12]}"
        self.session = session_manager.get_or_create(self.sid)
        self.other = session_manager.get_or_create(self.other_sid)

    def tearDown(self):
        session_manager.remove(self.sid)
        session_manager.remove(self.other_sid)

    def test_endpoint_is_session_scoped(self):
        current_job = self.session.job_runner.begin_tracked("conversation_analysis", "当前会话")
        self.session.job_runner.succeed_tracked(current_job, {"answer": "ok"})
        other_job = self.other.job_runner.begin_tracked("conversation_analysis", "其他会话")
        self.other.job_runner.succeed_tracked(other_job, {"answer": "private"})

        with patch("api.audit.list_registered_artifacts", return_value=[]):
            response = self.client.get(f"/api/session/{self.sid}/audit")

        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertIn(current_job, serialized)
        self.assertNotIn(other_job, serialized)
        self.assertNotIn("其他会话", serialized)

    def test_endpoint_validates_filters(self):
        response = self.client.get(f"/api/session/{self.sid}/audit?type=unknown")
        self.assertEqual(400, response.status_code)
        response = self.client.get(f"/api/session/{self.sid}/audit?limit=1001")
        self.assertEqual(400, response.status_code)


if __name__ == "__main__":
    unittest.main()
