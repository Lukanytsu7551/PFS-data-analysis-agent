import unittest

from pfs_agent.runs import (
    AnalysisRunAlreadyActive,
    AnalysisRunCanceled,
    AnalysisRunRegistry,
)


class AnalysisRunRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = AnalysisRunRegistry()

    def test_cancel_is_scoped_to_session_and_run(self):
        self.registry.begin("session-a", "run-1")

        self.assertFalse(self.registry.request_cancel("session-b", "run-1"))
        self.registry.checkpoint("session-a", "run-1")
        self.assertTrue(self.registry.request_cancel("session-a", "run-1"))
        with self.assertRaises(AnalysisRunCanceled):
            self.registry.checkpoint("session-a", "run-1")

    def test_duplicate_active_run_is_rejected_and_finish_is_idempotent(self):
        self.registry.begin("session-a", "run-1")
        with self.assertRaises(AnalysisRunAlreadyActive):
            self.registry.begin("session-a", "run-1")

        self.registry.finish("session-a", "run-1")
        self.registry.finish("session-a", "run-1")
        self.assertFalse(self.registry.is_active("session-a", "run-1"))

    def test_cancel_is_rejected_after_governance_commit_starts(self):
        self.registry.begin("session-a", "run-1")
        self.registry.begin_commit("session-a", "run-1")

        self.assertFalse(self.registry.request_cancel("session-a", "run-1"))
        self.registry.checkpoint("session-a", "run-1")


if __name__ == "__main__":
    unittest.main()
