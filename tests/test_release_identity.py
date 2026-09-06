import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "build-release.yml"
PUBLIC_LICENSE_PATH = PROJECT_ROOT / "LICENSE"
WINDOWS_INSTALLER_PATH = PROJECT_ROOT / "installer" / "setup.iss"
MACOS_BUILD_PATH = PROJECT_ROOT / "packaging" / "build_macos.sh"
WINDOWS_BUILD_PATH = PROJECT_ROOT / "packaging" / "build_windows.ps1"
SPEC_PATH = PROJECT_ROOT / "packaging" / "pfs_data_analysis_agent.spec"
DOCKERIGNORE_PATH = PROJECT_ROOT / ".dockerignore"
DOCKERFILE_PATH = PROJECT_ROOT / "Dockerfile"
CHAT_TEMPLATE_PATH = PROJECT_ROOT / "templates" / "agent_chat.html"
CHAT_BUNDLE_PATH = PROJECT_ROOT / "static" / "dist" / "chat-app.js"
I18N_PATH = PROJECT_ROOT / "frontend" / "legacy" / "i18n.js"
LEGACY_PRODUCT_NAMES = ("BusinessAnalyticsAgent", "Business Analytics Agent")
RETIRED_MODEL_LABELS = ("OpenAI / ChatGPT", "AtlasCloud")
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
        cls.chat_bundle = read_text(CHAT_BUNDLE_PATH)
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
        self.assertIn("SHA256SUMS.txt", self.workflow)

    def test_release_defaults_are_pfs_scoped_and_no_public_license_keeps_release_closed(self):
        self.assertIn('default: "0.1.0"', self.workflow)
        self.assertNotIn('default: "1.2.0"', self.workflow)
        self.assertIn("No public software license is present", self.workflow)
        self.assertIn("public_license_present", self.workflow)
        self.assertIn("release-disabled", self.workflow)
        self.assertIn(
            "needs.release-preflight.outputs.public_license_present == 'true'",
            self.workflow,
        )
        self.assertFalse(PUBLIC_LICENSE_PATH.exists())

    def test_release_matrix_targets_windows_and_apple_silicon_only(self):
        self.assertIn("name: Windows x64", self.workflow)
        self.assertIn("name: macOS Apple Silicon", self.workflow)
        self.assertNotIn("macos-15-intel", self.workflow)
        self.assertNotIn("macOS Intel x64", self.workflow)
        self.assertNotIn("macos-x64-package", self.workflow)

    def test_workflow_passes_version_and_refs_through_step_environment(self):
        expected_steps = (
            (
                "Resolve package version",
                "pwsh",
                "$version = $env:DISPATCH_VERSION",
                "$version = $env:REF_NAME -replace '^v', ''",
                "$version -notmatch",
            ),
            (
                "Resolve package version",
                "bash",
                'version="${DISPATCH_VERSION:-}"',
                'ref_name="${REF_NAME:-}"',
                'if [[ ! "$version" =~',
            ),
            (
                "Resolve release tag",
                "bash",
                'version="${DISPATCH_VERSION:-}"',
                'tag="${REF_NAME:-}"',
                'if [[ ! "$tag" =~',
            ),
        )
        seen_steps = set()
        for step_name, shell, env_version_read, env_ref_read, validation_read in expected_steps:
            marker = f"      - name: {step_name}\n        shell: {shell}\n"
            start = self.workflow.index(marker)
            end = self.workflow.find("\n      - name:", start + len(marker))
            step = self.workflow[start:] if end == -1 else self.workflow[start:end]
            script = step[step.index("        run: |") :]
            env_contract = (
                "        env:\n"
                "          DISPATCH_VERSION: ${{ github.event.inputs.version }}\n"
                "          REF_NAME: ${{ github.ref_name }}\n"
            )
            self.assertIn(env_contract, step)
            self.assertIn(env_version_read, script)
            self.assertIn(env_ref_read, script)
            self.assertIn(validation_read, script)
            self.assertNotIn("${{ github.event.inputs.version", script)
            self.assertNotIn("${{ github.ref_name }}", script)
            seen_steps.add((step_name, shell, env_version_read, env_ref_read, validation_read))

        self.assertEqual(seen_steps, set(expected_steps))

        release_step = self.workflow[self.workflow.index("      - name: Create release\n        env:\n") :]
        release_script = release_step[release_step.index("        run: |") :]
        self.assertIn("          TARGET_SHA: ${{ github.sha }}\n", release_step)
        self.assertNotIn("${{ github.sha }}", release_script)
        self.assertIn(
            'if [[ ! "$TARGET_SHA" =~ ^[0-9a-fA-F]{40}$ ]]; then',
            release_script,
        )

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

    def test_macos_builder_prefers_project_python_with_explicit_override(self):
        self.assertIn("PFS_BUILD_PYTHON", self.macos_build)
        self.assertIn("$PROJECT_ROOT/.venv/bin/python", self.macos_build)
        self.assertIn('"$PYTHON_BIN" -m PyInstaller', self.macos_build)

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
            "*-Agent-main/",
            "outputs/",
            "_pfs-export-test/",
            "build/",
            "installer/",
            "packaging/",
            "tests/",
            "business_canvas/",
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

    def test_chat_bundle_has_no_retired_model_cards(self):
        for label in RETIRED_MODEL_LABELS:
            with self.subTest(label=label):
                self.assertNotIn(label, self.chat_bundle)


if __name__ == "__main__":
    unittest.main()
