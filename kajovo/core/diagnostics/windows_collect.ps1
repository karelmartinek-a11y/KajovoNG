param([Parameter(Mandatory = $true)][string]$OutDir)
$ErrorActionPreference = 'Stop'
$encoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $encoding
$destination = [IO.Path]::GetFullPath($OutDir)
[IO.Directory]::CreateDirectory($destination) | Out-Null
$report = [ordered]@{
    os = Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, OSArchitecture, LastBootUpTime
    cpu = Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores, NumberOfLogicalProcessors
    memory = Get-CimInstance Win32_ComputerSystem | Select-Object TotalPhysicalMemory
}
[IO.File]::WriteAllText((Join-Path $destination 'system.json'), ($report | ConvertTo-Json -Depth 6), $encoding)
Write-Output 'Diagnostika system.json byla uložena.'
