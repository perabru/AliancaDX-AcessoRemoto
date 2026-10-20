# Build both roles, signing each generated uninstaller before its container.
# Run locally with a genuinely trusted certificate already in CurrentUser\My.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$CertificateThumbprint,
    [Parameter(Mandatory = $true)][string]$TimestampUrl,
    [Parameter(Mandatory = $true)][string]$SignToolPath,
    [Parameter(Mandatory = $true)][string]$MakensisPath,
    [Parameter(Mandatory = $true)][string]$PythonPath,
    [Parameter(Mandatory = $true)][string]$AssetsPath,
    [Parameter(Mandatory = $true)][string]$OutputDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$names = @('MINIANYDESK_SIGN_CERT_THUMBPRINT', 'MINIANYDESK_SIGN_TIMESTAMP_URL',
           'MINIANYDESK_SIGNTOOL_PATH')
$previous = @{}
foreach ($name in $names) { $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    if ($env:OS -ne 'Windows_NT') { throw 'This signing build requires Windows.' }
    foreach ($tool in @($SignToolPath, $MakensisPath, $PythonPath)) {
        if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) { throw ('Missing tool: ' + $tool) }
    }
    if (-not (Test-Path -LiteralPath $AssetsPath -PathType Container)) {
        throw 'The complete, pinned offline assets directory is required.'
    }
    $signer = Join-Path $PSScriptRoot 'sign_file.ps1'
    # NSIS expands its own dollar variables and %1. Restrict the callback path
    # instead of evaluating or trying to escape arbitrary command text.
    if ($signer -match '["`$%\r\n]') {
        throw 'Extract the project to a local path without quotes, dollars, percent signs or backticks.'
    }
    if (Test-Path -LiteralPath $OutputDirectory) {
        if (-not (Test-Path -LiteralPath $OutputDirectory -PathType Container) -or
            @(Get-ChildItem -LiteralPath $OutputDirectory -Force).Count -gt 0) {
            throw 'Choose an empty output directory to avoid confusing older files with this release.'
        }
    } else { [void](New-Item -ItemType Directory -Path $OutputDirectory) }
    $output = (Resolve-Path -LiteralPath $OutputDirectory).ProviderPath
    $env:MINIANYDESK_SIGN_CERT_THUMBPRINT = $CertificateThumbprint
    $env:MINIANYDESK_SIGN_TIMESTAMP_URL = $TimestampUrl
    $env:MINIANYDESK_SIGNTOOL_PATH = (Resolve-Path -LiteralPath $SignToolPath).ProviderPath
    & powershell.exe -NoProfile -NonInteractive -File $signer -Preflight
    if ($LASTEXITCODE -ne 0) { throw 'Certificate preflight failed. No installers were built.' }
    $buildArguments = @((Join-Path $PSScriptRoot 'build_installers.py'), '--assets',
        (Resolve-Path -LiteralPath $AssetsPath).ProviderPath, '--reuse-assets',
        '--output-dir', $output, '--makensis', (Resolve-Path -LiteralPath $MakensisPath).ProviderPath,
        '--signing-script', $signer, '--require-signing')
    & $PythonPath @buildArguments
    if ($LASTEXITCODE -ne 0) { throw 'Build or signing failed. Output is not a release.' }
    $hashLines = @()
    foreach ($name in @('AliancaDX_Controlador_Setup.exe', 'AliancaDX_Host_Setup.exe')) {
        $file = Join-Path $output $name
        & $SignToolPath verify /pa /all /tw /v $file
        if ($LASTEXITCODE -ne 0) { throw ('Final signature verification failed: ' + $name) }
        $signature = Get-AuthenticodeSignature -LiteralPath $file
        if ($signature.Status -ne 'Valid' -or -not $signature.SignerCertificate -or
            $signature.SignerCertificate.Thumbprint -ne ($CertificateThumbprint -replace '\s', '') -or
            -not $signature.TimeStamperCertificate) { throw ('Unexpected signer or missing timestamp: ' + $name) }
        $hashLines += ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() + '  ' + $name)
    }
    # Hash final signed bytes; no subsequent byte modification is allowed.
    [IO.File]::WriteAllLines((Join-Path $output 'SHA256_ASSINADOS.txt'), $hashLines,
                            (New-Object Text.UTF8Encoding $false))
    Write-Host ('Both roles were built and signed. Final files: ' + $output)
} catch {
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
    exit 1
} finally {
    foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process') }
}
