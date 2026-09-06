import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from infrastructure.compat import (
    cloud_login_enabled,
    env_from,
    optional_feature_enabled,
    request_user_id,
    workspace_hidden_dir,
    workspace_metadata_dir,
)
from agent.workflows.features import workflow_feature_flags
from data.workspace_metadata import WorkspaceMetadataStore
from LLM.prompt_cache import PromptCachePolicy, apply_prompt_cache_policy, stable_prompt_cache_key


ROOT = Path(__file__).resolve().parents[1]


class PfsIdentityMigrationTests(unittest.TestCase):
    def test_prompt_cache_keys_use_pfs_identity_and_keep_scope_isolation(self):
        key = stable_prompt_cache_key(
            provider="openai",
            model="test-model",
            workflow_stage="analysis",
            tools=[{"function": {"name": "get_schema"}}],
        )
        self.assertTrue(key.startswith("pfs-"))
        updated, metadata = apply_prompt_cache_policy(
            {},
            policy=PromptCachePolicy(enabled=True, mode="openai"),
            cache_key=key,
            user_id="user-1",
            workspace_id="workspace-1",
        )
        self.assertTrue(updated["prompt_cache_key"].startswith("pfs-"))
        self.assertTrue(metadata["scope_isolated"])

    def test_kimi_coding_cache_uses_supported_key_without_openai_retention(self):
        updated, metadata = apply_prompt_cache_policy(
            {},
            policy=PromptCachePolicy(enabled=True, mode="kimi"),
            cache_key="pfs-kimi-coding",
            user_id="user-1",
            workspace_id="workspace-1",
        )
        self.assertTrue(updated["prompt_cache_key"].startswith("pfs-"))
        self.assertNotIn("prompt_cache_retention", updated)
        self.assertEqual("kimi", metadata["mode"])
        self.assertTrue(metadata["scope_isolated"])

    def test_first_party_runtime_labels_use_pfs_names(self):
        checks = {
            ROOT / "infrastructure" / "cleanup.py": "name=\"pfs-cleanup\"",
            ROOT / "agent" / "hooks" / "engine.py": "thread_name_prefix=\"pfs-hook\"",
            ROOT / "agent" / "memory.py": "thread_name_prefix=\"pfs-memory\"",
            ROOT / "frontend" / "legacy" / "markdown.js": "[PFS]",
        }
        for source, marker in checks.items():
            with self.subTest(source=source):
                self.assertIn(marker, source.read_text(encoding="utf-8"))

    def test_pfs_environment_setting_and_default(self):
        environ = {"PFS_DATA_DIR": "/pfs"}
        self.assertEqual("/pfs", env_from(environ, "PFS_DATA_DIR", "/default"))
        self.assertEqual("/default", env_from({}, "PFS_DATA_DIR", "/default"))

    def test_cloud_login_is_enabled_by_default_only_on_cloud_host(self):
        self.assertTrue(cloud_login_enabled({"RAILWAY_PROJECT_ID": "project"}))
        self.assertFalse(cloud_login_enabled({"PFS_ENABLE_CLOUD_LOGIN": "0", "RAILWAY_PROJECT_ID": "project"}))
        self.assertTrue(cloud_login_enabled({
            "PFS_ENABLE_CLOUD_LOGIN": "1",
            "RAILWAY_PROJECT_ID": "project",
        }))

    def test_retained_optional_features_are_enabled_without_external_targets(self):
        self.assertTrue(optional_feature_enabled("HOOKS", {}))
        self.assertTrue(optional_feature_enabled("FEISHU_BOT", {}))
        self.assertFalse(optional_feature_enabled("HOOKS", {"PFS_ENABLE_HOOKS": "0"}))
        self.assertFalse(optional_feature_enabled("FEISHU_BOT", {"PFS_ENABLE_FEISHU_BOT": "off"}))
        self.assertTrue(optional_feature_enabled("HOOKS", {"PFS_ENABLE_HOOKS": "1"}))

    def test_teams_is_enabled_for_new_browser_state(self):
        state = (ROOT / "frontend" / "legacy" / "state.js").read_text(encoding="utf-8")
        settings = (ROOT / "frontend" / "legacy" / "app_settings.js").read_text(encoding="utf-8")
        self.assertIn('storage.get("teams_enabled", "1")', state)
        self.assertIn('pfsStorage.get("teams_enabled", "1")', settings)

    def test_embedding_configuration_exposes_pfs_primary_names(self):
        source = (ROOT / "Function" / "Knowledge" / "neural_embedder.py").read_text(encoding="utf-8")
        for name in ("PFS_MODEL_CACHE_DIR", "PFS_CLOUD_EMBED_URL", "PFS_CLOUD_EMBED_TOKEN", "PFS_CLOUD_EMBED_MODEL", "PFS_EMBED_MODE"):
            self.assertIn(name, source)

    def test_workflow_pfs_setting_is_read(self):
        with patch.dict(os.environ, {"PFS_WORKFLOW_FEATURES": "verifier_nodes=true"}, clear=False):
            flags = workflow_feature_flags()
            self.assertTrue(flags["verifier_nodes"])

    def test_pfs_request_identity_header_and_body_fallback(self):
        self.assertEqual(
            "pfs-user",
            request_user_id(
                {"X-PFS-User-ID": "pfs-user"},
                {"user_id": "body-user"},
            ),
        )
        self.assertEqual("body-user", request_user_id({}, {"user_id": "body-user"}))

    def test_new_workspace_hidden_directory_prefers_pfs_name(self):
        workdir = ROOT / "tests" / ".tmp-pfs-identity-new"
        self.assertEqual(workdir / ".pfs", workspace_hidden_dir(workdir, ".pfs"))

    def test_new_workspace_metadata_prefers_pfs_directory(self):
        workdir = ROOT / "tests" / ".tmp-pfs-meta-new"
        self.assertEqual(workdir / ".pfs", workspace_metadata_dir(workdir))

    def test_workspace_file_listing_excludes_new_pfs_metadata_directory(self):
        source = (ROOT / "data" / "workspace.py").read_text(encoding="utf-8")
        self.assertIn('\".pfs\"', source)
        self.assertNotIn("historical", source.lower())

    def test_workspace_metadata_store_writes_new_workspaces_to_pfs(self):
        with tempfile.TemporaryDirectory(prefix="pfs-meta-write-") as root, tempfile.TemporaryDirectory(prefix="pfs-index-") as index_root:
            store = WorkspaceMetadataStore(Path(index_root) / "index.json")
            metadata = store.open_or_create(root, name="PFS 测试工作区")
            self.assertEqual(Path(root) / ".pfs" / "workspace.json", store.metadata_path(root))
            self.assertTrue((Path(root) / ".pfs" / "workspace.json").exists())
            self.assertEqual("PFS 测试工作区", metadata.name)

    def test_runtime_identifiers_use_pfs_paths(self):
        sources = (
            ROOT / "infrastructure" / "logging_setup.py",
            ROOT / "LLM" / "prompt_cache.py",
            ROOT / "agent" / "skills" / "loader.py",
            ROOT / "agent" / "commands" / "loader.py",
        )
        for source in sources:
            with self.subTest(source=source):
                text = source.read_text(encoding="utf-8")
                self.assertIn("pfs", text.lower())

    def test_pfs_browser_storage_is_primary_for_high_frequency_keys(self):
        sources = (
            ROOT / "frontend" / "legacy" / "app.js",
            ROOT / "frontend" / "legacy" / "i18n.js",
            ROOT / "frontend" / "features" / "knowledge.js",
        )
        for source in sources:
            with self.subTest(source=source):
                self.assertIn("pfs", source.read_text(encoding="utf-8").lower())

    def test_dashboard_uses_pfs_storage_keys(self):
        source = (ROOT / "static" / "js" / "dashboard.js").read_text(encoding="utf-8")
        self.assertIn('const _LANG_KEY = "pfs_lang"', source)
        self.assertIn('const _SESSION_KEY = "pfs_session_id"', source)
        self.assertIn("_readPfsStorage", source)

    def test_new_chart_store_fallback_uses_pfs_directory(self):
        source = (ROOT / "api" / "state.py").read_text(encoding="utf-8")
        self.assertIn('Path("/tmp") / "pfs"', source)
        self.assertNotIn('Path("/tmp") / "baa"', source)

    def test_pfs_runtime_markers_are_primary(self):
        sources_and_markers = {
            ROOT / "frontend" / "entries" / "chat-app.js": "__pfsAppLoaded",
            ROOT / "frontend" / "legacy" / "app.js": "__pfsAppDelegationRegistered",
            ROOT / "frontend" / "features" / "sidebar.js": "__pfsPanelKeyCloseRegistered",
            ROOT / "frontend" / "core" / "overlay.js": "__pfsOverlayStack",
            ROOT / "scripts" / "verify-chat-bundle.mjs": "__pfsOverlayStack",
        }
        for source, marker in sources_and_markers.items():
            with self.subTest(source=source):
                self.assertIn(marker, source.read_text(encoding="utf-8"))

    def test_overlay_debug_reads_pfs_app_loaded_marker(self):
        source = (ROOT / "frontend" / "core" / "overlay.js").read_text(encoding="utf-8")
        self.assertIn("globalThis.__pfsAppLoaded", source)

    def test_core_runtime_modules_use_pfs_as_primary_namespace(self):
        sources = (
            ROOT / "frontend" / "core" / "event-bus.js",
            ROOT / "frontend" / "core" / "page-runtime.js",
            ROOT / "frontend" / "core" / "ui-registry.js",
            ROOT / "frontend" / "core" / "overlay.js",
        )
        for source in sources:
            with self.subTest(source=source):
                text = source.read_text(encoding="utf-8")
                self.assertIn("globalThis.PFS", text)

    def test_migrated_feature_modules_use_only_the_pfs_namespace(self):
        sources = (
            ROOT / "frontend" / "features" / "mcp.js",
            ROOT / "frontend" / "features" / "knowledge.js",
            ROOT / "frontend" / "features" / "workspace.js",
            ROOT / "frontend" / "features" / "teams.js",
            ROOT / "frontend" / "features" / "ui" / "chat-ui.js",
            ROOT / "frontend" / "legacy" / "autosave.js",
            ROOT / "frontend" / "legacy" / "pfs-report-preview.js",
        )
        for source in sources:
            with self.subTest(source=source):
                text = source.read_text(encoding="utf-8")
                self.assertIn("globalThis.PFS", text)

    def test_frontend_modules_use_only_the_pfs_namespace(self):
        legacy_global = "globalThis." + "B" + "AA"
        legacy_window = "window." + "B" + "AA"
        for source in (ROOT / "frontend").rglob("*.js"):
            with self.subTest(source=source):
                text = source.read_text(encoding="utf-8")
                self.assertNotIn(legacy_global, text)
                self.assertNotIn(legacy_window, text)

    def test_pfs_storage_keys_are_primary_for_theme_and_workflow_inputs(self):
        theme = (ROOT / "frontend" / "core" / "theme.js").read_text(encoding="utf-8")
        teams = (ROOT / "frontend" / "features" / "teams.js").read_text(encoding="utf-8")
        self.assertIn('const STORAGE_KEY = "pfs_theme"', theme)
        self.assertNotIn("LEGACY_STORAGE_KEY", theme)
        self.assertIn("pfs_workflow_inputs:", teams)
        self.assertNotIn("workflow_inputs", teams.split("pfs_workflow_inputs:", 1)[0])

    def test_sidebar_registration_locks_use_pfs_as_primary_marker(self):
        source = (ROOT / "frontend" / "features" / "sidebar.js").read_text(encoding="utf-8")
        self.assertIn("globalThis.__pfsPanelKeyCloseRegistered", source)
        self.assertIn("globalThis.__pfsAddSourceDropdownRegistered", source)

    def test_cloud_banner_uses_pfs_storage_key(self):
        source = (ROOT / "static" / "js" / "modules" / "cloud_banner.js").read_text(encoding="utf-8")
        self.assertIn("pfs_cloud_banner_dismissed", source)
        self.assertNotIn("sessionStorage.setItem('cloudBannerDismissed'", source)

    def test_ui_islands_do_not_initialize_legacy_namespace_directly(self):
        for relative in (
            "frontend/features/ui/mcp-ui.js",
            "frontend/features/ui/knowledge-ui.js",
            "frontend/features/ui/settings-ui.js",
        ):
            source = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(source=relative):
                self.assertIn("globalThis.PFS", source)

    def test_workspace_source_has_no_historical_directory_label(self):
        source = (ROOT / "frontend" / "legacy" / "i18n.js").read_text(encoding="utf-8")
        legacy_workspace_label = "." + "zhixi"
        self.assertNotIn(legacy_workspace_label, source.lower())

    def test_instruction_loader_uses_only_pfs_file_names(self):
        source = (ROOT / "agent" / "instructions.py").read_text(encoding="utf-8")
        legacy_instruction_name = "ZHI" + "XI"
        self.assertIn("PFS.md", source)
        self.assertNotIn(legacy_instruction_name, source)


if __name__ == "__main__":
    unittest.main()
