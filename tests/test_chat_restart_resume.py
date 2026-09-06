import multiprocessing
import os
import time
import unittest
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from api import create_app
from api.chat import _build_chat_resume_payload
from api.state import session_manager
from agent.agent import BusinessAgent
from agent.durable_handlers import build_handlers, chat_turn_handler
from agent.jobs import JobRunner
from data.chat_state_store import ChatStateStore
from data.durable_queue import QUEUE_SUCCEEDED, DurableQueueStore, DurableQueueWorker
from data.jobs_store import (
    CHAT_RECOVERY_CHECKPOINT_EVENT,
    CHAT_UNSAFE_RECOVERY_ACTION,
    CHAT_UNSAFE_RECOVERY_CODE,
    JobsStore,
    STATUS_FAILED,
    STATUS_SUCCEEDED,
)


def _stop_process(process) -> None:
    if process is None or process.pid is None:
        return
    if process.is_alive():
        process.terminate()
    process.join(timeout=5)


def _close_multiprocessing_queue(queue) -> None:
    if queue is not None:
        queue.close()
        queue.join_thread()


class _RaisingResponseIterator:
    def __iter__(self):
        return self

    def __next__(self):
        raise RuntimeError("stream iterator failed")


class _SeparateProcessChatAgent:
    _artifact_metadata = {}
    _provider = "test"
    model = "durable-chat-process-test"

    def run(self, *_args, **_kwargs):
        yield {
            "type": "text",
            "content": "独立 worker 进程已无损续跑聊天分析",
        }


class _ProcessRestartChatAgent:
    """Small fixture that simulates a worker dying after a visible prefix."""

    _provider = "test"
    model = "durable-chat-process-restart-test"

    def __init__(self, *, crash_after_delta: bool):
        self.crash_after_delta = bool(crash_after_delta)
        self._artifact_metadata = {}

    def run(self, *_args, **kwargs):
        if self.crash_after_delta:
            checkpoint = kwargs["recovery_checkpoint"]
            checkpoint(
                {
                    "phase": "model_call",
                    "replay_safe": True,
                    "run_id": kwargs["run_id"],
                    "partial_content": "",
                }
            )
            yield {"type": "text_delta", "content": "前缀"}
            # This is an actual process exit, leaving both durable leases to
            # expire just as they would after a worker crash or host restart.
            os._exit(0)

        recovery_state = kwargs.get("recovery_state") or {}
        if recovery_state.get("partial_content") != "前缀":
            raise AssertionError("replacement worker did not receive the persisted visible prefix")
        yield {"type": "text", "content": "后缀"}


