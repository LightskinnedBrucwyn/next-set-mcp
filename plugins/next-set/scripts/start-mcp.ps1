$ErrorActionPreference = 'Stop'
# No profiles, output banners, credential reads, or shell-based argument building.
# Relay raw bytes without PowerShell parsing or formatting JSON-RPC.
try {
    Add-Type -TypeDefinition @'
using System.IO;
using System.Threading.Tasks;
public static class NextSetStdioRelay {
    public static Task Pump(Stream source, Stream destination) {
        return Task.Run(() => {
            var buffer = new byte[8192];
            int count;
            while ((count = source.Read(buffer, 0, buffer.Length)) > 0) {
                destination.Write(buffer, 0, count);
                destination.Flush();
            }
        });
    }
}
'@
    $bindingPath = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'NextSet\project.json'
    $binding = Get-Content -LiteralPath $bindingPath -Raw | ConvertFrom-Json
    $projectRoot = [IO.Path]::GetFullPath([string]$binding.projectRoot)
    $pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
    $serverPath = Join-Path $projectRoot 'mcp\server.py'
    if (-not (Test-Path -LiteralPath $serverPath -PathType Leaf)) {
        [Console]::Error.WriteLine('Configured maintained MCP server does not exist. Rerun prepare-coach-plugin.ps1 from the current checkout.')
        exit 1
    }
    if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
        [Console]::Error.WriteLine('Configured project Python does not exist. Restore the project virtual environment.')
        exit 1
    }
    $startInfo = New-Object Diagnostics.ProcessStartInfo
    $startInfo.FileName = $pythonPath
    # Check in the actual MCP Python process before loading the maintained module.
    $startInfo.Arguments = '-B -c "import os,runpy,sys; ''NEXT_SET_SYNC_KEY'' not in os.environ or sys.exit(''Sync credential inheritance blocked.''); sys.argv=[''server'',''stdio'']; runpy.run_module(''server'',run_name=''__main__'')"'
    $startInfo.WorkingDirectory = Join-Path $projectRoot 'mcp'
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardInput = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.EnvironmentVariables.Clear()
    foreach ($name in @('APPDATA', 'LOCALAPPDATA', 'HOMEDRIVE', 'HOMEPATH', 'PATH', 'PATHEXT',
                        'SYSTEMDRIVE', 'SYSTEMROOT', 'TEMP', 'TMP', 'USERNAME', 'USERPROFILE')) {
        $value = [Environment]::GetEnvironmentVariable($name, 'Process')
        if ($null -ne $value) { $startInfo.EnvironmentVariables[$name] = $value }
    }
    $startInfo.EnvironmentVariables['NEXT_SET_COACH_KEY_SOURCE'] = 'windows-user'
    $startInfo.EnvironmentVariables['NEXT_SET_COACH_API_URL'] = 'http://127.0.0.1:8787'
    $startInfo.EnvironmentVariables['PYTHONIOENCODING'] = 'utf-8'
    $startInfo.EnvironmentVariables['PYTHONDONTWRITEBYTECODE'] = '1'
    $child = [Diagnostics.Process]::Start($startInfo)
    $inputTask = [NextSetStdioRelay]::Pump([Console]::OpenStandardInput(), $child.StandardInput.BaseStream)
    $outputTask = [NextSetStdioRelay]::Pump($child.StandardOutput.BaseStream, [Console]::OpenStandardOutput())
    $errorTask = [NextSetStdioRelay]::Pump($child.StandardError.BaseStream, [Console]::OpenStandardError())
    while (-not $child.WaitForExit(100)) {
        if ($inputTask.IsCompleted) {
            $child.StandardInput.Close()
            if (-not $child.WaitForExit(5000)) { $child.Kill(); $child.WaitForExit() }
            break
        }
    }
    $outputTask.GetAwaiter().GetResult()
    $errorTask.GetAwaiter().GetResult()
    $exitCode = $child.ExitCode
    $child.Dispose()
    exit $exitCode
} catch {
    if ($null -ne $child -and -not $child.HasExited) { $child.Kill(); $child.WaitForExit() }
    [Console]::Error.WriteLine('Next_Set launcher failed. Run the local preparation script and verify the existing project virtual environment. No credentials were displayed.')
    exit 1
}
