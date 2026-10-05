[CmdletBinding()]
param(
    [ValidateSet('web', 'cli')][string]$Mode = 'web',
    [ValidateSet('get_training_summary', 'get_training_context', 'get_latest_workout', 'get_next_workout', 'get_exercise_history', 'search_exercises')]
    [string]$ToolName = 'get_training_summary',
    [switch]$PreflightOnly
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$configPath = Join-Path $PSScriptRoot 'inspector.json'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Project Python is missing. Restore the existing virtual environment first.' }
$coachReadKey = [Environment]::GetEnvironmentVariable('NEXT_SET_COACH_READ_KEY', 'User')
if ([string]::IsNullOrWhiteSpace($coachReadKey)) { throw 'NEXT_SET_COACH_READ_KEY is missing from Windows User environment. No Inspector process was started.' }
try {
    $summary = Invoke-RestMethod -Method Get -Uri 'http://127.0.0.1:8787/training/summary' `
        -Headers @{ 'X-Coach-Read-Key' = $coachReadKey } -TimeoutSec 10 -MaximumRedirection 0
    if ($summary.ok -ne $true) { throw 'Invalid summary response' }
} catch {
    # Do not render ErrorRecord: HTTP exceptions may include request information.
    throw 'Coach API preflight failed. Keep Uvicorn running separately on 127.0.0.1:8787 and confirm it loaded the same Windows User coach key. No Inspector process was started.'
} finally {
    Remove-Variable coachReadKey -ErrorAction SilentlyContinue
}
Write-Host 'Coach credential exists; authenticated API preflight passed.'
Write-Host 'Keep Uvicorn running in Terminal 1. Inspector in Terminal 2 uses STDIO, not OAuth.'
if ($PreflightOnly) { return }
if (-not (Get-Command npx.cmd -ErrorAction SilentlyContinue)) { throw 'npx.cmd is unavailable. Install/use the existing Node.js toolchain.' }
if ($Mode -eq 'web') {
    $ports = @(6274, 6275)
    if ($env:CLIENT_PORT) { $ports[0] = [int]$env:CLIENT_PORT }
    if ($env:MCP_SANDBOX_PORT) { $ports[1] = [int]$env:MCP_SANDBOX_PORT }
    elseif ($env:SERVER_PORT) { $ports[1] = [int]$env:SERVER_PORT }
    foreach ($port in $ports) {
        $probe = New-Object Net.Sockets.TcpClient
        try {
            $attempt = $probe.BeginConnect('127.0.0.1', $port, $null, $null)
            if ($attempt.AsyncWaitHandle.WaitOne(300)) {
                try { $probe.EndConnect($attempt) } catch { }
            }
            if ($probe.Connected) { throw 'Inspector ports are already in use. Stop the old Inspector in its own terminal before starting another. Keep Uvicorn running.' }
        } finally { $probe.Dispose() }
    }
}

# Do not expose write-side credentials to Inspector. Restore the caller's process
# environment afterward. No Windows User/Machine environment values are changed.
$savedSync = $env:NEXT_SET_SYNC_KEY
$savedCoach = $env:NEXT_SET_COACH_READ_KEY
$savedHost = $env:HOST
try {
    Remove-Item Env:NEXT_SET_SYNC_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:NEXT_SET_COACH_READ_KEY -ErrorAction SilentlyContinue
    $env:HOST = '127.0.0.1'
    Push-Location -LiteralPath $projectRoot
    try {
        $inspectorArgs = @('--no-install', '@modelcontextprotocol/inspector', "--$Mode", '--config', $configPath, '--server', 'next-set')
        if ($Mode -eq 'cli') {
            $inspectorArgs += @('--method', 'tools/call', '--tool-name', $ToolName, '--format', 'json')
            if ($ToolName -eq 'search_exercises') { $inspectorArgs += @('--tool-arg', 'query=bench') }
            if ($ToolName -eq 'get_training_context') { $inspectorArgs += @('--tool-arg', 'history_sessions=3') }
            if ($ToolName -eq 'get_exercise_history') { $inspectorArgs += @('--tool-arg', 'exercise_id=2', 'history_sessions=5') }
        }
        & npx.cmd @inspectorArgs
        if ($LASTEXITCODE -ne 0) { throw 'Inspector exited unsuccessfully. This launcher requires the locally installed Inspector 2.9.0 CLI/config behavior; see mcp/README.md.' }
    } finally { Pop-Location }
} finally {
    $env:NEXT_SET_SYNC_KEY = $savedSync
    $env:NEXT_SET_COACH_READ_KEY = $savedCoach
    $env:HOST = $savedHost
    Remove-Variable savedSync, savedCoach, savedHost -ErrorAction SilentlyContinue
}