def _run_durable_chat_worker_in_child(
    jobs_path: str,
    queue_path: str,
    state_path: str,
    session_id: str,
    result_queue,
) -> None:
    os.environ.update(
        {
            "PFS_ENABLE_DURABLE_QUEUE": "1",
            "PFS_DURABLE_QUEUE_ROLE": "worker",
            "PFS_JOBS_DB_PATH": jobs_path,
            "PFS_DURABLE_QUEUE_DB_PATH": queue_path,
            "PFS_CHAT_STATE_DB_PATH": state_path,
        }
    )
    queue = None
    state_store = None
    try:
        from api.state import session_manager as child_session_manager
        from data.durable_queue import DurableQueueStore, DurableQueueWorker

        previous_state_store = getattr(child_session_manager, "_chat_state_store", None)
        if previous_state_store is not None:
            previous_state_store.close()
        state_store = ChatStateStore(Path(state_path))
        child_session_manager._chat_state_store = state_store
        app = create_app()
        app.config.update(TESTING=True)
        queue = DurableQueueStore(Path(queue_path), default_lease_seconds=1)
        worker = DurableQueueWorker(
            queue,
            build_handlers(app),
            worker_id="queue-service-chat-process",
            lease_seconds=1,
        )
        with patch("api.chat._build_agent", return_value=_SeparateProcessChatAgent()):
            completed = worker.run_once()
        result_queue.put({"ok": bool(completed)})
    except BaseException as exc:  # pragma: no cover - surfaced by parent assertion
        result_queue.put({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
    finally:
        if state_store is not None:
            state_store.close()
        if queue is not None:
            queue.close()


def _run_restarting_durable_chat_worker_in_child(
    jobs_path: str,
    queue_path: str,
    state_path: str,
    session_id: str,
    crash_after_delta: bool,
    result_queue,
) -> None:
    """Run the real chat queue handler, optionally simulating a process loss."""
    os.environ.update(
        {
            "PFS_ENABLE_DURABLE_QUEUE": "1",
            "PFS_DURABLE_QUEUE_ROLE": "worker",
            "PFS_JOBS_DB_PATH": jobs_path,
            "PFS_DURABLE_QUEUE_DB_PATH": queue_path,
            "PFS_CHAT_STATE_DB_PATH": state_path,
        }
    )
    queue = None
    state_store = None
    try:
        from api.state import session_manager as child_session_manager
        from data.durable_queue import DurableQueueStore, DurableQueueWorker

        previous_state_store = getattr(child_session_manager, "_chat_state_store", None)
        if previous_state_store is not None:
            previous_state_store.close()
        state_store = ChatStateStore(Path(state_path))
        child_session_manager._chat_state_store = state_store
        app = create_app()
        app.config.update(TESTING=True)
        queue = DurableQueueStore(Path(queue_path), default_lease_seconds=0.2)
        worker = DurableQueueWorker(
            queue,
            build_handlers(app),
            worker_id=(
                "queue-service-chat-restart-crash"
                if crash_after_delta
                else "queue-service-chat-restart-replacement"
            ),
            lease_seconds=0.2,
        )
        with patch(
            "api.chat._build_agent",
            return_value=_ProcessRestartChatAgent(
                crash_after_delta=crash_after_delta,
            ),
        ):
            completed = worker.run_once()
        result_queue.put({"ok": bool(completed)})
    except BaseException as exc:  # pragma: no cover - surfaced by parent assertion
        result_queue.put({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
    finally:
        if state_store is not None:
            state_store.close()
        if queue is not None:
            queue.close()


def _claim_durable_chat_task_then_exit(
    jobs_path: str,
    queue_path: str,
    job_id: str,
    result_queue,
) -> None:
    """Claim a chat task and leave both leases behind like a lost worker."""
    queue = DurableQueueStore(Path(queue_path), default_lease_seconds=0.2)
    store = JobsStore(
        Path(jobs_path),
        owner_id="queue-service-lost-chat:job",
        lease_seconds=0.2,
    )
    try:
        task = queue.claim("queue-service-lost-chat", lease_seconds=0.2)
        job_claimed = bool(task and store.claim_durable_job(job_id))
        result_queue.put(
            {
                "task_claimed": bool(task),
                "job_claimed": job_claimed,
            }
        )
    finally:
        # Closing SQLite connections does not acknowledge the task.  The
        # queue and Job leases are intentionally left to expire so a new
        # worker must perform the real recovery path.
        store.close()
        queue.close()


class ChatRestartResumeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config.update(TESTING=True)

    def setUp(self):
        self.sid = f"chat-resume-{uuid.uuid4().hex[:12]}"
        self.session = session_manager.get_or_create(self.sid)
        self.client = self.app.test_client()
        self.store = None

    def tearDown(self):
        if self.session is not None:
            session_manager.remove(self.sid)
        if self.store is not None:
            self.store.close()

    def test_chat_handler_closes_response_when_iterator_raises(self):
        request_payload = _build_chat_resume_payload(
            self.session,
            {"message": "迭代器异常"},
            "迭代器异常",
            "",
        )
        response = SimpleNamespace(
            response=_RaisingResponseIterator(),
            close=Mock(),
        )
        queue_context = SimpleNamespace(
            job_id="",
            job_store=object(),
            payload={"session_id": self.sid},
        )
        with patch("api.chat.chat_stream", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "stream iterator failed"):
                chat_turn_handler(
                    {"resume_request": request_payload, "session_id": self.sid},
                    queue_context,
                    self.app,
                )
        response.close.assert_called_once_with()

    def test_restart_during_non_idempotent_chat_step_fails_closed(self):
        """A lost worker must not replay an unknown side effect implicitly."""
        with TemporaryDirectory(prefix="pfs-chat-unsafe-recovery-") as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite3"
            first_store = JobsStore(
                db_path,
                owner_id="chat-service-before-unsafe-restart",
                lease_seconds=0.1,
            )
            job = first_store.create(
                self.sid,
                "conversation_analysis",
                label="副作用中断",
            )
            self.assertTrue(first_store.mark_queued(job["id"]))
            self.assertTrue(first_store.mark_started(job["id"]))
            self.assertTrue(
                first_store.set_request_payload(
                    job["id"],
                    {"schema_version": 1, "body": {}, "pre_turn_state": {}},
                    durable_handler="chat_turn",
                    queue_task_id="queue-unsafe-recovery",
                )
            )
            self.assertIsNotNone(
                first_store.append_event(
                    job["id"],
                    {
                        "type": CHAT_RECOVERY_CHECKPOINT_EVENT,
                        "phase": "tool_call",
                        "replay_safe": False,
                        "tool_names": ["workspace_write_file"],
                    },
                    owner_id=first_store.owner_id,
                )
            )
            first_store.close()
            time.sleep(0.15)

            recovered_store = JobsStore(
                db_path,
                owner_id="chat-service-after-unsafe-restart",
                lease_seconds=0.1,
            )
            try:
                self.store = recovered_store
                recovered = recovered_store.get(job["id"])
                self.assertEqual(STATUS_FAILED, recovered["status"])
                self.assertEqual(CHAT_UNSAFE_RECOVERY_CODE, recovered["error_code"])
                self.assertEqual(CHAT_UNSAFE_RECOVERY_ACTION, recovered["recovery_action"])
                events = recovered_store.list_events(self.sid, job_id=job["id"])
                error_event = next(item for item in events if item["type"] == "job_error")
                self.assertFalse(error_event["automatic_replay"])
                self.assertFalse(error_event["resume_available"])
                self.assertEqual("tool_call", error_event["recovery_phase"])
            finally:
                recovered_store.close()
                self.store = None

    def test_agent_resumes_from_a_safe_model_prefix_without_repeating_it(self):
        class FakeCompletions:
            def __init__(self):
                self.calls = []

            def create(self, **kwargs):
                self.calls.append(kwargs)
                return [
                    SimpleNamespace(
                        usage=None,
                        choices=[
                            SimpleNamespace(
                                finish_reason="stop",
                                delta=SimpleNamespace(
                                    content="后半句",
                                    reasoning_content=None,
                                    tool_calls=None,
                                ),
                            )
                        ],
                    )
                ]

        completions = FakeCompletions()
        checkpoints = []
        agent = BusinessAgent(
            client=SimpleNamespace(
                chat=SimpleNamespace(completions=completions),
            ),
            model="safe-recovery-model",
            session_id="safe-recovery-test",
            provider="test",
        )
        with patch.object(
            agent,
            "_mcp_manager",
            SimpleNamespace(get_all_openai_schemas=lambda: []),
        ):
            events = list(
                agent.run(
                    "原始问题",
                    history=[],
                    run_id="safe-recovery-run",
                    recovery_state={
                        "phase": "model_call",
                        "replay_safe": True,
                        "partial_content": "前半句",
                    },
                    recovery_checkpoint=lambda value: checkpoints.append(dict(value)) or True,
                )
            )

        self.assertIn(
            {"role": "assistant", "content": "前半句"},
            completions.calls[0]["messages"],
        )
        self.assertTrue(
            any(
                item.get("phase") == "model_call" and item.get("partial_content") == "前半句"
                for item in checkpoints
            )
        )
        self.assertEqual("后半句", next(event["content"] for event in events if event.get("type") == "text"))

    def test_active_hooks_disable_safe_model_replay(self):
        class FakeCompletions:
            def __init__(self):
                self.calls = []

            def create(self, **kwargs):
                self.calls.append(kwargs)
                return [
                    SimpleNamespace(
                        usage=None,
                        choices=[
                            SimpleNamespace(
                                finish_reason="stop",
                                delta=SimpleNamespace(
                                    content="重新生成",
                                    reasoning_content=None,
                                    tool_calls=None,
                                ),
                            )
                        ],
                    )
                ]

        completions = FakeCompletions()
        checkpoints = []
        agent = BusinessAgent(
            client=SimpleNamespace(
                chat=SimpleNamespace(completions=completions),
            ),
            model="hooks-recovery-model",
            session_id="hooks-recovery-test",
            provider="test",
            hook_engine=object(),
        )
        with patch.object(
            agent,
            "_mcp_manager",
            SimpleNamespace(get_all_openai_schemas=lambda: []),
        ):
            events = list(
                agent.run(
                    "原始问题",
                    history=[],
                    run_id="hooks-recovery-run",
                    recovery_state={
                        "phase": "model_call",
                        "replay_safe": True,
                        "partial_content": "不应重复的前缀",
                    },
                    recovery_checkpoint=lambda value: checkpoints.append(dict(value)) or True,
                )
            )

        self.assertNotIn(
            {"role": "assistant", "content": "不应重复的前缀"},
            completions.calls[0]["messages"],
        )
        self.assertTrue(
            any(
                item.get("phase") == "model_call" and item.get("replay_safe") is False for item in checkpoints
            )
        )
        self.assertEqual(
            "重新生成", next(event["content"] for event in events if event.get("type") == "text")
        )

    def test_agent_replays_a_persisted_safe_tool_call_before_new_model_work(self):
        class FakeCompletions:
            def __init__(self):
                self.calls = []

            def create(self, **kwargs):
                self.calls.append(kwargs)
                return [
                    SimpleNamespace(
                        usage=None,
                        choices=[
                            SimpleNamespace(
                                finish_reason="stop",
                                delta=SimpleNamespace(
                                    content="工具恢复完成",
                                    reasoning_content=None,
                                    tool_calls=None,
                                ),
                            )
                        ],
                    )
                ]

        completions = FakeCompletions()
        checkpoints = []
        agent = BusinessAgent(
            client=SimpleNamespace(
                chat=SimpleNamespace(completions=completions),
            ),
            model="safe-tool-recovery-model",
            session_id="safe-tool-recovery-test",
            provider="test",
        )
        with (
            patch.object(
                agent,
                "_mcp_manager",
                SimpleNamespace(get_all_openai_schemas=lambda: []),
            ),
            patch.object(
                agent,
                "_tool_workspace_status",
                return_value="workspace is ready",
            ),
        ):
            events = list(
                agent.run(
                    "请继续分析",
                    history=[],
                    run_id="safe-tool-recovery-run",
                    auto_match_skill=False,
                    memory_enabled=False,
                    recovery_state={
                        "phase": "tool_call",
                        "replay_safe": True,
                        "pending_tool_count": 1,
                        "tool_calls": [
                            {
                                "id": "call-recovered-1",
                                "name": "workspace_status",
                                "arguments": "{}",
                            }
                        ],
                    },
                    recovery_checkpoint=lambda value: checkpoints.append(dict(value)) or True,
                )
            )

        self.assertEqual(1, len(completions.calls))
        resumed_messages = completions.calls[0]["messages"]
        self.assertTrue(
            any(
                item.get("role") == "assistant"
                and item.get("tool_calls", [{}])[0]["function"]["name"] == "workspace_status"
                for item in resumed_messages
            )
        )
        self.assertTrue(
            any(
                item.get("role") == "tool"
                and item.get("tool_call_id") == "call-recovered-1"
                and "workspace is ready" in item.get("content", "")
                for item in resumed_messages
            )
        )
        self.assertTrue(
            any(
                item.get("phase") == "tool_complete" and item.get("replay_safe") is True
                for item in checkpoints
            )
        )
        self.assertEqual(
            "工具恢复完成", next(event["content"] for event in events if event.get("type") == "text")
        )

    def test_mixed_safe_and_side_effect_tool_checkpoint_never_auto_replays(self):
        class FakeCompletions:
            def __init__(self):
                self.calls = []

            def create(self, **kwargs):
                self.calls.append(kwargs)
                return [
                    SimpleNamespace(
                        usage=None,
                        choices=[
                            SimpleNamespace(
                                finish_reason="stop",
                                delta=SimpleNamespace(
                                    content="重新确认后继续",
                                    reasoning_content=None,
                                    tool_calls=None,
                                ),
                            )
                        ],
                    )
                ]

        completions = FakeCompletions()
        agent = BusinessAgent(
            client=SimpleNamespace(
                chat=SimpleNamespace(completions=completions),
            ),
            model="mixed-tool-recovery-model",
            session_id="mixed-tool-recovery-test",
            provider="test",
        )
        with (
            patch.object(
                agent,
                "_mcp_manager",
                SimpleNamespace(get_all_openai_schemas=lambda: []),
            ),
            patch.object(
                agent,
                "_tool_workspace_status",
                return_value="workspace is ready",
            ),
        ):
            events = list(
                agent.run(
                    "请继续分析",
                    history=[],
                    run_id="mixed-tool-recovery-run",
                    auto_match_skill=False,
                    memory_enabled=False,
                    recovery_state={
                        # A stale or tampered checkpoint must not widen the
                        # allow-list merely because replay_safe was set to true.
                        "phase": "tool_call",
                        "replay_safe": True,
                        "pending_tool_count": 2,
                        "tool_calls": [
                            {
                                "id": "call-safe-1",
                                "name": "workspace_status",
                                "arguments": "{}",
                            },
                            {
                                "id": "call-side-effect-1",
                                "name": "workspace_write_file",
                                "arguments": '{"file_path":"workspace://user/out.txt","content":"x"}',
                            },
                        ],
                    },
                )
            )

        self.assertEqual(1, len(completions.calls))
        self.assertFalse(
            any(
                item.get("role") == "assistant" and item.get("tool_calls")
                for item in completions.calls[0]["messages"]
            )
        )
        self.assertEqual(
            "重新确认后继续", next(event["content"] for event in events if event.get("type") == "text")
        )

    def test_agent_reuses_a_completed_safe_tool_batch_without_rerunning_it(self):
        class FakeCompletions:
            def __init__(self):
                self.calls = []

            def create(self, **kwargs):
                self.calls.append(kwargs)
                return [
                    SimpleNamespace(
                        usage=None,
                        choices=[
                            SimpleNamespace(
                                finish_reason="stop",
                                delta=SimpleNamespace(
                                    content="基于已完成查询继续",
                                    reasoning_content=None,
                                    tool_calls=None,
                                ),
                            )
                        ],
                    )
                ]

        completions = FakeCompletions()
        agent = BusinessAgent(
            client=SimpleNamespace(
                chat=SimpleNamespace(completions=completions),
            ),
            model="completed-tool-recovery-model",
            session_id="completed-tool-recovery-test",
            provider="test",
        )
        with (
            patch.object(
                agent,
                "_mcp_manager",
                SimpleNamespace(get_all_openai_schemas=lambda: []),
            ),
            patch.object(
                agent,
                "_tool_workspace_status",
                side_effect=AssertionError("completed safe tool must not run again"),
            ),
        ):
            events = list(
                agent.run(
                    "请继续分析",
                    history=[],
                    run_id="completed-tool-recovery-run",
                    auto_match_skill=False,
                    memory_enabled=False,
                    recovery_state={
                        "phase": "tool_complete",
                        "replay_safe": True,
                        "pending_tool_count": 0,
                        "tool_calls": [
                            {
                                "id": "call-completed-1",
                                "name": "workspace_status",
                                "arguments": "{}",
                            }
                        ],
                        "tool_results": [
                            {
                                "id": "call-completed-1",
                                "name": "workspace_status",
                                "content": "[TOOL_RESULT] workspace_status OK: saved workspace result",
                            }
                        ],
                    },
                )
            )

        self.assertEqual(1, len(completions.calls))
        messages = completions.calls[0]["messages"]
        self.assertTrue(
            any(
                item.get("role") == "assistant"
                and item.get("tool_calls", [{}])[0]["id"] == "call-completed-1"
                for item in messages
            )
        )
        self.assertTrue(
            any(
                item.get("role") == "tool"
                and item.get("tool_call_id") == "call-completed-1"
                and "saved workspace result" in item.get("content", "")
                for item in messages
            )
        )
        self.assertEqual(
            "基于已完成查询继续", next(event["content"] for event in events if event.get("type") == "text")
        )

    def test_recovery_checkpoints_stay_server_side_and_out_of_chat_replay(self):
        class FakeAgent:
            _artifact_metadata = {}
            _provider = "test"
            model = "checkpoint-contract-test"

            def run(self, *_args, **kwargs):
                checkpoint = kwargs["recovery_checkpoint"]
                checkpoint(
                    {
                        "phase": "model_call",
                        "replay_safe": True,
                        "run_id": kwargs["run_id"],
                        "partial_content": "server-only prefix",
                        "tool_calls": [
                            {
                                "id": "server-only-call",
                                "name": "workspace_status",
                                "arguments": "{}",
                            }
                        ],
                        "tool_results": [
                            {
                                "id": "server-only-call",
                                "name": "workspace_status",
                                "content": "server-only tool result",
                            }
                        ],
                    }
                )
                yield {"type": "text", "content": "可见结果"}

        with patch("api.chat._build_agent", return_value=FakeAgent()):
            response = self.client.post(
                f"/api/session/{self.sid}/chat",
                json={"message": "检查恢复检查点契约"},
            )
            body = response.get_data(as_text=True)

        self.assertEqual(200, response.status_code)
        self.assertIn("可见结果", body)
        self.assertNotIn("chat_recovery_checkpoint", body)
        self.assertNotIn("server-only prefix", body)
        job_id = response.headers["X-PFS-Conversation-Job"]
        records = self.session.job_runner.list_events(job_id=job_id)
        checkpoint = next(item for item in records if item["type"] == CHAT_RECOVERY_CHECKPOINT_EVENT)
        self.assertEqual("model_call", checkpoint["phase"])
        self.assertEqual("server-only-call", checkpoint["tool_calls"][0]["id"])
        self.assertEqual("server-only tool result", checkpoint["tool_results"][0]["content"])
        replay = self.client.get(f"/api/session/{self.sid}/chat/{job_id}/events?after_sequence=0").get_json()[
            "events"
        ]
        self.assertEqual(["text", "done"], [item["type"] for item in replay])

    def test_safe_model_checkpoint_rebuilds_persisted_text_deltas(self):
        with TemporaryDirectory(prefix="pfs-chat-recovery-deltas-") as temp_dir:
            store = JobsStore(
                Path(temp_dir) / "jobs.sqlite3",
                owner_id="chat-recovery-delta-owner",
            )
            try:
                job = store.create(self.sid, "conversation_analysis")
                self.assertTrue(store.mark_queued(job["id"]))
                self.assertTrue(store.mark_started(job["id"]))
                self.assertIsNotNone(
                    store.append_event(
                        job["id"],
                        {
                            "type": CHAT_RECOVERY_CHECKPOINT_EVENT,
                            "phase": "model_call",
                            "replay_safe": True,
                            "partial_content": "前缀",
                        },
                        owner_id=store.owner_id,
                    )
                )
                for content in ("一", "段", "尾巴"):
                    self.assertIsNotNone(
                        store.append_event(
                            job["id"],
                            {
                                "type": "conversation_stream_event",
                                "event": {"type": "text_delta", "content": content},
                            },
                            owner_id=store.owner_id,
                        )
                    )
                checkpoint = store.get_last_recovery_checkpoint(self.sid, job["id"])
                self.assertEqual("前缀一段尾巴", checkpoint["partial_content"])
            finally:
                store.close()

    def test_restart_recovered_chat_can_be_resumed_once_from_saved_request(self):
        with TemporaryDirectory(prefix="pfs-chat-resume-") as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite3"
            first_store = JobsStore(
                db_path,
                owner_id="chat-service-before-restart",
                lease_seconds=0.1,
            )
            job = first_store.create(
                self.sid,
                "conversation_analysis",
                label="恢复销售分析",
            )
            self.assertTrue(first_store.mark_queued(job["id"]))
            self.assertTrue(first_store.mark_started(job["id"]))
            request_payload = _build_chat_resume_payload(
                self.session,
                {"message": "请继续完成销售分析", "memory_enabled": False},
                "请继续完成销售分析",
                "",
            )
            self.assertTrue(first_store.set_request_payload(job["id"], request_payload))
            first_store.close()
            time.sleep(0.15)

            self.store = JobsStore(
                db_path,
                owner_id="chat-service-after-restart",
                lease_seconds=0.1,
            )
            try:
                self.session._job_runner = JobRunner(self.sid, self.store, max_workers=1)
                recovered = self.session.job_runner.get_status(job["id"])
                self.assertEqual(STATUS_FAILED, recovered["status"])
                self.assertEqual("job_interrupted_after_restart", recovered["error_code"])

                listed = self.client.get(f"/api/session/{self.sid}/jobs")
                self.assertEqual(200, listed.status_code)
                listed_job = listed.get_json()["jobs"][0]
                self.assertTrue(listed_job["resume_available"])

                class FakeAgent:
                    _artifact_metadata = {}
                    _provider = "test"
                    model = "chat-resume-test"

                    def run(self, *_args, **_kwargs):
                        yield {"type": "text", "content": "已从原请求继续完成"}

                with patch("api.chat._build_agent", return_value=FakeAgent()):
                    response = self.client.post(
                        f"/api/session/{self.sid}/chat/{job['id']}/resume",
                    )
                    response_body = response.get_data(as_text=True)

                self.assertEqual(200, response.status_code)
                self.assertIn("已从原请求继续完成", response_body)
                self.assertNotIn("job_interrupted_after_restart", response_body)
                final = self.store.get(job["id"])
                self.assertEqual(STATUS_SUCCEEDED, final["status"])
                conversation_jobs = [
                    item
                    for item in self.store.list_by_session(self.sid)
                    if item.get("type") == "conversation_analysis"
                ]
                self.assertEqual(1, len(conversation_jobs))
                event_types = [event["type"] for event in self.store.list_events(self.sid, job_id=job["id"])]
                self.assertIn("job_resume_requested", event_types)
                self.assertIn("conversation_stream_event", event_types)

                second_attempt = self.client.post(
                    f"/api/session/{self.sid}/chat/{job['id']}/resume",
                )
                self.assertEqual(409, second_attempt.status_code)
                self.assertEqual("chat_resume_terminal", second_attempt.get_json()["code"])
            finally:
                self.session.shutdown_job_runner(wait=True)
                self.store.close()
                self.store = None

    def test_durable_chat_handler_completes_from_a_reconstructable_snapshot(self):
        with TemporaryDirectory(prefix="pfs-durable-chat-") as temp_dir:
            jobs_path = Path(temp_dir) / "jobs.sqlite3"
            queue_path = Path(temp_dir) / "queue.sqlite3"
            with patch.dict(
                os.environ,
                {
                    "PFS_ENABLE_DURABLE_QUEUE": "1",
                    "PFS_JOBS_DB_PATH": str(jobs_path),
                    "PFS_DURABLE_QUEUE_DB_PATH": str(queue_path),
                },
                clear=False,
            ):
                store = JobsStore(jobs_path, owner_id="api-service", lease_seconds=1)
                runner = JobRunner(self.sid, store, max_workers=1)
                queue = DurableQueueStore(queue_path, default_lease_seconds=1)
                self.session._job_runner = runner
                self.session.model_provider = "test"
                try:
                    job_id = runner.begin_tracked("conversation_analysis", label="跨服务聊天")
                    request_payload = _build_chat_resume_payload(
                        self.session,
                        {"message": "继续完成这次分析", "memory_enabled": False},
                        "继续完成这次分析",
                        "",
                    )
                    self.assertTrue(
                        runner.persist_request_payload(
                            job_id,
                            request_payload,
                            durable_handler="chat_turn",
                        )
                    )
                    task_id = runner.enqueue_durable(
                        job_id,
                        "chat_turn",
                        {"resume_request": request_payload},
                        operation_key=f"chat:{job_id}",
                    )

                    class FakeAgent:
                        _artifact_metadata = {}
                        _provider = "test"
                        model = "durable-chat-test"

                        def run(self, *_args, **_kwargs):
                            yield {
                                "type": "text",
                                "content": "跨服务 worker 已完成聊天分析",
                            }

                    worker = DurableQueueWorker(
                        queue,
                        build_handlers(self.app),
                        worker_id="queue-service-chat-test",
                        lease_seconds=1,
                    )
                    with patch("api.chat._build_agent", return_value=FakeAgent()):
                        self.assertTrue(worker.run_once())

                    self.assertEqual(STATUS_SUCCEEDED, store.get(job_id)["status"])
                    self.assertEqual(
                        QUEUE_SUCCEEDED,
                        queue.get(task_id)["status"],
                    )
                    self.assertIn(
                        "跨服务 worker 已完成聊天分析",
                        [item.get("content") for item in self.session.history],
                    )
                finally:
                    queue.close()
                    runner.shutdown(wait=True)
                    store.close()

    def test_durable_chat_handler_completes_in_an_independent_worker_process(self):
        if "fork" not in multiprocessing.get_all_start_methods():
            self.skipTest("requires a process start method that can run this local fixture")
        with TemporaryDirectory(prefix="pfs-durable-chat-process-") as temp_dir:
            jobs_path = Path(temp_dir) / "jobs.sqlite3"
            queue_path = Path(temp_dir) / "queue.sqlite3"
            state_path = Path(temp_dir) / "chat-state.sqlite3"
            with patch.dict(
                os.environ,
                {
                    "PFS_ENABLE_DURABLE_QUEUE": "1",
                    "PFS_JOBS_DB_PATH": str(jobs_path),
                    "PFS_DURABLE_QUEUE_DB_PATH": str(queue_path),
                    "PFS_CHAT_STATE_DB_PATH": str(state_path),
                },
                clear=False,
            ):
                store = JobsStore(jobs_path, owner_id="api-service", lease_seconds=1)
                runner = JobRunner(self.sid, store, max_workers=1)
                self.session._job_runner = runner
                self.session.model_provider = "test"
                try:
                    job_id = runner.begin_tracked("conversation_analysis", label="独立进程聊天续跑")
                    request_payload = _build_chat_resume_payload(
                        self.session,
                        {"message": "继续完成这次分析", "memory_enabled": False},
                        "继续完成这次分析",
                        "",
                    )
                    self.assertTrue(runner.persist_request_payload(job_id, request_payload))
                    task_id = runner.enqueue_durable(
                        job_id,
                        "chat_turn",
                        {"resume_request": request_payload},
                        operation_key=f"chat:independent-process:{job_id}",
                    )

                    context = multiprocessing.get_context("fork")
                    result_queue = context.Queue()
                    process = context.Process(
                        target=_run_durable_chat_worker_in_child,
                        args=(
                            str(jobs_path),
                            str(queue_path),
                            str(state_path),
                            self.sid,
                            result_queue,
                        ),
                    )
                    try:
                        process.start()
                        result = result_queue.get(timeout=10)
                        process.join(timeout=10)
                        self.assertEqual(0, process.exitcode)
                        self.assertTrue(result.get("ok"), result)
                    finally:
                        _stop_process(process)
                        _close_multiprocessing_queue(result_queue)

                    reader = JobsStore(jobs_path, owner_id="api-service-reader")
                    queue_reader = DurableQueueStore(queue_path)
                    state_reader = ChatStateStore(state_path)
                    try:
                        self.assertEqual(STATUS_SUCCEEDED, reader.get(job_id)["status"])
                        self.assertEqual(QUEUE_SUCCEEDED, queue_reader.get(task_id)["status"])
                        saved = state_reader.get(self.sid)
                        self.assertIsNotNone(saved)
                        self.assertIn(
                            "独立 worker 进程已无损续跑聊天分析",
                            [item.get("content") for item in saved["state"]["history"]],
                        )
                    finally:
                        state_reader.close()
                        queue_reader.close()
                        reader.close()
                finally:
                    runner.shutdown(wait=True)
                    store.close()

    def test_safe_chat_prefix_survives_a_real_worker_process_restart(self):
        """A persisted safe prefix is consumed exactly once after process loss."""
        if "fork" not in multiprocessing.get_all_start_methods():
            self.skipTest("requires a process start method that can run this local fixture")
        with TemporaryDirectory(prefix="pfs-durable-chat-process-restart-") as temp_dir:
            jobs_path = Path(temp_dir) / "jobs.sqlite3"
            queue_path = Path(temp_dir) / "queue.sqlite3"
            state_path = Path(temp_dir) / "chat-state.sqlite3"
            with patch.dict(
                os.environ,
                {
                    "PFS_ENABLE_DURABLE_QUEUE": "1",
                    "PFS_DURABLE_QUEUE_DB_PATH": str(queue_path),
                    "PFS_CHAT_STATE_DB_PATH": str(state_path),
                },
                clear=False,
            ):
                store = JobsStore(
                    jobs_path,
                    owner_id="api-service-before-chat-process-restart",
                    lease_seconds=0.2,
                )
                runner = JobRunner(self.sid, store, max_workers=1)
                self.session._job_runner = runner
                self.session.model_provider = "test"
                try:
                    job_id = runner.begin_tracked(
                        "conversation_analysis",
                        label="进程重启后的聊天续跑",
                    )
                    request_payload = _build_chat_resume_payload(
                        self.session,
                        {"message": "继续完成这次分析", "memory_enabled": False},
                        "继续完成这次分析",
                        "",
                    )
                    task_id = runner.enqueue_durable(
                        job_id,
                        "chat_turn",
                        {"resume_request": request_payload},
                        operation_key=f"chat:process-restart:{job_id}",
                    )

                    context = multiprocessing.get_context("fork")
                    crash_result_queue = context.Queue()
                    crashed_worker = context.Process(
                        target=_run_restarting_durable_chat_worker_in_child,
                        args=(
                            str(jobs_path),
                            str(queue_path),
                            str(state_path),
                            self.sid,
                            True,
                            crash_result_queue,
                        ),
                    )
                    try:
                        crashed_worker.start()
                        crashed_worker.join(timeout=10)
                        self.assertFalse(
                            crashed_worker.is_alive(),
                            "crashed worker did not exit after the visible prefix",
                        )
                        self.assertEqual(0, crashed_worker.exitcode)
                    finally:
                        _stop_process(crashed_worker)
                        _close_multiprocessing_queue(crash_result_queue)

                    # The first process exited without acknowledging either
                    # lease.  Give the replacement worker a real expired-lease
                    # boundary instead of relying on a same-process shortcut.
                    time.sleep(0.5)
                    replacement_result_queue = context.Queue()
                    replacement = context.Process(
                        target=_run_restarting_durable_chat_worker_in_child,
                        args=(
                            str(jobs_path),
                            str(queue_path),
                            str(state_path),
                            self.sid,
                            False,
                            replacement_result_queue,
                        ),
                    )
                    try:
                        replacement.start()
                        result = replacement_result_queue.get(timeout=10)
                        replacement.join(timeout=10)
                        self.assertEqual(0, replacement.exitcode)
                        self.assertTrue(result.get("ok"), result)
                    finally:
                        _stop_process(replacement)
                        _close_multiprocessing_queue(replacement_result_queue)

                    reader = JobsStore(jobs_path, owner_id="api-service-reader")
                    queue_reader = DurableQueueStore(queue_path)
                    state_reader = ChatStateStore(state_path)
                    try:
                        self.assertEqual(STATUS_SUCCEEDED, reader.get(job_id)["status"])
                        self.assertEqual(
                            QUEUE_SUCCEEDED,
                            queue_reader.get(task_id)["status"],
                        )
                        events = reader.list_events(self.sid, job_id=job_id)
                        delta_events = [
                            item["event"]["content"]
                            for item in events
                            if item["type"] == "conversation_stream_event"
                            and item["event"].get("type") == "text_delta"
                        ]
                        text_events = [
                            item["event"]["content"]
                            for item in events
                            if item["type"] == "conversation_stream_event"
                            and item["event"].get("type") == "text"
                        ]
                        self.assertEqual(["前缀"], delta_events)
                        self.assertEqual(["后缀"], text_events)
                        saved = state_reader.get(self.sid)
                        self.assertIsNotNone(saved)
                        self.assertIn(
                            "前缀后缀",
                            [item.get("content") for item in saved["state"]["history"]],
                        )
                    finally:
                        state_reader.close()
                        queue_reader.close()
                        reader.close()
                finally:
                    runner.shutdown(wait=True)
                    store.close()

    def test_chat_task_is_reclaimed_after_the_worker_process_disappears(self):
        """A claimed chat turn must survive worker loss before handler entry."""
        if "fork" not in multiprocessing.get_all_start_methods():
            self.skipTest("requires a process start method that can run this local fixture")
        with TemporaryDirectory(prefix="pfs-durable-chat-worker-loss-") as temp_dir:
            jobs_path = Path(temp_dir) / "jobs.sqlite3"
            queue_path = Path(temp_dir) / "queue.sqlite3"
            state_path = Path(temp_dir) / "chat-state.sqlite3"
            with patch.dict(
                os.environ,
                {
                    "PFS_ENABLE_DURABLE_QUEUE": "1",
                    "PFS_DURABLE_QUEUE_DB_PATH": str(queue_path),
                    "PFS_CHAT_STATE_DB_PATH": str(state_path),
                },
                clear=False,
            ):
                store = JobsStore(
                    jobs_path,
                    owner_id="api-service-before-worker-loss",
                    lease_seconds=0.2,
                )
                runner = JobRunner(self.sid, store, max_workers=1)
                queue = DurableQueueStore(queue_path, default_lease_seconds=0.2)
                self.session._job_runner = runner
                self.session.model_provider = "test"
                try:
                    job_id = runner.begin_tracked(
                        "conversation_analysis",
                        label="worker 丢失后的聊天续跑",
                    )
                    request_payload = _build_chat_resume_payload(
                        self.session,
                        {"message": "继续完成这次分析", "memory_enabled": False},
                        "继续完成这次分析",
                        "",
                    )
                    task_id = runner.enqueue_durable(
                        job_id,
                        "chat_turn",
                        {"resume_request": request_payload},
                        operation_key=f"chat:worker-loss:{job_id}",
                    )

                    context = multiprocessing.get_context("fork")
                    lost_result_queue = context.Queue()
                    lost_worker = context.Process(
                        target=_claim_durable_chat_task_then_exit,
                        args=(
                            str(jobs_path),
                            str(queue_path),
                            job_id,
                            lost_result_queue,
                        ),
                    )
                    try:
                        lost_worker.start()
                        lost_result = lost_result_queue.get(timeout=10)
                        lost_worker.join(timeout=10)
                        self.assertEqual(0, lost_worker.exitcode)
                        self.assertEqual(
                            {"task_claimed": True, "job_claimed": True},
                            lost_result,
                        )
                    finally:
                        _stop_process(lost_worker)
                        _close_multiprocessing_queue(lost_result_queue)

                    # Both queue and Job leases are short.  The replacement
                    # worker must reclaim the task instead of requiring a new
                    # user message or silently marking it as lost.
                    time.sleep(0.45)
                    replacement_result_queue = context.Queue()
                    replacement = context.Process(
                        target=_run_durable_chat_worker_in_child,
                        args=(
                            str(jobs_path),
                            str(queue_path),
                            str(state_path),
                            self.sid,
                            replacement_result_queue,
                        ),
                    )
                    try:
                        replacement.start()
                        result = replacement_result_queue.get(timeout=10)
                        replacement.join(timeout=10)
                        self.assertEqual(0, replacement.exitcode)
                        self.assertTrue(result.get("ok"), result)
                    finally:
                        _stop_process(replacement)
                        _close_multiprocessing_queue(replacement_result_queue)

                    reader = JobsStore(jobs_path, owner_id="api-service-reader")
                    queue_reader = DurableQueueStore(queue_path)
                    state_reader = ChatStateStore(state_path)
                    try:
                        self.assertEqual(STATUS_SUCCEEDED, reader.get(job_id)["status"])
                        self.assertEqual(
                            QUEUE_SUCCEEDED,
                            queue_reader.get(task_id)["status"],
                        )
                        saved = state_reader.get(self.sid)
                        self.assertIsNotNone(saved)
                        self.assertIn(
                            "独立 worker 进程已无损续跑聊天分析",
                            [item.get("content") for item in saved["state"]["history"]],
                        )
                    finally:
                        state_reader.close()
                        queue_reader.close()
                        reader.close()
                finally:
                    queue.close()
                    runner.shutdown(wait=True)
                    store.close()


if __name__ == "__main__":
    unittest.main()
