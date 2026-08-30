@echo off
setlocal EnableExtensions

rem PFS installed-package launcher with a source-tree compatibility fallback.
rem This launcher never creates environments or installs dependencies.

cd /d "%~dp0"
if errorlevel 1 (
    echo [PFS][ERROR] Cannot switch to the launcher directory: %~dp0
    exit /b 1
)

set "PACKAGED_EXE=%CD%\PFSDataAnalysisAgent.exe"
title PFS Data Analysis Agent

if exist "%PACKAGED_EXE%" (
    echo [PFS][INFO] Starting the installed PFS Data Analysis Agent.
    start "" "%PACKAGED_EXE%"
    exit /b 0
)

rem Source compatibility: support a copied launcher or this repository's installer directory.
set "SOURCE_ROOT=%CD%"
if not exist "%SOURCE_ROOT%\app.py" if exist "%CD%\..\app.py" set "SOURCE_ROOT=%CD%\.."
set "APP_FILE=%SOURCE_ROOT%\app.py"
set "VENV_PYTHON=%SOURCE_ROOT%\.venv\Scripts\python.exe"
set "PORT=5001"

if not exist "%APP_FILE%" (
    echo [PFS][ERROR] Neither PFSDataAnalysisAgent.exe nor a source app.py was found.
    exit /b 1
)

if not exist "%VENV_PYTHON%" (
    echo [PFS][ERROR] The source virtual environment is not ready: %VENV_PYTHON%
    goto :install_help
)

"%VENV_PYTHON%" --version >nul 2>&1
if errorlevel 1 (
    echo [PFS][ERROR] The source Python executable cannot run: %VENV_PYTHON%
    goto :install_help
)

pushd "%SOURCE_ROOT%"
if errorlevel 1 (
    echo [PFS][ERROR] Cannot switch to the source directory: %SOURCE_ROOT%
    exit /b 1
)

rem Read-only preflight of the explicit core manifest; optional features never block startup.
"%VENV_PYTHON%" -c "from infrastructure.startup_requirements import inspect_startup_dependencies, require_core_dependencies; report = inspect_startup_dependencies(optional={}); print('[PFS][ERROR] Missing required Python dependencies: ' + ', '.join(item.package_name for item in report.missing_core)) if report.missing_core else None; require_core_dependencies(report)"
if errorlevel 1 (
    popd
    goto :install_help
)

for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr /r /c:":%PORT% .*LISTENING"') do (
    echo [PFS][ERROR] Port %PORT% is already in use. PID=%%a
    popd
    exit /b 1
)

set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
echo [PFS][INFO] Starting the PFS source compatibility entry.
echo [PFS][INFO] Open: http://127.0.0.1:%PORT%
start "" "http://127.0.0.1:%PORT%"
"%VENV_PYTHON%" "%APP_FILE%"
set "RET=%ERRORLEVEL%"
popd
exit /b %RET%

:install_help
echo.
echo [PFS][ACTION] Source dependencies are not installed. Run these commands explicitly:
echo   cd /d "%SOURCE_ROOT%"
echo   py -3 -m venv .venv
echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
exit /b 1
