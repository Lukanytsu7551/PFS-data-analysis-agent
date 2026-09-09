import json
import os
import plistlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


from config import product_identity


class ProductIdentityTests(unittest.TestCase):
    def test_source_default_and_environment_override_remain_unchanged(self):
        with patch.object(product_identity.sys, "frozen", False, create=True):
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual("0.1.0", product_identity._resolve_product_version())
            with patch.dict(os.environ, {"PFS_PRODUCT_VERSION": "2.4.0-dev"}, clear=True):
                self.assertEqual("2.4.0-dev", product_identity._resolve_product_version())

    def test_macos_frozen_runtime_prefers_bundle_info_plist_over_environment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            contents = Path(temp_dir) / "PFS Data Analysis Agent.app" / "Contents"
            executable = contents / "MacOS" / "PFSDataAnalysisAgent"
            executable.parent.mkdir(parents=True)
            plist_path = contents / "Info.plist"
            plist_path.write_bytes(
                plistlib.dumps(
                    {
                        "PFSProductVersion": "2.4.0-rc.1",
                        "CFBundleShortVersionString": "2.4.0",
                    }
                )
            )

            with (
                patch.object(product_identity.sys, "frozen", True, create=True),
                patch.object(product_identity.sys, "platform", "darwin"),
                patch.object(product_identity.sys, "executable", str(executable)),
                patch.dict(os.environ, {"PFS_PRODUCT_VERSION": "9.9.9"}, clear=True),
            ):
                self.assertEqual("2.4.0-rc.1", product_identity._resolve_product_version())

    def test_other_frozen_runtime_uses_packaged_metadata_before_environment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            metadata_path = Path(temp_dir) / "pfs-product-metadata.json"
            metadata_path.write_text(
                json.dumps({"product_version": "3.1.0"}),
                encoding="utf-8",
            )
            with (
                patch.object(product_identity.sys, "frozen", True, create=True),
                patch.object(product_identity.sys, "platform", "win32"),
                patch.object(product_identity.sys, "_MEIPASS", temp_dir, create=True),
                patch.dict(os.environ, {"PFS_PRODUCT_VERSION": "9.9.9"}, clear=True),
            ):
                self.assertEqual("3.1.0", product_identity._resolve_product_version())

    def test_frozen_runtime_without_embedded_metadata_ignores_environment_version(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(product_identity.sys, "frozen", True, create=True),
                patch.object(product_identity.sys, "platform", "win32"),
                patch.object(product_identity.sys, "_MEIPASS", temp_dir, create=True),
                patch.dict(os.environ, {"PFS_PRODUCT_VERSION": "9.9.9"}, clear=True),
            ):
                self.assertEqual(
                    "0.1.0",
                    product_identity._resolve_product_version(),
                )


if __name__ == "__main__":
    unittest.main()
