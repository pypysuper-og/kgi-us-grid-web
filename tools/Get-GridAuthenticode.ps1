# Read-only signature metadata for selected files. Never changes trust or SAC policies.
param([int]$TimeBudgetSeconds = 40)
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
[string[]]$paths = ([Console]::In.ReadToEnd() | ConvertFrom-Json)
$clock = [Diagnostics.Stopwatch]::StartNew()
for ($index = 0; $index -lt $paths.Count; $index++) {
    if ($clock.Elapsed.TotalSeconds -ge $TimeBudgetSeconds) { break }
    try {
        $signature = Get-AuthenticodeSignature -LiteralPath $paths[$index]
        $version = (Get-Item -LiteralPath $paths[$index]).VersionInfo.FileVersion
        $record = @{ index = $index; status = $signature.Status.ToString(); signature_type = $signature.SignatureType.ToString(); file_version = $version }
        if ($signature.SignerCertificate) { $record.signer = $signature.SignerCertificate.Subject }
        if ($signature.TimeStamperCertificate) { $record.timestamp_signer = $signature.TimeStamperCertificate.Subject }
    } catch { $record = @{ index = $index; status = 'INSPECTION_ERROR'; error_type = $_.Exception.GetType().Name } }
    [Console]::Out.WriteLine(($record | ConvertTo-Json -Compress))
    [Console]::Out.Flush()
}
