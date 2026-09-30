$ErrorActionPreference = 'Stop'
$repo = 'D:\kajovong'
$source = Join-Path $repo 'LOG\RUN_160920260039_LW0D'
$destination = Join-Path $PSScriptRoot 'source'
$utf8 = [System.Text.UTF8Encoding]::new($false, $true)

# Export je pouze lokální a při opakování ověřuje shodu místo přepisování.
function Save-Exact([string]$path, [string]$value) {
    $bytes = $utf8.GetBytes($value)
    if (Test-Path -LiteralPath $path) {
        $existing = [System.IO.File]::ReadAllBytes($path)
        if ([Convert]::ToBase64String($existing) -ne [Convert]::ToBase64String($bytes)) {
            throw "Existující soubor se liší: $path"
        }
        return
    }
    $stream = [System.IO.File]::Open($path, 'CreateNew', 'Write', 'None')
    try { $stream.Write($bytes, 0, $bytes.Length) } finally { $stream.Dispose() }
}
function Json($value) { ConvertTo-Json -InputObject $value -Depth 100 }

$statePath = Join-Path $source 'run_state.json'
$before = (Get-FileHash -LiteralPath $statePath -Algorithm SHA256).Hash
$state = Get-Content -LiteralPath $statePath -Encoding UTF8 -Raw | ConvertFrom-Json
if ($state.run_id -ne 'RUN_160920260039_LW0D') { throw 'Nesprávný zdrojový běh.' }
if ([string]::IsNullOrWhiteSpace($state.ui_state.prompt)) { throw 'Chybí původní zadání.' }
New-Item -ItemType Directory -Path $destination -Force | Out-Null
Save-Exact (Join-Path $destination 'SSOT.original.md') $state.ui_state.prompt
Save-Exact (Join-Path $destination 'A0.original.json') (Json $state.preparation_snapshot.requirements)
Save-Exact (Join-Path $destination 'A1.original.json') (Json $state.preparation_snapshot.plan)

$known = @{}
foreach ($requirement in @($state.preparation_snapshot.requirements.explicit_requirements) + @($state.preparation_snapshot.requirements.implicit_requirements)) {
    $known[$requirement.id] = $true
}
$plan = Json $state.preparation_snapshot.plan | ConvertFrom-Json
$relocated = @()
foreach ($item in $plan.architecture_items) {
    $references = @($item.requirement_ids)
    $other = @($references | Where-Object { -not $known.ContainsKey($_) })
    $item.requirement_ids = @($references | Where-Object { $known.ContainsKey($_) })
    if ($other.Count) {
        $relocated += [ordered]@{ architecture_id = $item.id; source_references = $other }
    }
}
Save-Exact (Join-Path $PSScriptRoot 'A1.requirement-links.normalized.json') (Json $plan)
Save-Exact (Join-Path $PSScriptRoot 'A1.non-requirement-links.json') (Json $relocated)

$responses = @(Get-ChildItem -LiteralPath (Join-Path $source 'responses') -File | Where-Object { $_.Name -like '*A2_response_resp_0dbb4f*.json' })
if ($responses.Count -ne 1) { throw 'Poslední známá odpověď A2 není jednoznačná.' }
$response = Get-Content -LiteralPath $responses[0].FullName -Encoding UTF8 -Raw | ConvertFrom-Json
$texts = @($response.output | ForEach-Object { $_.content } | Where-Object { $_.text -and $_.text.TrimStart().StartsWith('{') } | ForEach-Object { $_.text })
if ($texts.Count -ne 1) { throw 'Nelze jednoznačně extrahovat A2.' }
$a2 = $texts[0] | ConvertFrom-Json
Save-Exact (Join-Path $destination 'A2.rejected.json') $texts[0]

$inventory = [ordered]@{
    source_run = $state.run_id
    source_state_sha256 = $before.ToLowerInvariant()
    source_a2_response = $responses[0].Name
    source_a2_response_sha256 = (Get-FileHash -LiteralPath $responses[0].FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    requirement_count = $known.Count
    architecture_count = @($plan.architecture_items).Count
    relocated_reference_count = @($relocated | ForEach-Object { $_.source_references } | Select-Object -Unique).Count
    rejected_a2_file_count = @($a2.files).Count
    maximum_quality = $state.maximum_quality
    target_directory = $state.out_dir
    batch_submitted = $false
    readiness = 'INCOMPLETE_NOT_FOR_SUBMISSION'
}
Save-Exact (Join-Path $PSScriptRoot 'inventory.json') (Json $inventory)
if ((Get-FileHash -LiteralPath $statePath -Algorithm SHA256).Hash -ne $before) { throw 'Zdrojový stav byl souběžně změněn.' }
Write-Output (Json $inventory)
