param([switch]$CheckOnly)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

try {
    $repoRoot = Split-Path -Parent $PSScriptRoot
    Set-Location -LiteralPath $repoRoot
    $venvPath = Join-Path $repoRoot ".venv"
    $venvPython = Join-Path $venvPath "Scripts\python.exe"
    $versionCheck = "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) and sys.prefix != sys.base_prefix else 1)"

    if (-not (Test-Path -LiteralPath $venvPython)) {
        if (Test-Path -LiteralPath $venvPath) {
            throw "Existujici .venv je neuplne. Opravte prostredi; spoustec je neprepisuje."
        }
        $candidates = @(
            @{ Exe = "py"; Args = @("-3") },
            @{ Exe = "py"; Args = @("-3.14") },
            @{ Exe = "py"; Args = @("-3.13") },
            @{ Exe = "py"; Args = @("-3.12") },
            @{ Exe = "python"; Args = @() },
            @{ Exe = "python3"; Args = @() }
        )
        $selected = $null
        foreach ($candidate in $candidates) {
            try {
                & $candidate.Exe @($candidate.Args + @("-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)")) 2>$null | Out-Null
                if ($LASTEXITCODE -eq 0) { $selected = $candidate; break }
            } catch { }
        }
        if ($null -eq $selected) {
            throw "Python 3.12 nebo novejsi nebyl nalezen. Nainstalujte jej a spustte start.bat znovu."
        }
        Write-Host "Vytvarim projektove prostredi .venv..."
        & $selected.Exe @($selected.Args + @("-m", "venv", $venvPath))
        if ($LASTEXITCODE -ne 0) { throw "Vytvoreni .venv selhalo." }
    }

    & $venvPython -c $versionCheck
    if ($LASTEXITCODE -ne 0) {
        throw "Projektove .venv musi pouzivat Python 3.12+ a byt virtualnim prostredim. Opravte je pred spustenim."
    }
    $arguments = @((Join-Path $PSScriptRoot "start_app.py"))
    if ($CheckOnly) { $arguments += "--check-only" }
    & $venvPython @arguments
    exit $LASTEXITCODE
} catch {
    Write-Host ("Chyba: " + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
