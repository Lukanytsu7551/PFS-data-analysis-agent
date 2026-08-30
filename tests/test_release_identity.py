import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "build-release.yml"
WINDOWS_INSTALLER_PATH = PROJECT_ROOT / "installer" / "setup.iss"
MACOS_BUILD_PATH = PROJECT_ROOT / "packaging" / "build_macos.sh"
WINDOWS_BUILD_PATH = PROJECT_ROOT / "packaging" / "build_windows.ps1"
SPEC_PATH = PROJECT_ROOT / "packaging" / "pfs_data_analysis_agent.spec"
DOCKERIGNORE_PATH = PROJECT_ROOT / ".dockerignore"
DOCKERFILE_PATH = PROJECT_ROOT / "Dockerfile"
CHAT_TEMPLATE_PATH = PROJECT_ROOT / "templates" / "agent_chat.html"
I18N_PATH = PROJECT_ROOT / "frontend" / "legacy" / "i18n.js"
LEGACY_PRODUCT_NAMES = ("BusinessAnalyticsAgent", "Business Analytics Agent")
LEGACY_COMMUNITY_MARKERS = (
    "991636855",
    "cdRNfS68u9BlYjJl",
    "EEG4Sw7tde",
    "qm.qq.com",
    "sidebar.community",
    "ov-community",
    "sb-footer-community",
)


def read_text(path):
    return path.read_text(encoding="utf-8")


def require_match(pattern, text, source):
    match = re.search(pattern, text, flags=re.MULTILINE)
    if match is None:
        raise AssertionError(f"Could not resolve release identity from {source}")
    return match.group(1)


class ReleaseIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = read_text(WORKFLOW_PATH)
        cls.windows_installer = read_text(WINDOWS_INSTALLER_PATH)
        cls.macos_build = read_text(MACOS_BUILD_PATH)
        cls.windows_build = read_text(WINDOWS_BUILD_PATH)
        cls.spec = read_text(SPEC_PATH)
        cls.dockerignore = read_text(DOCKERIGNORE_PATH)
        cls.dockerfile = read_text(DOCKERFILE_PATH)
        cls.chat_template = read_text(CHAT_TEMPLATE_PATH)
        cls.i18n = read_text(I18N_PATH)

    def test_workflow_uploads_exact_windows_installer_output(self):
        output_base = require_match(
            r"^OutputBaseFilename=([^\r\n]+)$",
            self.windows_installer,
            WINDOWS_INSTALLER_PATH,
        )
        expected_path = f"build/w/installer/{output_base}.exe"

        self.assertIn(expected_path, self.workflow)

    def test_workflow_uploads_exact_macos_dmg_outputs(self):
        dmg_name = require_match(
            r'^DMG_NAME="([^"]+)"$',
            self.macos_build,
            MACOS_BUILD_PATH,
        )
        expected_path = f"build/macos-package/dmg/{dmg_name.replace('$ARCH', '*')}"

        self.assertIn(expected_path, self.workflow)

    def test_release_sources_do_not_use_legacy_product_name(self):
        for path, content in (
            (WORKFLOW_PATH, self.workflow),
            (WINDOWS_INSTALLER_PATH, self.windows_installer),
            (MACOS_BUILD_PATH, self.macos_build),
        ):
            with self.subTest(path=path):
                for legacy_name in LEGACY_PRODUCT_NAMES:
                    self.assertNotIn(legacy_name, content)

    def test_release_notes_identify_pfs_and_disclose_signing_boundary(self):
        self.assertIn("Unsigned PFS Data Analysis Agent desktop test packages.", self.workflow)
        self.assertIn("not code-signed", self.workflow)
        self.assertIn("notarized", self.workflow)

    def test_packaging_pipeline_uses_pfs_runtime_variables(self):
        for path, content in (
            (MACOS_BUILD_PATH, self.macos_build),
            (WINDOWS_BUILD_PATH, self.windows_build),
            (SPEC_PATH, self.spec),
        ):
            with self.subTest(path=path):
                self.assertIn("PFS_STAGING_ROOT", content)
        for name in ("PFS_DATA_DIR", "PFS_NO_BROWSER", "PFS_ONEDIR_SELF_TEST", "PFS_CLEANUP_DISABLED"):
            self.assertIn(name, self.macos_build)
            self.assertIn(name, self.windows_build)

    def test_docker_context_excludes_local_secrets_state_and_reference_snapshot(self):
        required_patterns = {
            "secret_key",
            ".env",
            ".env.*",
            "LLM/llm_config.json",
            "data/datasource_config.json",
            "auth.db",
            "*.sqlite",
            "*.db",
            "Data-Analysis-Agent-main/",
            "outputs/",
            "_pfs-export-test/",
        }
        configured = {
            line.strip()
            for line in self.dockerignore.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertFalse(required_patterns - configured)

    def test_docker_image_declares_pfs_healthcheck(self):
        self.assertIn("HEALTHCHECK", self.dockerfile)
        self.assertIn("/api/health", self.dockerfile)
        self.assertIn("${PFS_PORT:-5001}", self.dockerfile)

    def test_user_facing_chat_surface_has_no_legacy_community_entry(self):
        for marker in LEGACY_COMMUNITY_MARKERS:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, self.chat_template)
                self.assertNotIn(marker, self.i18n)


if __name__ == "__main__":
    unittest.main()
