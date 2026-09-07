import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / "templates" / "agent_chat.html").read_text(encoding="utf-8")
THEME = (ROOT / "static" / "css" / "pfs-theme.css").read_text(encoding="utf-8")
SIDEBAR = (ROOT / "frontend" / "features" / "sidebar.js").read_text(encoding="utf-8")
APP = (ROOT / "frontend" / "legacy" / "app.js").read_text(encoding="utf-8")
I18N = (ROOT / "frontend" / "legacy" / "i18n.js").read_text(encoding="utf-8")
ICONS = (ROOT / "frontend" / "core" / "icons.js").read_text(encoding="utf-8")
SKILLS = (ROOT / "frontend" / "features" / "skills.js").read_text(encoding="utf-8")
SLASH = (ROOT / "frontend" / "features" / "slash.js").read_text(encoding="utf-8")
CHAT_STREAM = (ROOT / "frontend" / "features" / "chat-stream.js").read_text(encoding="utf-8")
SESSIONS = (ROOT / "frontend" / "legacy" / "sessions.js").read_text(encoding="utf-8")
MSG = (ROOT / "frontend" / "legacy" / "msg.js").read_text(encoding="utf-8")
MODELS = (ROOT / "frontend" / "features" / "models.js").read_text(encoding="utf-8")
SETTINGS_UI = (ROOT / "frontend" / "features" / "ui" / "settings-ui.js").read_text(encoding="utf-8")
LLM_CONFIG = (ROOT / "LLM" / "llm_config_manager.py").read_text(encoding="utf-8")
DASHBOARD = (ROOT / "templates" / "dashboard.html").read_text(encoding="utf-8")
DASHBOARD_CSS = (ROOT / "static" / "css" / "dashboard.css").read_text(encoding="utf-8")
INDEX = (ROOT / "index.html").read_text(encoding="utf-8")
LOGIN = (ROOT / "templates" / "login.html").read_text(encoding="utf-8")
MARK = (ROOT / "static" / "Images" / "pfs-mark.svg").read_text(encoding="utf-8")
PRODUCTION_UI_FILES = (
    ROOT / "templates" / "agent_chat.html",
    ROOT / "templates" / "dashboard.html",
    *sorted((ROOT / "frontend").rglob("*.js")),
    *sorted((ROOT / "static" / "css").rglob("*.css")),
)


