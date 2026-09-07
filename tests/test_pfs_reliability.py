import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

from agent.workflows.scheduler import WorkflowConcurrencyLimiter, WorkflowScheduler
from data.jobs_store import (
    JobsStore,
    STATUS_CANCELED,
    STATUS_FAILED,
    STATUS_RUNNING,
)
from data.workflow_run_store import WorkflowRunStore
from data.workflow_store import WorkflowStore


class PfsDurableRecoveryTests(unittest.TestCase):
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

                def execute(_node, _materials, _context):
                    attempts.append(len(attempts) + 1)
                    if len(attempts) == 1:
                        raise RuntimeError("temporary node failure")
                    return {"answer": "retry succeeded"}

                jobs = ImmediateJobs()
                scheduler = WorkflowScheduler(
                    workflow_store=workflow_store,
                    run_store=run_store,
                    job_runner=jobs,
                    executor=execute,
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
                node_attempts = [
                    (node["status"], node["attempt"])
                    for node in final["nodes"]
                ]
                self.assertEqual(
                    [("failed", 1), ("succeeded", 2)],
                    node_attempts,
                )
                events = run_store.list_events(first["run"]["id"])
                self.assertIn(
                    "workflow_node_retry_created",
                    [event["type"] for event in events],
                )
            finally:
                run_store.close()
                workflow_store.close()


if __name__ == "__main__":
    unittest.main()
