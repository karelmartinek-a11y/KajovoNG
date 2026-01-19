param()

Set-Location (Split-Path $PSScriptRoot -Parent)

if (!(Test-Path ".venv")) {
  Write-Host "Creating venv..."
  py -3 -m venv .venv
}

Write-Host "Activating venv..."
. .\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
pip install -r requirements.txt

Write-Host "Done."
Write-Host "Optional: .\scripts\fetch_fonts.ps1"
