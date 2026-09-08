Param(
    [string]$Python = "python",
    [string]$AppName = "Kajovo"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

& $Python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Aktualizace pip selhala." }
& $Python -m pip install -r requirements.txt pyinstaller pillow
if ($LASTEXITCODE -ne 0) { throw "Instalace závislostí selhala." }
& $Python Build/generate_icons.py
if ($LASTEXITCODE -ne 0) { throw "Generování ikon selhalo." }

$iconPath = Join-Path $repoRoot "Build/assets/app_icon.ico"
$runtimeIcon = Join-Path $repoRoot "resources/app_icon.png"

$pyinstallerArgs = @(
    "--noconfirm"
    "--clean"
    "--windowed"
    "--collect-data", "kajovo.core.diagnostics"
    "--name", $AppName
    "--icon", $iconPath
    "--add-data", "$runtimeIcon;resources"
    "--add-data", "resources/Kajovo_new.png;resources"
    "--add-data", "resources/montserrat_regular.ttf;resources"
    "--add-data", "resources/montserrat_bold.ttf;resources"
    "kajovo/app/main.py"
)

& $Python -m PyInstaller @pyinstallerArgs
if ($LASTEXITCODE -ne 0) { throw "Sestavení aplikace selhalo." }

Write-Host "Build complete: dist/$AppName/$AppName.exe"
