param()

Set-Location (Split-Path $PSScriptRoot -Parent)
if (Test-Path ".venv\Scripts\Activate.ps1") { . .\.venv\Scripts\Activate.ps1 }
python -m kajovo.app.main
