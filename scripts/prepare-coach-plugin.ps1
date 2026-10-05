$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$sourceRoot = Join-Path $projectRoot 'plugins\next-set'
$personalRoot = [Environment]::GetFolderPath('UserProfile')
$destination = Join-Path $personalRoot '.codex\plugins\next-set'
$marketplacePath = Join-Path $personalRoot '.agents\plugins\marketplace.json'
$bindingPath = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'NextSet\project.json'
$packageFiles = @('plugin.json', 'mcp.json', 'README.md', 'scripts\start-mcp.ps1', 'skills\coach\SKILL.md')

foreach ($relative in $packageFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot $relative) -PathType Leaf)) {
        throw 'A required plugin package file is missing.'
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'mcp\server.py')) -or
    -not (Test-Path -LiteralPath (Join-Path $projectRoot '.venv\Scripts\python.exe'))) {
    throw 'Maintained MCP server or project virtual environment is missing.'
}
if (Test-Path -LiteralPath $marketplacePath) {
    $marketplace = Get-Content -LiteralPath $marketplacePath -Raw | ConvertFrom-Json
    if (-not $marketplace.name -or $null -eq $marketplace.plugins) { throw 'Existing marketplace needs manual review; no files were changed.' }
} else {
    $marketplace = [pscustomobject]@{
        name = 'next-set-local'
        interface = @{ displayName = 'Next_Set Local' }
        plugins = @()
    }
}
$existing = @($marketplace.plugins | Where-Object { $_.name -eq 'next-set' })
if ($existing.Count -gt 1 -or ($existing.Count -eq 1 -and $existing[0].source.path -ne './.codex/plugins/next-set')) {
    throw 'Conflicting personal plugin entry; no files were changed.'
}
if (Test-Path -LiteralPath $destination) {
    foreach ($file in Get-ChildItem -LiteralPath $destination -Recurse -File -Force) {
        $relative = $file.FullName.Substring($destination.Length + 1)
        if ($relative -notin $packageFiles) { throw 'Unexpected file in personal plugin destination; no files were changed.' }
    }
}
$utf8 = New-Object Text.UTF8Encoding($false)
foreach ($relative in $packageFiles) {
    $target = Join-Path $destination $relative
    [IO.Directory]::CreateDirectory((Split-Path -Parent $target)) | Out-Null
    Copy-Item -LiteralPath (Join-Path $sourceRoot $relative) -Destination $target -Force
}
if ($existing.Count -eq 0) {
    $marketplace.plugins = @($marketplace.plugins) + @([pscustomobject]@{
        name = 'next-set'
        source = @{ source = 'local'; path = './.codex/plugins/next-set' }
        policy = @{ installation = 'AVAILABLE'; authentication = 'ON_INSTALL' }
        category = 'Productivity'
    })
}
[IO.Directory]::CreateDirectory((Split-Path -Parent $marketplacePath)) | Out-Null
[IO.File]::WriteAllText($marketplacePath, ($marketplace | ConvertTo-Json -Depth 30), $utf8)
[IO.Directory]::CreateDirectory((Split-Path -Parent $bindingPath)) | Out-Null
[IO.File]::WriteAllText($bindingPath, (@{ projectRoot = $projectRoot } | ConvertTo-Json), $utf8)
Write-Host 'Personal Next_Set package and marketplace prepared. Restart Desktop and install from the personal marketplace.'
Write-Host 'Only a non-secret checkout path was stored. Existing credentials and the maintained server were not changed.'
