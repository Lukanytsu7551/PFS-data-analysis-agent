$ErrorActionPreference = "Stop"

$RepoUrl = if ($env:PFS_REPO_URL) { $env:PFS_REPO_URL } else { "https://github.com/Lukanytsu7551/PFS-data-analysis-agent.git" }
$ProjectName = if ($env:PFS_PROJECT_NAME) { $env:PFS_PROJECT_NAME } else { "PFS-data-analysis-agent" }
$InstallRoot = if ($env:PFS_INSTALL_ROOT) { $env:PFS_INSTALL_ROOT } else { Join-Path $env:USERPROFILE ".pfs-data-analysis-agent" }
$ProjectDir = Join-Path $InstallRoot $ProjectName

function Info($msg) {
    Write-Host "[PFS] $msg"
}

function Test-PythonCandidate {
    param(
        [object]$Command,
        [string[]]$Arguments = @()
    )

    if (-not $Command) {
        return $false
    }

    & $Command.Source @Arguments -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" *> $null
    if ($LASTEXITCODE -ne 0) {
        return $false
    }
    return $true
}

Info "Checking Python..."
$PythonCommand = Get-Command python -ErrorAction SilentlyContinue
$PythonArguments = @()
if (-not (Test-PythonCandidate $PythonCommand $PythonArguments)) {
    $PythonCommand = Get-Command py -ErrorAction SilentlyContinue
    $PythonArguments = @("-3")
}
if (-not (Test-PythonCandidate $PythonCommand $PythonArguments)) {
    throw "Python 3.10+ is required."
}

Info "Checking Git..."
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git not found. Please install Git first."
}

New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null

if (Test-Path $ProjectDir) {
    Info "Project already exists. Updating..."
    if (-not (Test-Path (Join-Path $ProjectDir ".git"))) {
        throw "Existing install is not a Git checkout: $ProjectDir"
    }
    Set-Location $ProjectDir
    $workingTree = git status --porcelain --untracked-files=all
    if ($LASTEXITCODE -ne 0) {
        throw "Git status failed."
    }
    if ($workingTree) {
        throw "Existing install has local changes; refusing to overwrite it: $ProjectDir"
    }
    git pull --ff-only
    if ($LASTEXITCODE -ne 0) {
        throw "Git pull failed."
    }
} else {
    Info "Cloning project..."
    git clone $RepoUrl $ProjectDir
    if ($LASTEXITCODE -ne 0) {
        throw "Git clone failed."
    }
    Set-Location $ProjectDir
}

Info "Creating virtual environment..."
& $PythonCommand.Source @PythonArguments -m venv .venv
if ($LASTEXITCODE -ne 0) {
    throw "Virtual environment creation failed."
}

$VenvPython = ".\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "Virtual environment Python was not created."
}

Info "Installing dependencies..."
& $VenvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "pip upgrade failed."
}
$DependencyFile = if (Test-Path -LiteralPath "requirements.lock.txt") { "requirements.lock.txt" } else { "requirements.txt" }
Info "Using dependency manifest: $DependencyFile"
& $VenvPython -m pip install -r $DependencyFile
if ($LASTEXITCODE -ne 0) {
    throw "Dependency installation failed."
}

$Launcher = Join-Path $env:USERPROFILE "pfs-data-analysis-agent.bat"
if ($env:PFS_LAUNCHER_PATH) {
    $Launcher = $env:PFS_LAUNCHER_PATH
}
$BatchProjectDir = $ProjectDir.Replace('%', '%%')

$LauncherContent = @"
@echo off
chcp 65001 >nul
if errorlevel 1 exit /b 1
set "PROJECT_DIR=$BatchProjectDir"
set "PYTHON_EXE=%PROJECT_DIR%\.venv\Scripts\python.exe"
set "APP_FILE=%PROJECT_DIR%\app.py"
if not exist "%PYTHON_EXE%" (
    echo PFS virtual environment Python not found: "%PYTHON_EXE%" 1>&2
    exit /b 1
)
if not exist "%APP_FILE%" (
    echo PFS application not found: "%APP_FILE%" 1>&2
    exit /b 1
)
"%PYTHON_EXE%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo [PFS][ERROR] Python 3.10+ is required in the project virtual environment: "%PYTHON_EXE%" 1>&2
    exit /b 1
)
cd /d "%PROJECT_DIR%"
if errorlevel 1 exit /b 1
"%PYTHON_EXE%" "%APP_FILE%"
set "APP_EXIT_CODE=%ERRORLEVEL%"
exit /b %APP_EXIT_CODE%
"@
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$LauncherParent = Split-Path -Parent $Launcher
if ($LauncherParent) {
    New-Item -ItemType Directory -Force -Path $LauncherParent | Out-Null
}
[System.IO.File]::WriteAllText($Launcher, $LauncherContent, $Utf8NoBom)

Info "Installed successfully."
Info "Start with: $Launcher"
Info "Or run:"
Info "cd $ProjectDir"
Info ".\.venv\Scripts\python.exe app.py"
