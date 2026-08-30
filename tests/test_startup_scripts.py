import re
from pathlib import Path
import stat
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATHS = (
    PROJECT_ROOT / "start.command",
    PROJECT_ROOT / "start.bat",
    PROJECT_ROOT / "installer" / "launch.bat",
)


def active_script_text(path: Path) -> str:
    """Return executable script lines, excluding comments and help text."""

    active_lines = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        lowered = line.lower()
        if not line or lowered.startswith(("#", "rem ", "::", "echo")):
            continue
        active_lines.append(line)
    return "\n".join(active_lines)


class StartupScriptTests(unittest.TestCase):
    def test_launchers_use_current_explicit_core_preflight_api(self):
        for path in SCRIPT_PATHS:
            with self.subTest(script=path.relative_to(PROJECT_ROOT)):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("inspect_requirements", text)
                self.assertIn("inspect_startup_dependencies", text)
                self.assertIn("require_core_dependencies", text)
                self.assertIn("optional={}", text)

    def test_macos_launcher_is_executable(self):
        mode = (PROJECT_ROOT / "start.command").stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR)

    def test_launchers_do_not_install_or_upgrade_dependencies(self):
        forbidden_commands = (
            r"(?:^|[&|])\s*[^\n]*-m\s+pip\s+install\b",
            r"(?:^|[&|])\s*[^\n]*-m\s+pip\s+(?:--upgrade|upgrade)\b",
            r"\bensurepip\b",
        )
        for path in SCRIPT_PATHS:
            with self.subTest(script=path.relative_to(PROJECT_ROOT)):
                active_text = active_script_text(path).lower()
                for pattern in forbidden_commands:
                    self.assertIsNone(re.search(pattern, active_text))

    def test_launchers_do_not_create_virtual_environments(self):
        for path in SCRIPT_PATHS:
            with self.subTest(script=path.relative_to(PROJECT_ROOT)):
                self.assertNotRegex(active_script_text(path).lower(), r"-m\s+venv\b")

    def test_launchers_use_pfs_identity_without_legacy_runtime_keys(self):
        for path in SCRIPT_PATHS:
            with self.subTest(script=path.relative_to(PROJECT_ROOT)):
                text = path.read_text(encoding="utf-8")
                self.assertIn("PFS Data Analysis Agent", text)
                self.assertNotIn("BAA_", text)
                self.assertNotIn("Data-Analysis-Agent", text)

    def test_source_launchers_start_app_with_project_python(self):
        mac_launcher = (PROJECT_ROOT / "start.command").read_text(encoding="utf-8")
        windows_launcher = (PROJECT_ROOT / "start.bat").read_text(encoding="utf-8")
        installer_launcher = (PROJECT_ROOT / "installer" / "launch.bat").read_text(encoding="utf-8")

        self.assertIn('exec "$VENV_PYTHON" "$APP_FILE"', mac_launcher)
        self.assertIn('"%VENV_PYTHON%" "%APP_FILE%"', windows_launcher)
        self.assertIn('"%VENV_PYTHON%" "%APP_FILE%"', installer_launcher)

    def test_missing_environment_help_is_explicit_and_nonzero(self):
        mac_launcher = (PROJECT_ROOT / "start.command").read_text(encoding="utf-8")
        self.assertIn("python3 -m venv .venv", mac_launcher)
        self.assertIn(".venv/bin/python -m pip install -r requirements.txt", mac_launcher)
        self.assertIn("exit 1", mac_launcher)

        for relative_path in (Path("start.bat"), Path("installer/launch.bat")):
            with self.subTest(script=relative_path):
                text = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn("py -3 -m venv .venv", text)
                self.assertIn(
                    ".venv\\Scripts\\python.exe -m pip install -r requirements.txt",
                    text,
                )
                self.assertIn("exit /b 1", text)

    def test_installer_prefers_packaged_executable(self):
        launcher = (PROJECT_ROOT / "installer" / "launch.bat").read_text(encoding="utf-8")
        packaged_check = 'if exist "%PACKAGED_EXE%"'
        source_check = 'if not exist "%APP_FILE%"'

        self.assertIn("PFSDataAnalysisAgent.exe", launcher)
        self.assertIn('start "" "%PACKAGED_EXE%"', launcher)
        self.assertLess(launcher.index(packaged_check), launcher.index(source_check))


if __name__ == "__main__":
    unittest.main()
