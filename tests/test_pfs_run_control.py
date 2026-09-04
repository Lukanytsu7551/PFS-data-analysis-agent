import multiprocessing
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pfs_agent.runs as runs_module
from pfs_agent.runs import (
    AnalysisRunError,
    AnalysisRunAlreadyActive,
    AnalysisRunCanceled,
    AnalysisRunRegistry,
    PersistentAnalysisRunRegistry,
)


def _persistent_run_owner(path, ready, release):
    registry = PersistentAnalysisRunRegistry(path)
    registry.begin("session-cross-process", "run-cross-process")
    ready.put("begun")
    release.get(timeout=15)
    try:
        registry.checkpoint("session-cross-process", "run-cross-process")
    except AnalysisRunCanceled:
        ready.put("canceled")
    finally:
        registry.finish("session-cross-process", "run-cross-process")


def _persistent_run_owner_crashes(path, ready):
    registry = PersistentAnalysisRunRegistry(path)
    registry.begin("session-recover", "run-recover")
    ready.put("begun")


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

    def test_sqlite_registry_propagates_cancel_across_processes(self):
        context_name = "fork" if "fork" in multiprocessing.get_all_start_methods() else "spawn"
        context = multiprocessing.get_context(context_name)
        with TemporaryDirectory() as directory:
            path = str(Path(directory) / "analysis-runs.sqlite3")
            ready = context.Queue()
            release = context.Queue()
            owner = context.Process(target=_persistent_run_owner, args=(path, ready, release))
            owner.start()
            try:
                self.assertEqual("begun", ready.get(timeout=20))
                canceller = PersistentAnalysisRunRegistry(path)
                self.assertTrue(canceller.request_cancel("session-cross-process", "run-cross-process"))
                release.put(True)
                self.assertEqual("canceled", ready.get(timeout=20))
            finally:
                owner.join(timeout=20)
                if owner.is_alive():
                    owner.terminate()
                    owner.join(timeout=5)
            self.assertEqual(0, owner.exitcode)
            self.assertFalse(canceller.is_active("session-cross-process", "run-cross-process"))

    def test_sqlite_registry_reclaims_run_after_owner_process_crashes(self):
        context_name = "fork" if "fork" in multiprocessing.get_all_start_methods() else "spawn"
        context = multiprocessing.get_context(context_name)
        with TemporaryDirectory() as directory:
            path = str(Path(directory) / "analysis-runs.sqlite3")
            ready = context.Queue()
            owner = context.Process(target=_persistent_run_owner_crashes, args=(path, ready))
            owner.start()
            self.assertEqual("begun", ready.get(timeout=20))
            owner.join(timeout=20)
            self.assertEqual(0, owner.exitcode)

            recovered = PersistentAnalysisRunRegistry(path)
            recovered.begin("session-recover", "run-recover")
            self.assertTrue(recovered.is_active("session-recover", "run-recover"))
            recovered.finish("session-recover", "run-recover")

    def test_registry_backend_factory_is_explicit(self):
        with TemporaryDirectory() as directory:
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("PFS_RUN_REGISTRY_BACKEND", None)
                self.assertIsInstance(runs_module._build_analysis_run_registry(), AnalysisRunRegistry)
            with patch.dict(os.environ, {"PFS_RUN_REGISTRY_BACKEND": "sqlite"}):
                with patch(
                    "infrastructure.paths.data_path",
                    return_value=Path(directory) / "analysis-runs.sqlite3",
                ):
                    registry = runs_module._build_analysis_run_registry()
            self.assertIsInstance(registry, PersistentAnalysisRunRegistry)
            with patch.dict(os.environ, {"PFS_RUN_REGISTRY_BACKEND": "redis"}):
                with self.assertRaisesRegex(AnalysisRunError, "must be memory or sqlite"):
                    runs_module._build_analysis_run_registry()


if __name__ == "__main__":
    unittest.main()
