$ErrorActionPreference = "Stop"

# UTF-8 výstup v PowerShellu (aby nebyly rozházené diakritiky)
try {
  chcp 65001 | Out-Null
  [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
} catch {}

# Spusť v kořeni projektu (tam, kde je main.py). Tento skript:
# 1) vytvoří .venv (pokud neexistuje)
# 2) nainstaluje závislosti (včetně fixu pro openai/httpx)
# 3) ověří syntaxi klíčových .py souborů
# 4) vypíše verze a spustí aplikaci

$proj = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $proj

$python = "python"
if (Get-Command py -ErrorAction SilentlyContinue) {
  $python = "py -3.13"
}

if (-not (Test-Path ".venv")) {
  Write-Host "Vytvářím .venv..."
  iex "$python -m venv .venv"
}

$venvPy = Join-Path $proj ".venv\Scripts\python.exe"

& $venvPy -m pip install -U pip
& $venvPy -m pip install -r requirements.txt

Write-Host "Kontrola syntaxe Python souborů:"
& $venvPy -m py_compile .\api_logic.py .\ui_main.py .\main.py

Write-Host "Verze knihoven:"
& $venvPy -c "import openai, httpx; print('openai', openai.__version__); print('httpx', httpx.__version__)"

Write-Host "Kontrola resources:"
if (-not (Test-Path "resources\montserrat_regular.ttf")) { Write-Warning "Chybí resources\montserrat_regular.ttf" }
if (-not (Test-Path "resources\montserrat_bold.ttf"))    { Write-Warning "Chybí resources\montserrat_bold.ttf" }
if (-not (Test-Path "resources\logo_hotel.png"))         { Write-Warning "Chybí resources\logo_hotel.png" }

Write-Host "Spouštím aplikaci..."
& $venvPy .\main.py
