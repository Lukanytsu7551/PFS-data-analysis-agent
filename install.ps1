$ErrorActionPreference = "Stop"

$RepoUrl = if ($env:PFS_REPO_URL) { $env:PFS_REPO_URL } else { "https://github.com/Lukanytsu7551/PFS-data-analysis-agent.git" }
$ProjectName = if ($env:PFS_PROJECT_NAME) { $env:PFS_PROJECT_NAME } else { "PFS-data-analysis-agent" }
$InstallDir = Join-Path $env:USERPROFILE ".pfs-data-analysis-agent"
$ProjectDir = Join-Path $InstallDir $ProjectName

function Info($msg) {
    Write-Host "[PFS] $msg"
}

Info "Checking Python..."
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python not found. Please install Python 3.10+ first."
}

Info "Checking Git..."
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git not found. Please install Git first."
}

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

if (Test-Path $ProjectDir) {
    Info "Project already exists. Updating..."
    Set-Location $ProjectDir
    git pull
} else {
    Info "Cloning project..."
    git clone $RepoUrl $ProjectDir
    Set-Location $ProjectDir
}

Info "Creating virtual environment..."
python -m venv .venv

Info "Installing dependencies..."
& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\pip.exe" install -r requirements.txt

$Launcher = Join-Path $env:USERPROFILE "pfs-data-analysis-agent.bat"

@"
@echo off
cd /d "$ProjectDir"
call ".venv\Scripts\activate.bat"
python app.py
pause
"@ | Set-Content -Encoding ASCII $Launcher

Info "Installed successfully."
Info "Start with: $Launcher"
Info "Or run:"
Info "cd $ProjectDir"
Info ".\.venv\Scripts\activate"
Info "python app.py"
