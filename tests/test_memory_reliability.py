import os
import time
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent.errors import AgentRunTimeout
from agent.jobs import JobCanceled
from agent.memory import _call_memory_provider


class MemoryProviderReliabilityTests(unittest.TestCase):
    def test_durable_worker_memory_tracking_survives_ephemeral_runner_close(self):
        """Background extraction must not retain the queue handler's closed DB."""
        from agent.jobs import JobRunner
        from agent.memory import _extract
        from data.jobs_store import JobsStore

        session_id = "memory-cross-process"
        config = SimpleNamespace(
            model="memory-fixture",
            context_window=8_000,
            max_output_tokens=2_000,
        )
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"ops":[]}'))],
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **_kwargs: response),
            ),
        )
        manager = SimpleNamespace(
            get_default_provider=lambda: "kimi",
            get_config=lambda _provider: config,
        )

        with TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "jobs.db"
            ephemeral_store = JobsStore(store_path)
            ephemeral_runner = JobRunner(session_id, ephemeral_store)
            # Match the durable queue handler: its store is closed as soon as
            # the synchronous chat handler drains, while extraction continues.
            ephemeral_store.close()

            with (
                patch.dict(os.environ, {"PFS_DURABLE_QUEUE_ROLE": "worker"}, clear=False),
                patch("LLM.llm_config_manager.get_config_manager", return_value=manager),
                patch("LLM.llm_config_manager.get_llm_client", return_value=client),
                patch("agent.memory.memory_store.list_records", return_value=[]),
                patch("agent.memory.memory_store.record_extraction_activity"),
            ):
                _extract(
                    provider="kimi",
                    session_id=session_id,
                    user_id="local-default",
                    workspace_id="",
                    user_message="请记住这个偏好",
                    assistant_message="收到。",
                    runner=ephemeral_runner,
                )

            ephemeral_runner.shutdown(wait=True)
            reader_store = JobsStore(store_path)
            jobs = reader_store.list_by_session(session_id, top_level_only=True)
            reader_store.close()

        self.assertEqual(1, len(jobs))
        self.assertEqual("memory_extraction", jobs[0]["type"])
        self.assertEqual("succeeded", jobs[0]["status"])

    def test_memory_provider_checks_cancellation_before_request(self):
        class FakeCompletions:
            def create(self, **_kwargs):
                raise AssertionError("canceled memory task must not call provider")

        client = SimpleNamespace(
            chat=SimpleNamespace(completions=FakeCompletions()),
        )

        def abort_check():
            raise JobCanceled("memory-stop")

        with self.assertRaises(JobCanceled):
            _call_memory_provider(
                client,
                deadline_ts=time.monotonic() + 1,
                abort_check=abort_check,
                model="memory-test",
                messages=[],
            )

    def test_memory_provider_passes_remaining_timeout_to_request(self):
        captured = {}

        class FakeCompletions:
            def create(self, **kwargs):
                captured["timeout"] = kwargs["timeout"]
                return "ok"

        client = SimpleNamespace(
            chat=SimpleNamespace(completions=FakeCompletions()),
        )
        deadline = time.monotonic() + 0.2

        result = _call_memory_provider(
            client,
            deadline_ts=deadline,
            model="memory-test",
            messages=[],
        )

        self.assertEqual("ok", result)
        self.assertGreater(captured["timeout"], 0)
        self.assertLessEqual(captured["timeout"], 0.2)

    def test_memory_provider_does_not_retry_after_shared_deadline(self):
        calls = []

        class FakeCompletions:
            def create(self, **kwargs):
                calls.append(kwargs["timeout"])
                raise RuntimeError("upstream returned 503")

        client = SimpleNamespace(
            chat=SimpleNamespace(completions=FakeCompletions()),
        )
        deadline = time.monotonic() + 0.2

        with patch("agent.retry.time.sleep"):
            with self.assertRaises(AgentRunTimeout):
                _call_memory_provider(
                    client,
                    deadline_ts=deadline,
                    model="memory-test",
                    messages=[],
                )

        self.assertEqual(1, len(calls))


if __name__ == "__main__":
    unittest.main()
