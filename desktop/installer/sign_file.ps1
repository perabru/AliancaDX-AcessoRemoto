# Requires Windows PowerShell 5.1+ and the Windows SDK SignTool.
# Uses an existing current-user certificate. Never imports keys or roots.
[CmdletBinding()]
param(
    [string]$Path,
    [switch]$Preflight,
    [string]$CertificateThumbprint = $env:MINIANYDESK_SIGN_CERT_THUMBPRINT,
    [string]$TimestampUrl = $env:MINIANYDESK_SIGN_TIMESTAMP_URL,
    [string]$SignToolPath = $env:MINIANYDESK_SIGNTOOL_PATH
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
try {
    if ($env:OS -ne 'Windows_NT') { throw 'Authenticode signing requires Windows.' }
    $thumbprint = ($CertificateThumbprint -replace '\s', '').ToUpperInvariant()
    if ($thumbprint -notmatch '^[0-9A-F]{40}$') {
        throw 'Select a real code-signing certificate by its 40-character thumbprint.'
    }
    $timestampUri = $null
    if (-not [Uri]::TryCreate($TimestampUrl, [UriKind]::Absolute, [ref]$timestampUri) -or
        $timestampUri.Scheme -notin @('http', 'https') -or $timestampUri.UserInfo) {
        throw 'Supply the RFC 3161 timestamp URL supported by your certificate provider.'
    }
    if (-not $SignToolPath) {
        $command = Get-Command signtool.exe -CommandType Application -ErrorAction SilentlyContinue
        if ($command) { $SignToolPath = $command.Source }
    }
    if (-not $SignToolPath -or -not (Test-Path -LiteralPath $SignToolPath -PathType Leaf)) {
        throw 'Supply the path to the official Windows SDK signtool.exe.'
    }
    $SignToolPath = (Resolve-Path -LiteralPath $SignToolPath).ProviderPath
    $certificate = Get-Item -LiteralPath ('Cert:\CurrentUser\My\' + $thumbprint)
    $now = Get-Date
    if (-not $certificate.HasPrivateKey -or $now -lt $certificate.NotBefore -or
        $now -ge $certificate.NotAfter) {
        throw 'The selected certificate needs an available private key and current validity.'
    }
    if ($certificate.Subject -eq $certificate.Issuer) {
        throw 'A self-signed certificate does not establish a public publisher identity.'
    }
    $usages = @($certificate.Extensions | Where-Object { $_.Oid.Value -eq '2.5.29.37' } |
        ForEach-Object { $_.EnhancedKeyUsages } | ForEach-Object { $_.Value })
    if ('1.3.6.1.5.5.7.3.3' -notin $usages) { throw 'Code Signing EKU is required.' }
    $chain = New-Object System.Security.Cryptography.X509Certificates.X509Chain
    try {
        $chain.ChainPolicy.RevocationMode = 'Online'
        $chain.ChainPolicy.RevocationFlag = 'ExcludeRoot'
        $chain.ChainPolicy.UrlRetrievalTimeout = [TimeSpan]::FromSeconds(20)
        [void]$chain.ChainPolicy.ApplicationPolicy.Add(
            (New-Object System.Security.Cryptography.Oid '1.3.6.1.5.5.7.3.3'))
        if (-not $chain.Build($certificate)) {
            $reasons = ($chain.ChainStatus | ForEach-Object { $_.Status.ToString() }) -join ', '
            throw ('Windows could not validate the signing certificate: ' + $reasons)
        }
    } finally { $chain.Dispose() }
    if ($Preflight) {
        Write-Host ('Local Windows certificate-chain preflight passed for: ' + $certificate.Subject)
        exit 0
    }
    if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw 'The executable to sign does not exist.'
    }
    $resolved = (Resolve-Path -LiteralPath $Path).ProviderPath
    # NSIS can give the generated uninstaller a .tmp name. Validate the PE
    # header rather than its extension; no script or arbitrary data is signed.
    $stream = [IO.File]::OpenRead($resolved)
    $reader = New-Object IO.BinaryReader $stream
    try {
        if ($stream.Length -lt 64 -or $reader.ReadUInt16() -ne 0x5A4D) {
            throw 'The signing target is not a Windows PE executable.'
        }
        $stream.Position = 0x3C
        $peOffset = $reader.ReadUInt32()
        if ($peOffset -lt 64 -or $peOffset -gt ($stream.Length - 24)) {
            throw 'The signing target has an invalid PE offset.'
        }
        $stream.Position = $peOffset
        if ($reader.ReadUInt32() -ne 0x00004550) {
            throw 'The signing target has an invalid PE header.'
        }
    } finally { $reader.Dispose() }
    $existing = Get-AuthenticodeSignature -LiteralPath $resolved
    if ($existing.Status -ne 'NotSigned') {
        throw 'An existing signature was found. It will not be replaced.'
    }
    & $SignToolPath sign /s My /sha1 $thumbprint /u 1.3.6.1.5.5.7.3.3 `
        /fd SHA256 /tr $TimestampUrl /td SHA256 /d 'AliançaDX Acesso Remoto' $resolved
    if ($LASTEXITCODE -ne 0) { throw 'SignTool signing or timestamping failed or warned.' }
    & $SignToolPath verify /pa /all /tw /v $resolved
    if ($LASTEXITCODE -ne 0) { throw 'SignTool signature verification failed or warned.' }
    $signature = Get-AuthenticodeSignature -LiteralPath $resolved
    if ($signature.Status -ne 'Valid' -or -not $signature.SignerCertificate -or
        $signature.SignerCertificate.Thumbprint -ne $thumbprint -or
        -not $signature.TimeStamperCertificate) {
        throw 'The signed file did not pass the expected publisher and timestamp checks.'
    }
    Write-Host ('Verified and timestamped: ' + [IO.Path]::GetFileName($resolved))
    exit 0
} catch {
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
    exit 1
}
