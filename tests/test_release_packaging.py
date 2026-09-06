import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGING_ROOT = PROJECT_ROOT / "packaging"
if str(PACKAGING_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGING_ROOT))

from audit_artifact import audit
from build_manifest import build_staging
from package_policy import classify_path


class ReleasePackagingTests(unittest.TestCase):
    def test_source_macos_metadata_is_excluded_before_staging(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source"
            destination = Path(temp_dir) / "staging"
            (source / "agent").mkdir(parents=True)
            (source / "app.py").write_text("print('ok')\n", encoding="utf-8")
            (source / "agent" / "runtime.py").write_text("VALUE = 1\n", encoding="utf-8")
            (source / "agent" / ".DS_Store").write_bytes(b"metadata")

            manifest = build_staging(source, destination)

            self.assertTrue((destination / "agent" / "runtime.py").is_file())
            self.assertFalse((destination / "agent" / ".DS_Store").exists())
            self.assertIn("agent/.DS_Store (OS metadata)", manifest["excluded"])
            self.assertTrue(audit(destination)["ok"])

    def test_staged_metadata_remains_forbidden_if_it_bypasses_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            package = Path(temp_dir) / "package"
            package.mkdir()
            (package / ".DS_Store").write_bytes(b"metadata")

            report = audit(package)

            self.assertFalse(report["ok"])
            self.assertIn(".DS_Store: local state or secret filename", report["findings"])

    def test_only_reviewed_fixtures_are_allowed_inside_frozen_resources(self):
        self.assertEqual(
            ("allow", "reviewed offline report fixture"),
            classify_path("Contents/Resources/data/fixtures/pfs_sales.csv"),
        )
        self.assertEqual(
            "deny",
            classify_path("Contents/Resources/data/fixtures/customer_export.csv")[0],
        )


if __name__ == "__main__":
    unittest.main()
