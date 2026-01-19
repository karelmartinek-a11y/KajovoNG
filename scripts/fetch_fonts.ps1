param()

Set-Location (Split-Path $PSScriptRoot -Parent)
$dest = Join-Path (Get-Location) "resources"
New-Item -ItemType Directory -Force -Path $dest | Out-Null

# Google Fonts: Montserrat (static). This is best-effort; if URLs change, download manually.
$regUrl = "https://github.com/google/fonts/raw/main/ofl/montserrat/Montserrat-Regular.ttf"
$boldUrl = "https://github.com/google/fonts/raw/main/ofl/montserrat/Montserrat-Bold.ttf"

try {
  Invoke-WebRequest -Uri $regUrl -OutFile (Join-Path $dest "montserrat_regular.ttf")
  Invoke-WebRequest -Uri $boldUrl -OutFile (Join-Path $dest "montserrat_bold.ttf")
  Write-Host "Fonts downloaded to resources\"
} catch {
  Write-Host "Download failed. Download Montserrat manually and place into resources\"
  Write-Host $_
}
