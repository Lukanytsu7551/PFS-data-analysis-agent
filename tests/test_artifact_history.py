import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api import create_app
from api.state import session_manager
from infrastructure.artifact_lifecycle import (
    list_registered_artifacts,
    register_artifact,
    registered_artifact_reference_preview,
    resolve_registered_artifact_path,
)
from infrastructure.paths import data_path


class ArtifactHistoryTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()
        self.sid = "artifact-history-session"
        self.other_sid = "artifact-history-other"
        session_manager.get_or_create(self.sid)
        session_manager.get_or_create(self.other_sid)

    def tearDown(self):
        session_manager.remove(self.sid)
        session_manager.remove(self.other_sid)

    def test_session_history_lists_details_and_downloads_without_cross_session_access(self):
        export_root = data_path("outputs", "exports")
        export_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="pfs-artifact-history-", dir=export_root) as temp_dir:
            temp = Path(temp_dir)
            registry = temp / "registry.json"
            artifact = temp / "sales.xlsx"
            artifact.write_bytes(b"pfs-artifact")
            with patch(
                "infrastructure.artifact_lifecycle._registry_path",
                return_value=registry,
            ):
                artifact_id = register_artifact(
                    artifact,
                    artifact_type="export",
                    session_id=self.sid,
                )

                listed = self.client.get(
                    f"/api/session/{self.sid}/lifecycle/artifacts"
                )
                self.assertEqual(200, listed.status_code)
                item = listed.get_json()["artifacts"][0]
                self.assertEqual(artifact_id, item["id"])
                self.assertEqual("xlsx", item["type"])
                self.assertEqual("sales.xlsx", item["filename"])
                self.assertNotIn("path", item)

                detail = self.client.get(
                    f"/api/session/{self.sid}/lifecycle/artifacts/{artifact_id}"
                )
                self.assertEqual(200, detail.status_code)
                downloaded = self.client.get(
                    f"/api/session/{self.sid}/lifecycle/artifacts/{artifact_id}/download"
                )
                self.assertEqual(200, downloaded.status_code)
                self.assertEqual(b"pfs-artifact", downloaded.data)
                downloaded.close()

                self.assertEqual(
                    404,
                    self.client.get(
                        f"/api/session/{self.other_sid}/lifecycle/artifacts/{artifact_id}"
                    ).status_code,
                )
                self.assertEqual(
                    404,
                    self.client.get(
                        f"/api/session/{self.other_sid}/lifecycle/artifacts/{artifact_id}/download"
                    ).status_code,
                )

    def test_workspace_result_keeps_lightweight_history_and_safe_resolution(self):
        with tempfile.TemporaryDirectory(prefix="pfs-workspace-artifact-") as temp_dir:
            workspace = Path(temp_dir).resolve()
            artifacts = workspace / "artifacts"
            artifacts.mkdir()
            target = artifacts / "workspace-report.docx"
            target.write_bytes(b"workspace-result")
            registry = workspace / "registry.json"
            with (
                patch(
                    "infrastructure.artifact_lifecycle._registry_path",
                    return_value=registry,
                ),
                patch(
                    "data.workspace.workspace_manager.root_for_workspace",
                    return_value=workspace,
                ),
            ):
                artifact_id = register_artifact(
                    target,
                    artifact_type="report",
                    session_id=self.sid,
                    workspace_id="workspace-1",
                )
                listed = list_registered_artifacts(session_id=self.sid)
                self.assertEqual("docx", listed[0]["type"])
                self.assertEqual("workspace-report.docx", listed[0]["filename"])
                resolved = resolve_registered_artifact_path(
                    artifact_id,
                    session_id=self.sid,
                )
                self.assertIsNotNone(resolved)
                self.assertEqual(target, resolved[1])
                preview = registered_artifact_reference_preview()
                self.assertNotIn(artifact_id, preview["unreferenced_ids"])


if __name__ == "__main__":
    unittest.main()
