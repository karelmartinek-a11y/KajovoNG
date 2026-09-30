param([string[]]$Sections)
$ErrorActionPreference = 'Stop'
$lines = Get-Content -Encoding UTF8 -LiteralPath (Join-Path $PSScriptRoot 'source/SSOT.original.md')
foreach ($section in $Sections) {
    $pattern = '^#{2,4} ' + [regex]::Escape($section) + '(?:\.|\s)'
    $start = -1
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match $pattern) { $start = $i; break }
    }
    if ($start -lt 0) { throw "Chybějící sekce: $section" }
    $level = ([regex]::Match($lines[$start], '^#+')).Value.Length
    $end = $start + 1
    while ($end -lt $lines.Count -and $lines[$end] -notmatch ('^#{1,' + $level + '} ')) { $end++ }
    $lines[$start..($end - 1)]
}