class QuietHarnessUiContractTests(unittest.TestCase):
    def test_template_locks_rail_and_quiet_welcome_copy(self):
        self.assertIn('id="sidebar-rail-toggle"', TEMPLATE)
        self.assertIn('data-action="toggleSidebarRail"', TEMPLATE)
        self.assertIn('aria-controls="app-sidebar"', TEMPLATE)
        self.assertIn('aria-expanded="true"', TEMPLATE)
        self.assertIn("连接数据源，开始分析", TEMPLATE)
        self.assertIn("口径、证据、交付可回溯", TEMPLATE)
        self.assertEqual(TEMPLATE.count('data-action="fillHint"'), 10)
        self.assertIn('data-action="openJobHistory"', TEMPLATE)
        self.assertIn('data-action="openSchemaView"', TEMPLATE)
        self.assertIn('aria-label="检查更新"', TEMPLATE)
        footer_button = re.search(
            r'<button class="sb-iconbtn sb-footer-update"([^>]*)>', TEMPLATE
        )
        self.assertIsNotNone(footer_button)
        self.assertNotIn("data-i18n=", footer_button.group(1))

        header = re.search(r'<div class="chat-header-title"[^>]*>(.*?)</div>', TEMPLATE, re.S)
        self.assertIsNotNone(header)
        self.assertNotRegex(header.group(1), r"[\U0001F300-\U0001FAFF]")
        self.assertNotIn("💬 对话分析", TEMPLATE)
        self.assertIn("filename='dist/chat-app.js')", TEMPLATE)
        self.assertNotRegex(TEMPLATE, r"filename='dist/chat-app\.js'[^}]*\bv=")
        self.assertRegex(
            TEMPLATE,
            r'<button class="sb-status-row sb-status-row--select"[^>]*id="model-picker-trigger-sidebar"',
        )
        self.assertIn('data-action="openModelSettings"', TEMPLATE)
        self.assertIn('data-pfs-icon="chevronLeft"', TEMPLATE)
        self.assertNotIn('data-pfs-icon="panelLeft"', TEMPLATE)

    def test_theme_contains_quiet_harness_surface_contract(self):
        for token in (
            "--color-primary: #002fa7",
            "--workspace-sidebar: 280px",
            "--workspace-rail: 56px",
            "body.pfs-sidebar-rail-collapsed",
            "box-sizing: border-box;\n  padding: 0;\n  gap: 0;",
            ".sb-main {\n  width: 100%;\n  min-width: 280px;\n  box-sizing: border-box;",
            "width: 56px !important;\n  min-width: 56px !important;\n  box-sizing: border-box;",
            "width: 100%;\n  box-sizing: border-box;\n  align-items: center;",
            "width: 40px;\n  height: 40px;\n  box-sizing: border-box;",
            ".overlay:has(.job-history-modal)",
            ".composer-shell",
            ":focus-visible",
            "@media (prefers-reduced-motion: reduce)",
            ".composer-group-label",
            ".pfs-icon-slot",
        ):
            self.assertIn(token, THEME)
        self.assertIn("background-image: none !important", THEME)

    def test_public_surfaces_use_the_blue_white_icon_system(self):
        for token in (
            "--color-icon: #145dcc",
            "--color-sidebar-bg: #ffffff",
            ".pfs-icon,",
            ".pfs-icon-slot > .pfs-icon",
            ".pfs-side-surface",
        ):
            self.assertIn(token, THEME)
        for token in ("Blue-white dashboard surface", "--db-nav-bg:  #ffffff", ".db-icon"):
            self.assertIn(token, DASHBOARD_CSS)
        self.assertIn("--primary: #0b5bd3", INDEX)
        self.assertIn("--pfs-blue:#2f6fe4", LOGIN)
        self.assertIn('fill="#0b5bd3"', MARK)
        self.assertNotRegex(DASHBOARD, r"[\U0001F300-\U0001FAFF]")

    def test_sidebar_rail_has_persistence_and_motion_guards(self):
        for token in (
            '"pfs.sidebar.collapsed"',
            "localStorage",
            "pfs-sidebar-rail-collapsed",
            "aria-expanded",
            "getAnimations",
            "closeSidebarSurfaces()",
            "rememberSurfaceTrigger",
            "restoreSurfaceFocus",
            "preventScroll",
            "function initSidebarRail",
            "function toggleSidebarRail",
        ):
            self.assertIn(token, SIDEBAR)

    def test_dynamic_high_frequency_icons_use_the_registry(self):
        self.assertIn('import { iconSpan, svgMarkup }', SKILLS)
        self.assertIn('svgMarkup("check"', SKILLS)
        self.assertNotIn("<svg", SKILLS)

    def test_skill_editor_stays_above_close_backdrop_and_has_edit_target(self):
        """The nested editor must not lose clicks to the side-surface backdrop."""
        self.assertIn("document.body.appendChild(overlay)", SKILLS)
        self.assertIn("panel.getBoundingClientRect().right", SKILLS)
        self.assertIn('data-skill-view="true"', SKILLS)

    def test_welcome_skill_shortcuts_use_skill_activation(self):
        """Skill shortcuts must not be sent as unknown slash commands."""
        self.assertIn('data-i18n="hint.ppt"', TEMPLATE)
        self.assertIn('data-i18n="hint.dashboard"', TEMPLATE)
        self.assertIn("function getSkill(name)", SKILLS)
        self.assertIn("getSkill, selectSkill", SKILLS)
        self.assertIn("skill: command ? null : getSkill(name)", SLASH)
        self.assertIn("pfs()?.skills?.selectSkill?.(skill.name)", SLASH)
        self.assertIn("text = parsed.arguments", CHAT_STREAM)
        self.assertIn("if (parsed.skill)", CHAT_STREAM)

    def test_app_registers_and_initializes_sidebar_rail(self):
        self.assertIn("toggleSidebarRail: () => sidebar.toggleSidebarRail()", APP)
        self.assertIn("sidebar.initSidebarRail();", APP)

    def test_i18n_is_the_single_quiet_copy_source(self):
        for token in (
            '"header.title": "数据分析工作台"',
            '"header.subtitle": "口径、证据、交付可回溯"',
            '"welcome.title": "连接数据源，开始分析"',
            '"header.title": "Data analysis workbench"',
            '"header.subtitle": "Definitions, evidence, and deliverables stay traceable."',
            '"welcome.title": "Connect a data source, start analysis"',
        ):
            self.assertIn(token, I18N)
        self.assertIn("连接数据文件、业务数据库或上传 Excel", I18N)
        self.assertIn("Connect a data file, business database, or upload Excel", I18N)
        self.assertNotIn("applyQuietHarnessCopy", APP)
        self.assertNotIn("DOMContentLoaded, applyQuietHarnessCopy", APP)
        self.assertNotIn('langchange", applyQuietHarnessCopy', APP)

    def test_composer_keeps_model_skill_and_execution_controls(self):
        start = TEMPLATE.index('<div class="composer-toolbar"')
        end = TEMPLATE.index("<!-- ══ MODALS", start)
        toolbar = TEMPLATE[start:end]

        for group in (
            'class="composer-group composer-group-context"',
            'class="composer-toolbar-actions composer-group composer-group-execution"',
        ):
            self.assertIn(group, toolbar)
        self.assertNotIn('class="composer-group composer-group-analysis"', toolbar)

        for action in (
            "openModelPicker",
            "openComposerSkillPicker",
            "onSendOrStop",
        ):
            self.assertRegex(toolbar, rf'data-action="{action}"')

        self.assertIn('id="composer-expand-btn"', toolbar)
        self.assertIn('id="send-btn"', toolbar)
        self.assertNotIn('id="pfs-chat-mode-toggle"', toolbar)
        self.assertNotIn('id="composer-permission-wrap"', toolbar)
        self.assertNotIn('id="token-bar-wrap"', toolbar)
        self.assertEqual(toolbar.count('data-composer-group='), 2)
        self.assertEqual(toolbar.count('data-composer-tool='), 4)
        self.assertGreaterEqual(toolbar.count("data-pfs-icon="), 5)
        self.assertEqual(TEMPLATE.count("<svg"), 0)
        self.assertNotRegex(toolbar, r'[\U0001F300-\U0001FAFF]')
        self.assertNotRegex(toolbar, r'[▦⤢⤡⌄▾⛶↩☀🌙＋×]')
        for icon_name in ("cpu", "spark", "expand", "collapse", "arrowUp", "stop"):
            self.assertIn(f'data-pfs-icon="{icon_name}"', toolbar)
            self.assertRegex(ICONS, rf'(?m)^  {icon_name}:')

        self.assertNotIn('class="composer-group-label"', toolbar)
        self.assertIn('aria-label="模型和技能选择"', toolbar)
        self.assertIn('aria-label="输入操作"', toolbar)

        for selector in (
            "composer-model",
            "composer-skill",
            "composer-expand-btn",
            "send-btn",
        ):
            match = re.search(rf'<(?:button|div)[^>]*class="[^"]*{selector}[^"]*"[^>]*>', toolbar)
            self.assertIsNotNone(match, selector)
            self.assertTrue(
                "aria-label=" in match.group(0) or "title=" in match.group(0),
                selector,
            )

        for token in (
            ".composer-toolbar-main {",
            ".composer-icon-box,",
            ".composer-chev,",
            "flex: 0 0 18px",
            "text-overflow: ellipsis",
            "@media (min-width: 961px) and (max-width: 1280px)",
        ):
            self.assertIn(token, THEME)

    def test_skills_sidebar_is_catalog_and_composer_owns_selection(self):
        self.assertIn('id="composer-skill-picker"', TEMPLATE)
        self.assertIn('id="composer-skill-list"', TEMPLATE)
        self.assertIn('id="skill-upload-input"', TEMPLATE)
        self.assertIn("内置技能", SKILLS)
        self.assertIn("个人技能", SKILLS)
        self.assertIn("默认全部启用", SKILLS)
        self.assertIn("renderComposerPicker", SKILLS)
        self.assertIn("openComposerSkillPicker", APP)
        self.assertIn("pickSkillUpload", APP)
        start = SKILLS.index("function onKeyDown(event)")
        end = SKILLS.index("// ── Skill detail", start)
        self.assertNotIn("selectSkill", SKILLS[start:end])

    def test_reset_and_saved_session_flows_are_failure_safe(self):
        self.assertIn("if (!wrap || !fill || !label) return;", MSG)
        self.assertIn("if (!response.ok || !data?.session_id)", CHAT_STREAM)
        self.assertIn("Promise.allSettled", CHAT_STREAM)
        self.assertIn("ui?.isVue", SESSIONS)
        self.assertIn("window.confirm", SESSIONS)
        self.assertIn("if (!r.ok || !d || d.ok === false || d.error)", SESSIONS)

    def test_navigation_and_model_catalog_match_the_distilled_workbench(self):
        status_start = TEMPLATE.index('<section class="sb-status">')
        status_end = TEMPLATE.index("</section>", status_start)
        status = TEMPLATE[status_start:status_end]
        for control in (
            'id="session-row"', 'id="model-picker-trigger-sidebar"', 'id="src-row"',
            'id="mcp-sidebar-card"', 'id="ws-sidebar-card"',
        ):
            self.assertIn(control, status)

        nav_start = TEMPLATE.index('<nav class="sb-nav"')
        nav_end = TEMPLATE.index("</nav>", nav_start)
        nav = TEMPLATE[nav_start:nav_end]
        self.assertNotIn("openBusinessCanvas", nav)
        self.assertNotIn('data-sidebar-nav="help"', nav)
        self.assertNotIn('data-sidebar-nav="mcp"', nav)
        self.assertNotIn('data-sidebar-nav="history"', nav)
        self.assertNotIn('id="business-canvas-root"', TEMPLATE)

        expected = (
            "DeepSeek", "Kimi", "Kimi Coding Plan", "GLM", "GLM Coding Plan",
            "MiniMax", "MiniMax Coding Plan",
        )
        for label in expected:
            self.assertIn(label, MODELS)
            self.assertIn(label, SETTINGS_UI)
        for removed in ("OpenAI / ChatGPT", "AtlasCloud", "Ollama (本地)"):
            self.assertNotIn(removed, MODELS)
            self.assertNotIn(removed, SETTINGS_UI)
        for provider in ('"kimi"', '"kimi_coding"', '"glm"', '"glm_coding"', '"minimax_coding"'):
            self.assertIn(provider, LLM_CONFIG)
        self.assertIn('"prompt_cache_mode": "kimi"', LLM_CONFIG)

    def test_side_surface_singleton_and_footer_icon_contract(self):
        surfaces = re.findall(r'class="[^"]*pfs-side-surface[^"]*"', TEMPLATE)
        self.assertEqual(len(surfaces), 4)
        for surface_id in (
            "sb-drawer",
            "sb-panel-skills",
            "sb-panel-knowledge",
            "sb-panel-mcp",
        ):
            self.assertRegex(TEMPLATE, rf'id="{surface_id}"[^>]*aria-hidden="true"')

        for token in (
            "let _activeSurface = null",
            "SIDE_SURFACE_SELECTOR",
            "closeCompetingSurfaces",
            "classList.toggle(\"is-active\"",
            "toggleAttribute(\"inert\"",
            "pfs-nav-surface-open",
            "left: var(--pfs-side-rail)",
            "width: min(380px, calc(100vw - var(--pfs-side-rail) - 16px))",
            "position: fixed !important",
            "z-index: 101",
            "backdrop-filter: none !important",
            "pointer-events: none !important",
            "visibility: hidden !important",
            "flex: 0 0 56px",
            "width: 40px",
            ".pfs-mobile-nav-backdrop.is-active",
            "left: calc(var(--pfs-side-rail) + 380px)",
            ".sb-footer-actions .pfs-icon-slot",
        ):
            self.assertIn(token, SIDEBAR + THEME)
        self.assertIn('aria-hidden="true" inert', TEMPLATE)

        footer_start = TEMPLATE.index('<div class="sb-footer-actions"')
        footer_end = TEMPLATE.index("</div>\n      </footer>", footer_start)
        footer = TEMPLATE[footer_start:footer_end]
        for control in ("sb-footer-update", "sb-footer-help", "sb-footer-settings", "lang-toggle", "theme-toggle"):
            self.assertIn(control, footer)
        self.assertGreaterEqual(footer.count("data-pfs-icon="), 5)
        self.assertEqual(footer.count("<svg"), 0)
        self.assertNotRegex(footer, r'[\U0001F300-\U0001FAFF]')
        self.assertNotRegex(footer, r'[▦⤢⤡⌄▾⛶↩☀🌙🔄⚙]')
        for label in ("检查更新", "帮助文档", "设置", "切换语言", "切换主题"):
            self.assertRegex(footer, rf'(?:aria-label|title)="{label}"')

    def test_production_ui_has_no_legacy_visual_glyphs(self):
        corpus = "\n".join(path.read_text(encoding="utf-8") for path in PRODUCTION_UI_FILES)

        self.assertNotRegex(corpus, r"[\U0001F300-\U0001FAFF]")
        self.assertNotRegex(
            corpus,
            r"[⛶⌘⎋⌫⏎⏵⏹⏸⏺◀▶▲▼◆◇●○★☆✓✔✕✖×＋⚙⚡☾☀↻↗↙↔]",
        )
        self.assertGreaterEqual(DASHBOARD.count('class="db-icon"'), 4)
        self.assertNotRegex(DASHBOARD, r"[\U0001F300-\U0001FAFF]")


if __name__ == "__main__":
    unittest.main()
