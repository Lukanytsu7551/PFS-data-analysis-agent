@echo off
setlocal EnableExtensions

rem PFS Data Analysis Agent launcher.
rem Environment setup is a separate, explicit action. This launcher never installs packages.

cd /d "%~dp0"
if errorlevel 1 (
    echo [PFS][ERROR] Failed to switch to the project directory.
    exit /b 1
)

set "APP_FILE=app.py"
set "VENV_PYTHON=.venv\Scripts\python.exe"
set "PORT=5001"

title PFS Data Analysis Agent
echo ============================================
echo   PFS Data Analysis Agent
echo ============================================

if not exist "%APP_FILE%" (
    echo [PFS][ERROR] Entry file not found: %APP_FILE%
    exit /b 1
)

if not exist "%VENV_PYTHON%" (
    echo [PFS][ERROR] The project virtual environment is not ready: %VENV_PYTHON%
    goto :install_help
)

"%VENV_PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo [PFS][ERROR] Python 3.10+ is required in the project virtual environment: %VENV_PYTHON%
    goto :install_help
)

rem Read-only preflight of the explicit core manifest; optional features never block startup.
"%VENV_PYTHON%" -c "from infrastructure.startup_requirements import inspect_startup_dependencies, require_core_dependencies; report = inspect_startup_dependencies(optional={}); print('[PFS][ERROR] Missing required Python dependencies: ' + ', '.join(item.package_name for item in report.missing_core)) if report.missing_core else None; require_core_dependencies(report)"
if errorlevel 1 goto :install_help

for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr /r /c:":%PORT% .*LISTENING"') do (
    echo [PFS][ERROR] Port %PORT% is already in use. PID=%%a
    echo [PFS][TIP] Inspect it with: tasklist /fi "PID eq %%a"
    exit /b 1
)

set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
echo [PFS][INFO] Starting PFS Data Analysis Agent
echo [PFS][INFO] Open: http://127.0.0.1:%PORT%
start "" "http://127.0.0.1:%PORT%"
"%VENV_PYTHON%" "%APP_FILE%"
set "RET=%ERRORLEVEL%"
if not "%RET%"=="0" echo [PFS][ERROR] Application exited with code %RET%
exit /b %RET%

:install_help
echo.
echo [PFS][ACTION] Prepare the local environment explicitly, then run start.bat again:
echo   py -3 -m venv .venv
echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
exit /b 1
