import multiprocessing
import tempfile
import threading
import time
import unittest
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

from agent.workflows.scheduler import WorkflowConcurrencyLimiter, WorkflowScheduler
from agent.jobs import JobCanceled, JobRunner
from data.jobs_store import (
    JobsStore,
    RESTART_RECOVERY_ACTION,
    RESTART_RECOVERY_CODE,
    STATUS_CANCELED,
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
)
from data.workflow_run_store import WorkflowRunStore
from data.workflow_store import WorkflowStore
from agent.workflows.models import NodeRunStatus, RunStatus


def _read_job_from_child_process(db_path: str, job_id: str, result_queue) -> None:
    store = JobsStore(Path(db_path), lease_seconds=0.1)
    try:
        job = store.get(job_id) or {}
        result_queue.put((job.get("status"), job.get("owner_id")))
    finally:
        store.close()


class PfsDurableRecoveryTests(unittest.TestCase):
    def test_cancel_before_detached_submission_runs_prestart_cleanup_once(self):
        with tempfile.TemporaryDirectory(prefix="pfs-job-prestart-cancel-") as temp_dir:
            store = JobsStore(Path(temp_dir) / "jobs.db")
            runner = JobRunner("prestart-cancel-session", store, max_workers=1)
            cleanup_calls = []
            started = threading.Event()

            def worker(_ctx):
                started.set()
                return {"should_not": "run"}

            try:
                job_id = runner.begin_tracked("conversation_analysis", "prestart")
                self.assertTrue(runner.cancel(job_id))
                runner.submit_tracked(
                    job_id,
                    worker,
                    on_cancel_before_start=lambda: cleanup_calls.append("cleanup"),
                )
                runner.shutdown(wait=True)
                self.assertEqual(["cleanup"], cleanup_calls)
                self.assertFalse(started.is_set())
                self.assertEqual(STATUS_CANCELED, store.get(job_id)["status"])
                self.assertNotIn(job_id, runner._prestart_cleanups)
            finally:
                runner.shutdown(wait=True)
                store.close()

    def test_job_runner_does_not_overwrite_explicit_cancel_with_late_worker_error(self):
        with tempfile.TemporaryDirectory(prefix="pfs-job-cancel-race-") as temp_dir:
            store = JobsStore(Path(temp_dir) / "jobs.db")
            runner = JobRunner("cancel-race-session", store, max_workers=1)
            started = threading.Event()
            release = threading.Event()

            def worker(ctx):
                started.set()
                release.wait(timeout=2)
                if ctx.is_canceled():
                    raise JobCanceled(ctx.job_id)
                raise RuntimeError("late worker failure")

            try:
                job_id = runner.create(worker, job_type="analysis")
                self.assertTrue(started.wait(timeout=2))
                runner.cancel_tracked(job_id)
                self.assertEqual(STATUS_CANCELED, runner.get_status(job_id)["status"])
                release.set()
                runner.shutdown(wait=True)
                self.assertEqual(STATUS_CANCELED, store.get(job_id)["status"])
                self.assertNotIn(job_id, runner._canceled)
            finally:
                release.set()
                runner.shutdown(wait=True)
                store.close()

    def test_job_runner_timeout_is_durable_and_survives_worker_unwind(self):
        with tempfile.TemporaryDirectory(prefix="pfs-job-timeout-") as temp_dir:
            store = JobsStore(Path(temp_dir) / "jobs.db")
            runner = JobRunner("timeout-session", store, max_workers=1)
            started = threading.Event()
            release = threading.Event()

            def worker(ctx):
                started.set()
                release.wait(timeout=2)
                ctx.check_canceled()
                return {"should_not": "complete"}

            try:
                job_id = runner.create(worker, job_type="analysis")
                self.assertTrue(started.wait(timeout=2))
                self.assertTrue(
                    runner.timeout_tracked(
                        job_id,
                        "后台任务超过 1 秒，已请求取消。",
                        error_code="job_timeout",
                        recovery_action="retry_with_smaller_scope",
                    )
                )
                failed = runner.get_status(job_id)
                self.assertEqual(STATUS_FAILED, failed["status"])
                self.assertEqual("job_timeout", failed["error_code"])
                self.assertEqual("retry_with_smaller_scope", failed["recovery_action"])
                release.set()
                runner.shutdown(wait=True)
                persisted = store.get(job_id)
                self.assertEqual(STATUS_FAILED, persisted["status"])
                self.assertEqual("job_timeout", persisted["error_code"])
                self.assertNotIn(job_id, runner._canceled)
            finally:
                release.set()
                runner.shutdown(wait=True)
                store.close()

    def test_workflow_cost_budget_only_uses_measured_usage_and_blocks_at_limit(self):
        class FakeRunStore:
            workspace_id = "pfs-cost-workspace"

            def __init__(self, nodes):
                self.nodes = nodes
                self.transitions = []
                self.run_transition = None

            def list_node_runs(self, _run_id):
                return self.nodes

            def transition_node(self, node_id, status, error=""):
                self.transitions.append((node_id, status, error))

            def transition_run(self, run_id, status, failure_code="", failure_message=""):
                self.run_transition = (run_id, status, failure_code, failure_message)

        scheduler = WorkflowScheduler.__new__(WorkflowScheduler)
        pending = {
            "id": "pending",
            "status": NodeRunStatus.READY.value,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": None,
        }
        measured = {
            "id": "measured",
            "status": NodeRunStatus.SUCCEEDED.value,
            "input_tokens": 80,
            "output_tokens": 20,
            "cost_usd": 0.001,
        }
        store = FakeRunStore([pending, measured])
        scheduler.run_store = store
        self.assertTrue(scheduler._expire_cost_budget("run-1", {"limits": {"max_total_cost_usd": 0.001}}))
        self.assertEqual(
            [
                ("pending", NodeRunStatus.CANCELED, "workflow cost budget exceeded"),
            ],
            store.transitions,
        )
        self.assertEqual(RunStatus.FAILED, store.run_transition[1])
        self.assertEqual("workflow_cost_budget_exceeded", store.run_transition[2])

    def test_workflow_cost_budget_does_not_fake_zero_for_unknown_measured_cost(self):
        class FakeRunStore:
            def __init__(self):
                self.transitions = []

            def list_node_runs(self, _run_id):
                return [
                    {
                        "id": "measured",
                        "status": NodeRunStatus.SUCCEEDED.value,
                        "input_tokens": 80,
                        "output_tokens": 20,
                        "cost_usd": None,
                    }
                ]

            def transition_node(self, *args, **kwargs):
                self.transitions.append((args, kwargs))

            def transition_run(self, *args, **kwargs):
                self.transitions.append((args, kwargs))

        scheduler = WorkflowScheduler.__new__(WorkflowScheduler)
        scheduler.run_store = FakeRunStore()
        self.assertFalse(
            scheduler._expire_cost_budget("run-unknown", {"limits": {"max_total_cost_usd": 0.001}})
        )
        self.assertEqual([], scheduler.run_store.transitions)

    def test_reopen_closes_interrupted_jobs_and_records_recovery_events(self):
        with tempfile.TemporaryDirectory(prefix="pfs-jobs-recovery-") as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            first_store = JobsStore(db_path, lease_seconds=0.2)
            reopened_store = None
            try:
                failed_job = first_store.create("recovery-session", "analysis")
                canceled_job = first_store.create("recovery-session", "export")
                self.assertTrue(first_store.mark_queued(failed_job["id"]))
                self.assertTrue(first_store.mark_started(failed_job["id"]))
                self.assertTrue(first_store.mark_queued(canceled_job["id"]))
                self.assertTrue(first_store.mark_started(canceled_job["id"]))
                self.assertTrue(first_store.mark_canceling(canceled_job["id"]))
                first_store.close()
                first_store = None
                time.sleep(0.25)

                reopened_store = JobsStore(db_path)
                recovered_failed = reopened_store.get(failed_job["id"])
                recovered_canceled = reopened_store.get(canceled_job["id"])

                self.assertEqual(STATUS_FAILED, recovered_failed["status"])
                self.assertIn("restarted", recovered_failed["error"])
                self.assertEqual(RESTART_RECOVERY_CODE, recovered_failed["error_code"])
                self.assertEqual(RESTART_RECOVERY_ACTION, recovered_failed["recovery_action"])
                from api.jobs import _job_to_dict

                api_job = _job_to_dict(recovered_failed)
                self.assertEqual(RESTART_RECOVERY_CODE, api_job["error_code"])
                self.assertEqual(RESTART_RECOVERY_ACTION, api_job["recovery_action"])
                self.assertEqual(STATUS_CANCELED, recovered_canceled["status"])
                self.assertIsNotNone(recovered_failed["finished_at"])
                self.assertIsNotNone(recovered_canceled["finished_at"])

                events = reopened_store.list_events("recovery-session")
                recovery_events = {
                    event["job_id"]: event
                    for event in events
                    if event["job_id"] in {failed_job["id"], canceled_job["id"]}
                    and event["type"] in {"job_error", "job_canceled"}
                }
                self.assertEqual("job_error", recovery_events[failed_job["id"]]["type"])
                self.assertEqual("job_canceled", recovery_events[canceled_job["id"]]["type"])
                self.assertEqual(
                    RESTART_RECOVERY_CODE,
                    recovery_events[failed_job["id"]]["error_code"],
                )
                self.assertEqual(
                    RESTART_RECOVERY_ACTION,
                    recovery_events[failed_job["id"]]["recovery_action"],
                )
                self.assertEqual(
                    STATUS_RUNNING,
                    next(
                        event["status"]
                        for event in events
                        if event["job_id"] == failed_job["id"] and event["type"] == "job_started"
                    ),
                )
            finally:
                if reopened_store is not None:
                    reopened_store.close()
                if first_store is not None:
                    first_store.close()

    def test_reopen_does_not_recover_a_live_job_lease(self):
        with tempfile.TemporaryDirectory(prefix="pfs-jobs-live-lease-") as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            first_store = JobsStore(db_path, lease_seconds=1)
            second_store = None
            try:
                job = first_store.create("live-lease-session", "analysis")
                self.assertTrue(first_store.mark_queued(job["id"]))
                self.assertTrue(first_store.mark_started(job["id"]))
                second_store = JobsStore(db_path, lease_seconds=1)
                current = second_store.get(job["id"])
                self.assertEqual(STATUS_RUNNING, current["status"])
                self.assertEqual(first_store.owner_id, current["owner_id"])
            finally:
                if second_store is not None:
                    second_store.close()
                first_store.close()

    def test_expired_job_lease_is_recovered_by_a_new_store(self):
        with tempfile.TemporaryDirectory(prefix="pfs-jobs-expired-lease-") as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            first_store = JobsStore(db_path, lease_seconds=0.1)
            second_store = None
            try:
                job = first_store.create("expired-lease-session", "analysis")
                self.assertTrue(first_store.mark_queued(job["id"]))
                self.assertTrue(first_store.mark_started(job["id"]))
                time.sleep(0.15)
                second_store = JobsStore(db_path, lease_seconds=0.1)
                recovered = second_store.get(job["id"])
                self.assertEqual(STATUS_FAILED, recovered["status"])
                self.assertEqual(RESTART_RECOVERY_CODE, recovered["error_code"])
                self.assertEqual("", recovered["owner_id"])
                self.assertIsNone(recovered["lease_until"])
            finally:
                if second_store is not None:
                    second_store.close()
                first_store.close()

    def test_open_store_rechecks_expired_jobs_without_a_third_process(self):
        with tempfile.TemporaryDirectory(prefix="pfs-jobs-recheck-lease-") as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            first_store = JobsStore(db_path, lease_seconds=0.1)
            second_store = None
            try:
                job = first_store.create("recheck-lease-session", "analysis")
                self.assertTrue(first_store.mark_queued(job["id"]))
                self.assertTrue(first_store.mark_started(job["id"]))
                second_store = JobsStore(db_path, lease_seconds=0.1)
                self.assertEqual(STATUS_RUNNING, second_store.get(job["id"])["status"])
                time.sleep(0.2)
                self.assertEqual(1, second_store.recover_expired_jobs())
                recovered = second_store.get(job["id"])
                self.assertEqual(STATUS_FAILED, recovered["status"])
                self.assertFalse(first_store.mark_succeeded(job["id"], {"late": True}))
                self.assertEqual(STATUS_FAILED, first_store.get(job["id"])["status"])
                before_events = len(first_store.list_events("recheck-lease-session", job_id=job["id"]))
                self.assertIsNone(
                    first_store.append_event(
                        job["id"],
                        {"type": "late_artifact", "artifact": {"name": "stale"}},
                        owner_id=first_store.owner_id,
                    )
                )
                self.assertEqual(
                    before_events,
                    len(first_store.list_events("recheck-lease-session", job_id=job["id"])),
                )
            finally:
                if second_store is not None:
                    second_store.close()
                first_store.close()

    def test_job_runner_heartbeat_keeps_a_long_job_live(self):
        with tempfile.TemporaryDirectory(prefix="pfs-job-heartbeat-") as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            # Keep enough scheduling margin for hosted CI while still waiting
            # past the lease to prove the heartbeat is renewing it.
            store = JobsStore(db_path, lease_seconds=0.5)
            runner = JobRunner("heartbeat-session", store, max_workers=1)
            started = threading.Event()
            release = threading.Event()

            def worker(_ctx):
                started.set()
                release.wait(timeout=2)
                return {"ok": True}

            try:
                job_id = runner.create(worker, job_type="analysis")
                self.assertTrue(started.wait(timeout=2))
                time.sleep(0.75)
                reopened_store = JobsStore(db_path, lease_seconds=0.5)
                try:
                    current = reopened_store.get(job_id)
                    self.assertEqual(STATUS_RUNNING, current["status"])
                    self.assertEqual(store.owner_id, current["owner_id"])
                finally:
                    reopened_store.close()
                release.set()
                runner.shutdown(wait=True)
                self.assertEqual(STATUS_SUCCEEDED, store.get(job_id)["status"])
            finally:
                release.set()
                runner.shutdown(wait=True)
                store.close()

    def test_job_runner_heartbeat_keeps_a_queued_job_live(self):
        with tempfile.TemporaryDirectory(prefix="pfs-job-queued-heartbeat-") as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            store = JobsStore(db_path, lease_seconds=0.5)
            runner = JobRunner("queued-heartbeat-session", store, max_workers=1)
            first_started = threading.Event()
            release_first = threading.Event()
            second_started = threading.Event()

            def first_worker(_ctx):
                first_started.set()
                release_first.wait(timeout=2)
                return {"job": "first"}

            def second_worker(_ctx):
                second_started.set()
                return {"job": "second"}

            try:
                first_id = runner.create(first_worker, job_type="analysis")
                self.assertTrue(first_started.wait(timeout=2))
                second_id = runner.create(second_worker, job_type="analysis")
                time.sleep(0.75)

                reopened_store = JobsStore(db_path, lease_seconds=0.5)
                try:
                    queued = reopened_store.get(second_id)
                    self.assertEqual("queued", queued["status"])
                    self.assertEqual(store.owner_id, queued["owner_id"])
                finally:
                    reopened_store.close()

                release_first.set()
                self.assertTrue(second_started.wait(timeout=2))
                runner.shutdown(wait=True)
                self.assertEqual(STATUS_SUCCEEDED, store.get(first_id)["status"])
                self.assertEqual(STATUS_SUCCEEDED, store.get(second_id)["status"])
            finally:
                release_first.set()
                runner.shutdown(wait=True)
                store.close()

    def test_live_job_lease_survives_a_real_child_process_read(self):
        with tempfile.TemporaryDirectory(prefix="pfs-job-child-process-") as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            store = JobsStore(db_path, lease_seconds=0.5)
            runner = JobRunner("child-process-session", store, max_workers=1)
            started = threading.Event()
            release = threading.Event()
            process = None
            result_queue = None

            def worker(_ctx):
                started.set()
                release.wait(timeout=2)
                return {"ok": True}

            try:
                job_id = runner.create(worker, job_type="analysis")
                self.assertTrue(started.wait(timeout=2))
                context = multiprocessing.get_context("spawn")
                result_queue = context.Queue()
                process = context.Process(
                    target=_read_job_from_child_process,
                    args=(str(db_path), job_id, result_queue),
                )
                process.start()
                status, owner_id = result_queue.get(timeout=5)
                process.join(timeout=5)
                self.assertEqual(0, process.exitcode)
                self.assertEqual(STATUS_RUNNING, status)
                self.assertEqual(store.owner_id, owner_id)
                release.set()
                runner.shutdown(wait=True)
                self.assertEqual(STATUS_SUCCEEDED, store.get(job_id)["status"])
            finally:
                release.set()
                if process is not None:
                    if process.is_alive():
                        process.terminate()
                    process.join(timeout=5)
                if result_queue is not None:
                    result_queue.close()
                    result_queue.join_thread()
                runner.shutdown(wait=True)
                store.close()

    def test_job_operation_key_is_idempotent_and_identity_scoped(self):
        with tempfile.TemporaryDirectory(prefix="pfs-job-operation-key-") as temp_dir:
            store = JobsStore(Path(temp_dir) / "jobs.db")
            try:
                first = store.create(
                    "operation-key-session",
                    "workflow_node",
                    label="step",
                    operation_key="dispatch:run:step:1:1",
                )
                second = store.create(
                    "operation-key-session",
                    "workflow_node",
                    label="step-again",
                    operation_key="dispatch:run:step:1:1",
                )
                self.assertEqual(first["id"], second["id"])
                self.assertTrue(first["_created"])
                self.assertFalse(second["_created"])
                self.assertEqual(
                    1,
                    len(store.list_events("operation-key-session")),
                )
                with self.assertRaises(ValueError):
                    store.create(
                        "other-session",
                        "workflow_node",
                        operation_key="dispatch:run:step:1:1",
                    )
            finally:
                store.close()

    def test_jobs_store_migrates_legacy_schema_before_operation_key_index(self):
        with tempfile.TemporaryDirectory(prefix="pfs-job-legacy-schema-") as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            import sqlite3

            connection = sqlite3.connect(db_path)
            try:
                connection.execute(
                    "CREATE TABLE jobs ("
                    "id TEXT PRIMARY KEY, session_id TEXT NOT NULL, "
                    "workspace_id TEXT DEFAULT '', type TEXT NOT NULL, "
                    "label TEXT DEFAULT '', parent_id TEXT DEFAULT '', "
                    "status TEXT NOT NULL, progress INTEGER DEFAULT 0, "
                    "message TEXT DEFAULT '', result TEXT, error TEXT, "
                    "created_at TEXT NOT NULL, updated_at TEXT, "
                    "started_at TEXT, finished_at TEXT)"
                )
                connection.commit()
            finally:
                connection.close()
            store = JobsStore(db_path)
            try:
                columns = {row["name"] for row in store._conn.execute("PRAGMA table_info(jobs)")}
                self.assertIn("operation_key", columns)
                created = store.create(
                    "legacy-session",
                    "workflow_node",
                    operation_key="dispatch:legacy:step:1:1",
                )
                self.assertTrue(created["_created"])
            finally:
                store.close()

    def test_job_runner_operation_key_does_not_submit_twice(self):
        with tempfile.TemporaryDirectory(prefix="pfs-job-operation-runner-") as temp_dir:
            store = JobsStore(Path(temp_dir) / "jobs.db")
            runner = JobRunner("operation-runner-session", store, max_workers=1)
            started = threading.Event()
            release = threading.Event()
            calls = []

            def worker(_ctx):
                calls.append("started")
                started.set()
                release.wait(timeout=2)
                return {"ok": True}

            try:
                first = runner.create_with_operation_key(
                    worker,
                    "workflow_node",
                    label="step",
                    operation_key="dispatch:run:step:1:1",
                )
                self.assertTrue(started.wait(timeout=2))
                second = runner.create_with_operation_key(
                    worker,
                    "workflow_node",
                    label="step",
                    operation_key="dispatch:run:step:1:1",
                )
                self.assertEqual(first, second)
                release.set()
                runner.shutdown(wait=True)
                self.assertEqual(["started"], calls)
                self.assertEqual(STATUS_SUCCEEDED, store.get(first)["status"])
            finally:
                release.set()
                runner.shutdown(wait=True)
                store.close()

    def test_scheduler_reattaches_an_unbound_dispatch_job(self):
        class FakeRunStore:
            workspace_id = "operation-key-workspace"

            def __init__(self):
                self.node = {
                    "id": "node-run-1",
                    "node_id": "step",
                    "status": NodeRunStatus.QUEUED.value,
                    "job_id": "",
                    "operation_key": "dispatch:run:step:1:1",
                    "attempt": 1,
                }

            def list_node_runs(self, _run_id):
                return [self.node]

            def bind_job(self, node_run_id, job_id):
                if node_run_id != self.node["id"] or self.node["job_id"]:
                    return False
                self.node["job_id"] = job_id
                return True

        with tempfile.TemporaryDirectory(prefix="pfs-workflow-reattach-") as temp_dir:
            jobs_store = JobsStore(Path(temp_dir) / "jobs.db")
            runner = JobRunner("operation-key-session", jobs_store, max_workers=1)
            run_store = FakeRunStore()
            scheduler = WorkflowScheduler(
                workflow_store=None,
                run_store=run_store,
                job_runner=runner,
                executor=lambda *_args: None,
            )
            try:
                orphan = jobs_store.create(
                    "operation-key-session",
                    "workflow_node",
                    label="step",
                    operation_key=run_store.node["operation_key"],
                )
                scheduler._reconcile_jobs("run", {"nodes": []})
                self.assertEqual(orphan["id"], run_store.node["job_id"])
            finally:
                runner.shutdown(wait=True)
                jobs_store.close()

    def test_chat_restart_recovery_is_explicit_and_never_auto_replayed(self):
        from api.chat import _chat_events_from_records
        from data.jobs_store import RESTART_RECOVERY_ERROR

        events = _chat_events_from_records(
            [
                {
                    "type": "job_error",
                    "job_id": "chat-restarted",
                    "sequence": 9,
                    "error": RESTART_RECOVERY_ERROR,
                    "error_code": RESTART_RECOVERY_CODE,
                    "recovery_action": RESTART_RECOVERY_ACTION,
                }
            ]
        )

        self.assertEqual("error", events[0]["type"])
        self.assertEqual(RESTART_RECOVERY_CODE, events[0]["code"])
        self.assertEqual(RESTART_RECOVERY_ACTION, events[0]["recovery_action"])
        self.assertFalse(events[0]["automatic_replay"])
        self.assertNotIn(RESTART_RECOVERY_ERROR, events[0]["message"])
        self.assertEqual("done", events[1]["type"])

    def test_failed_workflow_node_creates_retry_attempt_and_finishes(self):
        class ImmediateJobs:
            def __init__(self):
                self.jobs = {}
                self.count = 0

            def create(self, fn, job_type, label=""):
                self.count += 1
                job_id = f"workflow-job-{self.count}"
                try:
                    result = fn(object())
                except Exception as exc:
                    self.jobs[job_id] = {
                        "id": job_id,
                        "status": "failed",
                        "error": str(exc),
                    }
                else:
                    self.jobs[job_id] = {
                        "id": job_id,
                        "status": "succeeded",
                        "result": result,
                    }
                return job_id

            def get_status(self, job_id):
                return self.jobs.get(job_id)

            def add_terminal_listener(self, job_id, callback):
                return None

            def cancel(self, job_id):
                if job_id in self.jobs:
                    self.jobs[job_id]["status"] = "canceled"
                return True

        with tempfile.TemporaryDirectory(prefix="pfs-workflow-retry-") as temp_dir:
            db_path = Path(temp_dir) / "workflow.sqlite3"
            workspace_id = "pfs-workflow-test-workspace"
            session_id = "pfs-workflow-test-session"
            workflow_store = WorkflowStore(db_path, workspace_id)
            run_store = WorkflowRunStore(db_path, workspace_id)
            try:
                profile = workflow_store.create_agent_profile(
                    key="retry-test",
                    name="Retry test",
                    role="tester",
                    instructions="",
                    allowed_tools=(),
                    model_policy="inherit",
                )
                graph = {
                    "entry_node_ids": ["step"],
                    "nodes": [
                        {
                            "node_id": "step",
                            "type": "agent",
                            "agent_profile_id": profile["id"],
                            "output_contract": ["answer"],
                            "max_attempts": 2,
                        }
                    ],
                    "edges": [],
                    "run_policy": {"mode": "full_auto"},
                    "limits": {},
                }
                workflow = workflow_store.create_workflow(
                    name="Retry workflow",
                    description="",
                    graph=graph,
                    input_schema={},
                    output_schema={
                        "type": "object",
                        "properties": {"answer": {"type": "string"}},
                        "required": ["answer"],
                    },
                )
                version, _ = workflow_store.publish_workflow(
                    workflow["id"],
                    graph_hash=sha256(repr(graph).encode()).hexdigest(),
                )
                attempts = []
                execution_contexts = []
                terminal_runs = []

                def execute(_node, _materials, _context):
                    attempts.append(len(attempts) + 1)
                    execution_contexts.append(_node.get("__pfs_workflow_context__"))
                    if len(attempts) == 1:
                        raise RuntimeError("temporary node failure")
                    return {
                        "answer": "retry succeeded",
                        "__workflow_usage__": {
                            "model": "deepseek-chat",
                            "provider": "deepseek",
                            "model_calls": 2,
                            "input_tokens": 80,
                            "output_tokens": 20,
                            "cached_input_tokens": 8,
                            "tool_calls": 1,
                            "cost_usd": 0.001,
                        },
                    }

                jobs = ImmediateJobs()
                scheduler = WorkflowScheduler(
                    workflow_store=workflow_store,
                    run_store=run_store,
                    job_runner=jobs,
                    executor=execute,
                    on_run_terminal=lambda run_id, status: terminal_runs.append((run_id, status)),
                    limiter=WorkflowConcurrencyLimiter(
                        global_limit=10,
                        workspace_limit=10,
                        run_limit=10,
                        profile_limit=10,
                    ),
                )
                first = scheduler.start(
                    workflow_version_id=version["id"],
                    session_id=session_id,
                    inputs={},
                )
                second = scheduler.advance(first["run"]["id"])
                final = scheduler.advance(first["run"]["id"])

                self.assertEqual("running", first["run"]["status"])
                self.assertEqual("running", second["run"]["status"])
                self.assertEqual("succeeded", final["run"]["status"])
                self.assertEqual({"answer": "retry succeeded"}, final["outputs"])
                self.assertEqual([1, 2], attempts)
                self.assertTrue(all(item["run_id"] == first["run"]["id"] for item in execution_contexts))
                node_attempts = [(node["status"], node["attempt"]) for node in final["nodes"]]
                self.assertEqual(
                    [("failed", 1), ("succeeded", 2)],
                    node_attempts,
                )
                succeeded_node = final["nodes"][-1]
                self.assertEqual(2, succeeded_node["model_calls"])
                self.assertEqual("deepseek", succeeded_node["provider_name"])
                self.assertEqual([(first["run"]["id"], "succeeded")], terminal_runs)
                events = run_store.list_events(first["run"]["id"])
                self.assertIn(
                    "workflow_node_retry_created",
                    [event["type"] for event in events],
                )
            finally:
                run_store.close()
                workflow_store.close()

    def test_workflow_pause_resume_survives_store_reopen_without_dispatching_early(self):
        class ImmediateJobs:
            def __init__(self):
                self.jobs = {}
                self.count = 0

            def create(self, fn, job_type, label=""):
                self.count += 1
                job_id = f"pause-job-{self.count}"
                self.jobs[job_id] = {
                    "id": job_id,
                    "status": "succeeded",
                    "result": fn(object()),
                    "job_type": job_type,
                    "label": label,
                }
                return job_id

            def get_status(self, job_id):
                return self.jobs.get(job_id)

            def add_terminal_listener(self, _job_id, _callback):
                return None

            def cancel(self, job_id):
                if job_id in self.jobs:
                    self.jobs[job_id]["status"] = STATUS_CANCELED
                return True

        with tempfile.TemporaryDirectory(prefix="pfs-workflow-pause-") as temp_dir:
            db_path = Path(temp_dir) / "workflow.sqlite3"
            workspace_id = "pfs-workflow-pause-workspace"
            session_id = "pfs-workflow-pause-session"
            workflow_store = WorkflowStore(db_path, workspace_id)
            run_store = None
            reopened_workflow_store = None
            reopened_run_store = None
            try:
                run_store = WorkflowRunStore(db_path, workspace_id)
                profile = workflow_store.create_agent_profile(
                    key="pause-test",
                    name="Pause test",
                    role="tester",
                    instructions="",
                    allowed_tools=(),
                    model_policy="inherit",
                )
                graph = {
                    "entry_node_ids": ["step"],
                    "nodes": [
                        {
                            "node_id": "step",
                            "type": "agent",
                            "agent_profile_id": profile["id"],
                            "output_contract": ["answer"],
                        }
                    ],
                    "edges": [],
                    "run_policy": {"mode": "full_auto"},
                    "limits": {},
                }
                workflow = workflow_store.create_workflow(
                    name="Pause workflow",
                    description="",
                    graph=graph,
                    input_schema={},
                    output_schema={
                        "type": "object",
                        "properties": {"answer": {"type": "string"}},
                        "required": ["answer"],
                    },
                )
                version, _ = workflow_store.publish_workflow(
                    workflow["id"],
                    graph_hash=sha256(repr(graph).encode()).hexdigest(),
                )
                run = run_store.create_run(
                    workflow_version_id=version["id"],
                    session_id=session_id,
                    graph=graph,
                    inputs={},
                )
                run_store.transition_run(run["id"], RunStatus.RUNNING)
                jobs = ImmediateJobs()
                scheduler = WorkflowScheduler(
                    workflow_store=workflow_store,
                    run_store=run_store,
                    job_runner=jobs,
                    executor=lambda *_args: {"answer": "resumed"},
                    limiter=WorkflowConcurrencyLimiter(
                        global_limit=10,
                        workspace_limit=10,
                        run_limit=10,
                        profile_limit=10,
                    ),
                )

                paused = scheduler.pause(run["id"], reason="maintenance window")
                self.assertEqual(RunStatus.PAUSED.value, paused["run"]["status"])
                self.assertEqual(0, jobs.count)
                pause_event = next(item for item in paused["events"] if item["type"] == "workflow_run_paused")
                self.assertEqual("running", pause_event["previous_status"])
                self.assertEqual("maintenance window", pause_event["reason"])

                run_store.close()
                run_store = None
                workflow_store.close()
                workflow_store = None

                reopened_workflow_store = WorkflowStore(db_path, workspace_id)
                reopened_run_store = WorkflowRunStore(db_path, workspace_id)
                try:
                    resumed_jobs = ImmediateJobs()
                    reopened_scheduler = WorkflowScheduler(
                        workflow_store=reopened_workflow_store,
                        run_store=reopened_run_store,
                        job_runner=resumed_jobs,
                        executor=lambda *_args: {"answer": "resumed"},
                        limiter=WorkflowConcurrencyLimiter(
                            global_limit=10,
                            workspace_limit=10,
                            run_limit=10,
                            profile_limit=10,
                        ),
                    )
                    resumed = reopened_scheduler.resume(run["id"])
                    self.assertEqual(RunStatus.RUNNING.value, resumed["run"]["status"])
                    self.assertEqual(1, resumed_jobs.count)
                    final = reopened_scheduler.advance(run["id"])
                    self.assertEqual(RunStatus.SUCCEEDED.value, final["run"]["status"])
                    self.assertEqual({"answer": "resumed"}, final["outputs"])
                    event_types = [item["type"] for item in final["events"]]
                    self.assertIn("workflow_run_resumed", event_types)
                finally:
                    reopened_run_store.close()
                    reopened_run_store = None
                    reopened_workflow_store.close()
                    reopened_workflow_store = None
            finally:
                if reopened_run_store is not None:
                    reopened_run_store.close()
                if reopened_workflow_store is not None:
                    reopened_workflow_store.close()
                if run_store is not None:
                    run_store.close()
                if workflow_store is not None:
                    workflow_store.close()

    def test_workflow_resume_keeps_pending_approval_state(self):
        class ImmediateJobs:
            def __init__(self):
                self.jobs = {}
                self.count = 0

            def create(self, fn, job_type, label=""):
                self.count += 1
                job_id = f"approval-job-{self.count}"
                self.jobs[job_id] = {
                    "id": job_id,
                    "status": "succeeded",
                    "result": fn(object()),
                    "job_type": job_type,
                    "label": label,
                }
                return job_id

            def get_status(self, job_id):
                return self.jobs.get(job_id)

            def add_terminal_listener(self, _job_id, _callback):
                return None

            def cancel(self, job_id):
                if job_id in self.jobs:
                    self.jobs[job_id]["status"] = STATUS_CANCELED
                return True

        with tempfile.TemporaryDirectory(prefix="pfs-workflow-pause-approval-") as temp_dir:
            db_path = Path(temp_dir) / "workflow.sqlite3"
            workspace_id = "pfs-workflow-pause-approval-workspace"
            session_id = "pfs-workflow-pause-approval-session"
            workflow_store = WorkflowStore(db_path, workspace_id)
            run_store = WorkflowRunStore(db_path, workspace_id)
            try:
                profile = workflow_store.create_agent_profile(
                    key="approval-pause-test",
                    name="Approval pause test",
                    role="tester",
                    instructions="",
                    allowed_tools=(),
                    model_policy="inherit",
                )
                graph = {
                    "entry_node_ids": ["prepare"],
                    "nodes": [
                        {
                            "node_id": "prepare",
                            "type": "agent",
                            "agent_profile_id": profile["id"],
                            "output_contract": ["answer"],
                        },
                        {
                            "node_id": "deliver",
                            "type": "agent",
                            "agent_profile_id": profile["id"],
                            "output_contract": ["answer"],
                        },
                    ],
                    "edges": [
                        {
                            "from_node": "prepare",
                            "to_node": "deliver",
                            "type": "approval",
                        }
                    ],
                    "run_policy": {"mode": "key_approval"},
                    "limits": {},
                }
                workflow = workflow_store.create_workflow(
                    name="Approval pause workflow",
                    description="",
                    graph=graph,
                    input_schema={},
                    output_schema={
                        "type": "object",
                        "properties": {"answer": {"type": "string"}},
                        "required": ["answer"],
                    },
                )
                version, _ = workflow_store.publish_workflow(
                    workflow["id"],
                    graph_hash=sha256(repr(graph).encode()).hexdigest(),
                )
                jobs = ImmediateJobs()
                scheduler = WorkflowScheduler(
                    workflow_store=workflow_store,
                    run_store=run_store,
                    job_runner=jobs,
                    executor=lambda *_args: {"answer": "prepared"},
                    limiter=WorkflowConcurrencyLimiter(
                        global_limit=10,
                        workspace_limit=10,
                        run_limit=10,
                        profile_limit=10,
                    ),
                )
                first = scheduler.start(
                    workflow_version_id=version["id"],
                    session_id=session_id,
                    inputs={},
                )
                waiting = scheduler.advance(first["run"]["id"])
                self.assertEqual(
                    RunStatus.WAITING_APPROVAL.value,
                    waiting["run"]["status"],
                )
                self.assertEqual(1, len(waiting["approvals"]))

                paused = scheduler.pause(first["run"]["id"], reason="reviewer unavailable")
                self.assertEqual(RunStatus.PAUSED.value, paused["run"]["status"])
                resumed = scheduler.resume(first["run"]["id"])
                self.assertEqual(
                    RunStatus.WAITING_APPROVAL.value,
                    resumed["run"]["status"],
                )
                self.assertEqual("pending", resumed["approvals"][0]["status"])
                self.assertEqual(1, jobs.count)
            finally:
                run_store.close()
                workflow_store.close()

    def test_scheduler_recovery_reconciles_a_restarted_read_only_node(self):
        class DurableJobs:
            def __init__(self, store, session_id, *, complete_new_jobs):
                self.store = store
                self.session_id = session_id
                self.complete_new_jobs = complete_new_jobs
                self.count = 0

            def create(self, _fn, job_type, label=""):
                self.count += 1
                job = self.store.create(self.session_id, job_type, label=label)
                self.store.mark_queued(job["id"])
                self.store.mark_started(job["id"])
                if self.complete_new_jobs:
                    self.store.mark_succeeded(job["id"], {"answer": "recovered"})
                return job["id"]

            def get_status(self, job_id):
                return self.store.get_for_session(self.session_id, job_id)

            def add_terminal_listener(self, _job_id, _callback):
                # The real JobRunner invokes this immediately for a terminal
                # Job. The test advances once more explicitly to model the
                # next scheduler tick without recursive callbacks.
                return None

            def cancel(self, job_id):
                return self.store.mark_canceled(job_id)

        with tempfile.TemporaryDirectory(prefix="pfs-workflow-restart-") as temp_dir:
            workflow_db = Path(temp_dir) / "workflow.sqlite3"
            jobs_db = Path(temp_dir) / "jobs.sqlite3"
            workspace_id = "pfs-workflow-restart-workspace"
            session_id = "pfs-workflow-restart-session"
            workflow_store = WorkflowStore(workflow_db, workspace_id)
            run_store = None
            jobs_store = None
            reopened_workflow_store = None
            reopened_run_store = None
            reopened_jobs_store = None
            try:
                run_store = WorkflowRunStore(workflow_db, workspace_id)
                jobs_store = JobsStore(jobs_db, lease_seconds=0.1)
                profile = workflow_store.create_agent_profile(
                    key="restart-read-only",
                    name="Restart read-only",
                    role="tester",
                    instructions="",
                    allowed_tools=(),
                    model_policy="inherit",
                )
                graph = {
                    "entry_node_ids": ["step"],
                    "nodes": [
                        {
                            "node_id": "step",
                            "type": "agent",
                            "agent_profile_id": profile["id"],
                            "output_contract": ["answer"],
                            "side_effects": ["read_data"],
                            "max_attempts": 2,
                        }
                    ],
                    "edges": [],
                    "run_policy": {"mode": "full_auto"},
                    "limits": {},
                }
                workflow = workflow_store.create_workflow(
                    name="Restart recovery workflow",
                    description="",
                    graph=graph,
                    input_schema={},
                    output_schema={
                        "type": "object",
                        "properties": {"answer": {"type": "string"}},
                        "required": ["answer"],
                    },
                )
                version, _ = workflow_store.publish_workflow(
                    workflow["id"],
                    graph_hash=sha256(repr(graph).encode()).hexdigest(),
                )
                first_jobs = DurableJobs(
                    jobs_store,
                    session_id,
                    complete_new_jobs=False,
                )
                first_scheduler = WorkflowScheduler(
                    workflow_store=workflow_store,
                    run_store=run_store,
                    job_runner=first_jobs,
                    executor=lambda *_args: {"answer": "ignored"},
                    limiter=WorkflowConcurrencyLimiter(
                        global_limit=10,
                        workspace_limit=10,
                        run_limit=10,
                        profile_limit=10,
                    ),
                )
                first = first_scheduler.start(
                    workflow_version_id=version["id"],
                    session_id=session_id,
                    inputs={},
                )
                self.assertEqual(RunStatus.RUNNING.value, first["run"]["status"])
                self.assertEqual(NodeRunStatus.QUEUED.value, first["nodes"][0]["status"])
                old_job_id = first["nodes"][0]["job_id"]
                run_store.close()
                run_store = None
                workflow_store.close()
                workflow_store = None
                jobs_store.close()
                jobs_store = None
                time.sleep(0.25)

                reopened_workflow_store = WorkflowStore(workflow_db, workspace_id)
                reopened_run_store = WorkflowRunStore(workflow_db, workspace_id)
                reopened_jobs_store = JobsStore(jobs_db)
                try:
                    self.assertEqual(
                        STATUS_FAILED,
                        reopened_jobs_store.get(old_job_id)["status"],
                    )
                    second_jobs = DurableJobs(
                        reopened_jobs_store,
                        session_id,
                        complete_new_jobs=True,
                    )
                    second_scheduler = WorkflowScheduler(
                        workflow_store=reopened_workflow_store,
                        run_store=reopened_run_store,
                        job_runner=second_jobs,
                        executor=lambda *_args: {"answer": "recovered"},
                        limiter=WorkflowConcurrencyLimiter(
                            global_limit=10,
                            workspace_limit=10,
                            run_limit=10,
                            profile_limit=10,
                        ),
                    )
                    recovery = second_scheduler.recover_interrupted_runs(
                        session_id=session_id,
                    )
                    self.assertEqual(RunStatus.RUNNING.value, recovery[0]["status"])
                    self.assertEqual(1, second_jobs.count)
                    final = second_scheduler.advance(first["run"]["id"])
                    self.assertEqual(RunStatus.SUCCEEDED.value, final["run"]["status"])
                    self.assertEqual({"answer": "recovered"}, final["outputs"])
                    event_types = [item["type"] for item in final["events"]]
                    self.assertIn("workflow_run_recovery_started", event_types)
                    self.assertIn("workflow_run_recovery_completed", event_types)
                finally:
                    reopened_run_store.close()
                    reopened_run_store = None
                    reopened_workflow_store.close()
                    reopened_workflow_store = None
                    reopened_jobs_store.close()
                    reopened_jobs_store = None
            finally:
                if reopened_jobs_store is not None:
                    reopened_jobs_store.close()
                if reopened_run_store is not None:
                    reopened_run_store.close()
                if reopened_workflow_store is not None:
                    reopened_workflow_store.close()
                if jobs_store is not None:
                    jobs_store.close()
                if run_store is not None:
                    run_store.close()
                if workflow_store is not None:
                    workflow_store.close()

    def test_restart_recovery_blocks_irreversible_side_effect_replay(self):
        class FakeRunStore:
            workspace_id = "restart-safety-workspace"

            def __init__(self):
                self.nodes = [
                    {
                        "id": "node-run-1",
                        "node_id": "export",
                        "status": NodeRunStatus.RUNNING.value,
                        "job_id": "job-restarted",
                        "iteration": 1,
                        "attempt": 1,
                        "agent_profile_id": "profile",
                    }
                ]
                self.transitions = []
                self.events = []

            def list_node_runs(self, _run_id):
                return list(self.nodes)

            def transition_node(self, node_run_id, status, error="", **_kwargs):
                self.transitions.append((node_run_id, status, error))
                for node in self.nodes:
                    if node["id"] == node_run_id:
                        node["status"] = status.value

            def record_event(self, run_id, event_type, payload):
                self.events.append((run_id, event_type, payload))

        class RestartedJobs:
            def get_status(self, _job_id):
                return {
                    "id": "job-restarted",
                    "status": STATUS_FAILED,
                    "error": "Application restarted before the job completed.",
                }

        run_store = FakeRunStore()
        scheduler = WorkflowScheduler.__new__(WorkflowScheduler)
        scheduler.run_store = run_store
        scheduler.job_runner = RestartedJobs()
        scheduler.limiter = SimpleNamespace(release=lambda *_args: None)
        scheduler._reconcile_jobs(
            "run-restarted",
            {
                "run_policy": {"mode": "full_auto"},
                "nodes": [
                    {
                        "node_id": "export",
                        "type": "export",
                        "side_effects": ["export_file"],
                    }
                ],
                "edges": [],
            },
        )
        self.assertEqual(
            [
                (
                    "node-run-1",
                    NodeRunStatus.FAILED,
                    "workflow side-effect replay blocked after restart; manual review required",
                )
            ],
            run_store.transitions,
        )
        self.assertEqual(
            "workflow_side_effect_replay_blocked",
            run_store.events[0][1],
        )

    def test_restart_recovery_reconciles_completed_export_without_replay(self):
        class FakeRunStore:
            workspace_id = "restart-reconcile-workspace"

            def __init__(self):
                self.nodes = [
                    {
                        "id": "node-run-1",
                        "node_id": "export",
                        "status": NodeRunStatus.RUNNING.value,
                        "job_id": "job-restarted",
                        "iteration": 1,
                        "attempt": 1,
                        "agent_profile_id": "profile",
                    }
                ]
                self.transitions = []
                self.events = []

            def list_node_runs(self, _run_id):
                return list(self.nodes)

            def get_side_effect_for_node_run(self, node_run_id, *, effect_type=""):
                if node_run_id != "node-run-1" or effect_type != "export_file":
                    return None
                return {
                    "status": "succeeded",
                    "effect_type": "export_file",
                    "result": {
                        "delivery": {
                            "artifact_id": "artifact-1",
                            "content_sha256": "content-hash",
                        },
                    },
                }

            def transition_node(self, node_run_id, status, error="", **kwargs):
                self.transitions.append((node_run_id, status, error, kwargs))
                for node in self.nodes:
                    if node["id"] == node_run_id:
                        node["status"] = status.value

            def record_event(self, run_id, event_type, payload):
                self.events.append((run_id, event_type, payload))

        class RestartedJobs:
            def get_status(self, _job_id):
                return {
                    "id": "job-restarted",
                    "status": STATUS_FAILED,
                    "error": "Application restarted before the job completed.",
                }

        run_store = FakeRunStore()
        scheduler = WorkflowScheduler.__new__(WorkflowScheduler)
        scheduler.run_store = run_store
        scheduler.job_runner = RestartedJobs()
        scheduler.limiter = SimpleNamespace(release=lambda *_args: None)
        scheduler._reconcile_jobs(
            "run-restarted",
            {
                "run_policy": {"mode": "full_auto"},
                "nodes": [
                    {
                        "node_id": "export",
                        "type": "export",
                        "side_effects": ["export_file"],
                    }
                ],
                "edges": [],
            },
        )
        self.assertEqual(NodeRunStatus.SUCCEEDED.value, run_store.nodes[0]["status"])
        self.assertEqual(NodeRunStatus.OUTPUT_READY, run_store.transitions[0][1])
        self.assertEqual(
            {"delivery": {"artifact_id": "artifact-1", "content_sha256": "content-hash"}},
            run_store.transitions[0][3]["output"],
        )
        self.assertEqual("workflow_side_effect_recovered", run_store.events[0][1])


if __name__ == "__main__":
    unittest.main()
