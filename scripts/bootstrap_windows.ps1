param(
    [switch]$RuntimeOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-PythonCandidate {
    $candidates = @(
        @{ Exe = "py"; Args = @("-3.12") },
        @{ Exe = "python"; Args = @() },
        @{ Exe = "python3"; Args = @() }
    )

    foreach ($candidate in $candidates) {
        try {
            & $candidate.Exe @($candidate.Args + @("-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)")) | Out-Null
            if ($LASTEXITCODE -eq 0) {
                return $candidate
            }
        } catch {
        }
    }

    throw "Python 3.12+ was not found. Install Python 3.12 and rerun this script."
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPath = Join-Path $repoRoot ".venv"
$venvPython = Join-Path $venvPath "Scripts\python.exe"
$installTarget = if ($RuntimeOnly) { "." } else { ".[dev]" }
$python = Get-PythonCandidate

if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment in $venvPath"
    & $python.Exe @($python.Args + @("-m", "venv", $venvPath))
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create virtual environment."
    }
} else {
    Write-Host "Reusing existing virtual environment in $venvPath"
}

Write-Host "Upgrading pip tooling in local virtual environment"
& $venvPython -m pip install --upgrade pip setuptools wheel
if ($LASTEXITCODE -ne 0) {
    throw "Failed to upgrade pip tooling."
}

Write-Host "Installing $installTarget into local virtual environment"
& $venvPython -m pip install $installTarget
if ($LASTEXITCODE -ne 0) {
    throw "Dependency installation failed."
}

Write-Host ""
Write-Host "Environment is ready."
Write-Host "Activate it with:"
Write-Host "  .\.venv\Scripts\Activate.ps1"
