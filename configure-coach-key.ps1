# Run interactively. No secret is generated, printed, or written to source.
$ErrorActionPreference = 'Stop'
$coachSecure = Read-Host 'Enter a strong, distinct coach read key (at least 32 characters)' -AsSecureString
$coachPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($coachSecure)
try {
    $coachValue = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($coachPointer)
    $syncUserValue = [Environment]::GetEnvironmentVariable('NEXT_SET_SYNC_KEY', 'User')
    if ($coachValue.Trim().Length -lt 32 -or $coachValue -eq $syncUserValue -or $coachValue -eq $env:NEXT_SET_SYNC_KEY) {
        throw 'Use a distinct coach key of at least 32 nonblank characters.'
    }
    [Environment]::SetEnvironmentVariable('NEXT_SET_COACH_READ_KEY', $coachValue, 'User')
    $env:NEXT_SET_COACH_READ_KEY = $coachValue
    Write-Host 'Coach read key configured. Restart the app to load it.'
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($coachPointer)
    Remove-Variable coachValue, syncUserValue, coachSecure, coachPointer -ErrorAction SilentlyContinue
}
