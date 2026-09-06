import multiprocessing
import os
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, Mock, patch

from flask import Flask

from api import _start_durable_queue_worker
from agent.jobs import JobRunner
from data.durable_queue import (
    COMPLETION_DELIVERED,
    COMPLETION_PENDING,
    QUEUE_FAILED,
    QUEUE_LEASED,
    QUEUE_READY,
    QUEUE_SUCCEEDED,
    DurableQueueStore,
    DurableQueueWorker,
)
from data.jobs_store import JobsStore, STATUS_FAILED, STATUS_RUNNING, STATUS_SUCCEEDED


def _stop_process(process) -> None:
    if process is None:
        return
    try:
        if process.pid is None:
            return
    except (AssertionError, ValueError):
        return
    if process.is_alive():
        process.terminate()
    process.join(timeout=5)
    if process.is_alive():
        process.kill()
        process.join(timeout=5)
    try:
        process.close()
    except (AssertionError, ValueError):
        pass


def _close_multiprocessing_queue(queue) -> None:
    if queue is None:
        return
    try:
        queue.close()
    except (AssertionError, OSError, ValueError):
        pass
    try:
        queue.join_thread()
    except (AssertionError, OSError, ValueError):
        pass


def _wait_for_queue_claim(store, owner_id: str, *, lease_seconds: float, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while True:
        task = store.claim(owner_id, lease_seconds=lease_seconds)
        if task is not None:
            return task
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(f"queue task was not reclaimable within {timeout:.1f}s")
        threading.Event().wait(min(0.02, remaining))


def _claim_queue_task_in_child(db_path: str, result_queue) -> None:
    store = DurableQueueStore(Path(db_path), default_lease_seconds=0.1)
    try:
        task = store.claim("queue-service-a", lease_seconds=0.1)
        result_queue.put(task["id"] if task else "")
    finally:
        store.close()


def _run_probe_worker_in_child(db_path: str, result_queue) -> None:
    store = DurableQueueStore(Path(db_path), default_lease_seconds=1.0)
    try:
        worker = DurableQueueWorker(
            store,
            {"probe": lambda payload, _ctx: {"value": payload["value"] * 2}},
            worker_id="queue-service-child",
        )
        result_queue.put(worker.run_once())
    finally:
        store.close()


def _race_to_claim_queue_task_in_child(
    db_path: str,
    owner_id: str,
    ready_queue,
    start_event,
    result_queue,
) -> None:
    """Attempt the same claim from two independent worker processes."""
    store = DurableQueueStore(Path(db_path), default_lease_seconds=1.0)
    try:
        ready_queue.put(owner_id)
        start_event.wait(timeout=5)
        task = store.claim(owner_id, lease_seconds=1.0)
        result_queue.put(
            {
                "owner_id": owner_id,
                "task_id": task["id"] if task else "",
            }
        )
    finally:
        store.close()


class DurableQueueRecoveryTests(unittest.TestCase):
    def test_api_role_is_a_pure_queue_producer(self):
        app = Flask("pfs-queue-api-role")
        with (
            patch.dict(
                os.environ,
                {
                    "PFS_ENABLE_DURABLE_QUEUE": "1",
                    "PFS_DURABLE_QUEUE_ROLE": "api",
                },
                clear=False,
            ),
            patch("api.optional_feature_enabled", return_value=True),
            patch("data.durable_queue.DurableQueueWorker") as worker_class,
        ):
            _start_durable_queue_worker(app)

        worker_class.assert_not_called()
        self.assertNotIn("pfs_durable_queue_worker", app.extensions)

    def test_worker_role_does_not_start_an_embedded_sidecar(self):
        app = Flask("pfs-queue-worker-role")
        with (
            patch.dict(
                os.environ,
                {
                    "PFS_ENABLE_DURABLE_QUEUE": "1",
                    "PFS_DURABLE_QUEUE_ROLE": "worker",
                },
                clear=False,
            ),
            patch("api.optional_feature_enabled", return_value=True),
            patch("data.durable_queue.DurableQueueWorker") as worker_class,
        ):
            _start_durable_queue_worker(app)

        worker_class.assert_not_called()
        self.assertNotIn("pfs_durable_queue_worker", app.extensions)

    def test_embedded_role_starts_the_opt_in_sidecar(self):
        app = Flask("pfs-queue-embedded-role")
        with TemporaryDirectory(prefix="pfs-embedded-queue-") as temp_dir:
            with (
                patch.dict(
                    os.environ,
                    {
                        "PFS_ENABLE_DURABLE_QUEUE": "1",
                        "PFS_DURABLE_QUEUE_ROLE": "embedded",
                        "PFS_DURABLE_QUEUE_DB_PATH": str(Path(temp_dir) / "queue.sqlite3"),
                    },
                    clear=False,
                ),
                patch("api.optional_feature_enabled", return_value=True),
                patch("data.durable_queue.DurableQueueStore") as store_class,
                patch("data.durable_queue.DurableQueueWorker") as worker_class,
                patch("api.atexit.register") as register,
            ):
                store = store_class.return_value
                worker = worker_class.return_value
                worker.stop.side_effect = lambda *, wait: store.close()
                _start_durable_queue_worker(app)

            store_class.assert_called_once()
            worker_class.assert_called_once()
            worker_class.return_value.start.assert_called_once()
            self.assertIs(app.extensions["pfs_durable_queue_worker"], worker_class.return_value)
            cleanup = app.extensions["pfs_durable_queue_cleanup"]
            register.assert_called_once_with(cleanup)
            cleanup()
            worker.stop.assert_called_once_with(wait=True)
            store.close.assert_called_once_with()

    def test_repeated_embedded_sidecar_start_on_same_app_is_idempotent(self):
        app = Flask("pfs-queue-embedded-idempotent")
        with TemporaryDirectory(prefix="pfs-embedded-idempotent-") as temp_dir:
            with (
                patch.dict(
                    os.environ,
                    {
                        "PFS_ENABLE_DURABLE_QUEUE": "1",
                        "PFS_DURABLE_QUEUE_ROLE": "embedded",
                        "PFS_DURABLE_QUEUE_DB_PATH": str(Path(temp_dir) / "queue.sqlite3"),
                    },
                    clear=False,
                ),
                patch("api.optional_feature_enabled", return_value=True),
                patch("data.durable_queue.DurableQueueStore") as store_class,
                patch("data.durable_queue.DurableQueueWorker") as worker_class,
                patch("api.atexit.register") as register,
            ):
                _start_durable_queue_worker(app)
                _start_durable_queue_worker(app)

            worker_class.assert_called_once()
            worker_class.return_value.start.assert_called_once_with()
            register.assert_called_once_with(app.extensions["pfs_durable_queue_cleanup"])
            self.assertIs(
                app.extensions["pfs_durable_queue_worker"],
                worker_class.return_value,
            )

    def test_concurrent_embedded_sidecar_start_keeps_one_worker_per_app(self):
        """Two simultaneous startup hooks must not create two sidecars."""
        app = Flask("pfs-queue-embedded-concurrent")
        start_barrier = threading.Barrier(2)
        second_worker_created = threading.Event()
        created_workers = []
        created_lock = threading.Lock()
        errors = []

        def make_worker(*_args, **_kwargs):
            worker = Mock()
            with created_lock:
                created_workers.append(worker)
                index = len(created_workers)
            if index == 1:
                # Give a competing startup call a deterministic opportunity to
                # enter the construction path. A correctly locked
                # implementation simply times out here, then the second call
                # observes the installed extension and returns.
                second_worker_created.wait(timeout=1.0)
            else:
                second_worker_created.set()
            return worker

        def start_worker():
            try:
                start_barrier.wait(timeout=2.0)
                _start_durable_queue_worker(app)
            except BaseException as exc:  # pragma: no cover - assertion below
                errors.append(exc)

        with TemporaryDirectory(prefix="pfs-embedded-concurrent-") as temp_dir:
            with (
                patch.dict(
                    os.environ,
                    {
                        "PFS_ENABLE_DURABLE_QUEUE": "1",
                        "PFS_DURABLE_QUEUE_ROLE": "embedded",
                        "PFS_DURABLE_QUEUE_DB_PATH": str(Path(temp_dir) / "queue.sqlite3"),
                    },
                    clear=False,
                ),
                patch("api.optional_feature_enabled", return_value=True),
                patch("data.durable_queue.DurableQueueStore", return_value=Mock()),
                patch(
                    "data.durable_queue.DurableQueueWorker",
                    side_effect=make_worker,
                ) as worker_class,
                patch("api.atexit.register"),
            ):
                threads = [threading.Thread(target=start_worker) for _ in range(2)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=3.0)
                self.assertTrue(all(not thread.is_alive() for thread in threads))

                self.assertEqual([], errors)
                self.assertEqual(1, worker_class.call_count)
                self.assertEqual(1, len(created_workers))
                self.assertIs(
                    app.extensions["pfs_durable_queue_worker"],
                    created_workers[0],
                )
                app.extensions["pfs_durable_queue_cleanup"]()

    def test_worker_rejects_start_after_stop_closed_its_store(self):
        with TemporaryDirectory(prefix="pfs-worker-restart-closed-") as temp_dir:
            store = DurableQueueStore(Path(temp_dir) / "queue.sqlite3")
            worker = DurableQueueWorker(store, {}, worker_id="closed-store-worker")
            try:
                worker.stop(wait=True)
                self.assertTrue(worker._store_closed)
                with self.assertRaisesRegex(RuntimeError, "closed"):
                    worker.start()
                self.assertIsNone(worker._thread)
            finally:
                worker.stop(wait=True)

    def test_heartbeat_exception_marks_lease_lost_and_stale_worker_cannot_complete(self):
        with TemporaryDirectory(prefix="pfs-heartbeat-failure-") as temp_dir:
            jobs_path = Path(temp_dir) / "jobs.sqlite3"
            queue_path = Path(temp_dir) / "queue.sqlite3"
            store = JobsStore(jobs_path, owner_id="api-service-heartbeat", lease_seconds=0.2)
            queue = DurableQueueStore(queue_path, default_lease_seconds=0.2)
            observed = threading.Event()
            try:
                job = store.create(
                    "heartbeat-session",
                    "analysis",
                    label="心跳异常租约隔离",
                    operation_key="analysis:heartbeat-failure:1",
                )
                job_id = job["id"]
                self.assertTrue(store.mark_queued(job_id))
                payload = {
                    "job_id": job_id,
                    "session_id": "heartbeat-session",
                    "job_store_path": str(jobs_path),
                }
                task = queue.enqueue(
                    "heartbeat-probe",
                    payload,
                    operation_key="analysis:heartbeat-failure:queue:1",
                )
                self.assertTrue(
                    store.set_request_payload(
                        job_id,
                        payload,
                        durable_handler="heartbeat-probe",
                        queue_task_id=task["id"],
                    )
                )
                self.assertTrue(store.handoff_to_queue(job_id, task["id"]))

                def handler(_payload, context):
                    deadline = time.monotonic() + 0.5
                    while time.monotonic() < deadline and not context.lease_lost:
                        time.sleep(0.01)
                    observed.set()
                    return {"lease_lost": context.lease_lost}

                worker = DurableQueueWorker(
                    queue,
                    {"heartbeat-probe": handler},
                    worker_id="heartbeat-worker",
                    lease_seconds=0.2,
                )
                with patch.object(queue, "heartbeat", side_effect=RuntimeError("heartbeat down")):
                    self.assertTrue(worker.run_once())

                self.assertTrue(observed.is_set())
                persisted_task = queue.get(task["id"])
                persisted_job = store.get(job_id)
                self.assertIsNotNone(persisted_task)
                self.assertIsNotNone(persisted_job)
                self.assertEqual(QUEUE_LEASED, persisted_task["status"], persisted_task)
                self.assertIsNone(persisted_task["result"])
                self.assertNotEqual(STATUS_SUCCEEDED, persisted_job["status"])
                self.assertEqual(STATUS_RUNNING, persisted_job["status"])
                self.assertTrue(persisted_job["owner_id"])
            finally:
                queue.close()
                store.close()

    def test_store_constructor_closes_connection_for_initialization_failures(self):
        """Every SQLite setup phase must close a partially opened connection."""
        failure_phases = ("PRAGMA", "executescript", "commit")
        for phase in failure_phases:
            with self.subTest(phase=phase):
                with TemporaryDirectory(prefix="pfs-queue-init-failure-") as temp_dir:
                    connection = Mock()

                    def execute(sql, *args, _phase=phase):
                        del args
                        if _phase == "PRAGMA" and sql == "PRAGMA journal_mode=WAL":
                            raise RuntimeError("queue init PRAGMA failed")
                        if sql == "PRAGMA table_info(durable_queue_tasks)":
                            return []
                        return Mock()

                    connection.execute.side_effect = execute
                    if phase == "executescript":
                        connection.executescript.side_effect = RuntimeError("queue init executescript failed")
                    if phase == "commit":
                        connection.commit.side_effect = RuntimeError("queue init commit failed")

                    with patch(
                        "data.durable_queue.sqlite3.connect",
                        return_value=connection,
                    ):
                        with self.assertRaisesRegex(RuntimeError, "queue init"):
                            DurableQueueStore(Path(temp_dir) / "queue.sqlite3")

                    connection.close.assert_called_once_with()

    def test_terminal_succeeded_linked_job_settles_queue_and_calls_completion_hook(self):
        with TemporaryDirectory(prefix="pfs-linked-job-terminal-success-") as temp_dir:
            jobs_path = Path(temp_dir) / "jobs.sqlite3"
            queue_path = Path(temp_dir) / "queue.sqlite3"
            store = JobsStore(
                jobs_path,
                owner_id="api-service-terminal-success",
                lease_seconds=0.5,
            )
            queue = DurableQueueStore(queue_path, default_lease_seconds=0.5)
            try:
                job = store.create(
                    "terminal-success-session",
                    "workflow_node",
                    label="队列重复完成回调",
                )
                job_id = job["id"]
                self.assertTrue(store.mark_queued(job_id))
                self.assertTrue(store.mark_started(job_id))
                result = {"node_id": "sales-summary", "status": "done"}
                self.assertTrue(store.mark_succeeded(job_id, result))

                task = queue.enqueue(
                    "workflow_node",
                    {
                        "job_id": job_id,
                        "session_id": "terminal-success-session",
                        "job_store_path": str(jobs_path),
                    },
                    operation_key="workflow:terminal-success:queue",
                )
                hook = Mock()
                worker = DurableQueueWorker(
                    queue,
                    {"workflow_node": Mock(side_effect=AssertionError("handler must not run"))},
                    worker_id="terminal-success-worker",
                    lease_seconds=0.5,
                    on_complete=hook,
                )

                self.assertTrue(worker.run_once())
                self.assertFalse(worker.run_once())
                settled = queue.get(task["id"])
                self.assertEqual(QUEUE_SUCCEEDED, settled["status"])
                self.assertEqual(result, settled["result"])
                self.assertEqual(STATUS_SUCCEEDED, store.get(job_id)["status"])
                hook.assert_called_once_with(task, result)
            finally:
                queue.close()
                store.close()

    def test_completion_hook_failure_is_recoverable_without_exactly_once_claim(self):
        """A failed notification must be observable as an eventual retry."""
        with TemporaryDirectory(prefix="pfs-completion-hook-retry-") as temp_dir:
            queue = DurableQueueStore(Path(temp_dir) / "queue.sqlite3")
            hook_calls = []
            failures = []
            result = {"value": 42}

            def completion_hook(task, completed_result):
                hook_calls.append((task["id"], completed_result))
                if len(hook_calls) == 1:
                    failures.append("first callback failed")
                    raise RuntimeError("notification unavailable")

            try:
                task = queue.enqueue(
                    "completion-retry-probe",
                    {"value": 21},
                    operation_key="completion-hook-retry:1",
                )
                worker = DurableQueueWorker(
                    queue,
                    {
                        "completion-retry-probe": lambda _payload, _ctx: result,
                        "completion-recovery-probe": lambda _payload, _ctx: {"value": 84},
                    },
                    worker_id="completion-retry-worker",
                    poll_seconds=0.02,
                    on_complete=completion_hook,
                )

                self.assertTrue(worker.run_once())
                settled = queue.get(task["id"])
                self.assertEqual(QUEUE_SUCCEEDED, settled["status"])
                self.assertEqual(result, settled["result"])
                self.assertEqual(COMPLETION_PENDING, settled["completion_status"])
                self.assertEqual(1, len(failures))

                # A later worker poll is made visible by a second task. The
                # contract is at-least-once/recoverable: implementations may
                # retry in the first call or in a later poll. Do not assert an
                # exact callback count; require the pending first notification
                # to be delivered successfully and marked delivered.
                threading.Event().wait(0.05)
                queue.enqueue(
                    "completion-recovery-probe",
                    {"value": 43},
                    operation_key="completion-hook-recovery:1",
                )
                self.assertTrue(worker.run_once())
                self.assertGreaterEqual(len(hook_calls), 2, hook_calls)
                self.assertIn(result, [item[1] for item in hook_calls])
                self.assertEqual(
                    COMPLETION_DELIVERED,
                    queue.get(task["id"])["completion_status"],
                )
            finally:
                queue.close()

    def test_worker_without_completion_callback_leaves_notification_pending(self):
        """A callback-capable worker can deliver a prior worker's completion."""
        with TemporaryDirectory(prefix="pfs-completion-without-callback-") as temp_dir:
            queue = DurableQueueStore(Path(temp_dir) / "queue.sqlite3")
            result = {"value": 21}
            callbacks = []
            try:
                task = queue.enqueue(
                    "completion-without-callback-probe",
                    {"value": 7},
                    operation_key="completion-without-callback:1",
                )
                producer = DurableQueueWorker(
                    queue,
                    {
                        "completion-without-callback-probe": lambda _payload, _ctx: result,
                    },
                    worker_id="completion-producer-without-callback",
                )

                self.assertTrue(producer.run_once())
                settled = queue.get(task["id"])
                self.assertEqual(QUEUE_SUCCEEDED, settled["status"])
                self.assertEqual(result, settled["result"])
                self.assertEqual(COMPLETION_PENDING, settled["completion_status"])

                consumer = DurableQueueWorker(
                    queue,
                    {},
                    worker_id="completion-consumer-with-callback",
                    on_complete=lambda completed_task, completed_result: callbacks.append(
                        (completed_task["id"], completed_result)
                    ),
                )
                self.assertTrue(consumer.run_once())
                self.assertEqual([(task["id"], result)], callbacks)
                self.assertEqual(
                    COMPLETION_DELIVERED,
                    queue.get(task["id"])["completion_status"],
                )
            finally:
                queue.close()

    def test_expired_lease_does_not_exceed_max_attempts(self):
        with TemporaryDirectory(prefix="pfs-queue-max-attempts-") as temp_dir:
            queue = DurableQueueStore(
                Path(temp_dir) / "queue.sqlite3",
                default_lease_seconds=1.0,
            )
            try:
                base = time.time()
                task = queue.enqueue(
                    "probe",
                    {"value": 7},
                    max_attempts=2,
                    available_at=base - 1,
                    operation_key="probe:max-attempts:lease-expiry",
                )
                first = queue.claim("queue-max-attempts-1", lease_seconds=1.0, now=base)
                self.assertEqual(task["id"], first["id"])
                self.assertEqual(1, first["attempts"])

                retry = queue.claim(
                    "queue-max-attempts-2",
                    lease_seconds=1.0,
                    now=base + 1.1,
                )
                self.assertEqual(task["id"], retry["id"])
                self.assertEqual(2, retry["attempts"])

                self.assertIsNone(
                    queue.claim(
                        "queue-max-attempts-3",
                        lease_seconds=1.0,
                        now=base + 2.2,
                    )
                )
                exhausted = queue.get(task["id"])
                self.assertEqual(QUEUE_FAILED, exhausted["status"])
                self.assertEqual(2, exhausted["attempts"])
                self.assertEqual("", exhausted["owner_id"])
                self.assertIsNone(exhausted["lease_until"])
                self.assertIsNotNone(exhausted["finished_at"])
                self.assertTrue(exhausted["error"])
            finally:
                queue.close()

    def test_expired_max_attempts_does_not_terminalize_active_linked_job(self):
        """Queue exhaustion preserves an active linked Job for reconciliation."""
        with TemporaryDirectory(prefix="pfs-linked-max-attempts-") as temp_dir:
            jobs_path = Path(temp_dir) / "jobs.sqlite3"
            queue_path = Path(temp_dir) / "queue.sqlite3"
            producer_store = JobsStore(
                jobs_path,
                owner_id="api-service-linked-max-attempts",
                lease_seconds=30.0,
            )
            queue = DurableQueueStore(queue_path, default_lease_seconds=0.1)
            worker_store = None
            try:
                job = producer_store.create(
                    "linked-max-attempts-session",
                    "analysis",
                    label="关联 Job 租约耗尽",
                )
                job_id = job["id"]
                self.assertTrue(producer_store.mark_queued(job_id))
                payload = {
                    "job_id": job_id,
                    "session_id": "linked-max-attempts-session",
                    "job_store_path": str(jobs_path),
                }
                base = time.time()
                task = queue.enqueue(
                    "linked-max-attempts-probe",
                    payload,
                    max_attempts=1,
                    available_at=base - 1.0,
                    operation_key="linked-max-attempts:queue:1",
                )
                self.assertTrue(
                    producer_store.set_request_payload(
                        job_id,
                        payload,
                        durable_handler="linked-max-attempts-probe",
                        queue_task_id=task["id"],
                    )
                )
                self.assertTrue(producer_store.handoff_to_queue(job_id, task["id"]))

                worker_store = JobsStore(
                    jobs_path,
                    owner_id="queue-linked-max-attempts:job",
                    lease_seconds=30.0,
                )
                self.assertTrue(worker_store.claim_durable_job(job_id))
                self.assertEqual(
                    task["id"],
                    queue.claim(
                        "queue-linked-max-attempts",
                        lease_seconds=0.1,
                        now=base,
                    )["id"],
                )

                # The queue lease is expired while the linked Job lease is
                # intentionally still live. The replacement poll must not
                # terminalize only the queue row and strand the Job as active.
                replacement = queue.claim(
                    "queue-linked-max-attempts-replacement",
                    lease_seconds=0.1,
                    now=base + 1.0,
                )
                self.assertIsNotNone(replacement)
                queue_state = queue.get(task["id"])
                job_state = producer_store.get(job_id)
                self.assertIn(queue_state["status"], {QUEUE_READY, QUEUE_LEASED})
                self.assertEqual(STATUS_RUNNING, job_state["status"])
                self.assertFalse(
                    queue_state["status"] in {QUEUE_FAILED, QUEUE_SUCCEEDED}
                    and job_state["status"] not in {STATUS_FAILED, STATUS_SUCCEEDED, "canceled"},
                    {"queue": queue_state, "job": job_state},
                )
            finally:
                if worker_store is not None:
                    worker_store.close()
                queue.close()
                producer_store.close()

    def test_jobs_store_constructor_closes_connection_for_each_initialization_failure(self):
        """An opened SQLite handle is closed if any setup stage raises."""
        failure_phases = (
            "journal_mode",
            "schema",
            "ensure_columns",
            "migrate",
            "recover",
            "commit",
            "cleanup",
        )
        for phase in failure_phases:
            with self.subTest(phase=phase):
                with TemporaryDirectory(prefix="pfs-jobs-store-init-failure-") as temp_dir:
                    connection = MagicMock()
                    connection.total_changes = 0

                    def execute(sql, *args, _phase=phase):
                        del args
                        if _phase == "journal_mode" and sql == "PRAGMA journal_mode=WAL":
                            raise RuntimeError("jobs init journal_mode failed")
                        if sql == "PRAGMA table_info(jobs)":
                            return []
                        if _phase == "ensure_columns" and sql.startswith("ALTER TABLE jobs ADD COLUMN label"):
                            raise RuntimeError("jobs init ensure_columns failed")
                        if _phase == "migrate" and sql.startswith("UPDATE jobs SET status"):
                            raise RuntimeError("jobs init migrate failed")
                        if _phase == "recover" and sql.startswith("SELECT * FROM jobs WHERE status NOT IN"):
                            raise RuntimeError("jobs init recover failed")
                        cursor = MagicMock()
                        cursor.fetchall.return_value = []
                        cursor.fetchone.return_value = None
                        return cursor

                    connection.execute.side_effect = execute
                    if phase == "schema":
                        connection.executescript.side_effect = RuntimeError("jobs init schema failed")
                    if phase == "commit":
                        connection.commit.side_effect = RuntimeError("jobs init commit failed")

                    cleanup_side_effect = (
                        RuntimeError("jobs init cleanup failed") if phase == "cleanup" else None
                    )
                    with (
                        patch("data.jobs_store.sqlite3.connect", return_value=connection),
                        patch.object(
                            JobsStore,
                            "cleanup_events",
                            side_effect=cleanup_side_effect,
                            return_value=0 if cleanup_side_effect is None else None,
                        ),
                    ):
                        with self.assertRaises(RuntimeError):
                            JobsStore(Path(temp_dir) / "jobs.sqlite3")

                    connection.close.assert_called_once_with()

    def test_jobs_store_construction_failure_does_not_terminalize_linked_queue(self):
        """A missing linked store cannot turn an active Job into a queue failure."""
        with TemporaryDirectory(prefix="pfs-linked-store-init-failure-") as temp_dir:
            jobs_path = Path(temp_dir) / "jobs.sqlite3"
            queue_path = Path(temp_dir) / "queue.sqlite3"
            store = JobsStore(jobs_path, owner_id="api-service-linked-store", lease_seconds=5.0)
            queue = DurableQueueStore(queue_path, default_lease_seconds=1.0)
            try:
                job = store.create(
                    "linked-store-init-failure-session",
                    "analysis",
                    label="关联 JobsStore 构造失败",
                )
                job_id = job["id"]
                self.assertTrue(store.mark_queued(job_id))
                payload = {
                    "job_id": job_id,
                    "session_id": "linked-store-init-failure-session",
                    "job_store_path": str(jobs_path),
                }
                task = queue.enqueue(
                    "linked-store-init-failure-probe",
                    payload,
                    operation_key="linked-store-init-failure:queue:1",
                )
                self.assertTrue(
                    store.set_request_payload(
                        job_id,
                        payload,
                        durable_handler="linked-store-init-failure-probe",
                        queue_task_id=task["id"],
                    )
                )
                self.assertTrue(store.handoff_to_queue(job_id, task["id"]))

                def failing_handler(_payload, _context):
                    raise RuntimeError("handler failed while Job store unavailable")

                worker = DurableQueueWorker(
                    queue,
                    {"linked-store-init-failure-probe": failing_handler},
                    worker_id="linked-store-init-failure-worker",
                    lease_seconds=1.0,
                )
                with patch(
                    "data.jobs_store.JobsStore",
                    side_effect=RuntimeError("linked JobsStore unavailable"),
                ):
                    with self.assertRaisesRegex(RuntimeError, "linked JobsStore unavailable"):
                        worker.run_once()

                queue_state = queue.get(task["id"])
                job_state = store.get(job_id)
                self.assertFalse(
                    queue_state["status"] in {QUEUE_FAILED, QUEUE_SUCCEEDED}
                    and job_state["status"] not in {STATUS_FAILED, STATUS_SUCCEEDED, "canceled"},
                    {"queue": queue_state, "job": job_state},
                )
                self.assertIn(queue_state["status"], {QUEUE_READY, QUEUE_LEASED})
            finally:
                queue.close()
                store.close()

    def test_unknown_linked_job_state_keeps_queue_recoverable(self):
        """An unreadable Job state must preserve both sides for recovery."""
        with TemporaryDirectory(prefix="pfs-linked-job-unknown-state-") as temp_dir:
            jobs_path = Path(temp_dir) / "jobs.sqlite3"
            queue_path = Path(temp_dir) / "queue.sqlite3"
            store = JobsStore(jobs_path, owner_id="api-service-unknown-state", lease_seconds=5.0)
            queue = DurableQueueStore(queue_path, default_lease_seconds=1.0)
            try:
                job = store.create(
                    "linked-unknown-state-session",
                    "analysis",
                    label="关联 Job 状态未知",
                )
                job_id = job["id"]
                self.assertTrue(store.mark_queued(job_id))
                payload = {
                    "job_id": job_id,
                    "session_id": "linked-unknown-state-session",
                    "job_store_path": str(jobs_path),
                }
                task = queue.enqueue(
                    "linked-unknown-state-probe",
                    payload,
                    operation_key="linked-unknown-state:queue:1",
                )
                self.assertTrue(
                    store.set_request_payload(
                        job_id,
                        payload,
                        durable_handler="linked-unknown-state-probe",
                        queue_task_id=task["id"],
                    )
                )
                self.assertTrue(store.handoff_to_queue(job_id, task["id"]))

                def failing_handler(_payload, _context):
                    raise RuntimeError("handler failed while Job state is unknown")

                worker = DurableQueueWorker(
                    queue,
                    {"linked-unknown-state-probe": failing_handler},
                    worker_id="linked-unknown-state-worker",
                    lease_seconds=1.0,
                )
                with patch.object(
                    JobsStore,
                    "get",
                    side_effect=RuntimeError("linked Job state unreadable"),
                ):
                    self.assertTrue(worker.run_once())

                queue_state = queue.get(task["id"])
                job_state = store.get(job_id)
                self.assertIn(queue_state["status"], {QUEUE_READY, QUEUE_LEASED})
                self.assertFalse(
                    queue_state["status"] in {QUEUE_FAILED, QUEUE_SUCCEEDED}
                    and job_state["status"] not in {STATUS_FAILED, STATUS_SUCCEEDED, "canceled"},
                    {"queue": queue_state, "job": job_state},
                )
            finally:
                queue.close()
                store.close()

    def test_unknown_handler_never_leaves_terminal_queue_with_active_linked_job(self):
        for initial_status in ("queued", "running"):
            with self.subTest(initial_status=initial_status):
                with TemporaryDirectory(prefix="pfs-unknown-handler-state-") as temp_dir:
                    jobs_path = Path(temp_dir) / "jobs.sqlite3"
                    queue_path = Path(temp_dir) / "queue.sqlite3"
                    store = JobsStore(
                        jobs_path,
                        owner_id=f"api-service-unknown-{initial_status}",
                        lease_seconds=1.0,
                    )
                    queue = DurableQueueStore(queue_path, default_lease_seconds=1.0)
                    try:
                        job = store.create(
                            f"unknown-handler-{initial_status}",
                            "analysis",
                            label="未知队列 handler",
                        )
                        job_id = job["id"]
                        self.assertTrue(store.mark_queued(job_id))
                        payload = {
                            "job_id": job_id,
                            "session_id": f"unknown-handler-{initial_status}",
                            "job_store_path": str(jobs_path),
                        }
                        task = queue.enqueue(
                            "handler-that-does-not-exist",
                            payload,
                            max_attempts=1,
                            operation_key=f"unknown-handler:{initial_status}",
                        )
                        self.assertTrue(
                            store.set_request_payload(
                                job_id,
                                payload,
                                durable_handler="handler-that-does-not-exist",
                                queue_task_id=task["id"],
                            )
                        )
                        self.assertTrue(store.handoff_to_queue(job_id, task["id"]))
                        if initial_status == "running":
                            self.assertTrue(store.mark_started(job_id))

                        worker = DurableQueueWorker(
                            queue,
                            {},
                            worker_id=f"unknown-handler-worker-{initial_status}",
                            lease_seconds=1.0,
                        )
                        self.assertTrue(worker.run_once())

                        queue_state = queue.get(task["id"])
                        job_state = store.get(job_id)
                        self.assertFalse(
                            queue_state["status"] == QUEUE_FAILED
                            and job_state["status"] in {"queued", STATUS_RUNNING},
                            {"queue": queue_state, "job": job_state},
                        )
                    finally:
                        queue.close()
                        store.close()

    def test_linked_job_secondary_failure_never_finishes_queue_while_job_runs(self):
        failure_cases = (
            ("status lookup", "get", 3),
            ("retry requeue", "requeue_durable_job", 3),
            ("terminal failure", "mark_failed", 1),
        )
        for label, method_name, max_attempts in failure_cases:
            with self.subTest(label=label):
                with TemporaryDirectory(prefix="pfs-linked-job-secondary-failure-") as temp_dir:
                    jobs_path = Path(temp_dir) / "jobs.sqlite3"
                    queue_path = Path(temp_dir) / "queue.sqlite3"
                    store = JobsStore(
                        jobs_path,
                        owner_id="api-service-secondary-failure",
                        lease_seconds=0.5,
                    )
                    queue = DurableQueueStore(queue_path, default_lease_seconds=0.5)
                    try:
                        job = store.create(
                            "secondary-failure-session",
                            "analysis",
                            label=label,
                            operation_key=f"analysis:secondary-failure:{method_name}",
                        )
                        job_id = job["id"]
                        self.assertTrue(store.mark_queued(job_id))
                        payload = {
                            "job_id": job_id,
                            "session_id": "secondary-failure-session",
                            "job_store_path": str(jobs_path),
                        }
                        task = queue.enqueue(
                            "failing-probe",
                            payload,
                            max_attempts=max_attempts,
                            operation_key=f"analysis:secondary-failure:queue:{method_name}",
                        )
                        self.assertTrue(
                            store.set_request_payload(
                                job_id,
                                payload,
                                durable_handler="failing-probe",
                                queue_task_id=task["id"],
                            )
                        )
                        self.assertTrue(store.handoff_to_queue(job_id, task["id"]))

                        def fail_handler(_payload, _context):
                            raise RuntimeError("handler failed")

                        worker = DurableQueueWorker(
                            queue,
                            {"failing-probe": fail_handler},
                            worker_id="secondary-failure-worker",
                            lease_seconds=0.5,
                        )
                        real_method = getattr(JobsStore, method_name)

                        def fail_secondary(instance, *args, **kwargs):
                            if instance._owner_id.endswith(":job"):
                                raise RuntimeError(f"{method_name} failed")
                            return real_method(instance, *args, **kwargs)

                        with patch.object(JobsStore, method_name, new=fail_secondary):
                            self.assertTrue(worker.run_once())

                        persisted_task = queue.get(task["id"])
                        persisted_job = store.get(job_id)
                        self.assertIsNotNone(persisted_task)
                        self.assertIsNotNone(persisted_job)
                        self.assertIn(persisted_task["status"], {QUEUE_READY, QUEUE_LEASED})
                        self.assertNotIn(persisted_task["status"], {QUEUE_FAILED, QUEUE_SUCCEEDED})
                        self.assertEqual(STATUS_RUNNING, persisted_job["status"])
                    finally:
                        queue.close()
                        store.close()

    def test_durable_enqueue_failure_closes_queue_and_cleans_job_lease(self):
        with TemporaryDirectory(prefix="pfs-durable-enqueue-failure-") as temp_dir:
            jobs_path = Path(temp_dir) / "jobs.sqlite3"
            store = JobsStore(
                jobs_path,
                owner_id="api-service-enqueue-failure",
                lease_seconds=0.2,
            )
            runner = JobRunner("durable-enqueue-failure-session", store, max_workers=1)
            try:
                job_id = runner.begin_tracked(
                    "conversation_analysis",
                    label="队列提交失败清理",
                )
                with patch("data.durable_queue.DurableQueueStore") as queue_class:
                    queue = queue_class.return_value
                    queue.enqueue.side_effect = RuntimeError("queue unavailable")
                    with self.assertRaisesRegex(RuntimeError, "queue unavailable"):
                        runner.enqueue_durable(
                            job_id,
                            "chat_turn",
                            {"message": "提交失败"},
                            operation_key="chat:enqueue-failure:1",
                        )

                persisted = store.get(job_id)
                self.assertIsNotNone(persisted)
                self.assertEqual(STATUS_FAILED, persisted["status"])
                self.assertEqual("", persisted["owner_id"])
                self.assertIsNone(persisted["lease_until"])
                self.assertNotIn(job_id, runner._lease_stops)
                queue.close.assert_called_once_with()
            finally:
                runner.shutdown(wait=True)
                store.close()

    def test_concurrent_workers_only_one_claims_a_ready_task(self):
        if "fork" not in multiprocessing.get_all_start_methods():
            self.skipTest("requires a process start method that can run this local fixture")
        with TemporaryDirectory(prefix="pfs-concurrent-queue-claim-") as temp_dir:
            db_path = Path(temp_dir) / "queue.sqlite3"
            producer = DurableQueueStore(db_path, default_lease_seconds=1.0)
            task = producer.enqueue(
                "probe",
                {"value": 21},
                operation_key="probe:concurrent-claim:1",
            )
            producer.close()

            context = multiprocessing.get_context("fork")
            ready_queue = context.Queue()
            start_event = context.Event()
            result_queue = context.Queue()
            processes = [
                context.Process(
                    target=_race_to_claim_queue_task_in_child,
                    args=(
                        str(db_path),
                        f"queue-race-{index}",
                        ready_queue,
                        start_event,
                        result_queue,
                    ),
                )
                for index in (1, 2)
            ]
            try:
                for process in processes:
                    process.start()
                ready_owners = {ready_queue.get(timeout=5) for _ in processes}
                self.assertEqual({"queue-race-1", "queue-race-2"}, ready_owners)
                start_event.set()
                results = [result_queue.get(timeout=5) for _ in processes]
                for process in processes:
                    process.join(timeout=5)
                    self.assertEqual(0, process.exitcode)

                claimed = [result for result in results if result["task_id"] == task["id"]]
                self.assertEqual(1, len(claimed), results)
                self.assertEqual(
                    1,
                    len([result for result in results if not result["task_id"]]),
                )
                reader = DurableQueueStore(db_path)
                try:
                    persisted = reader.get(task["id"])
                    self.assertEqual("leased", persisted["status"])
                    self.assertEqual(1, persisted["attempts"])
                    self.assertEqual(claimed[0]["owner_id"], persisted["owner_id"])
                finally:
                    reader.close()
            finally:
                start_event.set()
                for process in processes:
                    if process.is_alive():
                        process.terminate()
                    process.join(timeout=5)
                ready_queue.close()
                ready_queue.join_thread()
                result_queue.close()
                result_queue.join_thread()

    def test_named_handler_completes_in_a_separate_worker_process(self):
        if "fork" not in multiprocessing.get_all_start_methods():
            self.skipTest("requires a process start method that can run this local fixture")
        with TemporaryDirectory(prefix="pfs-cross-service-handler-") as temp_dir:
            db_path = Path(temp_dir) / "queue.sqlite3"
            producer = DurableQueueStore(db_path, default_lease_seconds=1.0)
            task = producer.enqueue(
                "probe",
                {"value": 21},
                operation_key="probe:separate-worker:1",
            )
            producer.close()

            context = multiprocessing.get_context("fork")
            result_queue = context.Queue()
            process = context.Process(
                target=_run_probe_worker_in_child,
                args=(str(db_path), result_queue),
            )
            try:
                process.start()
                self.assertTrue(result_queue.get(timeout=5))
                process.join(timeout=5)
                self.assertEqual(0, process.exitcode)
                reader = DurableQueueStore(db_path)
                try:
                    completed = reader.get(task["id"])
                    self.assertEqual(QUEUE_SUCCEEDED, completed["status"])
                    self.assertEqual({"value": 42}, completed["result"])
                finally:
                    reader.close()
            finally:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
                result_queue.close()
                result_queue.join_thread()

    def test_task_can_be_reclaimed_by_another_process_after_worker_disappears(self):
        if "fork" not in multiprocessing.get_all_start_methods():
            self.skipTest("requires a process start method that can run this local fixture")
        with TemporaryDirectory(prefix="pfs-cross-service-queue-") as temp_dir:
            db_path = Path(temp_dir) / "queue.sqlite3"
            first = DurableQueueStore(db_path, default_lease_seconds=0.1)
            replacement = None
            process = None
            result_queue = None
            try:
                task = first.enqueue(
                    "probe",
                    {"value": 42},
                    operation_key="probe:cross-service:1",
                )
                context = multiprocessing.get_context("fork")
                result_queue = context.Queue()
                process = context.Process(
                    target=_claim_queue_task_in_child,
                    args=(str(db_path), result_queue),
                )
                process.start()
                self.assertEqual(task["id"], result_queue.get(timeout=5))
                process.join(timeout=5)
                self.assertEqual(0, process.exitcode)

                replacement = DurableQueueStore(db_path, default_lease_seconds=0.1)
                time.sleep(0.15)
                reclaimed = replacement.claim(
                    "queue-service-b",
                    lease_seconds=0.1,
                )
                self.assertIsNotNone(reclaimed)
                self.assertEqual(task["id"], reclaimed["id"])
                self.assertEqual(2, reclaimed["attempts"])
                self.assertFalse(first.heartbeat(task["id"], "queue-service-a"))
                self.assertFalse(first.complete(task["id"], "queue-service-a", {"stale": True}))
                self.assertTrue(
                    replacement.complete(
                        task["id"],
                        "queue-service-b",
                        {"value": reclaimed["payload"]["value"] + 1},
                    )
                )
                settled = replacement.get(task["id"])
                self.assertEqual(QUEUE_SUCCEEDED, settled["status"])
                self.assertEqual({"value": 43}, settled["result"])
            finally:
                if process is not None and process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
                if replacement is not None:
                    replacement.close()
                first.close()
                if result_queue is not None:
                    result_queue.close()
                    result_queue.join_thread()

    def test_worker_reconstructs_a_job_and_updates_both_queue_and_job_state(self):
        with TemporaryDirectory(prefix="pfs-durable-job-") as temp_dir:
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
                store = JobsStore(jobs_path, owner_id="http-service")
                runner = JobRunner("durable-job-session", store, max_workers=1)
                queue = DurableQueueStore(queue_path)
                try:
                    job_id = runner.create_durable(
                        "probe",
                        {"value": 6},
                        "analysis",
                        label="跨服务探针",
                        operation_key="analysis:cross-service:1",
                    )
                    worker = DurableQueueWorker(
                        queue,
                        {"probe": lambda payload, _ctx: {"value": payload["value"] * 7}},
                        worker_id="queue-service-test",
                    )
                    self.assertTrue(worker.run_once())
                    self.assertFalse(worker.run_once())
                    self.assertEqual(STATUS_SUCCEEDED, store.get(job_id)["status"])
                    self.assertEqual({"value": 42}, store.get(job_id)["result"])
                    queue_task = queue.get_by_operation_key("analysis:cross-service:1")
                    self.assertEqual(QUEUE_SUCCEEDED, queue_task["status"])
                    self.assertEqual({"value": 42}, queue_task["result"])
                finally:
                    runner.shutdown(wait=True)
                    queue.close()
                    store.close()

    def test_queue_failure_does_not_requeue_a_user_canceled_job(self):
        with TemporaryDirectory(prefix="pfs-durable-cancel-") as temp_dir:
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
                store = JobsStore(jobs_path, owner_id="http-service")
                runner = JobRunner("durable-cancel-session", store, max_workers=1)
                queue = DurableQueueStore(queue_path)
                try:
                    job_id = runner.create_durable(
                        "failing-probe",
                        {"value": 1},
                        "analysis",
                        operation_key="analysis:cross-service:cancel",
                    )
                    task = queue.claim("queue-service-test")
                    self.assertIsNotNone(task)
                    worker_store = JobsStore(
                        jobs_path,
                        owner_id="queue-service-test:job",
                    )
                    try:
                        self.assertTrue(worker_store.claim_durable_job(job_id))
                    finally:
                        worker_store.close()
                    self.assertTrue(runner.cancel(job_id))

                    handler_calls = []

                    def fail_if_called(_payload, _context):
                        handler_calls.append(True)
                        raise RuntimeError("boom")

                    worker = DurableQueueWorker(
                        queue,
                        {"failing-probe": fail_if_called},
                        worker_id="queue-service-test",
                    )
                    # The task was already claimed above; the worker's normal
                    # run_once path is represented by the same failure state
                    # through a fresh task so the cancellation branch is tested
                    # without reaching into private worker fields.
                    queue.complete(task["id"], "queue-service-test", None)
                    canceled_task = queue.enqueue(
                        "failing-probe",
                        {
                            "job_id": job_id,
                            "session_id": "durable-cancel-session",
                            "job_store_path": str(jobs_path),
                        },
                        operation_key="analysis:cross-service:cancel-retry",
                    )
                    # Link the second task to the already canceled Job. The
                    # worker must settle it as terminal instead of reviving it.
                    store.handoff_to_queue(job_id, canceled_task["id"])
                    self.assertTrue(worker.run_once())
                    self.assertEqual("canceled", store.get(job_id)["status"])
                    self.assertEqual(QUEUE_FAILED, queue.get(canceled_task["id"])["status"])
                    self.assertEqual([], handler_calls)
                finally:
                    runner.shutdown(wait=True)
                    queue.close()
                    store.close()


if __name__ == "__main__":
    unittest.main()
