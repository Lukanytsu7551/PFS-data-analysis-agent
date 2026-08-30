import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from data.session import ChatSession
from filehistory import FileHistory


class PfsWorkspaceCheckpointTests(unittest.TestCase):
    def test_checkpoint_survives_new_history_instance_and_restores_file_and_session(self):
        with tempfile.TemporaryDirectory(prefix="pfs-checkpoint-") as temp_dir:
            root = Path(temp_dir).resolve()
            meta_dir = root / ".pfs"
            meta_dir.mkdir()
            runtime = SimpleNamespace(
                workspace_id="pfs-workspace-checkpoint",
                workdir=root,
                meta_dir=meta_dir,
                permission="read_write",
            )

            def resolve_tool_path(value, *, write=False):
                candidate = (root / value).resolve()
                candidate.relative_to(root)
                if write and runtime.permission != "read_write":
                    raise PermissionError("read-only")
                return candidate

            runtime.resolve_tool_path = resolve_tool_path
            report = root / "report.csv"
            report.write_text("region,total\n华东,100\n", encoding="utf-8")

            original = ChatSession(session_id="checkpoint-session")
            original.history = [{"role": "user", "content": "原始问题"}]
            history = FileHistory(runtime, original.session_id)
            snapshot = history.begin_snapshot("生成报表", original.capture_rewind_state())
            report_history = history.track_before_write(report)
            self.assertTrue(report_history)

            report.write_text("region,total\n华南,999\n", encoding="utf-8")
            original.history.append({"role": "assistant", "content": "错误结果"})
            history.finalize_snapshot(snapshot["id"])

            reopened = FileHistory(runtime, original.session_id)
            snapshots = reopened.list_snapshots()
            self.assertEqual(snapshot["id"], snapshots[0]["id"])
            self.assertEqual("completed", snapshots[0]["status"])
            self.assertEqual(1, snapshots[0]["file_count"])

            restored = ChatSession(session_id=original.session_id)
            result = reopened.rewind(snapshot["id"], "code_and_conversation", restored)
            self.assertEqual(["report.csv"], result["changed_files"])
            self.assertTrue(result["conversation_restored"])
            self.assertEqual("region,total\n华东,100\n", report.read_text(encoding="utf-8"))
            self.assertEqual(original.history[:1], restored.history)
            self.assertEqual("code_and_conversation", reopened.list_snapshots()[0]["last_rewind_mode"])

    def test_read_only_runtime_rejects_file_restore(self):
        with tempfile.TemporaryDirectory(prefix="pfs-checkpoint-readonly-") as temp_dir:
            root = Path(temp_dir).resolve()
            meta_dir = root / ".pfs"
            meta_dir.mkdir()
            runtime = SimpleNamespace(
                workspace_id="pfs-workspace-readonly",
                workdir=root,
                meta_dir=meta_dir,
                permission="read_only",
            )
            report = root / "report.csv"
            report.write_text("region,total\n华东,100\n", encoding="utf-8")
            def resolve_tool_path(value, *, write=False):
                if write and runtime.permission != "read_write":
                    raise PermissionError("read-only")
                return (root / value).resolve()

            runtime.resolve_tool_path = resolve_tool_path
            history = FileHistory(runtime, "readonly-session")
            snapshot = history.begin_snapshot("只读回退", {})
            # Snapshot capture is allowed independently; the runtime permission
            # gate must reject the actual file restore.
            runtime.permission = "read_write"
            self.assertTrue(history.track_before_write(report))
            report.write_text("region,total\n华南,999\n", encoding="utf-8")
            history.finalize_snapshot(snapshot["id"])
            with self.assertRaises(PermissionError):
                runtime.permission = "read_only"
                history.rewind(snapshot["id"], "code_only", ChatSession(session_id="readonly-session"))


if __name__ == "__main__":
    unittest.main()
