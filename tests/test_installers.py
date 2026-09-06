from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class InstallerScriptTests(unittest.TestCase):
    def test_shell_installer_has_version_gate_safe_update_and_lock_fallback(self):
        text = (PROJECT_ROOT / "install.sh").read_text(encoding="utf-8")

        self.assertIn("sys.version_info >= (3, 10)", text)
        self.assertIn("git status --porcelain --untracked-files=all", text)
        self.assertNotIn("git diff --quiet", text)
        self.assertIn("git pull --ff-only", text)
        self.assertIn("if [ -f requirements.lock.txt ]; then", text)
        self.assertIn('DEPENDENCY_FILE="requirements.txt"', text)
        self.assertIn('.venv/bin/python -m pip install -r "$DEPENDENCY_FILE"', text)
        self.assertIn('INSTALL_ROOT="${PFS_INSTALL_ROOT:-$HOME/.pfs-data-analysis-agent}"', text)
        self.assertIn('LAUNCHER="${PFS_LAUNCHER_PATH:-$HOME/.local/bin/pfs-data-analysis-agent}"', text)

    def test_powershell_installer_static_contract_has_python_fallback_and_lock_manifest(self):
        """Static contract check; PowerShell is not executed by this test suite."""

        text = (PROJECT_ROOT / "install.ps1").read_text(encoding="utf-8")

        self.assertIn("Get-Command python", text)
        self.assertIn("Get-Command py", text)
        self.assertIn("function Test-PythonCandidate", text)
        self.assertIn("$env:PFS_INSTALL_ROOT", text)
        self.assertIn("$env:PFS_LAUNCHER_PATH", text)
        self.assertIn("sys.version_info >= (3, 10)", text)
        self.assertIn('$PythonArguments = @("-3")', text)
        self.assertRegex(
            text,
            r"if \(-not \(Test-PythonCandidate \$PythonCommand \$PythonArguments\)\)\s*"
            r"\{\s*\$PythonCommand = Get-Command py",
        )
        self.assertNotRegex(
            text,
            r"if \(-not \$PythonCommand\)\s*\{\s*\$PythonCommand = Get-Command py",
        )
        self.assertIn("git status --porcelain --untracked-files=all", text)
        self.assertIn("git pull --ff-only", text)
        self.assertRegex(
            text,
            r"git status --porcelain --untracked-files=all\s*"
            r"if \(\$LASTEXITCODE -ne 0\)",
        )
        self.assertRegex(
            text,
            r"git pull --ff-only\s*"
            r"if \(\$LASTEXITCODE -ne 0\)",
        )
        self.assertRegex(
            text,
            r"git clone \$RepoUrl \$ProjectDir\s*"
            r"if \(\$LASTEXITCODE -ne 0\)",
        )
        self.assertRegex(
            text,
            r"& \$PythonCommand\.Source @PythonArguments -m venv \.venv\s*"
            r"if \(\$LASTEXITCODE -ne 0\)\s*\{\s*"
            r'throw "Virtual environment creation failed\."',
        )
        self.assertRegex(
            text,
            r"& \$VenvPython -m pip install --upgrade pip\s*"
            r"if \(\$LASTEXITCODE -ne 0\)\s*\{\s*"
            r'throw "pip upgrade failed\."',
        )
        self.assertRegex(
            text,
            r"& \$VenvPython -m pip install -r \$DependencyFile\s*"
            r"if \(\$LASTEXITCODE -ne 0\)\s*\{\s*"
            r'throw "Dependency installation failed\."',
        )
        self.assertNotIn(".venv\\Scripts\\pip.exe", text)
        self.assertIn('Test-Path -LiteralPath "requirements.lock.txt"', text)
        success_message = 'Info "Installed successfully."'
        success_offset = text.index(success_message)
        for failure_message in (
            'throw "Virtual environment creation failed."',
            'throw "pip upgrade failed."',
            'throw "Dependency installation failed."',
        ):
            with self.subTest(failure=failure_message):
                self.assertLess(text.index(failure_message), success_offset)

    def test_powershell_generated_launcher_uses_venv_python_and_returns_exit_code(self):
        text = (PROJECT_ROOT / "install.ps1").read_text(encoding="utf-8")

        launcher_start = text.index('@"\n@echo off', text.index("$Launcher = Join-Path"))
        launcher_end = text.index('"@\n', launcher_start)
        launcher = text[launcher_start:launcher_end]

        self.assertIn("chcp 65001 >nul", launcher)
        self.assertIn("if errorlevel 1 exit /b 1", launcher)
        self.assertIn('set "PYTHON_EXE=%PROJECT_DIR%\\.venv\\Scripts\\python.exe"', launcher)
        self.assertIn('set "APP_FILE=%PROJECT_DIR%\\app.py"', launcher)
        self.assertIn('"%PYTHON_EXE%" "%APP_FILE%"', launcher)
        self.assertIn(
            '"%PYTHON_EXE%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1',
            launcher,
        )
        self.assertNotIn("activate.bat", launcher)
        self.assertNotIn("activate.ps1", launcher)
        self.assertNotRegex(launcher, r"(?im)^\s*python(?:\.exe)?\s+app\.py\s*$")
        self.assertNotIn("pause", launcher.lower())

        app_offset = launcher.index('"%PYTHON_EXE%" "%APP_FILE%"')
        capture_offset = launcher.index('set "APP_EXIT_CODE=%ERRORLEVEL%"')
        return_offset = launcher.index("exit /b %APP_EXIT_CODE%")
        self.assertLess(app_offset, capture_offset)
        self.assertLess(capture_offset, return_offset)

    def test_powershell_launcher_preserves_unicode_paths_with_utf8_without_bom(self):
        path = PROJECT_ROOT / "install.ps1"
        raw = path.read_bytes()
        text = raw.decode("utf-8")

        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        self.assertIn('$LauncherContent = @"', text)
        self.assertIn(
            "$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)",
            text,
        )
        self.assertIn(
            "[System.IO.File]::WriteAllText($Launcher, $LauncherContent, $Utf8NoBom)",
            text,
        )
        self.assertNotIn("Set-Content -Encoding ASCII $Launcher", text)

        launcher_start = text.index('@"\n@echo off', text.index("$Launcher = Join-Path"))
        launcher_end = text.index('"@\n', launcher_start)
        launcher = text[launcher_start:launcher_end]
        chinese_project_dir = r"C:\Users\用户\PFS 数据分析 Agent"
        rendered_launcher = launcher.replace("$BatchProjectDir", chinese_project_dir)

        self.assertIn(
            f'set "PROJECT_DIR={chinese_project_dir}"',
            rendered_launcher,
        )
        self.assertIn(
            'set "PYTHON_EXE=%PROJECT_DIR%\\.venv\\Scripts\\python.exe"',
            rendered_launcher,
        )
        expanded_launcher = rendered_launcher.replace("%PROJECT_DIR%", chinese_project_dir)
        expanded_command = expanded_launcher.replace(
            "%PYTHON_EXE%",
            f"{chinese_project_dir}\\.venv\\Scripts\\python.exe",
        ).replace("%APP_FILE%", f"{chinese_project_dir}\\app.py")
        self.assertIn(
            f'"{chinese_project_dir}\\.venv\\Scripts\\python.exe" "{chinese_project_dir}\\app.py"',
            expanded_command,
        )
        self.assertLess(
            rendered_launcher.index("chcp 65001 >nul"),
            rendered_launcher.index('set "PROJECT_DIR='),
        )

        percent_project_dir = r"C:\Users\100%\PFS 数据分析 Agent"
        escaped_percent_path = percent_project_dir.replace("%", "%%")
        rendered_percent_launcher = launcher.replace("$BatchProjectDir", escaped_percent_path)
        self.assertIn(
            f'set "PROJECT_DIR={escaped_percent_path}"',
            rendered_percent_launcher,
        )

    def test_shell_generated_launcher_checks_venv_python_version(self):
        text = (PROJECT_ROOT / "install.sh").read_text(encoding="utf-8")
        launcher_start = text.index('cat > "$LAUNCHER" <<EOF')
        launcher_end = text.index("\nEOF\n", launcher_start)
        launcher = text[launcher_start:launcher_end]

        self.assertIn(
            "sys.version_info >= (3, 10)",
            launcher,
        )
        self.assertIn('exec ".venv/bin/python" app.py', launcher)

    def test_shell_generated_launcher_fails_closed_when_project_directory_is_unavailable(self):
        text = (PROJECT_ROOT / "install.sh").read_text(encoding="utf-8")
        launcher_start = text.index('cat > "$LAUNCHER" <<EOF')
        launcher_end = text.index("\nEOF\n", launcher_start)
        launcher = text[launcher_start:launcher_end]

        cd_guard = 'if ! cd "$PROJECT_DIR"; then'
        self.assertIn(cd_guard, launcher)
        self.assertIn(
            'echo "[PFS][ERROR] Unable to enter the installed project directory: $PROJECT_DIR" >&2',
            launcher,
        )
        guard_offset = launcher.index(cd_guard)
        self.assertLess(guard_offset, launcher.index('if [ ! -x ".venv/bin/python" ]'))
        self.assertLess(guard_offset, launcher.index('exec ".venv/bin/python" app.py'))

    def test_macos_builder_static_contract_uses_selected_python_everywhere(self):
        """Static contract check for the native macOS build script."""

        text = (PROJECT_ROOT / "packaging" / "build_macos.sh").read_text(encoding="utf-8")

        for snippet in (
            'if [[ -n "${PFS_BUILD_PYTHON:-}" ]]; then',
            'PYTHON_BIN="$PFS_BUILD_PYTHON"',
            'elif [[ -x "$PROJECT_ROOT/.venv/bin/python" ]]; then',
            'PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"',
            'PYTHON_BIN="$(command -v python3 || true)"',
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, text)

        version_gate = (
            'if ! "$PYTHON_BIN" -c '
            "'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then"
        )
        self.assertIn(version_gate, text)
        version_export = 'PFS_PRODUCT_VERSION="$VERSION" "$PYTHON_BIN" -m PyInstaller'
        self.assertIn(version_export, text)
        self.assertNotIn('export PFS_PRODUCT_VERSION="$VERSION"', text)
        self.assertIn('echo "Python 3.10+ is required." >&2', text)
        self.assertLess(
            text.index('if [[ -z "$PYTHON_BIN" || ! -x "$PYTHON_BIN" ]]; then'),
            text.index(version_gate),
        )
        self.assertLess(text.index(version_gate), text.index('WORK_ROOT="$("$PYTHON_BIN"'))
        self.assertLess(text.index(version_export), text.index('"$APP/Contents/MacOS/PFSDataAnalysisAgent"'))
        self.assertIn(
            '"$PYTHON_BIN" - "$REPORTS/release.json" "$VERSION"',
            text,
        )

        self.assertNotRegex(text, r"(?m)^\s*python3(?:\s|$)")
        gate_tail = text[text.index(version_gate) :]
        self.assertNotRegex(gate_tail, r"(?m)^\s*python(?:3)?(?:\s|$)")
        for line in text.splitlines():
            if "packaging/audit_artifact.py" in line:
                self.assertTrue(
                    line.lstrip().startswith('"$PYTHON_BIN"'),
                    msg=f"release audit does not use selected Python: {line}",
                )

    def test_windows_builder_checks_python_310_and_scopes_product_version(self):
        """Static contract check; PowerShell is not executed by this test suite."""

        text = (PROJECT_ROOT / "packaging" / "build_windows.ps1").read_text(encoding="utf-8")
        self.assertIn("[System.Environment]::OSVersion.Platform", text)
        self.assertIn("[System.PlatformID]::Win32NT", text)
        self.assertNotIn("$IsWindows", text)
        self.assertIn("Get-Command python", text)
        self.assertIn("Get-Command py", text)
        self.assertIn("function Test-PythonCandidate", text)
        self.assertIn('$PythonArguments = @("-3")', text)
        self.assertIn("PFS_BUILD_PYTHON", text)
        self.assertIn("& $PythonCommand.Source @PythonArguments @Arguments", text)
        self.assertRegex(
            text,
            r"if \(-not \(Test-PythonCandidate \$PythonCommand \$PythonArguments\)\)\s*"
            r"\{\s*\$PythonCommand = Get-Command py",
        )
        self.assertIn("sys.version_info >= (3, 10)", text)
        self.assertIn("Python 3.10+ is required for the Windows package build", text)

    def test_windows_builder_restores_all_process_environment_overrides(self):
        text = (PROJECT_ROOT / "packaging" / "build_windows.ps1").read_text(encoding="utf-8")
        tracked_names = (
            "PFS_STAGING_ROOT",
            "PFS_DATA_DIR",
            "PFS_NO_BROWSER",
            "PFS_ONEDIR_SELF_TEST",
            "PFS_CLEANUP_DISABLED",
            "PFS_PRODUCT_VERSION",
        )

        self.assertIn("$previousEnvironment = @{}", text)
        self.assertIn(
            "$previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')",
            text,
        )
        self.assertIn("try {", text)
        self.assertIn("finally {", text)
        self.assertIn("Restore-TrackedEnvironment", text)
        self.assertIn(
            "$previousValue = $previousEnvironment[$name]",
            text,
        )
        self.assertIn(
            "[Environment]::SetEnvironmentVariable($name, $previousValue, 'Process')",
            text,
        )
        self.assertIn(
            'Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue',
            text,
        )
        for name in tracked_names:
            with self.subTest(name=name):
                self.assertIn(f"'{name}'", text)
                self.assertIn(f"$env:{name}", text)
        outer_try = text.index("try {\n    $env:PFS_STAGING_ROOT")
        outer_finally = text.index("} finally {\n    Restore-TrackedEnvironment")
        self.assertLess(outer_try, outer_finally)
        for name in tracked_names:
            with self.subTest(assignment=name):
                self.assertIn(f"$env:{name}", text[outer_try:outer_finally])

    def test_macos_builder_reports_missing_option_values(self):
        text = (PROJECT_ROOT / "packaging" / "build_macos.sh").read_text(encoding="utf-8")

        for option in ("--version", "--work-root"):
            with self.subTest(option=option):
                self.assertIn(f'echo "Option {option} requires a value." >&2', text)
        self.assertGreaterEqual(text.count("usage >&2"), 2)

    def test_installers_keep_the_pfs_repository_default(self):
        for relative_path in (Path("install.sh"), Path("install.ps1")):
            with self.subTest(script=relative_path):
                text = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn("PFS_REPO_URL", text)
                self.assertIn("PFS-data-analysis-agent.git", text)
                self.assertNotIn("Data-Analysis-Agent-main", text)


if __name__ == "__main__":
    unittest.main()
