Param(
    [string]$Python = "python",
    [string]$AppName = "Kajovo"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

& $Python -m pip install -c requirements/constraints.txt -e ".[build]"
if ($LASTEXITCODE -ne 0) { throw "Instalace závislostí podle constraints selhala." }
& $Python tools/verify_dependency_contract.py
if ($LASTEXITCODE -ne 0) { throw "Dependency contract není platný." }
& $Python -m pip check
if ($LASTEXITCODE -ne 0) { throw "Kontrola konzistence závislostí selhala." }
& $Python Build/generate_icons.py
if ($LASTEXITCODE -ne 0) { throw "Generování ikon selhalo." }

$iconPath = Join-Path $repoRoot "Build/assets/app_icon.ico"
$runtimeIcon = Join-Path $repoRoot "resources/app_icon.png"

$pyinstallerArgs = @(
    "--noconfirm"
    "--clean"
    "--windowed"
    "--collect-data", "kajovo.core"
    "--copy-metadata", "kajovong"
    "--name", $AppName
    "--icon", $iconPath
    "--add-data", "$runtimeIcon;resources"
    "--add-data", "resources/Kajovo_new.png;resources"
    "--add-data", "resources/studio-symbol.png;resources"
    "--add-data", "resources/montserrat_regular.ttf;resources"
    "--add-data", "resources/montserrat_bold.ttf;resources"
    "--add-data", "resources/orchestration;resources/orchestration"
    "kajovo/app/main.py"
)

& $Python -m PyInstaller @pyinstallerArgs
if ($LASTEXITCODE -ne 0) { throw "Sestavení aplikace selhalo." }
& $Python tools/write_build_metadata.py
if ($LASTEXITCODE -ne 0) { throw "Generování build metadat selhalo." }

$artifact = Join-Path $repoRoot "dist/$AppName/$AppName.exe"
if (-not (Test-Path $artifact -PathType Leaf)) { throw "Build artefakt nebyl vytvořen: $artifact" }
Write-Host "Build complete: dist/$AppName/$AppName.exe"
