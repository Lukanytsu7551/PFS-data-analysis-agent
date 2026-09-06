import io
import threading
import time
import uuid
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from api import create_app
from api.state import session_manager
from agent.agent import AgentRunTimeout


class PfsHttpVerticalSliceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config.update(TESTING=True)

    def setUp(self):
        self.client = self.app.test_client()
        self.sid = f"pfs-http-{uuid.uuid4().hex[:12]}"
        session_manager.get_or_create(self.sid)

    def tearDown(self):
        session_manager.remove(self.sid)

    def _upload(self):
        csv = (
            "month,region,sales_amount\n"
            "2026-01,华东,1200\n"
            "2026-01,华南,800\n"
            "2026-02,华东,1500\n"
            "2026-02,华南,900\n"
        ).encode("utf-8")
        return self.client.post(
            f"/api/session/{self.sid}/upload",
            data={"file": (io.BytesIO(csv), "quarterly_sales.csv")},
            content_type="multipart/form-data",
        )

    def _upload_profit_csv(self):
        csv = (
            "month,region,sales_amount,profit_amount\n"
            "2026-01,华东,1200,300\n"
            "2026-01,华南,800,160\n"
            "2026-02,华东,1500,450\n"
            "2026-02,华南,900,180\n"
        ).encode("utf-8")
        return self.client.post(
            f"/api/session/{self.sid}/upload",
            data={"file": (io.BytesIO(csv), "profit_sales.csv")},
            content_type="multipart/form-data",
        )

    def _upload_missing_metric_csv(self):
        csv = ("month,region,orders\n2026-01,华东,12\n2026-02,华南,8\n").encode("utf-8")
        return self.client.post(
            f"/api/session/{self.sid}/upload",
            data={"file": (io.BytesIO(csv), "missing-sales.csv")},
            content_type="multipart/form-data",
        )

    def test_upload_list_and_analyze_uploaded_csv(self):
        uploaded = self._upload()
        self.assertEqual(200, uploaded.status_code)
        upload_payload = uploaded.get_json()
        self.assertTrue(upload_payload["ok"])
        source_id = upload_payload["added"][0]["source_id"]

        listed = self.client.get(f"/api/session/{self.sid}/pfs/sources")
        self.assertEqual(200, listed.status_code)
        listed_payload = listed.get_json()
        self.assertTrue(listed_payload["ok"])
        self.assertEqual(source_id, listed_payload["sources"][0]["source_id"])
        self.assertEqual(4, listed_payload["sources"][0]["row_count"])

        analyzed = self.client.post(
            f"/api/session/{self.sid}/pfs/analyze",
            json={
                "source_id": source_id,
                "run_id": "http-upload-run",
                "value_column": "sales_amount",
                "date_column": "month",
                "dimension": "region",
                "date_from": "2026-01",
                "date_to": "2026-02",
            },
        )
        self.assertEqual(200, analyzed.status_code)
        result = analyzed.get_json()["result"]
        self.assertEqual(4400, result["total"])
        self.assertEqual(["华东", "华南"], [item["dimension"] for item in result["groups"]])
        self.assertEqual(2700, result["groups"][0]["value"])
        self.assertEqual(1, len(result["evidence"]))
        evidence = result["evidence"][0]
        self.assertEqual("quarterly_sales.csv", evidence["file_name"])
        self.assertEqual("", evidence["worksheet"])
        self.assertEqual(4, evidence["included_rows"])
        self.assertEqual(result["snapshot"]["content_sha256"], evidence["content_sha256"])
        self.assertIn("quarterly_sales.csv#sha256=", evidence["locator"])
        self.assertEqual([evidence["evidence_id"]], result["claims"][0]["evidence_ids"])
        self.assertEqual("http-upload-run", result["request"]["run_id"])

    def test_uploaded_analysis_surfaces_duplicate_row_warning(self):
        csv = (
            "month,region,sales_amount\n"
            "2026-01,华东,1200\n"
            "2026-01,华东,1200\n"
            "2026-02,华南,800\n"
        ).encode("utf-8")
        uploaded = self.client.post(
            f"/api/session/{self.sid}/upload",
            data={"file": (io.BytesIO(csv), "duplicate_sales.csv")},
            content_type="multipart/form-data",
        )
        source_id = uploaded.get_json()["added"][0]["source_id"]
        analyzed = self.client.post(
            f"/api/session/{self.sid}/pfs/analyze",
            json={
                "source_id": source_id,
                "run_id": "http-duplicate-rows",
                "value_column": "sales_amount",
                "date_column": "month",
                "dimension": "region",
            },
        )

        self.assertEqual(200, analyzed.status_code)
        result = analyzed.get_json()["result"]
        self.assertEqual(3200, result["total"])
        self.assertEqual(1, result["snapshot"]["duplicate_rows"])
        self.assertTrue(any("系统未自动去重" in warning for warning in result["warnings"]))

    def test_uploaded_analysis_supports_bounded_aggregate_selection(self):
        uploaded = self._upload_profit_csv()
        source_id = uploaded.get_json()["added"][0]["source_id"]
        analyzed = self.client.post(
            f"/api/session/{self.sid}/pfs/analyze",
            json={
                "source_id": source_id,
                "run_id": "http-average-run",
                "value_column": "profit_amount",
                "date_column": "month",
                "dimension": "region",
                "aggregation": "AVG",
            },
        )
        self.assertEqual(200, analyzed.status_code)
        result = analyzed.get_json()["result"]
        self.assertEqual(272.5, result["total"])
        self.assertEqual("AVG(profit_amount)", result["metric"]["formula"])
        self.assertEqual(375, result["groups"][0]["value"])

        invalid = self.client.post(
            f"/api/session/{self.sid}/pfs/analyze",
            json={
                "source_id": source_id,
                "run_id": "http-invalid-aggregate",
                "value_column": "profit_amount",
                "date_column": "month",
                "dimension": "region",
                "aggregation": "MEDIAN",
            },
        )
        self.assertEqual(400, invalid.status_code)
        self.assertEqual("metric_formula_unsupported", invalid.get_json()["code"])

    def test_active_uploaded_analysis_can_be_canceled_before_result_commit(self):
        uploaded = self._upload()
        source_id = uploaded.get_json()["added"][0]["source_id"]
        run_id = "http-cancel-run"
        analysis_started = threading.Event()
        release_analysis = threading.Event()
        response_holder = {}

        def slow_analysis(*_args, **_kwargs):
            analysis_started.set()
            release_analysis.wait(timeout=3)
            return object()

        def post_analysis():
            with self.app.test_client() as client:
                response_holder["response"] = client.post(
                    f"/api/session/{self.sid}/pfs/analyze",
                    json={
                        "source_id": source_id,
                        "run_id": run_id,
                        "value_column": "sales_amount",
                        "date_column": "month",
                        "dimension": "region",
                    },
                )

        with patch("api.pfs.analyze_file", side_effect=slow_analysis):
            worker = threading.Thread(target=post_analysis, daemon=True)
            worker.start()
            try:
                self.assertTrue(analysis_started.wait(timeout=2))
                canceled = self.client.post(f"/api/session/{self.sid}/pfs/runs/{run_id}/cancel")
                self.assertEqual(202, canceled.status_code)
                self.assertEqual("cancel_requested", canceled.get_json()["status"])
            finally:
                release_analysis.set()
                worker.join(timeout=3)

            self.assertFalse(worker.is_alive())
        response = response_holder["response"]
        self.assertEqual(409, response.status_code)
        self.assertEqual("pfs_analysis_canceled", response.get_json()["code"])

    def test_cancel_rejects_run_that_is_not_active_in_session(self):
        response = self.client.post(f"/api/session/{self.sid}/pfs/runs/not-running/cancel")
        self.assertEqual(404, response.status_code)
        self.assertEqual("pfs_analysis_run_not_active", response.get_json()["code"])

    def test_upload_rejects_oversized_file_with_explicit_code(self):
        from api.datasource import MAX_UPLOAD_BYTES

        response = self.client.post(
            f"/api/session/{self.sid}/upload",
            data={"file": (io.BytesIO(b"x" * (MAX_UPLOAD_BYTES + 1)), "too-large.csv")},
            content_type="multipart/form-data",
        )
        self.assertEqual(413, response.status_code)
        payload = response.get_json()
        self.assertFalse(payload["ok"])
        self.assertEqual("upload_file_too_large", payload["code"])

    def test_invalid_date_source_remains_selectable_and_analysis_explains_repair(self):
        csv = ("month,region,sales_amount\n2026-02-30,华东,1200\n").encode("utf-8")
        uploaded = self.client.post(
            f"/api/session/{self.sid}/upload",
            data={"file": (io.BytesIO(csv), "invalid-date.csv")},
            content_type="multipart/form-data",
        )
        self.assertEqual(200, uploaded.status_code)
        source_id = uploaded.get_json()["added"][0]["source_id"]

        listed = self.client.get(f"/api/session/{self.sid}/pfs/sources")
        self.assertEqual(200, listed.status_code)
        source = listed.get_json()["sources"][0]
        self.assertEqual(source_id, source["source_id"])
        self.assertEqual(["month", "region", "sales_amount"], source["columns"])
        self.assertEqual(1, source["row_count"])
        self.assertEqual("source_date_invalid", source["validation_error"]["code"])

        analyzed = self.client.post(
            f"/api/session/{self.sid}/pfs/analyze",
            json={
                "source_id": source_id,
                "run_id": "invalid-date-run",
                "value_column": "sales_amount",
                "date_column": "month",
                "dimension": "region",
            },
        )
        self.assertEqual(400, analyzed.status_code)
        self.assertEqual("source_date_invalid", analyzed.get_json()["code"])

    def test_question_missing_metric_column_keeps_actionable_error_code(self):
        uploaded = self._upload_missing_metric_csv()
        self.assertEqual(200, uploaded.status_code)
        source_id = uploaded.get_json()["added"][0]["source_id"]

        response = self.client.post(
            f"/api/session/{self.sid}/pfs/query",
            json={
                "source_id": source_id,
                "run_id": "missing-metric-query",
                "question": "按地区统计销售额",
            },
        )
        self.assertEqual(400, response.status_code)
        payload = response.get_json()
        self.assertFalse(payload["ok"])
        self.assertEqual("source_columns_missing", payload["code"])
        self.assertIn("指标字段", payload["error"])

    def test_fixture_report_export_returns_server_recomputed_json_and_csv(self):
        json_response = self.client.post(
            "/api/pfs/export",
            json={"format": "json", "run_id": "fixture-export-json"},
        )
        self.assertEqual(200, json_response.status_code)
        self.assertIn(
            'attachment; filename="pfs-report-fixture-export-json.json"',
            json_response.headers["Content-Disposition"],
        )
        json_payload = json_response.get_json()
        self.assertTrue(json_payload["ok"])
        self.assertEqual(100000, json_payload["result"]["total"])
        self.assertEqual("fixture-export-json", json_payload["result"]["run_id"])
        self.assertTrue(json_response.headers["X-PFS-Source-SHA256"])

        csv_response = self.client.post(
            "/api/pfs/export",
            json={"format": "csv", "run_id": "fixture-export-csv"},
        )
        self.assertEqual(200, csv_response.status_code)
        self.assertIn(
            'attachment; filename="pfs-report-fixture-export-csv.csv"',
            csv_response.headers["Content-Disposition"],
        )
        csv_body = csv_response.get_data(as_text=True)
        self.assertIn("summary,total,100000", csv_body)
        self.assertIn("group,region,华东,42000", csv_body)
        self.assertIn("evidence,", csv_body)

    def test_uploaded_report_export_recomputes_natural_language_contract(self):
        uploaded = self._upload()
        source_id = uploaded.get_json()["added"][0]["source_id"]
        response = self.client.post(
            f"/api/session/{self.sid}/pfs/export",
            json={
                "format": "csv",
                "source_id": source_id,
                "question": "按地区统计 2026年1月到2026年2月的销售额",
                "run_id": "uploaded-export",
            },
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual("uploaded-export", response.headers["X-PFS-Report-Run"])
        body = response.get_data(as_text=True)
        self.assertIn("summary,total,4400", body)
        self.assertIn("group,region,华东,2700", body)

    def test_report_export_rejects_unknown_format(self):
        response = self.client.post("/api/pfs/export", json={"format": "xlsx"})
        self.assertEqual(400, response.status_code)
        self.assertFalse(response.get_json()["ok"])
        self.assertIn("format must be json or csv", response.get_json()["error"])

    def test_capabilities_report_real_verified_and_pending_boundaries(self):
        response = self.client.get("/api/pfs/capabilities")
        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertEqual("implemented_server_recomputed", payload["reporting"]["report_export_json"])
        self.assertEqual("verified_local_http", payload["models"]["deepseek_chat"])
        self.assertEqual(
            "implemented_local_contract_v1_with_agent_model_output_adapters",
            payload["models"]["prediction_quality_evaluation"],
        )
        self.assertEqual(
            "implemented_validated_environment_contract_v1",
            payload["runtime"]["agent_runtime_budget"],
        )
        self.assertEqual(
            "implemented_graph_limits_atomic_reservation_v2",
            payload["runtime"]["workflow_budget"],
        )
        self.assertIn("other_connectors_pending", payload["runtime"]["external_sources"])

    def test_closing_sse_before_agent_build_keeps_background_turn_alive(self):
        agent_started = threading.Event()
        release_agent = threading.Event()

        class FakeAgent:
            _artifact_metadata = {}
            _provider = "test"
            model = "detached-chat-test"

            def run(self, *_args, **_kwargs):
                agent_started.set()
                release_agent.wait(timeout=3)
                yield {"type": "text", "content": "后台完成"}

        with patch("api.chat._build_agent", return_value=FakeAgent()):
            response = self.client.post(
                f"/api/session/{self.sid}/chat",
                json={"message": "测试断开后继续"},
                buffered=False,
            )
            stream = iter(response.response)
            self.assertIn("agent_activity", next(stream).decode("utf-8"))
            jobs = session_manager.get(self.sid).job_runner.list_jobs(limit=10)
            self.assertEqual(1, len(jobs))
            job_id = jobs[0]["id"]

            stream.close()
            response.close()
            self.assertTrue(agent_started.wait(timeout=2))
            status = session_manager.get(self.sid).job_runner.get_status(job_id)
            self.assertNotEqual("canceled", status["status"])
            release_agent.set()

        deadline = time.monotonic() + 3
        status = None
        while time.monotonic() < deadline:
            status = session_manager.get(self.sid).job_runner.get_status(job_id)
            if status and status["status"] in {"succeeded", "failed", "canceled"}:
                break
            time.sleep(0.02)
        self.assertEqual("succeeded", status["status"])
        replay = self.client.get(
            f"/api/session/{self.sid}/chat/{job_id}/events?after_sequence=0"
        ).get_json()
        self.assertEqual(["text", "done"], [event["type"] for event in replay["events"]])

    def test_closing_sse_after_partial_event_keeps_background_result(self):
        class FakeAgent:
            _artifact_metadata = {}
            _provider = "test"
            model = "stream-close-test"

            def run(self, *_args, **_kwargs):
                yield {"type": "text", "content": "部分结果"}

        with patch("api.chat._build_agent", return_value=FakeAgent()):
            response = self.client.post(
                f"/api/session/{self.sid}/chat",
                json={"message": "测试部分断开"},
                buffered=False,
            )
            stream = iter(response.response)
            next(stream)
            partial = next(stream).decode("utf-8")
            self.assertIn("部分结果", partial)
            jobs = session_manager.get(self.sid).job_runner.list_jobs(limit=10)
            self.assertEqual(1, len(jobs))
            job_id = jobs[0]["id"]

            stream.close()
            response.close()

        status = session_manager.get(self.sid).job_runner.get_status(job_id)
        self.assertEqual("succeeded", status["status"])
        self.assertNotIn('"type": "done"', partial)
        replay = self.client.get(
            f"/api/session/{self.sid}/chat/{job_id}/events?after_sequence=0"
        )
        self.assertEqual(200, replay.status_code)
        self.assertEqual(
            ["text", "done"],
            [event["type"] for event in replay.get_json()["events"]],
        )

    def test_explicit_stop_cancels_detached_chat_turn(self):
        agent_started = threading.Event()
        release_agent = threading.Event()

        class FakeAgent:
            _artifact_metadata = {}
            _provider = "test"
            model = "detached-stop-test"

            def run(self, *_args, **_kwargs):
                agent_started.set()
                release_agent.wait(timeout=3)
                yield {"type": "text", "content": "不应完成"}

        with patch("api.chat._build_agent", return_value=FakeAgent()):
            response = self.client.post(
                f"/api/session/{self.sid}/chat",
                json={"message": "测试显式停止"},
                buffered=False,
            )
            stream = iter(response.response)
            next(stream)
            jobs = session_manager.get(self.sid).job_runner.list_jobs(limit=10)
            job_id = jobs[0]["id"]
            self.assertTrue(agent_started.wait(timeout=2))

            stopped = self.client.post(f"/api/session/{self.sid}/stop")
            self.assertEqual(200, stopped.status_code)
            self.assertTrue(stopped.get_json()["ok"])
            self.assertIn(
                session_manager.get(self.sid).job_runner.get_status(job_id)["status"],
                {"canceling", "canceled"},
            )
            release_agent.set()
            stream.close()
            response.close()

        deadline = time.monotonic() + 3
        status = None
        while time.monotonic() < deadline:
            status = session_manager.get(self.sid).job_runner.get_status(job_id)
            if status and status["status"] == "canceled":
                break
            time.sleep(0.02)
        self.assertEqual("canceled", status["status"])
        replay = self.client.get(
            f"/api/session/{self.sid}/chat/{job_id}/events?after_sequence=0"
        ).get_json()
        self.assertEqual(
            ["stopped", "done"],
            [event["type"] for event in replay["events"][-2:]],
        )

    def test_explicit_stop_reaches_agent_retry_and_unwinds_as_canceled(self):
        agent_started = threading.Event()
        release_agent = threading.Event()

        class CancelAwareAgent:
            _artifact_metadata = {}
            _provider = "test"
            model = "cancel-aware-test"

            def run(self, *_args, **kwargs):
                agent_started.set()
                release_agent.wait(timeout=3)
                kwargs["cancel_check"]()
                yield {"type": "text", "content": "不应发送"}

        with patch("api.chat._build_agent", return_value=CancelAwareAgent()):
            response = self.client.post(
                f"/api/session/{self.sid}/chat",
                json={"message": "测试 Agent 内部取消"},
                buffered=False,
            )
            stream = iter(response.response)
            next(stream)
            jobs = session_manager.get(self.sid).job_runner.list_jobs(limit=10)
            job_id = jobs[0]["id"]
            self.assertTrue(agent_started.wait(timeout=2))

            stopped = self.client.post(f"/api/session/{self.sid}/stop")
            self.assertEqual(200, stopped.status_code)
            release_agent.set()
            stream.close()
            response.close()

        deadline = time.monotonic() + 3
        status = None
        while time.monotonic() < deadline:
            status = session_manager.get(self.sid).job_runner.get_status(job_id)
            if status and status["status"] == "canceled":
                break
            time.sleep(0.02)
        self.assertIsNotNone(status)
        self.assertEqual("canceled", status["status"])
        replay = self.client.get(
            f"/api/session/{self.sid}/chat/{job_id}/events?after_sequence=0"
        ).get_json()["events"]
        self.assertNotIn("agent_runtime_failed", [event.get("code") for event in replay])
        self.assertIn("stopped", [event["type"] for event in replay])

    def test_explicit_stop_interrupts_business_agent_stream_iteration(self):
        from types import SimpleNamespace

        from agent.agent import BusinessAgent

        provider_started = threading.Event()
        release_stream = threading.Event()

        def text_chunk(text, finish_reason=None):
            return SimpleNamespace(
                usage=None,
                choices=[SimpleNamespace(
                    finish_reason=finish_reason,
                    delta=SimpleNamespace(
                        content=text,
                        reasoning_content=None,
                        tool_calls=None,
                    ),
                )],
            )

        class FakeStream:
            def __iter__(self):
                yield text_chunk("已输出")
                release_stream.wait(timeout=3)
                yield text_chunk("不应继续", finish_reason="stop")

        class FakeCompletions:
            def create(self, **_kwargs):
                provider_started.set()
                return FakeStream()

        agent = BusinessAgent(
            client=SimpleNamespace(
                chat=SimpleNamespace(completions=FakeCompletions())
            ),
            model="pfs-http-cancel-stream-test",
            provider="test",
            session_id=self.sid,
        )

        with patch("api.chat._build_agent", return_value=agent):
            response = self.client.post(
                f"/api/session/{self.sid}/chat",
                json={"message": "测试流式取消"},
                buffered=False,
            )
            stream = iter(response.response)
            next(stream)
            jobs = session_manager.get(self.sid).job_runner.list_jobs(limit=10)
            job_id = jobs[0]["id"]
            self.assertTrue(provider_started.wait(timeout=2))

            stopped = self.client.post(f"/api/session/{self.sid}/stop")
            self.assertEqual(200, stopped.status_code)
            release_stream.set()
            stream.close()
            response.close()

        deadline = time.monotonic() + 3
        status = None
        while time.monotonic() < deadline:
            status = session_manager.get(self.sid).job_runner.get_status(job_id)
            if status and status["status"] == "canceled":
                break
            time.sleep(0.02)
        self.assertIsNotNone(status)
        self.assertEqual("canceled", status["status"])
        self.assertEqual(0, len(session_manager.get(self.sid).history))

    def test_chat_stream_exposes_job_cursor_and_replays_public_events(self):
        class FakeAgent:
            _artifact_metadata = {}
            _provider = "test"
            model = "chat-replay-test"

            def run(self, *_args, **_kwargs):
                yield {"type": "text", "content": "可回放结果"}

        with patch("api.chat._build_agent", return_value=FakeAgent()):
            response = self.client.post(
                f"/api/session/{self.sid}/chat",
                json={"message": "测试会话回放"},
            )
            self.assertEqual(200, response.status_code)
            job_id = response.headers["X-PFS-Conversation-Job"]
            self.assertTrue(job_id)
            self.assertIn(job_id, response.get_data(as_text=True))

        status = session_manager.get(self.sid).job_runner.get_status(job_id)
        self.assertEqual("succeeded", status["status"])
        replay = self.client.get(
            f"/api/session/{self.sid}/chat/{job_id}/events?after_sequence=0"
        )
        self.assertEqual(200, replay.status_code)
        payload = replay.get_json()
        self.assertEqual("succeeded", payload["status"])
        self.assertFalse(payload["replay_truncated"])
        self.assertEqual(
            ["text", "done"], [event["type"] for event in payload["events"]]
        )
        text_event = payload["events"][0]
        self.assertEqual("可回放结果", text_event["content"])
        self.assertGreater(text_event["stream_sequence"], 0)

        tail = self.client.get(
            f"/api/session/{self.sid}/chat/{job_id}/events"
            f"?after_sequence={text_event['stream_sequence']}"
        )
        self.assertEqual(200, tail.status_code)
        self.assertEqual(["done"], [event["type"] for event in tail.get_json()["events"]])

    def test_chat_structured_agent_timeout_fails_job_and_replays_error_contract(self):
        class FakeAgent:
            _artifact_metadata = {}
            _provider = "test"
            model = "timeout-chat-test"

            def run(self, *_args, **_kwargs):
                yield {
                    "type": "error",
                    "message": "分析超过运行时间上限，已安全终止。请缩小问题范围后重试。",
                    "code": "agent_run_timeout",
                    "recovery_action": "retry_with_smaller_scope",
                }

        with patch("api.chat._build_agent", return_value=FakeAgent()):
            response = self.client.post(
                f"/api/session/{self.sid}/chat",
                json={"message": "测试超时契约"},
            )
            body = response.get_data(as_text=True)

        self.assertEqual(200, response.status_code)
        self.assertIn("agent_run_timeout", body)
        job_id = response.headers["X-PFS-Conversation-Job"]
        job = session_manager.get(self.sid).job_runner.get_status(job_id)
        self.assertEqual("failed", job["status"])
        self.assertEqual("agent_run_timeout", job["error_code"])
        self.assertEqual("retry_with_smaller_scope", job["recovery_action"])
        replay = self.client.get(
            f"/api/session/{self.sid}/chat/{job_id}/events?after_sequence=0"
        )
        self.assertEqual(200, replay.status_code)
        events = replay.get_json()["events"]
        self.assertEqual(["error", "done"], [event["type"] for event in events])
        self.assertEqual("agent_run_timeout", events[0]["code"])
        self.assertEqual("retry_with_smaller_scope", events[0]["recovery_action"])

    def test_chat_raised_agent_timeout_uses_same_durable_error_contract(self):
        class FakeAgent:
            _artifact_metadata = {}
            _provider = "test"
            model = "raised-timeout-chat-test"

            def run(self, *_args, **_kwargs):
                raise AgentRunTimeout

        with patch("api.chat._build_agent", return_value=FakeAgent()):
            response = self.client.post(
                f"/api/session/{self.sid}/chat",
                json={"message": "测试委托超时契约"},
            )
            body = response.get_data(as_text=True)

        self.assertEqual(200, response.status_code)
        self.assertIn("agent_run_timeout", body)
        job_id = response.headers["X-PFS-Conversation-Job"]
        job = session_manager.get(self.sid).job_runner.get_status(job_id)
        self.assertEqual("failed", job["status"])
        self.assertEqual("agent_run_timeout", job["error_code"])
        self.assertEqual("retry_with_smaller_scope", job["recovery_action"])

    def test_natural_language_query_uses_deterministic_analysis(self):
        uploaded = self._upload()
        source_id = uploaded.get_json()["added"][0]["source_id"]
        response = self.client.post(
            f"/api/session/{self.sid}/pfs/query",
            json={
                "source_id": source_id,
                "question": "按地区统计 2026年1月到2026年2月的销售额",
                "run_id": "nl-run",
            },
        )
        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(4400, payload["result"]["total"])
        self.assertEqual("region", payload["interpretation"]["request"]["dimension"])
        self.assertEqual("2026-01", payload["interpretation"]["request"]["date_from"])
        self.assertEqual("2026-02", payload["interpretation"]["request"]["date_to"])
        self.assertEqual(1, len(payload["result"]["evidence"]))

    def test_natural_language_query_selects_profit_metric(self):
        uploaded = self._upload_profit_csv()
        source_id = uploaded.get_json()["added"][0]["source_id"]
        response = self.client.post(
            f"/api/session/{self.sid}/pfs/query",
            json={
                "source_id": source_id,
                "question": "按地区统计 2026年1月到2026年2月的利润",
                "run_id": "profit-run",
            },
        )
        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual("利润", payload["interpretation"]["metric"]["label"])
        self.assertEqual("profit_amount", payload["interpretation"]["metric"]["value_column"])
        self.assertEqual(1090, payload["result"]["total"])
        self.assertEqual(750, payload["result"]["groups"][0]["value"])

    def test_natural_language_query_selects_explicit_average(self):
        uploaded = self._upload_profit_csv()
        source_id = uploaded.get_json()["added"][0]["source_id"]
        response = self.client.post(
            f"/api/session/{self.sid}/pfs/query",
            json={
                "source_id": source_id,
                "question": "按地区统计平均利润",
                "run_id": "nl-average-run",
            },
        )
        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertEqual("AVG(profit_amount)", payload["result"]["metric"]["formula"])
        self.assertEqual(272.5, payload["result"]["total"])
        self.assertIn("做 AVG", payload["interpretation"]["interpretation"])

    def test_natural_language_query_rejects_unsupported_margin(self):
        uploaded = self._upload_profit_csv()
        source_id = uploaded.get_json()["added"][0]["source_id"]
        response = self.client.post(
            f"/api/session/{self.sid}/pfs/query",
            json={
                "source_id": source_id,
                "question": "按地区统计 2026年1月到2026年2月的利润率",
            },
        )
        self.assertEqual(400, response.status_code)
        self.assertEqual("pfs_query_failed", response.get_json()["code"])

    def test_natural_language_query_rejects_ambiguous_question(self):
        uploaded = self._upload()
        source_id = uploaded.get_json()["added"][0]["source_id"]
        response = self.client.post(
            f"/api/session/{self.sid}/pfs/query", json={"source_id": source_id, "question": "帮我看看数据"}
        )
        self.assertEqual(400, response.status_code)
        self.assertFalse(response.get_json()["ok"])
        self.assertIn("明确问题", response.get_json()["error"])

    def test_chat_deterministic_mode_streams_pfs_result(self):
        uploaded = self._upload()
        source_id = uploaded.get_json()["added"][0]["source_id"]
        response = self.client.post(
            f"/api/session/{self.sid}/chat",
            json={
                "message": "按地区统计 2026年1月到2026年2月的销售额",
                "pfs_mode": "deterministic",
                "source_id": source_id,
                "run_id": "chat-pfs-run",
            },
        )
        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn("pfs_result", body)
        self.assertIn("4400", body)
        self.assertIn('"claims"', body)
        self.assertIn('"evidence"', body)

    def test_chat_deterministic_mode_requires_source(self):
        response = self.client.post(
            f"/api/session/{self.sid}/chat",
            json={
                "message": "按地区统计销售额",
                "pfs_mode": "deterministic",
            },
        )
        self.assertEqual(400, response.status_code)
        self.assertEqual("pfs_source_required", response.get_json()["code"])

    def test_chat_deterministic_mode_rejects_ambiguous_question(self):
        uploaded = self._upload()
        source_id = uploaded.get_json()["added"][0]["source_id"]
        response = self.client.post(
            f"/api/session/{self.sid}/chat",
            json={
                "message": "帮我看看数据",
                "pfs_mode": "deterministic",
                "source_id": source_id,
            },
        )
        self.assertEqual(400, response.status_code)
        self.assertEqual("pfs_query_failed", response.get_json()["code"])

    def test_uploaded_analysis_rejects_unknown_metric_column(self):
        uploaded = self._upload()
        source_id = uploaded.get_json()["added"][0]["source_id"]
        response = self.client.post(
            f"/api/session/{self.sid}/pfs/analyze",
            json={"source_id": source_id, "value_column": "not_a_column"},
        )
        self.assertEqual(400, response.status_code)
        self.assertFalse(response.get_json()["ok"])
        self.assertIn("metric columns missing", response.get_json()["error"])

    def test_upload_list_and_analyze_uploaded_xlsx(self):
        with TemporaryDirectory() as temp_dir:
            workbook = Path(temp_dir) / "monthly_sales.xlsx"
            with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
                pd.DataFrame(
                    {
                        "month": ["2026-01", "2026-02", "2026-03"],
                        "region": ["华东", "华东", "华南"],
                        "sales_amount": [1200, 1500, 900],
                    }
                ).to_excel(writer, sheet_name="Monthly Sales", index=False)
                pd.DataFrame({"channel": ["线上", "线下"], "orders": [8, 4]}).to_excel(
                    writer, sheet_name="Orders", index=False
                )

            with workbook.open("rb") as handle:
                uploaded = self.client.post(
                    f"/api/session/{self.sid}/upload",
                    data={"file": (handle, workbook.name)},
                    content_type="multipart/form-data",
                )

        self.assertEqual(200, uploaded.status_code)
        upload_payload = uploaded.get_json()
        self.assertTrue(upload_payload["ok"])
        # One workbook is one data source; its worksheets remain tables inside
        # that source and are exposed by the source preview/schema.
        self.assertEqual(1, len(upload_payload["added"]))
        source_id = upload_payload["added"][0]["source_id"]

        listed = self.client.get(f"/api/session/{self.sid}/pfs/sources")
        self.assertEqual(200, listed.status_code)
        listed_payload = listed.get_json()
        self.assertTrue(listed_payload["ok"])
        self.assertEqual(1, len(listed_payload["sources"]))
        self.assertEqual("monthly_sales.xlsx", listed_payload["sources"][0]["name"])
        self.assertEqual("monthly_sales.xlsx", listed_payload["sources"][0]["file_name"])
        self.assertEqual(
            ["Monthly Sales", "Orders"],
            [item["name"] for item in listed_payload["sources"][0]["worksheets"]],
        )
        self.assertIn("month", listed_payload["sources"][0]["worksheets"][0]["columns"])

        analyzed = self.client.post(
            f"/api/session/{self.sid}/pfs/analyze",
            json={
                "source_id": source_id,
                "worksheet": "Monthly Sales",
                "run_id": "http-xlsx-run",
                "value_column": "sales_amount",
                "date_column": "month",
                "dimension": "region",
                "date_from": "2026-01",
                "date_to": "2026-03",
            },
        )
        self.assertEqual(200, analyzed.status_code)
        result = analyzed.get_json()["result"]
        self.assertEqual(3600, result["total"])
        self.assertEqual(["华东", "华南"], [item["dimension"] for item in result["groups"]])
        self.assertEqual("http-xlsx-run", result["request"]["run_id"])
        self.assertEqual(1, len(result["evidence"]))
        self.assertEqual("Monthly Sales", result["evidence"][0]["worksheet"])
        self.assertEqual(3, result["evidence"][0]["included_rows"])
        self.assertEqual("monthly_sales.xlsx", result["evidence"][0]["file_name"])
        self.assertEqual(result["snapshot"]["content_sha256"], result["evidence"][0]["content_sha256"])

        chat = self.client.post(
            f"/api/session/{self.sid}/chat",
            json={
                "message": "按地区统计 2026年1月到2026年3月的销售额",
                "pfs_mode": "deterministic",
                "source_id": source_id,
                "worksheet": "Monthly Sales",
                "run_id": "chat-xlsx-run",
            },
        )
        self.assertEqual(200, chat.status_code)
        self.assertIn("pfs_result", chat.get_data(as_text=True))
        self.assertIn('"claims"', chat.get_data(as_text=True))
        self.assertIn('"evidence"', chat.get_data(as_text=True))

        exported = self.client.post(
            f"/api/session/{self.sid}/pfs/export",
            json={
                "format": "json",
                "source_id": source_id,
                "worksheet": "Monthly Sales",
                "run_id": "http-xlsx-export",
                "value_column": "sales_amount",
                "date_column": "month",
                "dimension": "region",
                "date_from": "2026-01",
                "date_to": "2026-03",
            },
        )
        self.assertEqual(200, exported.status_code)
        self.assertEqual("http-xlsx-export", exported.headers["X-PFS-Report-Run"])
        self.assertIn("pfs-report-http-xlsx-export.json", exported.headers["Content-Disposition"])
        self.assertEqual(3600, exported.get_json()["result"]["total"])

    def test_multi_sheet_xlsx_requires_valid_worksheet_and_returns_stable_codes(self):
        with TemporaryDirectory() as temp_dir:
            workbook = Path(temp_dir) / "multi.xlsx"
            with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
                pd.DataFrame({"note": ["说明"]}).to_excel(writer, sheet_name="Readme", index=False)
                pd.DataFrame(
                    {
                        "month": ["2026-01"],
                        "region": ["华东"],
                        "sales_amount": [1200],
                    }
                ).to_excel(writer, sheet_name="Sales", index=False)
            with workbook.open("rb") as handle:
                uploaded = self.client.post(
                    f"/api/session/{self.sid}/upload",
                    data={"file": (handle, workbook.name)},
                    content_type="multipart/form-data",
                )
        source_id = uploaded.get_json()["added"][0]["source_id"]

        missing = self.client.post(
            f"/api/session/{self.sid}/pfs/analyze",
            json={"source_id": source_id},
        )
        unknown = self.client.post(
            f"/api/session/{self.sid}/pfs/analyze",
            json={"source_id": source_id, "worksheet": "Missing"},
        )

        self.assertEqual(400, missing.status_code)
        self.assertEqual("worksheet_required", missing.get_json()["code"])
        self.assertEqual(400, unknown.status_code)
        self.assertEqual("worksheet_not_found", unknown.get_json()["code"])

    def test_report_keeps_lightweight_trace_without_governance_endpoint(self):
        uploaded = self._upload()
        source_id = uploaded.get_json()["added"][0]["source_id"]
        response = self.client.post(
            f"/api/session/{self.sid}/pfs/analyze",
            json={"source_id": source_id, "run_id": "light-trace-run"},
        )
        self.assertEqual(200, response.status_code, response.get_data(as_text=True))
        result = response.get_json()["result"]
        self.assertTrue(result["claims"])
        self.assertTrue(result["evidence"])
        self.assertTrue(result["claims"][0]["evidence_ids"])
        self.assertNotIn("governance", result)
        self.assertNotIn("approval_status", result["claims"][0])
        self.assertEqual(404, self.client.get("/api/pfs/ledger").status_code)

if __name__ == "__main__":
    unittest.main()
