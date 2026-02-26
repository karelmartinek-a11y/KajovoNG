Param(
    [string]$Python = "python",
    [string]$AppName = "Kajovo"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

& $Python -m pip install --upgrade pip
& $Python -m pip install -r requirements.txt pyinstaller pillow
& $Python Build/generate_icons.py

$iconPath = Join-Path $repoRoot "Build/assets/app_icon.ico"
$runtimeIcon = Join-Path $repoRoot "resources/app_icon.png"

$pyinstallerArgs = @(
    "--noconfirm"
    "--clean"
    "--windowed"
    "--name", $AppName
    "--icon", $iconPath
    "--add-data", "$runtimeIcon;resources"
    "--add-data", "resources/montserrat_regular.ttf;resources"
    "--add-data", "resources/montserrat_bold.ttf;resources"
    "kajovo/app/main.py"
)

& $Python -m PyInstaller @pyinstallerArgs

Write-Host "Build complete: dist/$AppName/$AppName.exe"
