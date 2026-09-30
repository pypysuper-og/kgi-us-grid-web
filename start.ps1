param(
    [int]$Port = 8765,
    [string]$Runtime = '',
    [switch]$NoBrowser,
    [string]$UvPath = ''
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
. (Join-Path $PSScriptRoot 'bootstrap.ps1')
try {
    $pythonWindowless = Initialize-GridEnvironment -Runtime $Runtime -NoUI:$NoBrowser -UvPath $UvPath
    $script:SetupStage = 'launch_windowless'
    $entryScript = Join-Path $PSScriptRoot 'launch.pyw'
    if (-not (Test-Path -LiteralPath $pythonWindowless)) { throw 'Windowless Python missing' }
    $launchArguments = @('-X', 'utf8', $entryScript, '--port', [string]$Port)
    if ($Runtime) { $launchArguments += @('--runtime', $Runtime) }
    if ($NoBrowser) { $launchArguments += '--no-browser' }
    $quoted = ($launchArguments | ForEach-Object { ConvertTo-NativeArgument $_ }) -join ' '
    Start-Process -FilePath $pythonWindowless -ArgumentList $quoted -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
} catch {
    Show-SetupFailure -NoUI:$NoBrowser
    exit 1
}
