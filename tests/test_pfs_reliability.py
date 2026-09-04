import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

from agent.workflows.scheduler import WorkflowConcurrencyLimiter, WorkflowScheduler
from data.jobs_store import (
    JobsStore,
    STATUS_CANCELED,
    STATUS_FAILED,
    STATUS_RUNNING,
)
from data.workflow_run_store import WorkflowRunStore
from data.workflow_store import WorkflowStore
from agent.workflows.models import NodeRunStatus, RunStatus


class PfsDurableRecoveryTests(unittest.TestCase):
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
            "id": "pending", "status": NodeRunStatus.READY.value,
            "input_tokens": 0, "output_tokens": 0, "cost_usd": None,
        }
        measured = {
            "id": "measured", "status": NodeRunStatus.SUCCEEDED.value,
            "input_tokens": 80, "output_tokens": 20, "cost_usd": 0.001,
        }
        store = FakeRunStore([pending, measured])
        scheduler.run_store = store
        self.assertTrue(scheduler._expire_cost_budget("run-1", {"limits": {"max_total_cost_usd": 0.001}}))
        self.assertEqual([
            ("pending", NodeRunStatus.CANCELED, "workflow cost budget exceeded"),
        ], store.transitions)
        self.assertEqual(RunStatus.FAILED, store.run_transition[1])
        self.assertEqual("workflow_cost_budget_exceeded", store.run_transition[2])

    def test_workflow_cost_budget_does_not_fake_zero_for_unknown_measured_cost(self):
        class FakeRunStore:
            def __init__(self):
                self.transitions = []

            def list_node_runs(self, _run_id):
                return [{
                    "id": "measured", "status": NodeRunStatus.SUCCEEDED.value,
                    "input_tokens": 80, "output_tokens": 20, "cost_usd": None,
                }]

            def transition_node(self, *args, **kwargs):
                self.transitions.append((args, kwargs))

            def transition_run(self, *args, **kwargs):
                self.transitions.append((args, kwargs))

        scheduler = WorkflowScheduler.__new__(WorkflowScheduler)
        scheduler.run_store = FakeRunStore()
        self.assertFalse(scheduler._expire_cost_budget("run-unknown", {"limits": {"max_total_cost_usd": 0.001}}))
        self.assertEqual([], scheduler.run_store.transitions)

    def test_reopen_closes_interrupted_jobs_and_records_recovery_events(self):
        with tempfile.TemporaryDirectory(prefix="pfs-jobs-recovery-") as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            first_store = JobsStore(db_path)
            failed_job = first_store.create("recovery-session", "analysis")
            canceled_job = first_store.create("recovery-session", "export")
            self.assertTrue(first_store.mark_queued(failed_job["id"]))
            self.assertTrue(first_store.mark_started(failed_job["id"]))
            self.assertTrue(first_store.mark_queued(canceled_job["id"]))
            self.assertTrue(first_store.mark_started(canceled_job["id"]))
            self.assertTrue(first_store.mark_canceling(canceled_job["id"]))
            first_store._conn.close()

            reopened_store = JobsStore(db_path)
            recovered_failed = reopened_store.get(failed_job["id"])
            recovered_canceled = reopened_store.get(canceled_job["id"])

            self.assertEqual(STATUS_FAILED, recovered_failed["status"])
            self.assertIn("restarted", recovered_failed["error"])
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
                STATUS_RUNNING,
                next(
                    event["status"]
                    for event in events
                    if event["job_id"] == failed_job["id"]
                    and event["type"] == "job_started"
                ),
            )

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
                    "nodes": [{
                        "node_id": "step",
                        "type": "agent",
                        "agent_profile_id": profile["id"],
                        "output_contract": ["answer"],
                        "max_attempts": 2,
                    }],
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
                    on_run_terminal=lambda run_id, status: terminal_runs.append(
                        (run_id, status)
                    ),
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
                node_attempts = [
                    (node["status"], node["attempt"])
                    for node in final["nodes"]
                ]
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
            run_store = WorkflowRunStore(db_path, workspace_id)
            try:
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
                    "nodes": [{
                        "node_id": "step",
                        "type": "agent",
                        "agent_profile_id": profile["id"],
                        "output_contract": ["answer"],
                    }],
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
                pause_event = next(
                    item for item in paused["events"]
                    if item["type"] == "workflow_run_paused"
                )
                self.assertEqual("running", pause_event["previous_status"])
                self.assertEqual("maintenance window", pause_event["reason"])

                run_store.close()
                workflow_store.close()

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
                    reopened_workflow_store.close()
            except Exception:
                # The first pair is closed above on the successful restart
                # path; this keeps failure cleanup safe without masking the
                # original assertion.
                try:
                    run_store.close()
                finally:
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
                    "edges": [{
                        "from_node": "prepare",
                        "to_node": "deliver",
                        "type": "approval",
                    }],
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
            run_store = WorkflowRunStore(workflow_db, workspace_id)
            jobs_store = JobsStore(jobs_db)
            try:
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
                    "nodes": [{
                        "node_id": "step",
                        "type": "agent",
                        "agent_profile_id": profile["id"],
                        "output_contract": ["answer"],
                        "side_effects": ["read_data"],
                        "max_attempts": 2,
                    }],
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
                workflow_store.close()
                jobs_store.close()

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
                    reopened_workflow_store.close()
                    reopened_jobs_store.close()
            finally:
                # The successful branch closes the first pair before opening
                # the replacement; close() is intentionally idempotent here.
                try:
                    run_store.close()
                except Exception:
                    pass
                try:
                    workflow_store.close()
                except Exception:
                    pass
                try:
                    jobs_store.close()
                except Exception:
                    pass

    def test_restart_recovery_blocks_irreversible_side_effect_replay(self):
        class FakeRunStore:
            workspace_id = "restart-safety-workspace"

            def __init__(self):
                self.nodes = [{
                    "id": "node-run-1",
                    "node_id": "export",
                    "status": NodeRunStatus.RUNNING.value,
                    "job_id": "job-restarted",
                    "iteration": 1,
                    "attempt": 1,
                    "agent_profile_id": "profile",
                }]
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
                "nodes": [{
                    "node_id": "export",
                    "type": "export",
                    "side_effects": ["export_file"],
                }],
                "edges": [],
            },
        )
        self.assertEqual(
            [(
                "node-run-1",
                NodeRunStatus.FAILED,
                "workflow side-effect replay blocked after restart; manual review required",
            )],
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
                self.nodes = [{
                    "id": "node-run-1",
                    "node_id": "export",
                    "status": NodeRunStatus.RUNNING.value,
                    "job_id": "job-restarted",
                    "iteration": 1,
                    "attempt": 1,
                    "agent_profile_id": "profile",
                }]
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
                "nodes": [{
                    "node_id": "export",
                    "type": "export",
                    "side_effects": ["export_file"],
                }],
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
