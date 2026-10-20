# Read-only inspection. Does not execute the installers or change Windows.
[CmdletBinding()]
param(
    [string]$PackageDirectory = (Split-Path -Parent $PSScriptRoot),
    [string]$ReportPath,
    [switch]$RequireVerifiedPublisher
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
try {
    if ($env:OS -ne 'Windows_NT') { throw 'Windows is required to validate Authenticode trust.' }
    $root = (Resolve-Path -LiteralPath $PackageDirectory).ProviderPath
    $files = @('dist\AliancaDX_Controlador_Setup.exe', 'dist\AliancaDX_Host_Setup.exe',
               'easytier_bundle\easytier-core.exe', 'easytier_bundle\Packet.dll',
               'python-3.12.10-amd64.exe')
    $rows = @()
    $ownFilesValid = $true
    foreach ($relative in $files) {
        $path = Join-Path $root $relative
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            $rows += [pscustomobject]@{File = $relative; Status = 'Missing'; SHA256 = $null; Publisher = $null; Timestamp = $false}
            if ($relative.StartsWith('dist\')) { $ownFilesValid = $false }
            continue
        }
        $sig = Get-AuthenticodeSignature -LiteralPath $path
        $publisher = $null
        if ($sig.SignerCertificate) { $publisher = $sig.SignerCertificate.Subject }
        $rows += [pscustomobject]@{File = $relative; Status = $sig.Status.ToString();
            SHA256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant();
            Publisher = $publisher; Timestamp = [bool]$sig.TimeStamperCertificate}
        if ($relative.StartsWith('dist\') -and ($sig.Status -ne 'Valid' -or -not $sig.TimeStamperCertificate)) {
            $ownFilesValid = $false
        }
    }
    $report = [pscustomobject]@{Application = 'AliançaDX Acesso Remoto'; CheckedAtUtc = [DateTime]::UtcNow.ToString('o');
        Scope = 'Windows Authenticode and SHA256; not an antivirus scan or SmartScreen reputation check'; Files = $rows}
    $json = $report | ConvertTo-Json -Depth 5
    if ($ReportPath) {
        # Never overwrite an existing file, including any inspected executable.
        $reportFile = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($ReportPath)
        $reportStream = [IO.File]::Open($reportFile,
            [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        try {
            $reportBytes = [Text.Encoding]::UTF8.GetBytes($json)
            $reportStream.Write($reportBytes, 0, $reportBytes.Length)
        } finally { $reportStream.Dispose() }
    }
    Write-Output $json
    if ($RequireVerifiedPublisher -and -not $ownFilesValid) { exit 1 }
    exit 0
} catch {
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
    exit 1
}
