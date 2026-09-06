[CmdletBinding()]
param(
    [ValidatePattern('^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$')]
    [string]$Version = '0.1.0',
    [string]$WorkRoot,
    [string]$InnoCompiler,
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
    throw 'Windows packages must be built on a native Windows runner.'
}
if (-not [Environment]::Is64BitOperatingSystem) {
    throw 'The Windows x64 package requires a 64-bit operating system.'
}

function Test-PythonCandidate {
    param(
        [object]$Command,
        [string[]]$Arguments = @()
    )

    if (-not $Command) {
        return $false
    }

    & $Command.Source @Arguments -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' *> $null
    return $LASTEXITCODE -eq 0
}

$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$buildRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot 'build'))
$PythonCommand = $null
$PythonArguments = @()
if ($env:PFS_BUILD_PYTHON) {
    $PythonCommand = Get-Command $env:PFS_BUILD_PYTHON -ErrorAction SilentlyContinue
    if (-not $PythonCommand -and (Test-Path -LiteralPath $env:PFS_BUILD_PYTHON -PathType Leaf)) {
        $PythonCommand = [PSCustomObject]@{ Source = $env:PFS_BUILD_PYTHON }
    }
} else {
    $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not (Test-PythonCandidate $PythonCommand $PythonArguments)) {
        $PythonCommand = Get-Command py -ErrorAction SilentlyContinue
        $PythonArguments = @("-3")
    }
}
if (-not (Test-PythonCandidate $PythonCommand $PythonArguments)) {
    throw 'Python 3.10+ is required for the Windows package build; use python, py -3, or set PFS_BUILD_PYTHON.'
}
if (-not $WorkRoot) {
    # Keep this path short: PyTorch ships deeply nested license files that Inno Setup must compress.
    $WorkRoot = Join-Path $buildRoot 'w'
}
$WorkRoot = [System.IO.Path]::GetFullPath($WorkRoot)
if (-not $WorkRoot.StartsWith($buildRoot + [System.IO.Path]::DirectorySeparatorChar,
        [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'WorkRoot must stay below the project build directory.'
}

if (Test-Path -LiteralPath $WorkRoot) {
    Remove-Item -LiteralPath $WorkRoot -Recurse -Force
}
$staging = Join-Path $WorkRoot 'staging'
$pyiWork = Join-Path $WorkRoot 'pyinstaller-work'
$pyiDist = Join-Path $WorkRoot 'pyinstaller-dist'
$onedir = Join-Path $pyiDist 'PFSDataAnalysisAgent'
$runtimeSmoke = Join-Path $WorkRoot 'runtime-smoke'
$installerOutput = Join-Path $WorkRoot 'installer'
$reports = Join-Path $WorkRoot 'reports'
New-Item -ItemType Directory -Path $reports, $installerOutput | Out-Null

function Invoke-CheckedPython {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & $PythonCommand.Source @PythonArguments @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed with exit code $LASTEXITCODE"
    }
}

Invoke-CheckedPython (Join-Path $projectRoot 'packaging\build_manifest.py') `
    --source $projectRoot --destination $staging `
    --manifest (Join-Path $reports 'staging-manifest.json')
Invoke-CheckedPython (Join-Path $projectRoot 'packaging\audit_artifact.py') `
    $staging --report (Join-Path $reports 'staging-audit.json')

$trackedEnvironmentVariables = @(
    'PFS_STAGING_ROOT',
    'PFS_DATA_DIR',
    'PFS_NO_BROWSER',
    'PFS_ONEDIR_SELF_TEST',
    'PFS_CLEANUP_DISABLED',
    'PFS_PRODUCT_VERSION'
)
$previousEnvironment = @{}
foreach ($name in $trackedEnvironmentVariables) {
    $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}

function Restore-TrackedEnvironment {
    foreach ($name in $trackedEnvironmentVariables) {
        $previousValue = $previousEnvironment[$name]
        if ($null -eq $previousValue) {
            Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
        } else {
            [Environment]::SetEnvironmentVariable($name, $previousValue, 'Process')
        }
    }
}

try {
    $env:PFS_STAGING_ROOT = $staging
    $env:PFS_PRODUCT_VERSION = $Version
    Invoke-CheckedPython -m PyInstaller --clean --noconfirm `
        --distpath $pyiDist --workpath $pyiWork `
        (Join-Path $projectRoot 'packaging\pfs_data_analysis_agent.spec')
    Invoke-CheckedPython (Join-Path $projectRoot 'packaging\audit_artifact.py') `
        $onedir --report (Join-Path $reports 'onedir-audit.json')

    $env:PFS_DATA_DIR = $runtimeSmoke
    $env:PFS_NO_BROWSER = '1'
    $env:PFS_ONEDIR_SELF_TEST = '1'
    $env:PFS_CLEANUP_DISABLED = '1'
    $process = Start-Process -FilePath (Join-Path $onedir 'PFSDataAnalysisAgent.exe') `
        -WindowStyle Hidden -PassThru -Wait
    if ($process.ExitCode -ne 0) {
        throw "Frozen self-test failed with exit code $($process.ExitCode)."
    }
    $smokeReport = Join-Path $runtimeSmoke 'outputs\build-smoke.json'
    if (-not (Test-Path -LiteralPath $smokeReport)) {
        throw 'Frozen self-test did not create its report.'
    }
    $smoke = Get-Content -LiteralPath $smokeReport -Raw | ConvertFrom-Json
    if (-not $smoke.ok -or -not $smoke.frozen) {
        throw 'Frozen self-test report is not successful.'
    }
    Copy-Item -LiteralPath $smokeReport -Destination (Join-Path $reports 'frozen-smoke.json')

    if ($PrepareOnly) {
        Write-Host "Audited onedir ready for manual Inno compilation: $onedir"
        return
    }
    if (-not $InnoCompiler) {
        $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        if ($command) {
            $InnoCompiler = $command.Source
        } else {
            $InnoCompiler = @(
                'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
                'C:\Program Files\Inno Setup 6\ISCC.exe'
            ) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
        }
    }
    if (-not $InnoCompiler -or -not (Test-Path -LiteralPath $InnoCompiler)) {
        throw 'Inno Setup 6 compiler (ISCC.exe) was not found.'
    }

    $iconFile = Join-Path $projectRoot 'installer\icon.ico'
    if (-not (Test-Path -LiteralPath $iconFile -PathType Leaf)) {
        throw "Installer icon was not found: $iconFile"
    }

    # Inno Setup still uses legacy path handling while compressing. PyTorch ships
    # license paths that exceed MAX_PATH under the GitHub Actions checkout, so map
    # the existing work directory to a temporary drive for the compile step only.
    $innoDrive = @('Z', 'Y', 'X', 'W', 'V', 'U', 'T') |
        Where-Object { -not (Test-Path -LiteralPath "${_}:\") } |
        Select-Object -First 1
    if (-not $innoDrive) {
        throw 'No free drive letter is available for the Inno Setup build.'
    }
    $innoDriveName = "${innoDrive}:"
    & subst.exe $innoDriveName $WorkRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to map $WorkRoot to $innoDriveName for Inno Setup."
    }

    $innoExitCode = $null
    try {
        $innoRoot = "${innoDriveName}\"
        $innoOnedir = Join-Path $innoRoot 'pyinstaller-dist\PFSDataAnalysisAgent'
        $innoInstallerOutput = Join-Path $innoRoot 'installer'
        & $InnoCompiler "/DOnedirSource=$innoOnedir" `
            "/DInstallerOutputDir=$innoInstallerOutput" `
            "/DIconFilePath=$iconFile" "/DAppVersion=$Version" `
            (Join-Path $projectRoot 'installer\setup.iss')
        $innoExitCode = $LASTEXITCODE
    } finally {
        & subst.exe $innoDriveName /D | Out-Null
    }
    if ($innoExitCode -ne 0) {
        throw "Inno Setup failed with exit code $innoExitCode."
    }

    $installer = Join-Path $installerOutput 'PFSDataAnalysisAgent-Windows-x64.exe'
    if (-not (Test-Path -LiteralPath $installer)) {
        throw 'Inno Setup did not produce the expected installer.'
    }
    Invoke-CheckedPython (Join-Path $projectRoot 'packaging\audit_artifact.py') `
        $installer --report (Join-Path $reports 'installer-audit.json')

    $hash = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
    $release = [ordered]@{
        schema_version = 1
        version = $Version
        platform = 'windows-x64'
        filename = [System.IO.Path]::GetFileName($installer)
        size = (Get-Item -LiteralPath $installer).Length
        sha256 = $hash
        unsigned = $true
    }
    $release | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $reports 'release.json') -Encoding utf8
    Write-Host "Windows installer ready: $installer"
    Write-Host "SHA-256: $hash"
} finally {
    Restore-TrackedEnvironment
}
